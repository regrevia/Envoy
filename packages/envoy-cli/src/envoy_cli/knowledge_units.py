"""Knowledge units: candidates that carry their evidence or stay candidates.

A unit is a small, reviewable claim distilled from a material. The rules are
deliberately strict in one direction only: nothing becomes ``verified`` without
being checked against the material record, and nothing becomes ``promoted``
without the parts a reader would need to apply it. Everything is reversible —
editing a unit after verification drops it back to ``candidate`` and keeps the
old body as a numbered snapshot.

No model is called from here. A caller supplies the distilled text; this module
only stores it, checks it against evidence and tracks its versions.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from . import evidence_pack, storage

SCHEMA = "envoy.knowledge-unit/2"
SCHEMAS = ("envoy.knowledge-unit/1", SCHEMA)
UNIT_DIR = "knowledge-units"

UNIT_ID = re.compile(r"^ku-[a-f0-9]{16}$")
RELATION_ID = re.compile(r"^kr-[a-f0-9]{16}$")
EVIDENCE_ID = re.compile(r"^ev-[a-f0-9]{16}$")
MATERIAL_ID = evidence_pack.MATERIAL_ID

CANDIDATE = "candidate"
VERIFIED = "verified"
PROMOTED = "promoted"
ARCHIVED = "archived"
STATUSES = (CANDIDATE, VERIFIED, PROMOTED, ARCHIVED)

UNIT_TYPES = ("principle", "procedure", "method", "example", "counterexample", "condition")
RELATION_TYPES = ("supports", "contradicts", "extends", "depends_on")
TEST_KINDS = ("positive", "near_miss", "negative", "boundary")
TITLE_LIMIT = 120
SHORT_LIMIT = 2000


class UnitError(RuntimeError):
    """A unit cannot be stored or advanced; the message is meant for the user."""

    def __init__(self, code: str, message: str, **details) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def units_dir(*, root: Path | None = None) -> Path:
    base = Path(root) if root is not None else storage.resolve_material_root()
    return base / UNIT_DIR


_LOCK_STATE = threading.local()


@contextmanager
def _units_lock(*, root: Path | None = None) -> Iterator[None]:
    """Serialise writers so a compare-and-set cannot be raced.

    Reads never take the lock: every write replaces a whole file atomically, so an
    unlocked reader always sees a complete unit, just possibly a stale one. Writers
    do need it, because checking ``expected_version`` and then writing is two steps
    and two writers can otherwise both see the version they expected to find.
    """

    if getattr(_LOCK_STATE, "depth", 0):
        # Re-entering from the same thread would deadlock: flock is per open file
        # description, so a second acquire on a fresh handle waits on ourselves.
        _LOCK_STATE.depth += 1
        try:
            yield
        finally:
            _LOCK_STATE.depth -= 1
        return
    directory = units_dir(root=root)
    directory.mkdir(parents=True, exist_ok=True)
    handle = open(str(directory / ".units.lock"), "a+b")
    windows = os.name == "nt"
    if windows:  # pragma: no cover - Windows only
        import msvcrt

        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
    else:
        import fcntl
    try:
        if windows:  # pragma: no cover - Windows only
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        _LOCK_STATE.depth = 1
        try:
            yield
        finally:
            _LOCK_STATE.depth = 0
    finally:
        try:
            if windows:  # pragma: no cover - Windows only
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _serialised(writer):
    """Run a writing command under the store lock; ``root`` stays keyword-only."""

    @functools.wraps(writer)
    def wrapped(*args, **kwargs):
        with _units_lock(root=kwargs.get("root")):
            return writer(*args, **kwargs)

    return wrapped


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _write_if_changed(path: Path, text: str) -> bool:
    """Write only when the file does not already hold exactly this text.

    Every mutation rewrites the snapshot of its own version, and a version bump
    first closes the version it leaves behind. Those two writes often carry the
    same bytes the file already has, so comparing first keeps the fsync and the
    ``os.replace`` for the writes that actually change something. A symlink is
    always replaced rather than followed, so it is never read back here.
    """

    if not path.is_symlink() and path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeDecodeError):
            pass
    _write_atomic(path, text)
    return True


CONTENT_FIELDS = (
    "type",
    "title",
    "summary",
    "conditions",
    "steps",
    "limitations",
    "source_refs",
)


def _content_fields(body: dict) -> dict:
    return {key: body.get(key) for key in CONTENT_FIELDS}


def content_hash_of(body: dict) -> str:
    """Fingerprint of what a unit currently says; moves only when the body moves."""

    return hashlib.sha256(_canonical(_content_fields(body)).encode("utf-8")).hexdigest()[:16]


def unit_content_hash(unit: dict) -> str:
    """The stored content hash, or the computed one for records written before it existed."""

    stored = unit.get("content_hash")
    if isinstance(stored, str) and stored.strip():
        return stored.strip()
    return content_hash_of(unit)


def _stamp_content_hash(unit: dict) -> dict:
    unit["content_hash"] = content_hash_of(unit)
    return unit


def _unit_identity(body: dict) -> str:
    # The identity is frozen at creation: later versions of the same claim keep it.
    return "ku-" + content_hash_of(body)


def _require_text(value, field: str, *, limit: int = SHORT_LIMIT) -> str:
    text = str(value or "").strip()
    if not text:
        raise UnitError("invalid_unit", f"{field} is empty")
    if len(text) > limit:
        raise UnitError("invalid_unit", f"{field} is longer than {limit} characters")
    return text


def _require_list(value, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise UnitError("invalid_unit", f"{field} must be a list")
    return [str(item).strip() for item in value if str(item).strip()]


def _validate_ref(ref, field: str) -> dict:
    if not isinstance(ref, dict):
        raise UnitError("invalid_unit", f"{field} must be an object")
    material_id = str(ref.get("material_id") or "")
    evidence_id = str(ref.get("evidence_id") or "")
    if not MATERIAL_ID.fullmatch(material_id):
        raise UnitError("invalid_unit", f"{field} has no material id: {material_id!r}")
    if not EVIDENCE_ID.fullmatch(evidence_id):
        raise UnitError("invalid_unit", f"{field} has no evidence id: {evidence_id!r}")
    locator = ref.get("locator")
    if not isinstance(locator, (dict, str)) or not locator:
        # A quote with no place in the source is not evidence, it is a memory.
        raise UnitError("invalid_unit", f"{field} carries no locator")
    stored = {
        "material_id": material_id,
        "evidence_id": evidence_id,
        "locator": locator,
    }
    revision = ref.get("source_revision")
    if isinstance(revision, str) and revision.strip():
        stored["source_revision"] = revision.strip()
    return stored


def _validate_body(payload: dict) -> dict:
    unit_type = str(payload.get("type") or "").strip()
    if unit_type not in UNIT_TYPES:
        raise UnitError("invalid_unit", f"type must be one of: {', '.join(UNIT_TYPES)}")
    title = _require_text(payload.get("title"), "title", limit=TITLE_LIMIT)
    summary = _require_text(payload.get("summary"), "summary")
    refs = payload.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise UnitError("invalid_unit", "source_refs must name at least one piece of evidence")
    return {
        "schema_version": SCHEMA,
        "type": unit_type,
        "title": title,
        "summary": summary,
        "conditions": _require_list(payload.get("conditions"), "conditions"),
        "steps": _require_list(payload.get("steps"), "steps"),
        "limitations": _require_list(payload.get("limitations"), "limitations"),
        "source_refs": [_validate_ref(ref, f"source_refs[{index}]") for index, ref in enumerate(refs)],
        "tests": _validate_tests(payload.get("tests")),
    }


def _validate_tests(value) -> list:
    if value is None:
        return []
    if not isinstance(value, list):
        raise UnitError("invalid_unit", "tests must be a list")
    stored = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise UnitError("invalid_unit", f"tests[{index}] must be an object")
        kind = str(item.get("kind") or "positive").strip()
        if kind not in TEST_KINDS:
            raise UnitError("invalid_unit", f"tests[{index}].kind must be one of: {', '.join(TEST_KINDS)}")
        question = _require_text(item.get("question"), f"tests[{index}].question")
        stored.append({"kind": kind, "question": question})
    return stored


def _unit_path(unit_id: str, *, root: Path | None = None) -> Path:
    return units_dir(root=root) / f"{unit_id}.json"


def _snapshot_path(unit_id: str, version: int, *, root: Path | None = None) -> Path:
    return units_dir(root=root) / unit_id / f"v{version}.json"


def load_unit(unit_id: str, *, root: Path | None = None) -> dict:
    if not isinstance(unit_id, str) or not UNIT_ID.fullmatch(unit_id):
        raise UnitError("invalid_unit_id", f"not a knowledge unit id: {unit_id!r}")
    path = _unit_path(unit_id, root=root)
    if path.is_symlink() or not path.is_file():
        raise UnitError("unit_missing", f"knowledge unit {unit_id} is not stored")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnitError("unit_unreadable", f"knowledge unit {unit_id} cannot be read: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("unit_id") != unit_id:
        raise UnitError("unit_unreadable", f"knowledge unit {unit_id} is not a unit record")
    return payload


def _save_unit(unit: dict, *, root: Path | None = None) -> dict:
    _write_if_changed(_unit_path(unit["unit_id"], root=root), _canonical(unit))
    return unit


def _snapshot(unit: dict, *, root: Path | None = None) -> Path:
    path = _snapshot_path(unit["unit_id"], int(unit["version"]), root=root)
    _write_if_changed(path, _canonical(unit))
    return path


def _commit(unit: dict, *, root: Path | None = None) -> dict:
    """Store the record and keep the snapshot for its version in step with it.

    A snapshot is the whole record as it stood at one body version, so a mutation
    that does not edit the body rewrites the same ``v<version>.json`` instead of
    leaving an older state of that version behind. ``rollback`` restores only the
    body fields, so rewriting it here cannot change what a rollback brings back.
    """

    text = _canonical(unit)
    _write_if_changed(_unit_path(unit["unit_id"], root=root), text)
    _write_if_changed(_snapshot_path(unit["unit_id"], int(unit["version"]), root=root), text)
    return unit


@_serialised
def create_unit(payload: dict, *, root: Path | None = None) -> dict:
    """Store a new candidate; an identical body returns the existing unit."""

    body = _validate_body(payload if isinstance(payload, dict) else {})
    unit_id = _unit_identity(body)
    path = _unit_path(unit_id, root=root)
    if path.is_file():
        return load_unit(unit_id, root=root)
    unit = {
        "schema_version": SCHEMA,
        "unit_id": unit_id,
        "version": 1,
        "content_hash": content_hash_of(body),
        "status": CANDIDATE,
        "relations": [],
        "verified_at": None,
        "verified_against": None,
        "history": [
            {"version": 1, "at": _now(), "change": "created", "status": CANDIDATE},
        ],
        **body,
    }
    return _commit(unit, root=root)


def _material_revision(built: dict) -> str:
    try:
        return str(built["document"]["source"]["revision"])
    except (KeyError, TypeError):
        return ""


def _verification_fingerprint(unit: dict, revisions: dict[str, str]) -> str:
    """What the unit was checked against: its refs plus the revisions seen at check time."""

    return hashlib.sha256(
        _canonical(
            {
                "refs": unit.get("source_refs") or [],
                "material_revisions": sorted({revisions.get(ref["material_id"], "") for ref in unit.get("source_refs") or []}),
            }
        ).encode("utf-8")
    ).hexdigest()


def _check_refs(unit: dict, *, root: Path | None = None) -> tuple[list[str], list[str], dict[str, str]]:
    """Check every source ref against the material it claims to come from.

    Returns the problems that block verification, the refs that carry no
    ``source_revision`` (visible, never fatal: a ref written before revisions were
    recorded is still a ref), and the revisions observed while checking.
    """

    problems: list[str] = []
    unpinned: list[str] = []
    revisions: dict[str, str] = {}
    material_cache: dict[str, dict] = {}
    for index, ref in enumerate(unit.get("source_refs") or []):
        material_id = ref["material_id"]
        if material_id not in material_cache:
            try:
                stored = evidence_pack.load_material(material_id, root=root)
                built = evidence_pack.build_source_document(stored, material_id)
                material_cache[material_id] = built
            except evidence_pack.PackError as exc:
                problems.append(f"source_refs[{index}]: {exc}")
                material_cache[material_id] = {}
                continue
        built = material_cache[material_id]
        if not built:
            continue
        revisions[material_id] = _material_revision(built)
        index_map = built["index"]
        evidence = index_map.get(ref["evidence_id"])
        if evidence is None:
            problems.append(
                f"source_refs[{index}]: {ref['evidence_id']} is not in {material_id}"
            )
            continue
        revision = _material_revision(built)
        claimed = ref.get("source_revision")
        if not claimed:
            unpinned.append(
                f"source_refs[{index}]: no source_revision recorded; the check is not pinned to a revision"
            )
        elif claimed != revision:
            problems.append(
                f"source_refs[{index}]: material revision moved ({claimed} -> {revision})"
            )
        stored_locator = evidence.get("locator")
        if stored_locator is not None and ref.get("locator") != stored_locator:
            problems.append(f"source_refs[{index}]: locator does not match the material")
    return problems, unpinned, revisions


def _check_expected_version(unit: dict, expected_version: int | None) -> None:
    """Refuse a write aimed at a version that has since moved (compare-and-set)."""

    if expected_version is None:
        return
    if not isinstance(expected_version, int) or isinstance(expected_version, bool):
        raise UnitError(
            "invalid_version",
            f"expected version must be an integer, got {expected_version!r}",
        )
    actual = int(unit["version"])
    if actual != expected_version:
        raise UnitError(
            "version_conflict",
            f"{unit['unit_id']} is at version {actual}, not {expected_version}; reload and retry",
            expected=expected_version,
            actual=actual,
        )


def _refuse_archived(unit: dict, action: str) -> None:
    """Refuse to advance a unit that is archived.

    Archival is a decision about the claim, not a hidden flag: reviving an archived
    unit behind ``update`` or ``rollback`` would silently put a withdrawn claim back
    in play at a new version. ``reopen`` is the one path out, so the refusal names it.
    """

    if unit["status"] == ARCHIVED:
        raise UnitError(
            "unit_archived",
            f"{unit['unit_id']} is archived; reopen it before {action}",
        )


@_serialised
def reopen_unit(unit_id: str, *, expected_version: int | None = None, root: Path | None = None) -> dict:
    """Take an archived unit back into use, as a candidate again.

    Archival never deletes, and a claim archived because its source was gone may be
    worth re-checking when the source reappears. Reopening does not bump ``version``
    (the body did not change) and does not restore any earlier verification: the unit
    comes back as ``candidate`` and has to earn ``verified`` again.
    """

    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    if unit["status"] != ARCHIVED:
        raise UnitError("not_archived", f"{unit_id} is not archived; there is nothing to reopen")
    unit["status"] = CANDIDATE
    unit["verified_at"] = None
    unit["verified_against"] = None
    unit["history"].append(
        {
            "version": unit["version"],
            "at": _now(),
            "change": "reopened",
            "status": CANDIDATE,
            "from_status": ARCHIVED,
        }
    )
    _stamp_content_hash(unit)
    return _commit(unit, root=root)


@_serialised
def verify_unit(unit_id: str, *, expected_version: int | None = None, root: Path | None = None) -> dict:
    """Move a unit to ``verified`` only when every ref still resolves.

    ``unpinned_refs`` counts the refs that carry no ``source_revision``; those are
    reported back rather than refused, so units written before revisions were
    recorded stay verifiable.
    """

    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    _refuse_archived(unit, "verifying")
    problems, unpinned, revisions = _check_refs(unit, root=root)
    problems = problems + _check_relations(unit)
    if problems:
        raise UnitError("evidence_unresolved", "evidence does not resolve", problems=problems)
    unit["status"] = VERIFIED
    unit["verified_at"] = _now()
    unit["verified_against"] = _verification_fingerprint(unit, revisions)
    unit["history"].append(
        {"version": unit["version"], "at": unit["verified_at"], "change": "verified", "status": VERIFIED}
    )
    _stamp_content_hash(unit)
    _commit(unit, root=root)
    return {**unit, "unpinned_refs": len(unpinned), "warnings": list(unpinned)}


@_serialised
def promote_unit(unit_id: str, *, expected_version: int | None = None, root: Path | None = None) -> dict:
    """Move a verified unit to ``promoted`` once it is usable, not merely true.

    Promotion re-checks the evidence instead of trusting the stored status: a unit
    whose material moved on since ``verify`` is refused with ``verification_stale``
    rather than promoted on a fingerprint that no longer describes anything.
    """

    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    _refuse_archived(unit, "promoting")
    if unit["status"] != VERIFIED:
        raise UnitError("needs_verification", f"{unit_id} is {unit['status']}; verify its evidence first")
    missing = [
        field
        for field in ("conditions", "steps", "limitations")
        if not unit.get(field)
    ]
    if not unit.get("tests"):
        missing.append("tests")
    if missing:
        raise UnitError(
            "unit_incomplete",
            "a promoted unit needs conditions, steps, limitations and at least one test",
            missing=missing,
        )
    problems, unpinned, revisions = _check_refs(unit, root=root)
    problems = problems + _check_relations(unit)
    if problems:
        raise UnitError(
            "evidence_unresolved",
            "evidence no longer resolves; verify the unit again",
            problems=problems,
        )
    current = _verification_fingerprint(unit, revisions)
    if unit.get("verified_against") != current:
        raise UnitError(
            "verification_stale",
            f"{unit_id} was verified against evidence that has since changed; verify it again",
            expected=unit.get("verified_against"),
            actual=current,
        )
    unit["status"] = PROMOTED
    unit["history"].append(
        {"version": unit["version"], "at": _now(), "change": "promoted", "status": PROMOTED}
    )
    _stamp_content_hash(unit)
    _commit(unit, root=root)
    return {**unit, "unpinned_refs": len(unpinned), "warnings": list(unpinned)}


@_serialised
def archive_unit(
    unit_id: str, *, reason: str = "", expected_version: int | None = None, root: Path | None = None
) -> dict:
    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    if unit["status"] == ARCHIVED:
        raise UnitError("unit_archived", f"{unit_id} is already archived")
    unit["status"] = ARCHIVED
    entry = {"version": unit["version"], "at": _now(), "change": "archived", "status": ARCHIVED}
    if reason.strip():
        entry["reason"] = reason.strip()
    unit["history"].append(entry)
    _stamp_content_hash(unit)
    return _commit(unit, root=root)


@_serialised
def update_unit(
    unit_id: str, patch: dict, *, expected_version: int | None = None, root: Path | None = None
) -> dict:
    """Edit a unit into a new version; any earlier verification is now void."""

    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    _refuse_archived(unit, "updating")
    if not isinstance(patch, dict):
        raise UnitError("invalid_unit", "update needs an object")
    merged = copy.deepcopy(unit)
    for key in ("type", "title", "summary", "conditions", "steps", "limitations", "source_refs", "tests"):
        if key in patch:
            merged[key] = patch[key]
    body = _validate_body(merged)
    revised = {**copy.deepcopy(unit), **body}
    if _canonical(body) == _canonical({key: unit.get(key) for key in body}):
        return unit
    _snapshot(unit, root=root)  # closing state of the version being left behind
    revised["version"] = int(unit["version"]) + 1
    previous = unit["status"]
    revised["status"] = CANDIDATE
    revised["verified_at"] = None
    revised["verified_against"] = None
    _stamp_content_hash(revised)
    revised["history"] = list(unit["history"]) + [
        {
            "version": revised["version"],
            "at": _now(),
            "change": "updated",
            "status": CANDIDATE,
            "from_status": previous,
        }
    ]
    return _commit(revised, root=root)


@_serialised
def rollback_unit(
    unit_id: str, version: int, *, expected_version: int | None = None, root: Path | None = None
) -> dict:
    """Restore an older body as a new version, so history is never rewritten."""

    unit = load_unit(unit_id, root=root)
    _check_expected_version(unit, expected_version)
    _refuse_archived(unit, "rolling back")
    if not isinstance(version, int) or version < 1 or version >= int(unit["version"]):
        raise UnitError("invalid_version", f"version must be between 1 and {int(unit['version']) - 1}")
    snapshot = _snapshot_path(unit_id, version, root=root)
    if not snapshot.is_file():
        raise UnitError("version_missing", f"{unit_id} has no stored version {version}")
    try:
        old = json.loads(snapshot.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnitError("version_unreadable", f"{unit_id} v{version} cannot be read: {exc}") from exc
    rollback = copy.deepcopy(unit)
    for key in ("type", "title", "summary", "conditions", "steps", "limitations", "source_refs", "tests"):
        rollback[key] = old.get(key)
    body = _validate_body(rollback)
    _snapshot(unit, root=root)  # closing state of the version being left behind
    rollback = {**rollback, **body}
    rollback["version"] = int(unit["version"]) + 1
    rollback["status"] = CANDIDATE
    rollback["verified_at"] = None
    rollback["verified_against"] = None
    _stamp_content_hash(rollback)
    rollback["history"] = list(unit["history"]) + [
        {
            "version": rollback["version"],
            "at": _now(),
            "change": "rolled_back",
            "status": CANDIDATE,
            "restored_version": version,
        }
    ]
    return _commit(rollback, root=root)


def _relation_fingerprint(entry: dict) -> str:
    """Fingerprint of what a relation asserts: its type, its ends and its evidence."""

    payload = {
        key: entry.get(key)
        for key in ("type", "from_unit_id", "to_unit_id", "evidence", "inverse_of")
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def _relation_id(entry: dict) -> str:
    payload = {
        key: entry.get(key)
        for key in ("type", "from_unit_id", "to_unit_id", "evidence", "inverse_of", "at")
    }
    return "kr-" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:16]


def relation_view(unit: dict) -> list[dict]:
    """Normalize stored relations, including the inline entries written before v2.

    Pre-v2 records hold ``{"type", "to", "evidence", "at"}`` on the source only.
    They stay readable: the view fills in the missing ends and marks them
    ``legacy`` instead of refusing the unit.
    """

    rows = []
    for entry in unit.get("relations") or []:
        if not isinstance(entry, dict):
            continue
        to_unit_id = entry.get("to_unit_id") or entry.get("to")
        rows.append(
            {
                "relation_id": entry.get("relation_id"),
                "type": entry.get("type"),
                "from_unit_id": entry.get("from_unit_id") or unit.get("unit_id"),
                "to_unit_id": to_unit_id,
                "from_version": entry.get("from_version"),
                "inverse_of": entry.get("inverse_of"),
                "evidence": entry.get("evidence") or [],
                "at": entry.get("at"),
                "content_hash": entry.get("content_hash"),
                "legacy": "to_unit_id" not in entry,
            }
        )
    return rows


def _check_relations(unit: dict) -> list[str]:
    """Check that every stored relation entry still says what its fingerprint says.

    Legacy inline entries carry no fingerprint and are skipped: they are readable,
    they are simply not verifiable.
    """

    problems: list[str] = []
    for index, entry in enumerate(unit.get("relations") or []):
        if not isinstance(entry, dict) or "to_unit_id" not in entry:
            continue
        relation_id = entry.get("relation_id")
        if not isinstance(relation_id, str) or not RELATION_ID.fullmatch(relation_id):
            problems.append(f"relations[{index}]: relation_id is not a relation id")
            continue
        if entry.get("content_hash") != _relation_fingerprint(entry):
            problems.append(f"relations[{index}]: content hash does not match the entry")
    return problems


def list_relations(unit_id: str, *, root: Path | None = None) -> list[dict]:
    """Every relation stored on a unit, in both directions."""

    return relation_view(load_unit(unit_id, root=root))


def _refuse_existing_relation(source: dict, target: dict, relation: str, from_id: str, to_id: str) -> None:
    """Refuse a relation that is already recorded between these two units.

    The two ends of a relation are one fact: the asserted entry on one unit and the
    mirror carrying ``inverse_of`` on the other. So the pair is unordered here — a
    relation already stored ``to_id <relation> from_id`` is the same fact written
    the other way round, and asserting it again would append a second row for one
    relation instead of reporting ``relation_exists``.
    """

    for unit in (source, target):
        for existing in relation_view(unit):
            if existing["inverse_of"] or existing["type"] != relation:
                continue
            ends = {existing["from_unit_id"], existing["to_unit_id"]}
            if ends == {from_id, to_id}:
                raise UnitError(
                    "relation_exists",
                    f"{from_id} and {to_id} already relate through {relation}",
                )


@_serialised
def relate_units(
    from_id: str,
    relation: str,
    to_id: str,
    evidence,
    *,
    expected_version: int | None = None,
    expected_target_version: int | None = None,
    root: Path | None = None,
) -> dict:
    """Record how two units bear on each other; the relation must cite evidence.

    The relation is stored as a standalone record on both units: ``from`` gets the
    asserted entry, ``to`` gets the reversed mirror carrying ``inverse_of``, so the
    pair is one fact with two ends rather than two independent claims. Each end has
    its own ``relation_id``, its own ``content_hash`` and the version it was
    written against, so either side stays checkable on its own.

    Because the write lands on two records, it takes two compare-and-set gates:
    ``expected_version`` for the source and ``expected_target_version`` for the
    target. A caller that saw both sides can then refuse the write if either moved,
    instead of annotating a version it never read.
    """

    if relation not in RELATION_TYPES:
        raise UnitError("invalid_relation", f"relation must be one of: {', '.join(RELATION_TYPES)}")
    source = load_unit(from_id, root=root)
    _check_expected_version(source, expected_version)
    target = load_unit(to_id, root=root)
    _check_expected_version(target, expected_target_version)
    if from_id == to_id:
        raise UnitError("invalid_relation", "a unit cannot relate to itself")
    refs = source.get("source_refs") or []
    known = {(ref["material_id"], ref["evidence_id"]) for ref in refs}
    known |= {(ref["material_id"], ref["evidence_id"]) for ref in target.get("source_refs") or []}
    if not isinstance(evidence, list) or not evidence:
        raise UnitError("invalid_relation", "a relation needs at least one piece of evidence")
    checked = []
    for index, item in enumerate(evidence):
        ref = _validate_ref(item, f"evidence[{index}]")
        if (ref["material_id"], ref["evidence_id"]) not in known:
            raise UnitError(
                "evidence_unresolved",
                f"evidence[{index}] is not cited by either unit; cite it on a unit first",
            )
        checked.append(ref)
    _refuse_existing_relation(source, target, relation, from_id, to_id)
    at = _now()
    entry = {
        "relation_id": "",
        "type": relation,
        "from_unit_id": from_id,
        "to_unit_id": to_id,
        "from_version": int(source["version"]),
        "inverse_of": None,
        "evidence": checked,
        "at": at,
        "content_hash": "",
    }
    entry["content_hash"] = _relation_fingerprint(entry)
    entry["relation_id"] = _relation_id(entry)
    inverse = {
        "relation_id": "",
        "type": relation,
        "from_unit_id": to_id,
        "to_unit_id": from_id,
        "from_version": int(target["version"]),
        "inverse_of": entry["relation_id"],
        "evidence": copy.deepcopy(checked),
        "at": at,
        "content_hash": "",
    }
    inverse["content_hash"] = _relation_fingerprint(inverse)
    inverse["relation_id"] = _relation_id(inverse)
    updated = copy.deepcopy(source)
    updated.setdefault("relations", []).append(entry)
    _stamp_content_hash(updated)
    _commit(updated, root=root)
    mirrored = copy.deepcopy(target)
    mirrored.setdefault("relations", []).append(inverse)
    _stamp_content_hash(mirrored)
    try:
        _commit(mirrored, root=root)
    except Exception as exc:
        # The two ends are one fact, so a relation must never survive on only one
        # of them: if the mirror cannot be written, put the asserted end back.
        try:
            _commit(source, root=root)
        except Exception:
            raise UnitError(
                "relation_half_written",
                f"{from_id} was annotated but {to_id} could not be updated, and the "
                f"first write could not be undone; check both units",
            ) from exc
        raise
    return updated


def list_units(*, status: str | None = None, root: Path | None = None) -> list[dict]:
    directory = units_dir(root=root)
    if status is not None and status not in STATUSES:
        raise UnitError("invalid_status", f"status must be one of: {', '.join(STATUSES)}")
    rows = []
    if not directory.is_dir():
        return rows
    for path in sorted(directory.glob("ku-*.json")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            unit = load_unit(path.stem, root=root)
        except UnitError:
            continue
        if status is not None and unit["status"] != status:
            continue
        rows.append(unit)
    return rows


def render_unit(unit: dict) -> str:
    lines = [
        f"{unit['unit_id']}  v{unit['version']}  [{unit['status']}]  {unit['type']}",
        f"  {unit['title']}",
        f"  {unit['summary']}",
        f"  content {unit_content_hash(unit)}",
    ]
    for field in ("conditions", "steps", "limitations"):
        for item in unit.get(field) or []:
            lines.append(f"  {field[:-1]}: {item}")
    for ref in unit.get("source_refs") or []:
        lines.append(f"  evidence {ref['material_id']} {ref['evidence_id']}")
    for relation in relation_view(unit):
        mirror = f" (inverse of {relation['inverse_of']})" if relation["inverse_of"] else ""
        lines.append(f"  relation {relation['type']} -> {relation['to_unit_id']}{mirror}")
    return "\n".join(lines)


def render_list(rows: list[dict]) -> str:
    if not rows:
        return "No knowledge units."
    return "\n".join(
        f"{row['unit_id']}  v{row['version']:<3} [{row['status']:<9}] {row['title']}" for row in rows
    )

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
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import evidence_pack, storage

SCHEMA = "envoy.knowledge-unit/1"
UNIT_DIR = "knowledge-units"

UNIT_ID = re.compile(r"^ku-[a-f0-9]{16}$")
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


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
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


def _unit_identity(body: dict) -> str:
    fields = {
        key: body.get(key)
        for key in ("type", "title", "summary", "conditions", "steps", "limitations", "source_refs")
    }
    return "ku-" + hashlib.sha256(_canonical(fields).encode("utf-8")).hexdigest()[:16]


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
    _write_atomic(_unit_path(unit["unit_id"], root=root), _canonical(unit))
    return unit


def _snapshot(unit: dict, *, root: Path | None = None) -> Path:
    path = _snapshot_path(unit["unit_id"], int(unit["version"]), root=root)
    _write_atomic(path, _canonical(unit))
    return path


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
        "status": CANDIDATE,
        "relations": [],
        "verified_at": None,
        "verified_against": None,
        "history": [
            {"version": 1, "at": _now(), "change": "created", "status": CANDIDATE},
        ],
        **body,
    }
    _save_unit(unit, root=root)
    _snapshot(unit, root=root)
    return unit


def _check_refs(unit: dict, *, root: Path | None = None) -> list[str]:
    """Check every source ref against the material it claims to come from."""

    problems: list[str] = []
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
        index_map = built["index"]
        evidence = index_map.get(ref["evidence_id"])
        if evidence is None:
            problems.append(
                f"source_refs[{index}]: {ref['evidence_id']} is not in {material_id}"
            )
            continue
        revision = built["document"]["source"]["revision"]
        claimed = ref.get("source_revision")
        if claimed and claimed != revision:
            problems.append(
                f"source_refs[{index}]: material revision moved ({claimed} -> {revision})"
            )
        stored_locator = evidence.get("locator")
        if stored_locator is not None and ref.get("locator") != stored_locator:
            problems.append(f"source_refs[{index}]: locator does not match the material")
    return problems


def verify_unit(unit_id: str, *, root: Path | None = None) -> dict:
    """Move a unit to ``verified`` only when every ref still resolves."""

    unit = load_unit(unit_id, root=root)
    if unit["status"] == ARCHIVED:
        raise UnitError("unit_archived", f"{unit_id} is archived; reopen it before verifying")
    problems = _check_refs(unit, root=root)
    if problems:
        raise UnitError("evidence_unresolved", "evidence does not resolve", problems=problems)
    fingerprint = hashlib.sha256(
        _canonical(
            {
                "refs": unit["source_refs"],
                "material_revisions": sorted(
                    {str(ref.get("source_revision") or "") for ref in unit["source_refs"]}
                ),
            }
        ).encode("utf-8")
    ).hexdigest()
    unit["status"] = VERIFIED
    unit["verified_at"] = _now()
    unit["verified_against"] = fingerprint
    unit["history"].append(
        {"version": unit["version"], "at": unit["verified_at"], "change": "verified", "status": VERIFIED}
    )
    return _save_unit(unit, root=root)


def promote_unit(unit_id: str, *, root: Path | None = None) -> dict:
    """Move a verified unit to ``promoted`` once it is usable, not merely true."""

    unit = load_unit(unit_id, root=root)
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
    unit["status"] = PROMOTED
    unit["history"].append(
        {"version": unit["version"], "at": _now(), "change": "promoted", "status": PROMOTED}
    )
    return _save_unit(unit, root=root)


def archive_unit(unit_id: str, *, reason: str = "", root: Path | None = None) -> dict:
    unit = load_unit(unit_id, root=root)
    unit["status"] = ARCHIVED
    entry = {"version": unit["version"], "at": _now(), "change": "archived", "status": ARCHIVED}
    if reason.strip():
        entry["reason"] = reason.strip()
    unit["history"].append(entry)
    return _save_unit(unit, root=root)


def update_unit(unit_id: str, patch: dict, *, root: Path | None = None) -> dict:
    """Edit a unit into a new version; any earlier verification is now void."""

    unit = load_unit(unit_id, root=root)
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
    _snapshot(unit, root=root)
    revised["version"] = int(unit["version"]) + 1
    previous = unit["status"]
    revised["status"] = CANDIDATE
    revised["verified_at"] = None
    revised["verified_against"] = None
    revised["history"] = list(unit["history"]) + [
        {
            "version": revised["version"],
            "at": _now(),
            "change": "updated",
            "status": CANDIDATE,
            "from_status": previous,
        }
    ]
    _save_unit(revised, root=root)
    _snapshot(revised, root=root)
    return revised


def rollback_unit(unit_id: str, version: int, *, root: Path | None = None) -> dict:
    """Restore an older body as a new version, so history is never rewritten."""

    unit = load_unit(unit_id, root=root)
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
    _snapshot(unit, root=root)
    rollback = {**rollback, **body}
    rollback["version"] = int(unit["version"]) + 1
    rollback["status"] = CANDIDATE
    rollback["verified_at"] = None
    rollback["verified_against"] = None
    rollback["history"] = list(unit["history"]) + [
        {
            "version": rollback["version"],
            "at": _now(),
            "change": "rolled_back",
            "status": CANDIDATE,
            "restored_version": version,
        }
    ]
    _save_unit(rollback, root=root)
    _snapshot(rollback, root=root)
    return rollback


def relate_units(from_id: str, relation: str, to_id: str, evidence, *, root: Path | None = None) -> dict:
    """Record how two units bear on each other; the relation must cite evidence."""

    if relation not in RELATION_TYPES:
        raise UnitError("invalid_relation", f"relation must be one of: {', '.join(RELATION_TYPES)}")
    source = load_unit(from_id, root=root)
    target = load_unit(to_id, root=root)
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
    updated = copy.deepcopy(source)
    for existing in updated["relations"]:
        if existing["type"] == relation and existing["to"] == to_id:
            raise UnitError("relation_exists", f"{from_id} already {relation} {to_id}")
    updated["relations"].append({"type": relation, "to": to_id, "evidence": checked, "at": _now()})
    return _save_unit(updated, root=root)


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
    ]
    for field in ("conditions", "steps", "limitations"):
        for item in unit.get(field) or []:
            lines.append(f"  {field[:-1]}: {item}")
    for ref in unit.get("source_refs") or []:
        lines.append(f"  evidence {ref['material_id']} {ref['evidence_id']}")
    for relation in unit.get("relations") or []:
        lines.append(f"  relation {relation['type']} -> {relation['to']}")
    return "\n".join(lines)


def render_list(rows: list[dict]) -> str:
    if not rows:
        return "No knowledge units."
    return "\n".join(
        f"{row['unit_id']}  v{row['version']:<3} [{row['status']:<9}] {row['title']}" for row in rows
    )

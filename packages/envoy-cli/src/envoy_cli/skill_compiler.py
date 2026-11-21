"""Compile a promoted knowledge unit into a skill another runtime can load.

This is a local, deterministic step. No model is called, no unit is modified, and
the artifacts land outside the unit store: the same unit produces the same bytes
on every machine. What the compiler refuses to do is the point of it — a unit
that is only a candidate, or one whose evidence has moved since it was verified,
does not compile, because a skill is read as an instruction and an instruction
with a broken citation is worse than no skill at all.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from . import knowledge_units

SCHEMA = "envoy.skill/1"
TARGETS = ("codex", "claude", "dsh")
SKILL_NAME_LIMIT = 48
DESCRIPTION_LIMIT = 400
SLUG = re.compile(r"[^a-z0-9]+")

# Where each runtime looks for skills, relative to the project or home directory.
INSTALL_PATHS = {
    "codex": ".codex/skills/{name}/SKILL.md",
    "claude": ".claude/skills/{name}/SKILL.md",
    "dsh": ".dsh/skills/{name}/SKILL.md",
}


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _write_if_changed(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.is_file() and not path.is_symlink() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def skills_dir(*, root: Path | None = None) -> Path:
    """Artifacts sit next to the unit store, never inside it."""

    return knowledge_units.units_dir(root=root).parent / "skills"


def _slug(text: str, limit: int) -> str:
    slug = SLUG.sub("-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "unit"


def skill_name(unit: dict) -> str:
    """Readable and stable: the title, plus the frozen identity it came from."""

    stem = _slug(str(unit.get("title") or ""), SKILL_NAME_LIMIT - 9)
    return f"{stem}-{unit['unit_id'][3:11]}"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _description(unit: dict) -> str:
    text = _one_line(unit.get("summary"))
    trigger = _one_line(unit.get("trigger"))
    if trigger:
        text = f"{text} Use when {trigger.rstrip('.')}."
    if len(text) > DESCRIPTION_LIMIT:
        text = text[: DESCRIPTION_LIMIT - 1].rstrip() + "…"
    return text


def _yaml_string(text: str) -> str:
    # Double-quoted style with the two escapes YAML requires inside it; the
    # output is byte-for-byte reproducible and survives every parser we target.
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _frontmatter(unit: dict, target: str) -> str:
    lines = [
        "---",
        f"name: {_yaml_string(skill_name(unit))}",
        f"description: {_yaml_string(_description(unit))}",
    ]
    if target == "dsh":
        # DSH carries the identity in metadata; the other two want it in the
        # manifest only, so the frontmatter stays within what they validate.
        lines.extend(
            [
                "metadata:",
                f"  unit_id: {_yaml_string(unit['unit_id'])}",
                f"  unit_version: {int(unit['version'])}",
                f"  content_hash: {_yaml_string(knowledge_units.unit_content_hash(unit))}",
                f"  schema: {_yaml_string(SCHEMA)}",
            ]
        )
    lines.append("---")
    return "\n".join(lines)


def _locator_text(locator) -> str:
    if isinstance(locator, str):
        return _one_line(locator)
    text = json.dumps(locator, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= 240 else text[:239] + "…"


def _evidence_line(ref: dict) -> str:
    revision = ref.get("source_revision") or "(unrecorded)"
    return (
        f"- `{ref['material_id']}` `{ref['evidence_id']}` at "
        f"`{_locator_text(ref.get('locator'))}`, material revision `{revision}`"
    )


def render_skill(unit: dict, target: str) -> str:
    """The skill body. Pure function of the unit and the target."""

    lines = [_frontmatter(unit, target), "", f"# {_one_line(unit['title'])}", "", _one_line(unit["summary"])]
    sections = (
        ("When to Use This Skill", [unit.get("trigger")] if _one_line(unit.get("trigger")) else []),
        ("Inputs", list(unit.get("inputs") or [])),
        ("Conditions", list(unit.get("conditions") or [])),
        ("Limitations", list(unit.get("limitations") or [])),
    )
    for heading, items in sections:
        if not items:
            continue
        lines.extend(["", f"## {heading}", ""])
        lines.extend(f"- {_one_line(item)}" for item in items)
    steps = list(unit.get("steps") or [])
    if steps:
        lines.extend(["", "## Steps", ""])
        lines.extend(f"{index}. {_one_line(step)}" for index, step in enumerate(steps, start=1))
    refs = list(unit.get("source_refs") or [])
    if refs:
        lines.extend(
            [
                "",
                "## Evidence",
                "",
                "Every claim above was checked against these places in the recorded material.",
                "",
            ]
        )
        lines.extend(_evidence_line(ref) for ref in refs)
    tests = list(unit.get("tests") or [])
    if tests:
        lines.extend(["", "## Checks", ""])
        lines.extend(f"- {item['kind']}: {_one_line(item['question'])}" for item in tests)
    return "\n".join(lines) + "\n"


def _manifest(unit: dict, target: str, name: str) -> dict:
    """What a reviewer needs to trace the skill back without the unit store."""

    return {
        "schema": SCHEMA,
        "target": target,
        "skill_name": name,
        "install_path": INSTALL_PATHS[target].format(name=name),
        "unit": {
            "unit_id": unit["unit_id"],
            "schema_version": unit.get("schema_version"),
            "version": int(unit["version"]),
            "content_hash": knowledge_units.unit_content_hash(unit),
            "verified_against": unit.get("verified_against"),
        },
        "evidence": [
            {
                "material_id": ref["material_id"],
                "evidence_id": ref["evidence_id"],
                "locator": ref.get("locator"),
                "source_revision": ref.get("source_revision"),
            }
            for ref in unit.get("source_refs") or []
        ],
        "files": ["SKILL.md", "manifest.json"],
    }


def _refuse_unusable(unit: dict, *, root: Path | None) -> None:
    """A skill is an instruction; it is only cut from evidence that still resolves."""

    if unit["status"] == knowledge_units.ARCHIVED:
        raise knowledge_units.UnitError(
            "unit_archived", f"{unit['unit_id']} is archived; reopen it before compiling"
        )
    if unit["status"] != knowledge_units.PROMOTED:
        raise knowledge_units.UnitError(
            "needs_promotion",
            f"{unit['unit_id']} is {unit['status']}; only a promoted unit compiles into a skill",
        )
    problems, _unpinned, revisions = knowledge_units._check_refs(unit, root=root)
    problems = problems + knowledge_units._check_relations(unit)
    if problems:
        raise knowledge_units.UnitError(
            "evidence_unresolved",
            "evidence no longer resolves; verify the unit again before compiling",
            problems=problems,
        )
    current = knowledge_units._verification_fingerprint(unit, revisions)
    if unit.get("verified_against") != current:
        raise knowledge_units.UnitError(
            "verification_stale",
            f"{unit['unit_id']} was verified against evidence that has since changed; verify it again",
            expected=unit.get("verified_against"),
            actual=current,
        )


def compile_unit(
    unit_id: str,
    *,
    target: str,
    out: Path | None = None,
    force: bool = False,
    root: Path | None = None,
) -> dict:
    """Write ``SKILL.md`` and ``manifest.json`` for one promoted unit."""

    if target not in TARGETS:
        raise knowledge_units.UnitError(
            "invalid_request", f"target must be one of: {', '.join(TARGETS)}"
        )
    unit = knowledge_units.load_unit(unit_id, root=root)
    _refuse_unusable(unit, root=root)
    name = skill_name(unit)
    directory = Path(out) if out is not None else skills_dir(root=root) / name
    existing = directory / "manifest.json"
    if existing.is_file() and not force:
        try:
            previous = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        recorded = str((previous.get("unit") or {}).get("unit_id") or "")
        if recorded and recorded != unit_id:
            raise knowledge_units.UnitError(
                "output_conflict",
                f"{directory} already holds a skill compiled from {recorded}; pass --force to replace it",
            )
    manifest = _manifest(unit, target, name)
    changed = []
    if _write_if_changed(directory / "SKILL.md", render_skill(unit, target)):
        changed.append("SKILL.md")
    if _write_if_changed(directory / "manifest.json", _canonical(manifest)):
        changed.append("manifest.json")
    return {
        "unit_id": unit_id,
        "unit_version": int(unit["version"]),
        "target": target,
        "skill_name": name,
        "output_dir": str(directory),
        "written": changed,
        "manifest": manifest,
        "content_digest": hashlib.sha256(
            (render_skill(unit, target) + _canonical(manifest)).encode("utf-8")
        ).hexdigest(),
    }

"""Knowledge packs: several units applied together, in the order they must be read.

A single skill answers one question. Real work usually needs a set of them, and
the set is not a list a person writes down: it is what the units already say
about each other. ``composed_of`` and ``depends_on`` pull another unit in — the
pack cannot be applied without it — while ``alternative_to`` points at a way out
when one path is blocked, which is a choice rather than a requirement.

So the pack is derived, never invented: the closure is walked over relations that
were each recorded with evidence at both ends, every member has to be a promoted
unit whose evidence still resolves, and the order is dependency-first so the
reader never meets a step that assumes a unit they have not read.

Like the skill compiler this is a deterministic local step. No model is called,
no unit is modified, and the same set of units produces the same bytes. The
refusals are the product: a pack that quietly dropped an unverified dependency
would be a bundle of instructions with a hole in it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import knowledge_units, skill_compiler

SCHEMA = "envoy.knowledge-pack/1"
PACK_LIMIT = 24
"""A pack is what a reader can hold at once; past this it is a library, not a pack."""


def _canonical(payload) -> str:
    return skill_compiler._canonical(payload)


def _write_if_changed(path: Path, text: str) -> bool:
    return skill_compiler._write_if_changed(path, text)


def _slug(value: str, limit: int) -> str:
    return skill_compiler._slug(value, limit)


def _one_line(value: str) -> str:
    return skill_compiler._one_line(value)


def pack_name(roots: list[dict]) -> str:
    """A directory name that says which question the pack answers."""

    first = _slug(str(roots[0]["title"] or ""), skill_compiler.SKILL_NAME_LIMIT - 5)
    return f"{first}-pack"


def _requiring(unit: dict) -> list[dict]:
    """Relations that pull the target in, in a stable order."""

    rows = [
        entry
        for entry in knowledge_units.relation_view(unit)
        if entry.get("type") in knowledge_units.REQUIRING_RELATIONS and entry.get("to_unit_id")
    ]
    rows.sort(key=lambda entry: (str(entry.get("type")), str(entry.get("to_unit_id"))))
    return rows


def _fallbacks(unit: dict) -> list[dict]:
    rows = [
        entry
        for entry in knowledge_units.relation_view(unit)
        if entry.get("type") in knowledge_units.FALLBACK_RELATIONS and entry.get("to_unit_id")
    ]
    rows.sort(key=lambda entry: str(entry.get("to_unit_id")))
    return rows


def _usable(unit: dict, *, root: Path | None) -> tuple[bool, str]:
    try:
        skill_compiler._refuse_unusable(unit, root=root)
    except knowledge_units.UnitError as exc:
        return False, f"{exc.code}: {exc}"
    return True, ""


def resolve_pack(unit_ids: list[str], *, root: Path | None = None) -> dict:
    """Walk the dependency closure of ``unit_ids`` and check every member.

    Returns the members in dependency-first order, the fallbacks those members
    point at, and the cycles that were cut so the walk terminated. Raises
    ``UnitError`` from the member that cannot be part of a pack — the code and
    message name the offending unit, so the caller never has to guess which
    dependency broke.
    """

    if not isinstance(unit_ids, list) or not unit_ids:
        raise knowledge_units.UnitError(
            "invalid_request", "a pack needs at least one starting unit"
        )
    roots: list[dict] = []
    seen_roots: set[str] = set()
    for unit_id in unit_ids:
        unit = knowledge_units.load_unit(unit_id, root=root)
        if unit["unit_id"] in seen_roots:
            continue
        seen_roots.add(unit["unit_id"])
        roots.append(unit)

    order: list[dict] = []
    placed: set[str] = set()
    edges: list[dict] = []
    fallbacks: list[dict] = []
    cycles: list[dict] = []
    visiting: set[str] = set()

    def walk(unit: dict) -> None:
        unit_id = unit["unit_id"]
        if unit_id in placed:
            return
        if unit_id in visiting:
            return
        skill_compiler._refuse_unusable(unit, root=root)
        visiting.add(unit_id)
        for entry in _requiring(unit):
            target_id = entry["to_unit_id"]
            if target_id in visiting:
                # A cycle is a fact about how the units were related, not a reason
                # to refuse the pack: cut the edge, keep walking, and say so.
                cycles.append(
                    {
                        "from_unit_id": unit_id,
                        "type": entry["type"],
                        "to_unit_id": target_id,
                        "reason": "cycle",
                    }
                )
                continue
            try:
                target = knowledge_units.load_unit(target_id, root=root)
            except knowledge_units.UnitError as exc:
                raise knowledge_units.UnitError(
                    exc.code,
                    f"{unit_id} {entry['type']} {target_id}, which cannot be read: {exc}",
                    from_unit_id=unit_id,
                    relation=entry["type"],
                    to_unit_id=target_id,
                ) from exc
            walk(target)
            edges.append(
                {
                    "from_unit_id": unit_id,
                    "type": entry["type"],
                    "to_unit_id": target_id,
                }
            )
        for entry in _fallbacks(unit):
            target_id = entry["to_unit_id"]
            if target_id == unit_id:
                continue
            try:
                alternative = knowledge_units.load_unit(target_id, root=root)
            except knowledge_units.UnitError as exc:
                fallbacks.append(
                    {
                        "of_unit_id": unit_id,
                        "unit_id": target_id,
                        "title": "",
                        "usable": False,
                        "reason": f"{exc.code}: {exc}",
                    }
                )
            else:
                usable, reason = _usable(alternative, root=root)
                fallbacks.append(
                    {
                        "of_unit_id": unit_id,
                        "unit_id": target_id,
                        "title": alternative["title"],
                        "usable": usable,
                        "reason": reason,
                    }
                )
        visiting.discard(unit_id)
        placed.add(unit_id)
        order.append(unit)

    for unit in roots:
        walk(unit)

    if len(order) > PACK_LIMIT:
        raise knowledge_units.UnitError(
            "invalid_request",
            f"this pack would hold {len(order)} units; the limit is {PACK_LIMIT}",
            unit_ids=[unit["unit_id"] for unit in order[PACK_LIMIT:]],
        )
    return {
        "roots": [unit["unit_id"] for unit in roots],
        "members": order,
        "edges": edges,
        "fallbacks": fallbacks,
        "cycles": cycles,
    }


def _description(resolved: dict) -> str:
    members = resolved["members"]
    roots = set(resolved["roots"])
    lead = next(unit for unit in members if unit["unit_id"] in roots)
    extra = len(members) - 1
    head = _one_line(lead.get("summary") or lead["title"])
    if not extra:
        return head[: skill_compiler.DESCRIPTION_LIMIT]
    tail = f" Apply it together with {extra} dependent unit{'s' if extra > 1 else ''}."
    room = skill_compiler.DESCRIPTION_LIMIT - len(tail)
    return (head[:room] + tail)[: skill_compiler.DESCRIPTION_LIMIT]


def _frontmatter(name: str, description: str, *, target: str, resolved: dict) -> str:
    """A pack is a skill like any other, so it carries the same two required keys.

    DSH reads identity from ``metadata``; the other two runtimes validate the same
    frontmatter and ignore the extra keys, which is why the block is target-gated
    exactly as the per-unit compiler gates it.
    """

    lines = [
        f"name: {skill_compiler._yaml_string(name)}",
        f"description: {skill_compiler._yaml_string(description)}",
    ]
    if target == "dsh":
        digest = hashlib.sha256(
            _canonical(
                [knowledge_units.unit_content_hash(unit) for unit in resolved["members"]]
            ).encode("utf-8")
        ).hexdigest()[:16]
        lines.extend(
            [
                "metadata:",
                f"  schema: {skill_compiler._yaml_string(SCHEMA)}",
                f"  pack_digest: {skill_compiler._yaml_string(digest)}",
                f"  unit_count: {len(resolved['members'])}",
            ]
        )
    return lines


def _relative(from_dir: Path, to_path: Path) -> str:
    try:
        return str(to_path.relative_to(from_dir))
    except ValueError:
        return str(to_path)


def render_pack(resolved: dict, *, target: str, directory: Path) -> str:
    """The reader's copy: what to read first, and why each unit is here."""

    members = resolved["members"]
    name = pack_name([unit for unit in members if unit["unit_id"] in set(resolved["roots"])])
    lines = ["---"]
    lines.extend(_frontmatter(name, _description(resolved), target=target, resolved=resolved))
    lines.extend([""])
    first = members[0]
    title = _one_line(first["title"]) if len(members) == 1 else f"{_one_line(first['title'])} and what it needs"
    lines.extend([f"# {title}", ""])
    lines.append(
        f"This pack applies {len(members)} promoted unit{'s' if len(members) > 1 else ''}. "
        "Read them in the order below: every step after the first assumes the ones before it."
    )
    lines.append("")
    triggers = [unit for unit in members if str(unit.get("trigger") or "").strip()]
    if triggers:
        lines.extend(["## When to Use This Pack", ""])
        for unit in triggers:
            lines.append(f"- `{unit['unit_id']}` — {_one_line(unit['trigger'])}")
        lines.append("")
    lines.extend(["## Order", ""])
    for index, unit in enumerate(members, start=1):
        skill_dir = directory / skill_compiler.skill_name(unit)
        lines.append(
            f"{index}. `{unit['unit_id']}` {_one_line(unit['title'])} — "
            f"`{_relative(directory, skill_dir / 'SKILL.md')}`"
        )
    lines.append("")
    if resolved["edges"]:
        lines.extend(["## Why Each Unit Is Here", ""])
        for edge in sorted(
            resolved["edges"], key=lambda row: (row["from_unit_id"], row["type"], row["to_unit_id"])
        ):
            lines.append(
                f"- `{edge['from_unit_id']}` {edge['type']} `{edge['to_unit_id']}`"
            )
        lines.append("")
    if resolved["fallbacks"]:
        lines.extend(["## If a Step Is Blocked", ""])
        for row in sorted(resolved["fallbacks"], key=lambda item: (item["of_unit_id"], item["unit_id"])):
            state = "compiled" if row["usable"] else f"not usable yet ({row['reason']})"
            lines.append(
                f"- `{row['unit_id']}` stands in for `{row['of_unit_id']}` — "
                f"{_one_line(row['title']) or 'no title recorded'}; {state}"
            )
        lines.append("")
    if resolved["cycles"]:
        lines.extend(["## Relations Cut to Stop the Walk", ""])
        for row in resolved["cycles"]:
            lines.append(f"- `{row['from_unit_id']}` {row['type']} `{row['to_unit_id']}` closes a loop")
        lines.append("")
    lines.extend(["## Evidence", ""])
    lines.append("Every unit above was checked against these places in the recorded material.")
    lines.append("")
    seen: set[tuple[str, str]] = set()
    for unit in members:
        for ref in unit.get("source_refs") or []:
            key = (ref["material_id"], ref["evidence_id"])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- {skill_compiler._evidence_line(ref)}")
    lines.append("")
    checks: list[str] = []
    for unit in members:
        for test in unit.get("tests") or []:
            question = _one_line(test.get("question") or "")
            if question:
                checks.append(f"- {unit['unit_id']} {test.get('kind') or 'positive'}: {question}")
    if checks:
        lines.extend(["## Checks", ""])
        lines.extend(checks)
        lines.append("")
    return "\n".join(lines) + "\n"


def _manifest(resolved: dict, *, target: str, name: str, directory: Path) -> dict:
    members = resolved["members"]
    return {
        "schema": SCHEMA,
        "pack_name": name,
        "install_path": str(directory / "PACK.md"),
        "roots": list(resolved["roots"]),
        "members": [
            {
                "unit_id": unit["unit_id"],
                "schema_version": unit.get("schema_version"),
                "version": int(unit["version"]),
                "content_hash": knowledge_units.unit_content_hash(unit),
                "verified_against": unit.get("verified_against"),
                "target": target,
                "skill_dir": skill_compiler.skill_name(unit),
                "install_path": str(directory / skill_compiler.skill_name(unit) / "SKILL.md"),
            }
            for unit in members
        ],
        "relations": sorted(
            (dict(edge) for edge in resolved["edges"]),
            key=lambda row: (row["from_unit_id"], row["type"], row["to_unit_id"]),
        ),
        "fallbacks": sorted(
            (dict(row) for row in resolved["fallbacks"]),
            key=lambda row: (row["of_unit_id"], row["unit_id"]),
        ),
        "cycles": sorted(
            (dict(row) for row in resolved["cycles"]),
            key=lambda row: (row["from_unit_id"], row["to_unit_id"]),
        ),
        "evidence": [
            {
                "material_id": ref["material_id"],
                "evidence_id": ref["evidence_id"],
                "locator": ref.get("locator"),
                "source_revision": ref.get("source_revision"),
            }
            for ref in _unique_refs(members)
        ],
        "files": ["PACK.md", "manifest.json"],
    }


def _unique_refs(members: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    rows: list[dict] = []
    for unit in members:
        for ref in unit.get("source_refs") or []:
            key = (ref["material_id"], ref["evidence_id"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(ref)
    return rows


def build_pack(
    unit_ids: list[str],
    *,
    target: str,
    out: Path | None = None,
    force: bool = False,
    root: Path | None = None,
) -> dict:
    """Write one pack directory: ``PACK.md``, ``manifest.json`` and each member's skill."""

    if target not in skill_compiler.TARGETS:
        raise knowledge_units.UnitError(
            "invalid_request", f"target must be one of: {', '.join(skill_compiler.TARGETS)}"
        )
    resolved = resolve_pack(unit_ids, root=root)
    roots = [unit for unit in resolved["members"] if unit["unit_id"] in set(resolved["roots"])]
    name = pack_name(roots)
    directory = Path(out) if out is not None else skill_compiler.skills_dir(root=root) / name
    existing = directory / "manifest.json"
    if existing.is_file() and not force:
        try:
            previous = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            previous = {}
        recorded = sorted(str(item) for item in (previous.get("roots") or []))
        if recorded and recorded != sorted(resolved["roots"]):
            raise knowledge_units.UnitError(
                "output_conflict",
                f"{directory} already holds a pack over {', '.join(recorded)}; pass --force to replace it",
            )
    text = render_pack(resolved, target=target, directory=directory)
    manifest = _manifest(resolved, target=target, name=name, directory=directory)
    changed = []
    if _write_if_changed(directory / "PACK.md", text):
        changed.append("PACK.md")
    if _write_if_changed(directory / "manifest.json", _canonical(manifest)):
        changed.append("manifest.json")
    compiled = []
    for unit in resolved["members"]:
        outcome = skill_compiler.compile_unit(
            unit["unit_id"],
            target=target,
            out=directory / skill_compiler.skill_name(unit),
            force=True,
            root=root,
        )
        compiled.append(
            {
                "unit_id": unit["unit_id"],
                "skill_name": outcome["skill_name"],
                "install_path": str(directory / outcome["skill_name"] / "SKILL.md"),
            }
        )
        for filename in outcome["written"]:
            changed.append(f"{outcome['skill_name']}/{filename}")
    return {
        "pack_name": name,
        "target": target,
        "output_dir": str(directory),
        "roots": list(resolved["roots"]),
        "members": compiled,
        "written": changed,
        "manifest": manifest,
        "content_digest": hashlib.sha256((text + _canonical(manifest)).encode("utf-8")).hexdigest(),
    }

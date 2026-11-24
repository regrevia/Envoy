"""Score a knowledge-bundle run across four branches and three arms.

The engine is a deterministic reader of a record somebody else produced. It calls
no model, opens no socket, and writes nothing: every number it reports is either
counted from the record or re-derived from the store at scoring time, so a run
cannot improve its own grade by describing itself well.

Four branches, because a single score cannot say which layer failed:

``evidence``      do the refs and citations still resolve, mechanically?
``distillation``  do two independent humans agree each unit stays inside its evidence?
``skill``         is the shipped artifact promoted, current and byte-identical?
``runtime``       did the host agent do better with the bundle than without it?

Three arms, because ``bundle - material`` is only meaningful beside
``material - plain``: the three isolate reach, then distillation.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from . import evidence_pack
from . import knowledge_pack
from . import knowledge_units
from . import skill_compiler

SCHEMA = "envoy.knowledge-eval/1"
ARMS = ("plain", "material", "bundle")
VERDICTS = ("supported", "overreach", "unsupported")
CACHE_CLAIMABLE = frozenset({"verified_disabled", "verified_isolated"})
CACHE_STATES = frozenset({*CACHE_CLAIMABLE, "cache_confounded", "unknown"})
# A paired delta over fewer tasks than this is reported and named as too small to
# claim, rather than quietly rounded into a conclusion.
MIN_PAIRED_TASKS = 12
BOOTSTRAP_RESAMPLES = 1000
CI_CONFIDENCE = 0.95


class EvalError(knowledge_units.UnitError):
    """The record cannot be scored as written; the code names what is wrong."""


def _require(condition: bool, code: str, message: str, **details) -> None:
    if not condition:
        raise EvalError(code, message, **details)


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _known(obj, allowed, where: str) -> None:
    _require(isinstance(obj, dict), "invalid_record", f"{where} must be an object")
    unknown = sorted(set(obj) - set(allowed))
    _require(not unknown, "invalid_record", f"{where} has unknown fields: {', '.join(unknown)}")


def _list(value, where: str) -> list:
    _require(isinstance(value, list), "invalid_record", f"{where} must be a list")
    return value


def _text(value) -> str:
    return str(value or "").strip()


def _mean(values) -> float | None:
    kept = [value for value in values if value is not None]
    if not kept:
        return None
    return round(sum(kept) / len(kept), 6)


def _ratio(numerator: int, denominator: int) -> float | None:
    # An empty denominator is a hole in the data, not a zero.
    if not denominator:
        return None
    return round(numerator / denominator, 6)


# --------------------------------------------------------------------------- load


_RUN_FIELDS = {
    "envoy_commit",
    "at",
    "host_agent",
    "model",
    "cache_isolation_status",
    "budget",
}
_ARM_FIELDS = {"name", "tools", "pack", "members"}
_UNIT_FIELDS = {"unit_id", "artifact", "reviewers", "adjudication"}
_TASK_FIELDS = {"task_id", "reference_facts", "arms"}
_ARM_TASK_FIELDS = {
    "correct",
    "claimed_facts",
    "supported_facts",
    "citations",
    "calls",
    "elapsed_ms",
    "first_evidence_ms",
    "usage",
    "applied_unit",
    "should_apply",
}


def load_record(path: Path) -> dict:
    """Read one JSON object, or the first line of a JSONL file."""

    text = Path(path).read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        raise EvalError("invalid_record", f"{path} is empty")
    if stripped.startswith("{"):
        try:
            record = json.loads(stripped)
        except ValueError as exc:
            raise EvalError("invalid_record", f"{path} is not one JSON object: {exc}") from exc
        return record
    first = stripped.splitlines()[0]
    try:
        return json.loads(first)
    except ValueError as exc:
        raise EvalError("invalid_record", f"{path} has no JSON record on its first line: {exc}") from exc


def _validate(record: dict) -> dict:
    _known(record, {"schema_version", "run", "arms", "reviewers", "units", "tasks"}, "record")
    _require(
        record.get("schema_version") == SCHEMA,
        "invalid_record",
        f"schema_version must be {SCHEMA}",
        found=record.get("schema_version"),
    )
    run = record.get("run")
    _known(run, _RUN_FIELDS, "run")
    for field in ("envoy_commit", "at", "host_agent", "model"):
        _require(_text(run.get(field)), "invalid_record", f"run.{field} is required")
    cache = _text(run.get("cache_isolation_status"))
    _require(
        cache in CACHE_STATES,
        "invalid_record",
        f"run.cache_isolation_status must be one of: {', '.join(sorted(CACHE_STATES))}",
        found=cache,
    )
    budget = run.get("budget") or {}
    _known(budget, {"max_calls", "max_seconds"}, "run.budget")

    arms = {}
    for index, arm in enumerate(_list(record.get("arms"), "arms")):
        _known(arm, _ARM_FIELDS, f"arms[{index}]")
        name = _text(arm.get("name"))
        _require(name in ARMS, "invalid_record", f"arms[{index}].name must be one of: {', '.join(ARMS)}")
        _require(name not in arms, "invalid_record", f"arm {name} is declared twice")
        _list(arm.get("tools"), f"arms[{index}].tools")
        if name == "bundle":
            pack = arm.get("pack")
            _known(pack, {"unit_ids", "target"}, "arms[bundle].pack")
            _require(
                _list(pack.get("unit_ids"), "arms[bundle].pack.unit_ids"),
                "invalid_record",
                "arms[bundle].pack.unit_ids must name at least one unit",
            )
            target = _text(pack.get("target"))
            _require(
                target in skill_compiler.TARGETS,
                "invalid_record",
                f"arms[bundle].pack.target must be one of: {', '.join(skill_compiler.TARGETS)}",
            )
        arms[name] = arm
    missing = [name for name in ARMS if name not in arms]
    _require(not missing, "invalid_record", f"the record is missing arms: {', '.join(missing)}")

    reviewers: dict[str, bool] = {}
    for index, reviewer in enumerate(_list(record.get("reviewers"), "reviewers")):
        _known(reviewer, {"name", "independent"}, f"reviewers[{index}]")
        name = _text(reviewer.get("name"))
        _require(name, "invalid_record", f"reviewers[{index}].name is required")
        _require(name not in reviewers, "invalid_record", f"reviewer {name} is declared twice")
        reviewers[name] = reviewer.get("independent") is True
    _require(reviewers, "invalid_record", "reviewers must name at least one reviewer")
    independent = {name for name, flag in reviewers.items() if flag}
    _require(
        len(independent) >= 2,
        "invalid_record",
        "a claimable run needs at least two independent reviewers",
        independent=sorted(independent),
    )

    units = {}
    for index, unit in enumerate(_list(record.get("units"), "units")):
        _known(unit, _UNIT_FIELDS, f"units[{index}]")
        unit_id = _text(unit.get("unit_id"))
        _require(
            knowledge_units.UNIT_ID.fullmatch(unit_id),
            "invalid_record",
            f"units[{index}].unit_id is not a unit id: {unit_id!r}",
        )
        _require(unit_id not in units, "invalid_record", f"unit {unit_id} is reviewed twice")
        units[unit_id] = unit

    tasks = {}
    for index, task in enumerate(_list(record.get("tasks"), "tasks")):
        _known(task, _TASK_FIELDS, f"tasks[{index}]")
        task_id = _text(task.get("task_id"))
        _require(task_id, "invalid_record", f"tasks[{index}].task_id is required")
        _require(task_id not in tasks, "invalid_record", f"task {task_id} appears twice")
        arms_of_task = task.get("arms")
        _known(arms_of_task, set(ARMS), f"tasks[{task_id}].arms")
        for name, body in arms_of_task.items():
            _known(body, _ARM_TASK_FIELDS, f"tasks[{task_id}].arms.{name}")
        tasks[task_id] = task

    return {
        "run": run,
        "cache": cache,
        "budget": budget,
        "arms": arms,
        "reviewers": reviewers,
        "independent": independent,
        "units": units,
        "tasks": tasks,
    }


# ----------------------------------------------------------------- human labels


def _verdict_of(unit: dict, independent: set[str]) -> tuple[str | None, str | None]:
    """The verdict that counts, or why there is none yet."""

    rows = [
        row
        for row in unit.get("reviewers") or []
        if _text(row.get("reviewer")) in independent
    ]
    verdicts = [_text(row.get("verdict")) for row in rows]
    for verdict in verdicts:
        _require(
            verdict in VERDICTS,
            "invalid_record",
            f"a reviewer verdict must be one of: {', '.join(VERDICTS)}",
            found=verdict,
        )
    if len(verdicts) < 2:
        return None, f"single_reviewer:{unit['unit_id']}"
    if len(set(verdicts)) == 1:
        return verdicts[0], None
    adjudication = unit.get("adjudication")
    if not adjudication:
        return None, f"unadjudicated_disagreement:{unit['unit_id']}"
    decided = _text(adjudication.get("verdict"))
    _require(
        decided in VERDICTS,
        "invalid_record",
        "an adjudication is present but its verdict is not one of the three",
        found=decided,
    )
    return decided, None


def _boolean_of(rows, independent: set[str], field: str) -> bool | None:
    """A reviewed boolean that counts only when two independent reviewers agree."""

    values = [
        row.get(field)
        for row in rows or []
        if _text(row.get("reviewer")) in independent
    ]
    values = [value for value in values if isinstance(value, bool)]
    if len(values) < 2 or len(set(values)) != 1:
        return None
    return values[0]


# --------------------------------------------------------------- branch: evidence


def _citation_problem(citation: dict, root: Path | None, materials: dict) -> str | None:
    material_id = _text(citation.get("material_id"))
    evidence_id = _text(citation.get("evidence_id"))
    if not evidence_pack.MATERIAL_ID.fullmatch(material_id):
        return f"citation has no material id: {material_id!r}"
    if not knowledge_units.EVIDENCE_ID.fullmatch(evidence_id):
        return f"citation has no evidence id: {evidence_id!r}"
    if material_id not in materials:
        try:
            stored = evidence_pack.load_material(material_id, root=root)
            materials[material_id] = evidence_pack.build_source_document(stored, material_id)
        except evidence_pack.PackError as exc:
            materials[material_id] = None
            return str(exc)
    built = materials[material_id]
    if not built:
        return f"{material_id} could not be read"
    evidence = built["index"].get(evidence_id)
    if evidence is None:
        return f"{evidence_id} is not in {material_id}"
    stored_locator = evidence.get("locator")
    if stored_locator is not None and citation.get("locator") != stored_locator:
        return f"citation locator does not match {evidence_id}"
    return None


def score_evidence(units: dict, tasks: dict, *, root: Path | None) -> dict:
    problems: list[str] = []
    materials: dict = {}
    refs_total = refs_resolved = 0
    citations_total = citations_resolved = 0
    for unit_id, entry in units.items():
        try:
            unit = knowledge_units.load_unit(unit_id, root=root)
        except knowledge_units.UnitError as exc:
            problems.append(f"{unit_id}: {exc}")
            continue
        unit_problems, _unpinned, _revisions = knowledge_units._check_refs(unit, root=root)
        refs_total += len(unit.get("source_refs") or [])
        refs_resolved += max(0, len(unit.get("source_refs") or []) - len(unit_problems))
        problems.extend(f"{unit_id}: {problem}" for problem in unit_problems)
    for task_id, task in tasks.items():
        for arm_name, body in (task.get("arms") or {}).items():
            for index, citation in enumerate(body.get("citations") or []):
                citations_total += 1
                problem = _citation_problem(citation, root, materials)
                if problem is None:
                    citations_resolved += 1
                else:
                    problems.append(f"{task_id}/{arm_name}/citations[{index}]: {problem}")
    return {
        "units": len(units),
        "refs_total": refs_total,
        "refs_resolved": refs_resolved,
        "ref_resolution_rate": _ratio(refs_resolved, refs_total),
        "citations_total": citations_total,
        "citations_resolved": citations_resolved,
        "citation_resolution_rate": _ratio(citations_resolved, citations_total),
        "problems": problems,
        "clean": not problems,
    }


# ----------------------------------------------------------- branch: distillation


def score_distillation(units: dict, independent: set[str]) -> dict:
    blockers: list[str] = []
    verdicts: dict[str, str] = {}
    for unit_id, entry in units.items():
        verdict, problem = _verdict_of(entry, independent)
        if problem:
            blockers.append(problem)
            continue
        verdicts[unit_id] = verdict
    counts = {verdict: 0 for verdict in VERDICTS}
    for verdict in verdicts.values():
        counts[verdict] += 1
    effective = len(verdicts)
    return {
        "units": len(units),
        "labeled": effective,
        "coverage": _ratio(effective, len(units)),
        "precision": _ratio(counts["supported"], effective),
        "overreach_rate": _ratio(counts["overreach"], effective),
        "unsupported_rate": _ratio(counts["unsupported"], effective),
        "counts": counts,
        "blockers": blockers,
    }


# ------------------------------------------------------------------ branch: skill


def score_skill(arm: dict, units: dict, *, root: Path | None) -> dict:
    blockers: list[str] = []
    pack = arm.get("pack") or {}
    target = _text(pack.get("target"))
    problems: list[str] = []
    members: list[str] = []
    try:
        resolved = knowledge_pack.resolve_pack(list(pack.get("unit_ids") or []), root=root)
        members = [unit["unit_id"] for unit in resolved["members"]]
    except knowledge_units.UnitError as exc:
        problems.append(f"pack does not resolve: {exc}")
        blockers.append(f"pack_refused:{getattr(exc, 'code', 'invalid_request')}")
        resolved = {"members": [], "cycles": [], "fallbacks": []}
    declared = [str(unit_id) for unit_id in arm.get("members") or []]
    if declared and sorted(declared) != sorted(members):
        blockers.append("pack_membership_mismatch")
        problems.append(
            "the members the run declared are not the closure the store resolves to now"
        )
    if resolved.get("cycles"):
        blockers.append("pack_cycle")
        problems.append(f"{len(resolved['cycles'])} requiring edge(s) had to be cut to close the pack")

    promoted = 0
    artifacts_current = 0
    artifacts_checked = 0
    for unit_id in members:
        try:
            unit = knowledge_units.load_unit(unit_id, root=root)
        except knowledge_units.UnitError as exc:
            problems.append(f"{unit_id}: {exc}")
            continue
        if unit["status"] == knowledge_units.PROMOTED:
            promoted += 1
        else:
            blockers.append(f"not_promoted:{unit_id}")
            problems.append(f"{unit_id} is {unit['status']}; a pack member must be promoted")
        entry = units.get(unit_id) or {}
        artifact = entry.get("artifact")
        if not artifact:
            continue
        artifacts_checked += 1
        path = Path(_text(artifact.get("path")))
        artifact_target = _text(artifact.get("target")) or target
        if not path.is_file():
            blockers.append(f"artifact_missing:{unit_id}")
            problems.append(f"{unit_id}: {path} is not a file")
            continue
        if path.read_text(encoding="utf-8") == skill_compiler.render_skill(unit, artifact_target):
            artifacts_current += 1
        else:
            blockers.append(f"artifact_stale:{unit_id}")
            problems.append(f"{unit_id}: {path} is not what this unit compiles to now")

    return {
        "target": target,
        "members": members,
        "promoted": promoted,
        "artifacts_checked": artifacts_checked,
        "artifacts_current": artifacts_current,
        "problems": problems,
        "blockers": blockers,
    }


def _score_routing(members: list[str], tasks: dict, *, independent: set[str]) -> dict:
    wanted = applied = agreed = 0
    blocked: list[str] = []
    member_set = set(members)
    for task_id, task in tasks.items():
        body = (task.get("arms") or {}).get("bundle") or {}
        should = _boolean_of(body.get("should_apply"), independent, "applies")
        used = _text(body.get("applied_unit"))
        if should is True and not member_set:
            blocked.append(f"routing_unverifiable:{task_id}")
            continue
        if should is True:
            wanted += 1
            if used in member_set:
                applied += 1
        if used in member_set and should is True:
            agreed += 1
    return {
        "should_apply_tasks": wanted,
        "applied_tasks": applied,
        "recall": _ratio(applied, wanted),
        "precision": _ratio(agreed, applied),
        "blockers": blocked,
    }


# ---------------------------------------------------------------- branch: runtime


def score_runtime(tasks: dict, budget: dict, independent: set[str]) -> dict:
    max_calls = (budget or {}).get("max_calls")
    report: dict[str, dict] = {}
    for name in ARMS:
        successes: list[float] = []
        recalls: list[float] = []
        precisions: list[float] = []
        citation_support: list[float] = []
        calls: list[int] = []
        elapsed: list[float] = []
        tokens: list[int] = []
        tokens_complete = True
        applied = 0
        labelled_tasks = 0
        over_budget: list[str] = []
        for task_id, task in tasks.items():
            body = (task.get("arms") or {}).get(name)
            if body is None:
                continue
            correct = _boolean_of(body.get("correct"), independent, "correct")
            if correct is not None:
                successes.append(1.0 if correct else 0.0)
                labelled_tasks += 1
            facts = task.get("reference_facts") or []
            claimed = body.get("claimed_facts") or []
            supported = body.get("supported_facts") or []
            if facts:
                recalls.append(len(set(supported) & {row.get("fact_id") for row in facts}) / len(facts))
            if claimed:
                precisions.append(len(set(supported) & set(claimed)) / len(set(claimed)))
            citations = body.get("citations") or []
            if citations:
                judged = [row for row in citations if isinstance(row.get("supported"), bool)]
                if judged:
                    citation_support.append(
                        sum(1 for row in judged if row["supported"]) / len(judged)
                    )
            if isinstance(body.get("calls"), int):
                calls.append(body["calls"])
                if isinstance(max_calls, int) and body["calls"] > max_calls:
                    over_budget.append(task_id)
            for key in ("elapsed_ms", "first_evidence_ms"):
                if isinstance(body.get(key), (int, float)):
                    elapsed.append(float(body[key]))
            usage = body.get("usage")
            if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                tokens.append(int(usage.get("input_tokens")) + int(usage.get("output_tokens") or 0))
            else:
                tokens_complete = False
            if _text(body.get("applied_unit")):
                applied += 1
        report[name] = {
            "tasks": len((tasks or {})),
            "success_labelled": labelled_tasks,
            "success_rate": _mean(successes),
            "fact_recall": _mean(recalls),
            "fact_precision": _mean(precisions),
            "citation_support": _mean(citation_support),
            "calls_total": sum(calls) if calls else None,
            "elapsed_ms_median": _median(elapsed),
            "host_tokens_total": sum(tokens) if tokens and tokens_complete else None,
            "usage_complete": tokens_complete,
            "applied_unit_tasks": applied,
            "over_budget_tasks": sorted(over_budget),
        }
    return report


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 3)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 3)


# ------------------------------------------------------------------------- claims


def _paired_success(tasks: dict, treatment: str, baseline: str, independent: set[str]) -> dict:
    pairs: list[tuple[float, float]] = []
    for task in tasks.values():
        arms = task.get("arms") or {}
        left = _boolean_of((arms.get(baseline) or {}).get("correct"), independent, "correct")
        right = _boolean_of((arms.get(treatment) or {}).get("correct"), independent, "correct")
        if left is None or right is None:
            continue
        pairs.append((1.0 if left else 0.0, 1.0 if right else 0.0))
    if not pairs:
        return {
            "metric": "paired_task_success",
            "baseline": baseline,
            "treatment": treatment,
            "paired_tasks": 0,
            "delta": None,
            "ci_low": None,
            "ci_high": None,
            "supported": False,
        }
    deltas = [right - left for left, right in pairs]
    mean = sum(deltas) / len(deltas)
    low, high = _bootstrap_ci(deltas)
    return {
        "metric": "paired_task_success",
        "baseline": baseline,
        "treatment": treatment,
        "paired_tasks": len(pairs),
        "baseline_rate": _mean([left for left, _right in pairs]),
        "treatment_rate": _mean([right for _left, right in pairs]),
        "delta": round(mean, 6),
        "ci_low": low,
        "ci_high": high,
        # A delta is supported when the whole interval is above parity. An
        # interval that merely leans positive is not a result.
        "supported": low is not None and low > 0,
    }


def _bootstrap_ci(deltas: list[float]) -> tuple[float | None, float | None]:
    if len(deltas) < 2:
        return None, None
    seed = int(hashlib.sha256(_canonical(deltas).encode("utf-8")).hexdigest()[:16], 16)
    generator = random.Random(seed)
    means = []
    size = len(deltas)
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [deltas[generator.randrange(size)] for _ in range(size)]
        means.append(sum(sample) / size)
    means.sort()
    tail = (1.0 - CI_CONFIDENCE) / 2.0
    low = means[int(tail * (len(means) - 1))]
    high = means[int((1.0 - tail) * (len(means) - 1))]
    return round(low, 6), round(high, 6)


def _gate(record: dict, branches: dict, claims: dict, errors: int) -> dict:
    run = record["run"]
    tasks = record["tasks"]
    paired = all(
        set((task.get("arms") or {})) == set(ARMS) for task in tasks.values()
    )
    protocol: list[str] = []
    if not tasks:
        protocol.append("no_tasks")
    if not paired:
        protocol.append("arms_not_paired")
    if _text(run.get("cache_isolation_status")) not in CACHE_CLAIMABLE:
        protocol.append("cache_not_isolated")
    if len(tasks) < MIN_PAIRED_TASKS:
        protocol.append(f"too_few_tasks:{len(tasks)}<{MIN_PAIRED_TASKS}")

    data: list[str] = []
    evidence = branches["evidence"]
    if not evidence["clean"]:
        data.append("evidence_unresolved")
    for arm_name, arm in branches["runtime"].items():
        if not arm["usage_complete"]:
            data.append(f"usage_missing:{arm_name}")
        if arm["over_budget_tasks"]:
            data.append(f"over_budget:{arm_name}")
    data.extend(branches["skill"]["blockers"])

    quality: list[str] = []
    quality.extend(branches["distillation"]["blockers"])
    quality.extend(f"routing_blocked:{code}" for code in branches["skill"]["routing"]["blockers"])
    for task_id, task in tasks.items():
        for arm_name, body in (task.get("arms") or {}).items():
            if _boolean_of(body.get("correct"), record["independent"], "correct") is None:
                quality.append(f"answer_unreviewed:{task_id}/{arm_name}")

    claim_blockers = sorted(set(protocol + data + quality))
    if not claims["bundle_vs_material"]["supported"]:
        claim_blockers.append("no_success_signal")
    protocol_valid = not protocol
    data_complete = not data
    quality_reviewed = not quality
    claim_eligible = (
        protocol_valid
        and data_complete
        and quality_reviewed
        and claims["bundle_vs_material"]["supported"]
    )
    if errors:
        status = "failed"
    elif not (protocol_valid and data_complete and quality_reviewed):
        status = "incomplete"
    elif claim_eligible:
        status = "passed"
    else:
        status = "complete"
    return {
        "protocol_valid": protocol_valid,
        "data_complete": data_complete,
        "quality_reviewed": quality_reviewed,
        "claim_eligible": claim_eligible,
        "claim_blockers": sorted(set(claim_blockers)),
        "gate_status": status,
    }


# ---------------------------------------------------------------------- evaluate


def evaluate_run(record: dict, *, root: Path | None = None) -> dict:
    """Score one run. Pure: the record and the store in, the report out."""

    checked = _validate(record)
    evidence = score_evidence(checked["units"], checked["tasks"], root=root)
    distillation = score_distillation(checked["units"], checked["independent"])
    skill = score_skill(checked["arms"]["bundle"], checked["units"], root=root)
    skill["routing"] = _score_routing(
        skill["members"], checked["tasks"], independent=checked["independent"]
    )
    runtime = score_runtime(checked["tasks"], checked["budget"], checked["independent"])
    claims = {
        "material_vs_plain": _paired_success(checked["tasks"], "material", "plain", checked["independent"]),
        "bundle_vs_material": _paired_success(checked["tasks"], "bundle", "material", checked["independent"]),
    }
    branches = {
        "evidence": evidence,
        "distillation": distillation,
        "skill": skill,
        "runtime": runtime,
    }
    gate = _gate(checked, branches, claims, errors=0)
    return {
        "schema_version": SCHEMA,
        "run": {
            "envoy_commit": _text(checked["run"].get("envoy_commit")),
            "at": _text(checked["run"].get("at")),
            "host_agent": _text(checked["run"].get("host_agent")),
            "model": _text(checked["run"].get("model")),
            "cache_isolation_status": checked["cache"],
            "budget": checked["budget"],
            "arms": list(ARMS),
            "tasks": len(checked["tasks"]),
            "reviewers": len(checked["reviewers"]),
            "independent_reviewers": len(checked["independent"]),
        },
        "branches": branches,
        "claims": claims,
        "gate": gate,
        "limits": [
            f"a paired delta needs at least {MIN_PAIRED_TASKS} tasks with two independent reviewers to be claimable",
            "tokens are only host-reported usage; Envoy's own text estimate is never substituted",
            "the task set is not asserted to be representative; every claim is conditional on it",
            "answer correctness is a human judgment, not a property this engine can derive",
        ],
    }


def evaluate_file(path: Path, *, root: Path | None = None) -> dict:
    return evaluate_run(load_record(path), root=root)


# ---------------------------------------------------------------------- render


def _number(value, *, percent: bool = False) -> str:
    if value is None:
        return "not measured"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if percent:
        return f"{value * 100:.1f}%"
    return f"{value:.3f}"


def render_report(report: dict) -> str:
    """A report a person can read, and can check line by line against the record."""

    run = report["run"]
    branches = report["branches"]
    gate = report["gate"]
    lines = [
        "# Knowledge evaluation",
        "",
        f"- commit: `{run['envoy_commit']}`",
        f"- host agent: {run['host_agent']}",
        f"- model: {run['model']}",
        f"- tasks: {run['tasks']}",
        f"- reviewers: {run['reviewers']} ({run['independent_reviewers']} independent)",
        f"- cache: {run['cache_isolation_status']}",
        "",
        f"**Gate: {gate['gate_status']}**",
        "",
        "## Evidence",
        "",
        f"- refs resolved: {_number(branches['evidence']['ref_resolution_rate'], percent=True)}"
        f" ({branches['evidence']['refs_resolved']}/{branches['evidence']['refs_total']})",
        f"- citations resolved: {_number(branches['evidence']['citation_resolution_rate'], percent=True)}"
        f" ({branches['evidence']['citations_resolved']}/{branches['evidence']['citations_total']})",
        "",
        "## Distillation",
        "",
        f"- labelled: {branches['distillation']['labeled']}/{branches['distillation']['units']}",
        f"- precision: {_number(branches['distillation']['precision'], percent=True)}",
        f"- overreach: {_number(branches['distillation']['overreach_rate'], percent=True)}",
        "",
        "## Skill",
        "",
        f"- members: {len(branches['skill']['members'])}",
        f"- promoted: {branches['skill']['promoted']}",
        f"- artifacts current: {branches['skill']['artifacts_current']}/{branches['skill']['artifacts_checked']}",
        f"- routing recall: {_number(branches['skill']['routing']['recall'], percent=True)}",
        "",
        "## Runtime",
        "",
        "| arm | success | fact recall | citation support | calls | host tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in ARMS:
        arm = branches["runtime"][name]
        lines.append(
            f"| {name} | {_number(arm['success_rate'], percent=True)} | "
            f"{_number(arm['fact_recall'], percent=True)} | "
            f"{_number(arm['citation_support'], percent=True)} | "
            f"{_number(arm['calls_total'])} | {_number(arm['host_tokens_total'])} |"
        )
    lines.extend(["", "## Paired deltas", ""])
    for key, claim in report["claims"].items():
        lines.append(
            f"- {key}: delta {_number(claim['delta'])} "
            f"[{_number(claim['ci_low'])}, {_number(claim['ci_high'])}] over "
            f"{claim['paired_tasks']} paired tasks — "
            f"{'supported' if claim['supported'] else 'not supported'}"
        )
    if gate["claim_blockers"]:
        lines.extend(["", "## Why no claim", ""])
        lines.extend(f"- `{code}`" for code in gate["claim_blockers"])
    problems = branches["evidence"]["problems"] + branches["skill"]["problems"]
    if problems:
        lines.extend(["", "## Problems", ""])
        lines.extend(f"- {problem}" for problem in problems)
    lines.extend(["", "## Limits", ""])
    lines.extend(f"- {note}" for note in report["limits"])
    return "\n".join(lines) + "\n"

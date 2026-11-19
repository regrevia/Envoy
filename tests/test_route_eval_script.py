"""Guard for ``scripts/acceptance/route_eval.py``.

The script is the only place that checks the routing corpus against the rules
the product actually ships, and nothing ran it. Two things would silently rot:

- the fixture and the routing rules drift apart, so the documented corpus stops
  describing what the hook does;
- ``## Decide before you call`` is renamed or reshaped, so the skill table the
  script parses quietly disappears and its route hints go with it.

Both are asserted here. No test touches the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "acceptance" / "route_eval.py"
FIXTURE = ROOT / "tests" / "fixtures" / "routing" / "links.json"


def _run(*args: str, fixture: Path | None = None):
    command = [sys.executable, str(SCRIPT), *(("--fixture", str(fixture)) if fixture else ()), *args]
    return subprocess.run(command, capture_output=True, text=True, check=False, cwd=str(ROOT))


def _summary(result) -> dict:
    start = result.stdout.find("{")
    assert start != -1, result.stdout + result.stderr
    return json.loads(result.stdout[start:])


def test_the_shipped_corpus_routes_cleanly():
    result = _run("--json-only")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = _summary(result)
    assert summary["status"] == "ok"
    assert summary["totals"]["schema_errors"] == 0
    assert summary["totals"]["route_mismatches"] == 0
    assert summary["totals"]["links"] == summary["totals"]["expected_links"] == 60


def test_the_rules_come_from_the_product_not_a_fallback():
    summary = _summary(_run("--json-only"))
    rules = {rule["rule_source"]: rule for rule in summary["rules"]}
    # These two are imported from envoy_cli; a fallback would report unavailable.
    for name in ("hooks.inspect_prompt", "hooks.is_code_host_diff_url"):
        assert name in rules, sorted(rules)
        assert rules[name]["status"] == "available", rules[name]


def test_the_skill_routing_table_is_parsed_not_skipped():
    summary = _summary(_run("--json-only"))
    table = summary["skill_table"]
    assert table["status"] == "available", table
    assert table["errors"] == []
    assert table["rows_parsed"] >= 9
    assert table["route_rows"] >= 6
    assert table["hints"], table


def test_a_disagreeing_route_is_reported_as_a_failure():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    link = payload["links"][0]
    link["expected_route"] = "envoy_task" if link["expected_route"] != "envoy_task" else "host_fetch"
    with tempfile.TemporaryDirectory() as directory:
        mutated = Path(directory) / "links.json"
        mutated.write_text(json.dumps(payload), encoding="utf-8")
        result = _run("--json-only", fixture=mutated)
    assert result.returncode == 1
    summary = _summary(result)
    assert summary["status"] == "failed"
    assert summary["totals"]["route_mismatches"] == 1


def test_live_mode_stays_a_stub_that_spends_nothing():
    result = _run("--live")
    assert result.returncode == 2
    plan = json.loads(result.stdout)
    assert plan["mode"] == "live"
    assert plan["status"] == "not_implemented"


def test_continuous_integration_runs_the_evaluator():
    """A check nobody runs is not a check.

    The evaluator is the only thing comparing the corpus to the shipped rules,
    so CI has to invoke it, and has to let its exit code decide: a `|| true`
    would turn every mismatch into a green build.
    """

    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    line = "python3 scripts/acceptance/route_eval.py"

    assert line in workflow
    assert f"{line} ||" not in workflow
    assert f"{line} |" not in workflow

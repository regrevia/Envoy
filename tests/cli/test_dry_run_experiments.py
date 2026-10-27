"""Checks for scripts/dry_run_experiments.py: the plan reader must never execute."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT = ROOT_DIR / "scripts" / "dry_run_experiments.py"


def load_module():
    spec = importlib.util.spec_from_file_location("dry_run_experiments", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def plan(entries: list[dict] | None = None, holdout_status: str = "pending_sample") -> dict:
    return {
        "schema": "envoy.experiment-plan/1",
        "version": "0.1.24",
        "status": "not_run",
        "axes": {
            "long-video": {
                "entries": entries
                if entries is not None
                else [
                    {
                        "id": "V01",
                        "axis": "long-video",
                        "title": "baseline",
                        "command": "envoy read <public-video-url> --json",
                        "expected": "transcript non-empty",
                        "requires": "user_present,network",
                        "status": "not_run",
                    }
                ]
            }
        },
        "holdout": {"id": "H12", "status": holdout_status, "size": 12, "sha256": "abc", "rows": ["u"]},
    }


def write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data))
    return path


def test_good_plan_passes(tmp_path: Path) -> None:
    module = load_module()
    path = write(tmp_path, plan())
    assert module.main(["--plan", str(path), "--check"]) == 0


def test_duplicate_id_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    entry = plan()["axes"]["long-video"]["entries"][0]
    path = write(tmp_path, plan([entry, dict(entry)]))
    problems = module.validate(module.load_plan(path))
    assert any("duplicate entry id" in problem for problem in problems)


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["status"] = "probably_fine"
    path = write(tmp_path, data)
    assert module.main(["--plan", str(path), "--check"]) == 1


def test_unknown_requirement_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["requires"] = "user_present,telepathy"
    path = write(tmp_path, data)
    problems = module.validate(module.load_plan(path))
    assert any("unknown requirement" in problem for problem in problems)


def test_render_marks_placeholders(tmp_path: Path) -> None:
    module = load_module()
    path = write(tmp_path, plan())
    output = module.render(module.load_plan(path), "V01")
    assert "fill in:" in output
    assert "<public-video-url>" in output
    assert "nothing was executed" in output


def test_frozen_holdout_needs_rows(tmp_path: Path) -> None:
    module = load_module()
    data = plan(holdout_status="frozen")
    data["holdout"].pop("rows")
    path = write(tmp_path, data)
    problems = module.validate(module.load_plan(path))
    assert any("missing rows" in problem for problem in problems)


def test_a_prose_command_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "同一命令执行中 Ctrl-C，再执行同一命令"
    problems = module.validate(module.load_plan(write(tmp_path, data)))
    assert any("runnable program" in problem for problem in problems), problems


def test_a_prefixed_command_is_accepted(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "PATH=/usr/bin:/bin sh install.sh"
    assert module.validate(module.load_plan(write(tmp_path, data))) == []



def test_a_dependent_entry_that_does_not_exist_is_reported(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["holdout"]["dependent_entries"] = ["V01", "X99"]
    path = write(tmp_path, data)
    assert module.main(["--plan", str(path), "--check"]) == 1
    assert module.validate(data)


def test_dependent_entries_that_exist_pass_the_check(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["holdout"]["dependent_entries"] = ["V01"]
    path = write(tmp_path, data)
    assert module.validate(data) == []
    assert module.main(["--plan", str(path), "--check"]) == 0


def test_a_public_url_entry_missing_from_dependent_entries_is_reported() -> None:
    module = load_module()
    data = plan()
    data["holdout"]["dependent_entries"] = []
    assert any("not in holdout dependent_entries" in problem for problem in module.validate(data))


def test_a_dependent_entry_without_a_placeholder_is_reported() -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "envoy read https://example.com/v --json"
    data["holdout"]["dependent_entries"] = ["V01"]
    assert any("has no <public-" in problem for problem in module.validate(data))

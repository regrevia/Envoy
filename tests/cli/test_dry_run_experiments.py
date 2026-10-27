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


def read_command(request_id: str = "V01", url: str = "<public-video-url>") -> str:
    """The documented read form: request JSON on stdin, no invented CLI flags."""
    body = {"request_id": request_id, "source": {"url": url}}
    payload = json.dumps(body, separators=(",", ":")).replace('"', '\\"')
    return "sh -c 'printf %s \"" + payload + "\" | envoy read --request-json -'"


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
                        "command": read_command(),
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
    data["axes"]["long-video"]["entries"][0]["command"] = read_command(url="https://example.com/v")
    data["holdout"]["dependent_entries"] = ["V01"]
    assert any("has no <public-" in problem for problem in module.validate(data))


def test_axis_count_must_match_its_entries(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["count"] = 2
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    problems = module.validate(module.load_plan(path))
    assert any("count says 2" in problem for problem in problems)


def test_a_flag_the_cli_never_had_is_reported(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "envoy read /path/big.pdf --pages 10-25 --json"
    problems = module.validate(data)
    assert any("has no --pages flag" in problem for problem in problems)
    assert any("has no --json flag" in problem for problem in problems)


def test_an_unknown_subcommand_is_reported() -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "envoy transcribe <public-video-url>"
    problems = module.validate(data)
    assert any("is not a CLI subcommand" in problem for problem in problems)


def test_an_unquoted_placeholder_is_reported() -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "ENVOY_REPO=<owner>/Envoy sh install.sh"
    problems = module.validate(data)
    assert any("must be quoted" in problem for problem in problems)


def test_a_quoted_placeholder_passes(tmp_path: Path) -> None:
    module = load_module()
    data = plan()
    data["axes"]["long-video"]["entries"][0]["command"] = "ENVOY_REPO='<owner>/Envoy' sh install.sh"
    assert module.validate(data) == []


def test_holdout_command_naming_a_flag_the_tool_never_had_fails() -> None:
    module = load_module()
    data = plan()
    data["holdout"]["freeze_command"] = "python3 freeze_holdout.py --input c.txt --rows 12"
    problems = module.validate(data)
    assert any("unknown freeze_holdout.py flag --rows" in problem for problem in problems)


def test_holdout_commands_on_the_real_tool_surface_pass() -> None:
    module = load_module()
    data = plan()
    data["holdout"]["freeze_command"] = (
        "python3 freeze_holdout.py --input candidates.txt --size 12 --id H12 "
        "--out holdout.json --apply-plan EXPERIMENT-PLAN.json"
    )
    data["holdout"]["verify_command"] = (
        "python3 freeze_holdout.py --check holdout.json --plan EXPERIMENT-PLAN.json"
    )
    assert module.validate(data) == []

def test_dry_run_block_naming_a_flag_the_tool_never_had_fails() -> None:
    module = load_module()
    data = plan()
    data["dry_run"] = {"render": "python3 dry_run_experiments.py --render-me V02"}
    problems = module.validate(data)
    assert any("unknown dry_run_experiments.py flag --render-me" in problem for problem in problems)


def test_dry_run_block_on_the_real_tool_surface_passes() -> None:
    module = load_module()
    data = plan()
    data["dry_run"] = {
        "check": "python3 dry_run_experiments.py --check",
        "list": "python3 dry_run_experiments.py --list",
        "render": "python3 dry_run_experiments.py --render V02",
    }
    assert module.validate(data) == []
    assert module.TOOL_FLAGS == {"--help", "--plan", "--check", "--list", "--render", "--json", "-h"}

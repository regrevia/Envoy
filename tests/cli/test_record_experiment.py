from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import record_experiment  # noqa: E402


def read_command(request_id: str = "V01", url: str = "<public-video-url>") -> str:
    body = {"request_id": request_id, "source": {"url": url}}
    payload = json.dumps(body, separators=(",", ":")).replace('"', '\\"')
    return "sh -c 'printf %s \"" + payload + "\" | envoy read --request-json -'"


def _plan() -> dict:
    return {
        "schema": "envoy.experiment-plan/1",
        "version": "test",
        "status": "not_run",
        "axes": {
            "video": {
                "entries": [
                    {
                        "id": "V01",
                        "axis": "video",
                        "title": "short clip",
                        "command": read_command(url="<url>"),
                        "expected": "one commit",
                        "requires": "user_present,network",
                        "status": "not_run",
                        "notes": "",
                    }
                ]
            },
            "pdf": {
                "entries": [
                    {
                        "id": "D01",
                        "axis": "pdf",
                        "title": "small pdf",
                        "command": read_command(url="<path>"),
                        "expected": "text",
                        "requires": "network_or_local",
                        "status": "not_run",
                        "notes": "",
                    }
                ]
            },
        },
        "holdout": {"status": "pending_sample", "size": 12},
    }


def _write(tmp_path: Path, plan: dict | None = None) -> Path:
    path = tmp_path / "EXPERIMENT-PLAN.json"
    path.write_text(json.dumps(plan or _plan(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def test_a_recorded_result_keeps_the_numbers_and_the_time(tmp_path, capsys):
    path = _write(tmp_path)
    code = record_experiment.main(
        [
            "--plan",
            str(path),
            "--id",
            "V01",
            "--status",
            "passed",
            "--evidence",
            "413/600 chunks committed, gap=187",
            "--observed",
            '{"processed": 413, "total": 600}',
            "--write",
        ]
    )
    assert code == 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    entry = stored["axes"]["video"]["entries"][0]
    assert entry["status"] == "passed"
    assert entry["evidence"] == "413/600 chunks committed, gap=187"
    assert entry["observed"] == {"processed": 413, "total": 600}
    assert entry["observed_at"]
    other = stored["axes"]["pdf"]["entries"][0]
    assert other["status"] == "not_run" and "evidence" not in other
    assert "recorded V01 -> passed" in capsys.readouterr().out


def test_an_unknown_id_is_refused(tmp_path, capsys):
    path = _write(tmp_path)
    before = path.read_text(encoding="utf-8")
    code = record_experiment.main(
        ["--plan", str(path), "--id", "X99", "--status", "passed", "--evidence", "no such entry", "--write"]
    )
    assert code == 1
    assert "no entry with id" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == before


def test_a_finished_status_needs_evidence(tmp_path, capsys):
    path = _write(tmp_path)
    code = record_experiment.main(
        ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "ok", "--write"]
    )
    assert code == 1
    assert "needs an evidence line" in capsys.readouterr().err


def test_a_finished_entry_cannot_be_un_recorded_without_force(tmp_path, capsys):
    path = _write(tmp_path)
    assert (
        record_experiment.main(
            ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "read 600/600", "--write"]
        )
        == 0
    )
    code = record_experiment.main(["--plan", str(path), "--id", "V01", "--status", "running", "--write"])
    assert code == 1
    assert "un-record" in capsys.readouterr().err
    assert (
        record_experiment.main(
            ["--plan", str(path), "--id", "V01", "--status", "running", "--write", "--force"]
        )
        == 0
    )
    entry = json.loads(path.read_text(encoding="utf-8"))["axes"]["video"]["entries"][0]
    assert entry["status"] == "running"
    assert entry["evidence"] == "read 600/600"


def test_a_dry_run_leaves_the_plan_byte_identical(tmp_path, capsys):
    path = _write(tmp_path)
    before = path.read_bytes()
    code = record_experiment.main(
        ["--plan", str(path), "--id", "D01", "--status", "passed", "--evidence", "12 pages, gap false"]
    )
    assert code == 0
    assert path.read_bytes() == before
    assert "nothing written" in capsys.readouterr().out


def test_the_plan_status_follows_its_entries(tmp_path):
    path = _write(tmp_path)
    record_experiment.main(
        ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "600/600 chunks", "--write"]
    )
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "running"
    record_experiment.main(
        ["--plan", str(path), "--id", "D01", "--status", "passed", "--evidence", "12 pages read", "--write"]
    )
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "passed"
    record_experiment.main(
        ["--plan", str(path), "--id", "D01", "--status", "failed", "--evidence", "page 7 crashed", "--write"]
    )
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "failed"


def test_the_summary_reports_the_holdout_state(tmp_path, capsys):
    path = _write(tmp_path)
    code = record_experiment.main(["--plan", str(path), "--json"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["entries"] == 2
    assert payload["by_status"]["not_run"] == 2
    assert payload["holdout"] == "pending_sample"
    assert payload["plan_status"] == "not_run"


def test_an_invalid_plan_is_refused_before_any_write(tmp_path, capsys):
    plan = _plan()
    plan["axes"]["video"]["entries"][0]["requires"] = "user_present,ethereum"
    path = _write(tmp_path, plan)
    before = path.read_bytes()
    code = record_experiment.main(
        ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "read 600/600", "--write"]
    )
    assert code == 1
    assert "unknown requirement" in capsys.readouterr().err
    assert path.read_bytes() == before


def _holdout_plan(dependent: bool = True) -> dict:
    plan = _plan()
    # The validator ties dependent_entries to entries whose command reads a
    # public URL, so the fixture has to carry the placeholder.
    for axis in plan["axes"].values():
        for entry in axis["entries"]:
            if entry["id"] == "V01":
                entry["command"] = read_command()
    plan["holdout"] = {
        "status": "pending_sample",
        "size": 12,
        "dependent_entries": ["V01"] if dependent else [],
    }
    return plan


def test_a_holdout_dependent_result_needs_a_frozen_holdout(tmp_path, capsys):
    path = _write(tmp_path, _holdout_plan())
    before = path.read_text(encoding="utf-8")
    code = record_experiment.main(
        ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "12 rows ok", "--write"]
    )
    assert code == 1
    assert "measures the holdout sample" in capsys.readouterr().err
    assert path.read_text(encoding="utf-8") == before


def test_a_non_dependent_entry_records_without_a_frozen_holdout(tmp_path):
    path = _write(tmp_path, _holdout_plan())
    code = record_experiment.main(
        ["--plan", str(path), "--id", "D01", "--status", "passed", "--evidence", "21 pages, 2 gaps", "--write"]
    )
    assert code == 0


def test_a_frozen_holdout_unblocks_the_dependent_result(tmp_path):
    plan = _holdout_plan()
    plan["holdout"].update({"status": "frozen", "sha256": "ab" * 32, "rows": ["https://example.com/a"]})
    path = _write(tmp_path, plan)
    code = record_experiment.main(
        ["--plan", str(path), "--id", "V01", "--status", "passed", "--evidence", "12/12 rows read", "--write"]
    )
    assert code == 0


def test_progress_names_the_axis_split_and_what_the_holdout_blocks() -> None:
    plan = {
        "axes": {
            "long-video": {"entries": [{"id": "V01", "status": "passed"}]},
            "install": {"entries": [{"id": "I01", "status": "not_run"}]},
        },
        "holdout": {"id": "H12", "status": "pending_sample", "dependent_entries": ["V01"]},
    }
    line = record_experiment.progress(plan)
    assert line.startswith("progress 1/2 (long-video 1/1, install 0/1)")
    assert "H12 is pending_sample and still blocks V01" in line


def test_progress_stops_mentioning_a_frozen_holdout() -> None:
    plan = {
        "axes": {"long-video": {"entries": [{"id": "V01", "status": "passed"}]}},
        "holdout": {"id": "H12", "status": "frozen", "dependent_entries": ["V01"]},
    }
    line = record_experiment.progress(plan)
    assert line == "progress 1/1 (long-video 1/1)"

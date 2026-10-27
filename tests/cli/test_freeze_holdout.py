"""Checks for scripts/freeze_holdout.py, the offline half of holdout sampling."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
SCRIPT = ROOT_DIR / "scripts" / "freeze_holdout.py"


def load_module():
    spec = importlib.util.spec_from_file_location("freeze_holdout", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def candidates(count: int = 20) -> str:
    lines = [f"# candidates for the holdout\nhttps://example.com/post-{i:02d}\n" for i in range(count)]
    lines.insert(3, "https://example.com/post-00\n")  # duplicate must be collapsed
    return "".join(lines)


def test_freeze_writes_deterministic_sample(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text(candidates())
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    assert module.main(["--input", str(source), "--out", str(first), "--size", "12"]) == 0
    assert module.main(["--input", str(source), "--out", str(second), "--size", "12"]) == 0
    left = json.loads(first.read_text())
    right = json.loads(second.read_text())
    assert left["rows"] == right["rows"]
    assert left["size"] == len(left["rows"]) == 12
    assert left["sha256"] == right["sha256"]
    assert left["status"] == "frozen"
    assert len(set(left["rows"])) == 12


def test_frozen_file_verifies(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text(candidates())
    out = tmp_path / "holdout.json"
    assert module.main(["--input", str(source), "--out", str(out), "--size", "6"]) == 0
    assert module.check(out) == []


def test_tampered_rows_are_detected(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text(candidates())
    out = tmp_path / "holdout.json"
    assert module.main(["--input", str(source), "--out", str(out), "--size", "6"]) == 0
    data = json.loads(out.read_text())
    data["rows"][0] = "https://example.com/swapped-in-after-results"
    out.write_text(json.dumps(data))
    problems = module.check(out)
    assert any("sha256" in problem for problem in problems)


def test_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text(candidates())
    out = tmp_path / "holdout.json"
    assert module.main(["--input", str(source), "--out", str(out), "--size", "6"]) == 0
    assert module.main(["--input", str(source), "--out", str(out), "--size", "6"]) == 1
    assert module.main(["--input", str(source), "--out", str(out), "--size", "6", "--force"]) == 0


def test_asks_for_more_rows_than_available(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text(candidates(3))
    assert module.main(["--input", str(source), "--out", str(tmp_path / "h.json"), "--size", "12"]) == 1


def test_non_url_candidate_is_rejected(tmp_path: Path) -> None:
    module = load_module()
    source = tmp_path / "candidates.txt"
    source.write_text("https://example.com/ok\nnot a url\n")
    assert module.main(["--input", str(source), "--out", str(tmp_path / "h.json")]) == 1

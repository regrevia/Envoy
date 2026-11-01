"""Guards the committed long-task fixtures against a silent reader-shape change.

``tests/fixtures/long_task/`` holds receipts captured from the real reader code
paths with every external I/O boundary monkeypatched (see the fixture README and
each file's ``_provenance``). This test regenerates them into a temporary
directory with the same monkeypatches and compares the regenerated body with the
committed body; a reader-shape change therefore fails here instead of ageing the
fixtures quietly.

The signal carried by these fixtures is synthetic (fake ASR / fake subtitle
track); only the receipt shape is real. ``_provenance`` is compared by schema,
because ``generated_at`` and ``commit`` are capture-time facts.

Each case prints the committed file's byte count and SHA-256, so the fixture
README's table is pasted from this output instead of being maintained by hand.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "long_task"
CAPTURE = FIXTURE_DIR / "capture_receipts.py"

CASES = {
    "video_asr_partial": "test_capture_video_asr_partial",
    "video_asr_complete": "test_capture_video_asr_complete",
    "page_video_asr_partial": "test_capture_page_video_asr_partial",
    "video_subtitles": "test_capture_video_subtitles",
}

PROVENANCE_KEYS = {
    "kind",
    "signal",
    "commit",
    "source",
    "generated_at",
    "replaced_call",
    "fake_output",
    "proof_scope",
    "note",
}


def _load_capture_module():
    spec = importlib.util.spec_from_file_location("long_task_capture_receipts", CAPTURE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", sorted(CASES))
def test_committed_fixture_matches_a_fresh_reader_capture(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVOY_CAPTURE_OUT", str(tmp_path))
    module = _load_capture_module()
    getattr(module, CASES[name])(tmp_path, monkeypatch)

    regenerated = json.loads((tmp_path / f"{name}.json").read_text(encoding="utf-8"))
    committed = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))

    assert set(regenerated) == set(committed), "top-level receipt shape changed"
    for key in committed:
        if key == "_provenance":
            continue
        assert regenerated[key] == committed[key], f"receipt field {key!r} changed"

    provenance = committed["_provenance"]
    assert set(provenance) == PROVENANCE_KEYS, "provenance schema changed"
    assert provenance["kind"] == "captured_shape"
    assert provenance["signal"] == "synthetic"
    assert provenance["proof_scope"].strip(), "a fixture must say what it cannot prove"
    assert provenance["replaced_call"].strip(), "a fixture must name the replaced call"
    assert provenance["source"].startswith("tests/fixtures/long_task/capture_receipts.py::")

    raw = (FIXTURE_DIR / f"{name}.json").read_bytes()
    print(
        "FIXTURE %s.json bytes=%d sha256=%s"
        % (name, len(raw), hashlib.sha256(raw).hexdigest())
    )

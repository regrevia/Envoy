"""Unit tests for scripts/windows/read_acceptance.py.

The driver itself talks to a real shim on a real host; here the shim is
replaced by a scripted fake so the assertions stay offline and fast.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "windows" / "read_acceptance.py"

MATERIAL = "mat-0123456789abcdef01234567"
REPLAY_MATERIAL = "mat-fedcba9876543210fedcba98"
REVISION = "a" * 64


def _load_module():
    spec = importlib.util.spec_from_file_location("read_acceptance", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def driver():
    return _load_module()


def _text_coverage(**counters):
    return {"coverage": {"modalities": {"text": {"details": dict(counters)}}}}


def _block(block_id, text):
    return {"id": block_id, "content": {"type": "text", "text": text}}


def _read_receipt(result="partial", continuation_available=True, **counters):
    receipt = {
        "result": result,
        "material_id": MATERIAL,
        "source_revision": REVISION,
        "blocks": [_block("blk-body-0001", "first range body")],
    }
    counters.setdefault("continuation_available", continuation_available)
    receipt.update(_text_coverage(**counters))
    return receipt


class FakeInvoker:
    """Answers like the CLI does, and records what it was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, prefix, argv, cwd=None, timeout=240, env=None):
        self.calls.append(list(argv))
        key = argv[0]
        if key not in self.answers:
            raise AssertionError("unexpected envoy call: %s" % (argv,))
        return self.answers[key]

    def subcommands(self):
        return [item[0] for item in self.calls]


def _expand_ok(text="second range body"):
    receipt = {
        "blocks": [_block("blk-body-0001-range-532", text)],
        "errors": [],
    }
    receipt.update(_text_coverage(returned_chars=len(text)))
    return 0, json.dumps(receipt), ""


def _replay_ok():
    receipt = {
        "material_id": REPLAY_MATERIAL,
        "source_revision": REVISION,
        "blocks": [_block("blk-body-0001", "first range body")],
        "errors": [],
    }
    return 0, json.dumps(receipt), ""


def _patch(driver, monkeypatch, answers):
    fake = FakeInvoker(answers)
    monkeypatch.setattr(driver, "_invoke", fake)
    return fake


def test_chain_passes_when_read_expand_and_replay_agree(driver, monkeypatch, tmp_path):
    fake = _patch(
        driver,
        monkeypatch,
        {
            "read": (
                0,
                json.dumps(_read_receipt(total_chars=18970, returned_chars=532, next_offset=532)),
                "",
            ),
            "expand": _expand_ok(),
            "replay": _replay_ok(),
        },
    )
    evidence = []
    chain = driver.run_chain(["envoy"], tmp_path, "https://example.test/doc", evidence)

    assert [(item["id"], item["ok"]) for item in evidence] == [
        ("read_range", True),
        ("expand_continues", True),
        ("replay_reuses_material", True),
    ]
    assert fake.subcommands() == ["read", "expand", "replay"]
    assert chain["material_id"] == MATERIAL
    assert chain["next_offset"] == 532

    replay_detail = evidence[2]["detail"]
    assert replay_detail["revision_matches"] is True
    assert replay_detail["isolated_material"] is True


def test_expand_must_carry_the_offset_and_real_text(driver, monkeypatch, tmp_path):
    answers = {
        "read": (
            0,
            json.dumps(_read_receipt(total_chars=900, returned_chars=532, next_offset=532)),
            "",
        ),
        # The block text is identical to the read head and the id has no range
        # suffix: that is a replay of what was already delivered, not a continue.
        "expand": (
            0,
            json.dumps({"blocks": [_block("blk-body-0001", "first range body")], "errors": []}),
            "",
        ),
        "replay": _replay_ok(),
    }
    fake = _patch(driver, monkeypatch, answers)
    evidence = []
    driver.run_chain(["envoy"], tmp_path, "https://example.test/doc", evidence)

    status = {item["id"]: item for item in evidence}
    assert status["read_range"]["ok"] is True
    assert status["expand_continues"]["ok"] is False
    assert status["expand_continues"]["detail"]["carries_offset"] is False
    assert status["expand_continues"]["detail"]["delivered_new_text"] is False
    # A failed expand is reported, not fatal: replay still gets its own verdict.
    assert status["replay_reuses_material"]["ok"] is True
    assert fake.subcommands() == ["read", "expand", "replay"]


def test_blocked_read_stops_the_chain(driver, monkeypatch, tmp_path):
    blocked = {
        "result": "blocked",
        "material_id": None,
        "source_revision": None,
        "errors": [{"code": "source_unavailable"}],
    }
    fake = _patch(driver, monkeypatch, {"read": (0, json.dumps(blocked), "")})
    evidence = []
    driver.run_chain(["envoy"], tmp_path, "https://example.test/doc", evidence)

    assert [item["id"] for item in evidence] == ["read_range"]
    assert evidence[0]["ok"] is False
    assert evidence[0]["detail"]["result"] == "blocked"
    assert fake.subcommands() == ["read"]


def test_counters_are_read_from_nested_details(driver, monkeypatch, tmp_path):
    receipt = _read_receipt(total_chars=18970, returned_chars=532, next_offset=532)
    details = driver._text_details(receipt)
    assert details["total_chars"] == 18970
    assert details["returned_chars"] == 532
    assert details["next_offset"] == 532


def test_block_text_accepts_both_content_shapes(driver):
    assert driver._block_text({"content": {"type": "text", "text": "body"}}) == "body"
    assert driver._block_text({"content": "raw"}) == "raw"
    assert driver._block_text({}) == ""
    assert driver._receipt_head({"blocks": [{"id": "x"}]}) == ""


def test_receipt_head_uses_content_not_a_text_key(driver):
    receipt = {"blocks": [{"id": "blk", "content": {"text": "  real body  "}}]}
    assert driver._receipt_head(receipt) == "real body"


def test_failed_read_does_not_invent_a_material(driver, monkeypatch, tmp_path):
    fake = _patch(driver, monkeypatch, {"read": (2, "", "boom")})
    evidence = []
    chain = driver.run_chain(["envoy"], tmp_path, "https://example.test/doc", evidence)

    assert evidence[0]["ok"] is False
    assert evidence[0]["detail"]["rc"] == 2
    assert chain["material_id"] is None
    assert fake.subcommands() == ["read"]

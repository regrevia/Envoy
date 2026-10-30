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


_OMIT = object()


def _pdf_read_receipt(result="partial", **counters):
    values = {
        "page_count": 142,
        "total_chars": 520000,
        "returned_chars": 1180,
        "next_offset": 1180,
        "continuation_available": True,
    }
    values.update(counters)
    values = {key: value for key, value in values.items() if value is not _OMIT}
    receipt = {
        "result": result,
        "material_id": MATERIAL,
        "source_revision": REVISION,
        "blocks": [
            _block("blk-pdf-page-0001", "page one body"),
            _block("blk-pdf-page-0002", "page two body"),
        ],
    }
    receipt.update(_text_coverage(**values))
    return receipt


def _pdf_expand_ok(
    block_id="blk-pdf-page-0003-range-200",
    text="page three body",
    next_offset=2000,
):
    receipt = {"blocks": [_block(block_id, text)], "errors": []}
    receipt.update(
        _text_coverage(
            returned_chars=len(text),
            next_offset=next_offset,
            continuation_available=True,
        )
    )
    return 0, json.dumps(receipt), ""


def _resume_read_ok(offset=600, text="first range body"):
    receipt = {
        "result": "partial",
        "material_id": MATERIAL,
        "source_revision": REVISION,
        "blocks": [_block("blk-body-0001", text)],
    }
    receipt.update(
        _text_coverage(
            total_chars=18970,
            returned_chars=len(text),
            next_offset=offset,
            continuation_available=True,
        )
    )
    return 0, json.dumps(receipt), ""


def _resume_expand_ok(offset=1200, text="second range body", material=MATERIAL):
    receipt = {
        "material_id": material,
        "source_revision": REVISION,
        "blocks": [_block("blk-body-0001-range-600", text)],
        "errors": [],
    }
    receipt.update(_text_coverage(returned_chars=len(text), next_offset=offset))
    return 0, json.dumps(receipt), ""


class RequestRecordingInvoker(FakeInvoker):
    """FakeInvoker that also keeps the request JSON each call was handed."""

    def __init__(self, answers):
        super().__init__(answers)
        self.requests = []

    def __call__(self, prefix, argv, cwd=None, timeout=240, env=None):
        path = argv[argv.index("--request-json") + 1]
        self.requests.append(json.loads(Path(path).read_text(encoding="utf-8")))
        return super().__call__(prefix, argv, cwd=cwd, timeout=timeout, env=env)


def test_long_pdf_pages_ok_when_pages_are_counted_and_new_pages_arrive(
    driver, monkeypatch, tmp_path
):
    fake = _patch(
        driver,
        monkeypatch,
        {"read": (0, json.dumps(_pdf_read_receipt()), ""), "expand": _pdf_expand_ok()},
    )
    evidence = []
    ok = driver.check_long_pdf_pages(["envoy"], tmp_path, driver.PDF_SOURCE, evidence)

    assert ok is True
    assert fake.subcommands() == ["read", "expand"]
    assert evidence[0]["id"] == "long_pdf_pages"
    assert evidence[0]["ok"] is True
    detail = evidence[0]["detail"]
    assert detail["page_count"] == 142
    assert detail["next_offset"] == 1180
    assert detail["pages_after_expand"] == [3]
    assert detail["returned_chars"] == 1180
    assert detail["material_id"] == MATERIAL
    assert driver.PDF_SOURCE.endswith("/tests/fixtures/pdf/long-120-pages.pdf")


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"page_count": _OMIT}, id="no-page-count"),
        pytest.param({"page_count": 42}, id="too-few-pages"),
        pytest.param({"result": "complete"}, id="not-partial"),
    ],
)
def test_long_pdf_pages_fails_honestly_when_accounting_is_wrong(
    driver, monkeypatch, tmp_path, kwargs
):
    fake = _patch(
        driver, monkeypatch, {"read": (0, json.dumps(_pdf_read_receipt(**kwargs)), "")}
    )
    evidence = []
    ok = driver.check_long_pdf_pages(["envoy"], tmp_path, driver.PDF_SOURCE, evidence)

    assert ok is False
    assert evidence[0]["ok"] is False
    assert evidence[0]["detail"]["pages_after_expand"] == []
    assert evidence[0]["detail"].get("material_id") is None
    # An unproven accounting must not be papered over by an expand.
    assert fake.subcommands() == ["read"]


def test_long_pdf_pages_never_invents_a_material_after_a_failed_read(
    driver, monkeypatch, tmp_path
):
    fake = _patch(driver, monkeypatch, {"read": (2, "", "boom")})
    evidence = []
    ok = driver.check_long_pdf_pages(["envoy"], tmp_path, driver.PDF_SOURCE, evidence)

    assert ok is False
    assert evidence[0]["detail"].get("material_id") is None
    assert evidence[0]["detail"]["pages_after_expand"] == []
    assert fake.subcommands() == ["read"]


def test_resume_after_interrupt_continues_the_same_material(driver, monkeypatch, tmp_path):
    fake = RequestRecordingInvoker(
        {"read": _resume_read_ok(), "expand": _resume_expand_ok()}
    )
    monkeypatch.setattr(driver, "_invoke", fake)
    evidence = []
    ok = driver.check_resume_after_interrupt(
        ["envoy"], tmp_path, "https://example.test/doc", evidence
    )

    assert ok is True
    assert fake.subcommands() == ["read", "expand"]
    detail = evidence[0]["detail"]
    assert detail["material_id"] == MATERIAL
    assert detail["offset_from"] == 600
    assert detail["offset_to"] == 1200
    assert detail["blocks_step_a"] == 1
    assert detail["blocks_step_b"] == 1
    assert detail["revision_kept"] is True
    assert detail["same_material"] is True
    # The second call resumes by material + offset only: no source, no budget.
    resume_request = fake.requests[1]
    assert set(resume_request) == {
        "protocol_version",
        "request_id",
        "material_id",
        "offset",
    }
    assert resume_request["material_id"] == MATERIAL
    assert resume_request["offset"] == 600


def test_resume_fails_when_the_second_call_restarts_from_the_top(
    driver, monkeypatch, tmp_path
):
    restart = {
        "material_id": MATERIAL,
        "source_revision": REVISION,
        "blocks": [_block("blk-body-0001", "first range body")],
        "errors": [],
    }
    _patch(
        driver,
        monkeypatch,
        {"read": _resume_read_ok(), "expand": (0, json.dumps(restart), "")},
    )
    evidence = []
    ok = driver.check_resume_after_interrupt(
        ["envoy"], tmp_path, "https://example.test/doc", evidence
    )

    assert ok is False
    detail = evidence[0]["detail"]
    assert detail["changed_text"] is False
    assert detail["fresh_blocks"] is False
    assert detail["same_material"] is True


def test_resume_fails_when_the_material_changes(driver, monkeypatch, tmp_path):
    _patch(
        driver,
        monkeypatch,
        {"read": _resume_read_ok(), "expand": _resume_expand_ok(material=REPLAY_MATERIAL)},
    )
    evidence = []
    ok = driver.check_resume_after_interrupt(
        ["envoy"], tmp_path, "https://example.test/doc", evidence
    )

    assert ok is False
    assert evidence[0]["detail"]["same_material"] is False
    assert evidence[0]["detail"]["changed_text"] is True
    assert evidence[0]["detail"]["fresh_blocks"] is True


def test_resume_never_invents_an_offset_when_the_first_read_fails(
    driver, monkeypatch, tmp_path
):
    fake = _patch(driver, monkeypatch, {"read": (2, "", "boom")})
    evidence = []
    ok = driver.check_resume_after_interrupt(
        ["envoy"], tmp_path, "https://example.test/doc", evidence
    )

    assert ok is False
    detail = evidence[0]["detail"]
    assert detail["offset_from"] is None
    assert detail["offset_to"] is None
    assert detail["material_id"] is None
    assert detail["reason"]
    assert fake.subcommands() == ["read"]


def test_long_pdf_pages_fails_when_expands_never_reach_a_new_page(
    driver, monkeypatch, tmp_path
):
    # An expand that keeps coming back inside the pages the read already
    # delivered proves nothing about reaching later pages.
    fake = _patch(
        driver,
        monkeypatch,
        {
            "read": (0, json.dumps(_pdf_read_receipt()), ""),
            "expand": _pdf_expand_ok(
                block_id="blk-pdf-page-0002-range-1180", text="page two again"
            ),
        },
    )
    evidence = []
    ok = driver.check_long_pdf_pages(["envoy"], tmp_path, driver.PDF_SOURCE, evidence)

    assert ok is False
    detail = evidence[0]["detail"]
    assert detail["pages_after_read"] == [1, 2]
    assert detail["pages_after_expand"] == [2]
    assert detail["expand_rounds"] == 2
    assert "did not advance" in detail["stderr"]
    assert fake.subcommands() == ["read", "expand", "expand"]

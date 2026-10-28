"""Deterministic checks for the long-PDF pipeline (download budget, deadline,
page-level continuation, local-PDF dispatch and page-range semantics).

Every case here is offline: PDFs are built as tiny text-layer files in a tmp
directory, downloads are stubbed, and nothing needs a model or an OCR engine.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
for _path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


import pytest

from envoy_core.schemas.read_v1 import Budget, ErrorCode, ReadRequest, SourceRef


def _build_pdf(pages: list[str]) -> bytes:
    """A minimal text-layer PDF, so pypdf can extract_text without a model."""

    objects: list[bytes | str] = []
    font_id = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(len(pages)))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    for index, text in enumerate(pages):
        stream = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {4 + 2 * index} 0 R "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
        )
        objects.append(
            (f"<< /Length {len(stream)} >>\nstream\n").encode() + stream + b"\nendstream"
        )
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        body = obj if isinstance(obj, bytes) else obj.encode()
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _request(**budget) -> ReadRequest:
    return ReadRequest(
        request_id="req-pdf",
        source=SourceRef(url="https://example.com/paper", kind="document"),
        budget=Budget(**budget),
    )


def _pdf_source(data: bytes) -> dict:
    return {
        "url": "https://example.com/paper",
        "data": data,
        "mime_type": "application/pdf",
        "input_kind": "remote_pdf",
    }


def test_explicit_zero_download_budget_is_not_overwritten():
    from envoy_cli import reader

    assert reader._pdf_download_budget(_request(max_download_bytes=0)) == 0
    # The `or` bug turned every explicit 0 into 64 MiB.
    assert reader._pdf_download_budget(
        _request(max_download_bytes=reader.MAX_PDF_BYTES + 10)
    ) == reader.MAX_PDF_BYTES
    assert reader._pdf_download_budget(_request(max_download_bytes=1024)) == 1024


def test_zero_download_budget_blocks_without_fetching(monkeypatch):
    from envoy_cli import reader

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("max_download_bytes=0 must not start a download")

    monkeypatch.setattr(reader, "_fetch_pdf_source", fail_fetch)
    monkeypatch.setattr(reader, "_reuse_pdf_material", lambda *a, **k: None)

    response = reader._read_pdf_source(
        _request(max_download_bytes=0), "https://example.com/paper", {}
    )

    assert response.result.value == "blocked"
    error = response.errors[0]
    assert error.code == ErrorCode.BUDGET_EXCEEDED
    assert error.details["max_download_bytes"] == 0
    assert error.details["downloaded_bytes"] == 0


def test_zero_download_budget_may_reuse_existing_material(monkeypatch):
    from envoy_cli import reader

    def fail_fetch(*_args, **_kwargs):
        raise AssertionError("a reused material must not be downloaded again")

    text = "Hello page one\n\nSecond page text"
    reader._store_material(
        "mat-pdf-reuse",
        {
            "source_kind": "pdf",
            "source_url": "https://example.com/paper",
            "source_revision": "rev-1",
            "session_id": None,
            "text": text,
            "title_text": "",
            "page_spans": [
                {"page_number": 1, "start": 0, "end": 14},
                {"page_number": 2, "start": 16, "end": len(text)},
            ],
            "page_count": 2,
            "page_coverage": {"page_count": 2},
        },
    )
    monkeypatch.setattr(reader, "_fetch_pdf_source", fail_fetch)

    response = reader._read_pdf_source(
        _request(max_download_bytes=0), "https://example.com/paper", {}
    )

    assert response.result.value in {"ready", "partial"}
    assert response.material_id == "mat-pdf-reuse"
    returned = "".join(block.content.text for block in response.blocks)
    assert "Hello page one" in returned


def test_local_pdf_is_not_routed_through_the_video_pipeline(tmp_path):
    from envoy_cli import local_source, mcp

    path = tmp_path / "paper.pdf"
    path.write_bytes(_build_pdf(["Hello local page"]))

    decision = local_source.classify_local_source(path)
    assert decision.kind == "pdf"
    assert decision.detected_by == "magic"

    request = local_source.local_read_request(
        {"source": {"url": f"file://{path}"}, "goal": "read it"},
        str(path),
        kind="document",
    )
    response = local_source.read_local_pdf(request, path)

    assert response.result.value in {"ready", "partial"}
    text = "".join(block.content.text for block in response.blocks)
    assert "Hello local page" in text
    page_blocks = [b for b in response.blocks if b.id.startswith("blk-pdf-page-")]
    assert page_blocks[0].id == "blk-pdf-page-0001"
    assert response.coverage.modalities["text"].details["input_kind"] == "local_file"
    assert response.coverage.modalities["text"].details["downloaded_bytes"] == 0

    # And the bridge dispatch must not build a LocalVideoReadRequest for it.
    def fail_video(*_args, **_kwargs):
        raise AssertionError("a local PDF must not go through the video reader")

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(mcp, "read_local_video", fail_video)
        monkeypatch.setattr(
            mcp, "read_public_page",
            lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no public fetch")),
        )
        mcp._RETAINED_READS.clear()
        import io
        import json
        buffer = io.StringIO()
        monkeypatch.setattr(sys, "stdout", buffer)
        mcp.run_lines([{
            "jsonrpc": "2.0", "id": "init", "method": "initialize",
            "params": {"protocolVersion": "2025-11-25"},
        }, {
            "jsonrpc": "2.0", "id": "call", "method": "tools/call",
            "params": {"name": "envoy_read", "arguments": {"source": {"url": f"file://{path}"}}},
        }])
        payload = json.loads(buffer.getvalue().splitlines()[1])["result"]["structuredContent"]
        assert payload["result"] in {"ready", "partial"}
        assert payload["blocks"]
    finally:
        monkeypatch.undo()
        mcp._RETAINED_READS.clear()


def test_deadline_exhaustion_returns_partial_and_keeps_completed_pages(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf(["Hello page one", "Second page text", "Third page text"])
    real_time = time

    class Clock:
        """Page 1 completes; page 2 starts only after the deadline passed."""

        def __init__(self):
            self.calls = 0

        def monotonic(self):
            self.calls += 1
            return 0.0 if self.calls <= 2 else 10_000.0

    monkeypatch.setattr(reader, "time", types.SimpleNamespace(
        monotonic=Clock().monotonic, time=real_time.time, sleep=real_time.sleep,
    ))

    response = reader._parse_pdf_source(_request(), _pdf_source(data), deadline=1.0)

    assert response.result.value == "partial"
    page_text = "\n".join(block.content.text for block in response.blocks)
    assert "Hello page one" in page_text
    assert "Second page text" not in page_text
    coverage = response.coverage.modalities["text"]
    assert coverage.details["not_processed_pages"] == [2, 3]
    assert response.coverage.unresolved


def test_delivery_gap_converges_with_expand_but_real_gaps_stay():
    from envoy_cli import reader

    delivery_only = {
        "source_kind": "pdf",
        "source_coverage_unresolved": [
            "PDF source text exceeds the initial response budget; expand for more",
        ],
    }
    assert reader._pdf_source_unresolved(delivery_only) == []

    real_gap = {
        "source_kind": "pdf",
        "source_coverage_unresolved": [
            "PDF source text exceeds the initial response budget; expand for more",
            "PDF pages without an extractable text layer may need OCR: 2",
        ],
    }
    assert reader._pdf_source_unresolved(real_gap) == [
        "PDF pages without an extractable text layer may need OCR: 2",
    ]

    details = reader._pdf_page_coverage_details({
        "source_kind": "pdf",
        "page_count": 3,
        "page_spans": [{"page_number": 1, "start": 0, "end": 10}],
        "page_coverage": {"page_count": 3, "not_processed_pages": [2, 3]},
        "delivery_gap": False,
    })
    assert details["indexed_pages"] == [1]
    assert details["unindexed_pages"] == [2, 3]
    assert details["indexed_page_count"] == 1


def test_pdf_page_ranges_are_one_based_and_inclusive():
    from envoy_cli import reader

    data = _build_pdf(["First page text", "Second page text"])
    response = reader._parse_pdf_source(_request(), _pdf_source(data))
    blocks = [block for block in response.blocks if block.id.startswith("blk-pdf-page-")]

    assert [block.id for block in blocks] == ["blk-pdf-page-0001", "blk-pdf-page-0002"]
    assert [block.locator.page_number for block in blocks] == [1, 2]
    assert "First page text" in blocks[0].content.text
    assert "Second page text" in blocks[1].content.text


def test_restart_reuses_committed_pages_and_extracts_only_the_rest(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf(["Hello page one", "Second page text", "Third page text"])
    digest = __import__("hashlib").sha256(data).hexdigest()

    store = reader._pdf_page_store(digest)
    assert store is not None
    # Page 1 was committed by an earlier run that the work deadline stopped.
    assert store.commit_page(
        1, status="text", text="Hello page one", extraction_mode="text", page_count=3,
    )

    class _Page:
        def __init__(self, text=None):
            self._text = text

        def extract_text(self):
            if self._text is None:
                raise AssertionError("a committed page must not be parsed again")
            return self._text

    class _FakePdf:
        is_encrypted = False
        metadata = None
        pages = [_Page(), _Page("Second page text"), _Page("Third page text")]

    monkeypatch.setattr(reader, "PdfReader", lambda *_a, **_k: _FakePdf())
    source = {
        "url": "https://example.com/paper",
        "data": data,
        "mime_type": "application/pdf",
        "sha256": digest,
    }

    response = reader._parse_pdf_source(_request(), source)

    assert response.result.value in {"ready", "partial"}
    blocks = [b for b in response.blocks if b.id.startswith("blk-pdf-page-")]
    assert [b.locator.page_number for b in blocks] == [1, 2, 3]
    assert "Hello page one" in blocks[0].content.text
    assert "Third page text" in blocks[2].content.text
    assert set(store.committed()) == {1, 2, 3}


def test_expand_clears_the_pdf_delivery_gap(monkeypatch):
    from envoy_cli import reader
    from envoy_core.schemas.read_v1 import ExpandRequest

    data = _build_pdf(["Alpha page text", "Beta page text"])
    monkeypatch.setattr(reader, "_is_public_http_url", lambda _url, _cache=None: True)
    monkeypatch.setattr(reader, "_fetch_pdf_source", lambda *_a, **_k: {
        "url": "https://example.com/guide.pdf",
        "data": data,
        "mime_type": "application/pdf",
    })

    response = reader.read_public_page(ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "req-gap-pdf",
        "source": {"url": "https://example.com/guide.pdf"},
        "budget": {"max_response_chars": 256},
    }))
    assert response.result.value == "partial"
    details = response.coverage.modalities["text"].details
    def load(mid):
        return __import__("json").loads(
            (reader._material_root() / f"{mid}.json").read_text(encoding="utf-8")
        )

    stored_before = load(response.material_id)
    assert stored_before["delivery_gap"] is True
    assert stored_before["source_coverage_unresolved"] == []

    offset = details["next_offset"]
    delivered = ""
    for round_number in range(3):
        expanded = reader.expand_material(ExpandRequest.model_validate({
            "request_id": f"req-gap-expand-{round_number}",
            "material_id": response.material_id,
            "offset": offset,
            "max_chars": 900,
            "max_total_response_chars": 4000,
        }))
        delivered += "".join(b.content.text for b in expanded.blocks)
        offset = expanded.coverage.modalities["text"].details.get("next_offset")
        if offset is None:
            break

    assert "Beta page text" in delivered
    stored = load(response.material_id)
    assert reader._pdf_source_unresolved(stored) == []
    assert stored["delivery_gap"] is False
    assert reader._pdf_page_coverage_details(stored)["page_coverage"]["page_count"] == 2


def _scoped_request(**scope) -> ReadRequest:
    return ReadRequest(
        request_id="req-pdf-window",
        source=SourceRef(url="https://example.com/paper", kind="document"),
        scope=scope,
    )


def test_page_window_reads_only_the_requested_pages():
    from envoy_cli import reader

    data = _build_pdf(["First page text", "Second page text", "Third page text"])
    response = reader._parse_pdf_source(
        _scoped_request(page_start=2, page_end=2), _pdf_source(data)
    )

    blocks = [b for b in response.blocks if b.id.startswith("blk-pdf-page-")]
    assert [b.locator.page_number for b in blocks] == [2]
    details = response.coverage.modalities["text"].details
    assert details["page_window"] == {"page_start": 2, "page_end": 2, "limit": reader.MAX_PDF_PAGES}
    assert details["not_processed_ranges"] == [[1, 1], [3, 3]]
    assert details["not_processed_count"] == 2
    assert response.result.value == "partial"
    note = " ".join(response.coverage.unresolved)
    assert "page_start" in note and "Pages not covered: 1, 3" in note


def test_page_end_is_clamped_and_a_start_past_the_end_takes_the_tail():
    from envoy_cli import reader

    data = _build_pdf(["First page text", "Second page text"])
    clamped = reader._parse_pdf_source(
        _scoped_request(page_start=1, page_end=99), _pdf_source(data)
    )
    pages = [b.locator.page_number for b in clamped.blocks if b.id.startswith("blk-pdf-page-")]
    assert pages == [1, 2]
    assert clamped.result.value == "ready"

    tail = reader._parse_pdf_source(_scoped_request(page_start=99), _pdf_source(data))
    pages = [b.locator.page_number for b in tail.blocks if b.id.startswith("blk-pdf-page-")]
    assert pages == [2]


def test_large_pdf_is_windowed_instead_of_rejected(monkeypatch):
    from envoy_cli import reader

    class _Page:
        def __init__(self, text):
            self._text = text

        def extract_text(self):
            return self._text

    total = reader.MAX_PDF_PAGES + 200

    class _FakePdf:
        is_encrypted = False
        metadata = None
        pages = [_Page(f"page {number} text") for number in range(1, total + 1)]

    monkeypatch.setattr(reader, "PdfReader", lambda *_a, **_k: _FakePdf())
    response = reader._parse_pdf_source(
        _request(max_response_chars=1_000_000, max_total_response_chars=5_000_000),
        {"url": "https://example.com/huge.pdf", "data": b"%PDF-fake"},
    )

    details = response.coverage.modalities["text"].details
    assert details["page_count"] == total
    assert details["page_window"]["page_end"] == reader.MAX_PDF_PAGES
    assert details["not_processed_ranges"] == [[reader.MAX_PDF_PAGES + 1, total]]
    assert details["not_processed_count"] == 200
    assert response.result.value == "partial"
    assert any(str(total) in note for note in response.coverage.unresolved)


def test_invalid_page_ranges_are_rejected_by_the_request_model():
    from envoy_core.schemas.read_v1 import Scope

    with pytest.raises(ValueError):
        Scope(page_start=3, page_end=2)
    with pytest.raises(ValueError):
        Scope(page_start=0)
    assert Scope(page_start=5).page_start == 5


def test_mcp_flattens_page_window_into_scope():
    from envoy_cli import mcp

    prepared = mcp._server_arguments(
        "envoy_read",
        {
            "source": {"url": "https://example.com/paper.pdf"},
            "page_start": 3,
            "page_end": 9,
        },
    )
    assert "page_start" not in prepared and "page_end" not in prepared
    assert prepared["scope"]["kind"] == "range"
    assert prepared["scope"]["page_start"] == 3
    assert prepared["scope"]["page_end"] == 9

    declared = mcp._READ_SCOPE_PROPERTIES
    assert declared["page_start"]["minimum"] == 1
    assert declared["page_end"]["minimum"] == 1

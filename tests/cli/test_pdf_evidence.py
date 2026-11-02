"""§1/§2/§3 验收：page_evidence 的形状、上限、计数与挂载条件。

全部离线。OCR / 渲染后端不参与：形状用例直接构造页记录调用 `_pdf_page_evidence`，
端到端用例只用带文本层的手写 PDF。夹具是合成的，所以这里是 `unit_checked`。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
for relative in ("packages/envoy-core/src", "packages/envoy-cli/src"):
    candidate = ROOT_DIR / relative
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

pypdf = pytest.importorskip("pypdf")

from envoy_core.schemas.read_v1 import ReadRequest, SourceRef  # noqa: E402
from envoy_cli import reader  # noqa: E402

TOP_LEVEL_KEYS = {
    "schema",
    "page_count",
    "window",
    "indexed_page_count",
    "pages",
    "pages_truncated",
    "summary",
}
PAGE_KEYS = {
    "page_number",
    "status",
    "extraction_mode",
    "chars",
    "text_sha256",
    "reading_order",
    "layout",
    "ocr",
    "citation",
    "flags",
}
CITATION_KEYS = {"text_offset_start", "text_offset_end", "block_ids", "page_number"}
SUMMARY_KEYS = {
    "counts",
    "ocr_pages",
    "ocr_failed_pages",
    "scanned_pages",
    "complex_pages",
    "parse_error_pages",
    "not_processed_pages",
}
CONTRACT_STATUSES = {
    "text",
    "ocr",
    "ocr_failed",
    "ocr_candidate",
    "blank",
    "parse_error",
    "unprocessed",
}
CONTRACT_MODES = {"text", "ocr", "layout", "none", "error"}
PAGE_LIST_CAP = 200


def _record(**overrides) -> dict:
    record = {
        "status": "text",
        "extraction_mode": "text",
        "text": "hello page",
        "reading_order": "stream",
        "layout": None,
        "ocr": None,
        "flags": [],
    }
    record.update(overrides)
    return record


def _evidence(page_records, *, total_page_count=None, window_start=1, window_end=None,
              page_spans=None, delivered_pages=None) -> dict:
    if window_end is None:
        window_end = max(page_records) if page_records else window_start
    return reader._pdf_page_evidence(
        total_page_count=(
            total_page_count if total_page_count is not None else window_end
        ),
        window_start=window_start,
        window_end=window_end,
        page_records=page_records,
        page_spans=page_spans or [],
        delivered_pages=delivered_pages,
    )


# ---------------------------------------------------------------- 形状与派生值


def test_schema_and_top_level_shape():
    evidence = _evidence({1: _record(), 2: _record(text="second")}, total_page_count=2)

    assert evidence["schema"] == "envoy.page-evidence/1"
    assert set(evidence) == TOP_LEVEL_KEYS
    assert evidence["page_count"] == 2
    assert evidence["window"] == {"page_start": 1, "page_end": 2}
    assert evidence["indexed_page_count"] == 2
    assert evidence["pages_truncated"] is False
    assert set(evidence["summary"]) == SUMMARY_KEYS
    assert list(evidence["summary"]["counts"]) == list(reader._PDF_PAGE_STATUSES)
    assert set(evidence["summary"]["counts"]) == CONTRACT_STATUSES


def test_page_items_expose_the_contract_keys_and_derived_values():
    spans = [{"page_number": 1, "start": 4, "end": 14}]
    evidence = _evidence(
        {1: _record(text="hello page")},
        page_spans=spans,
        delivered_pages={1},
    )
    page = evidence["pages"][0]

    assert set(page) == PAGE_KEYS
    assert set(page["citation"]) == CITATION_KEYS
    assert page["page_number"] == 1
    assert page["chars"] == len("hello page")
    assert page["text_sha256"] == hashlib.sha256(b"hello page").hexdigest()
    assert page["citation"] == {
        "text_offset_start": 4,
        "text_offset_end": 14,
        "block_ids": ["blk-pdf-page-0001"],
        "page_number": 1,
    }

    # 没有投递 / 没有 span 的页不给 block_ids，但 citation 结构不变。
    undelivered = _evidence({1: _record()}, page_spans=[])["pages"][0]
    assert undelivered["citation"]["block_ids"] == []
    assert undelivered["citation"]["text_offset_start"] == 0
    assert undelivered["citation"]["text_offset_end"] == 0


# ---------------------------------------------------------------- 上限与截断


def _many_pages(count: int, status: str = "text") -> dict:
    return {
        number: _record(
            status=status,
            extraction_mode="text" if status == "text" else "none",
            text=f"page {number} body",
        )
        for number in range(1, count + 1)
    }


def test_pages_are_ascending_and_capped_at_two_hundred():
    records = _many_pages(PAGE_LIST_CAP + 1)
    evidence = _evidence(records, total_page_count=PAGE_LIST_CAP + 1)

    assert evidence["page_count"] == PAGE_LIST_CAP + 1
    assert evidence["indexed_page_count"] == PAGE_LIST_CAP + 1
    assert len(evidence["pages"]) == PAGE_LIST_CAP
    assert evidence["pages_truncated"] is True
    numbers = [page["page_number"] for page in evidence["pages"]]
    assert numbers == list(range(1, PAGE_LIST_CAP + 1))


def test_summary_counts_the_whole_window_even_where_pages_are_missing():
    # 第 201 页的状态只出现在 summary 里：它会从 pages 中截掉。
    records = _many_pages(PAGE_LIST_CAP + 1)
    records[201] = _record(
        status="ocr", extraction_mode="ocr", text="scanned", flags=["ocr_applied"]
    )
    evidence = _evidence(records, total_page_count=PAGE_LIST_CAP + 1)

    counts = evidence["summary"]["counts"]
    assert counts["ocr"] == 1
    assert counts["text"] == PAGE_LIST_CAP
    assert sum(counts.values()) == evidence["indexed_page_count"] == PAGE_LIST_CAP + 1
    assert evidence["summary"]["ocr_pages"] == [201]
    # 区域清单按窗口给页码，同样不受 pages 截断影响。
    assert evidence["summary"]["scanned_pages"] == [201]
    # 被截掉的页不在 pages 里，但计数没有丢。
    assert 201 not in [page["page_number"] for page in evidence["pages"]]


def test_summary_areas_agree_with_statuses_inside_the_window():
    records = {
        1: _record(status="text", extraction_mode="text"),
        2: _record(status="ocr", extraction_mode="ocr", flags=["ocr_applied"]),
        3: _record(status="ocr_failed", extraction_mode="none", flags=["ocr_failed"]),
        4: _record(status="ocr_candidate", extraction_mode="none"),
        5: _record(status="blank", extraction_mode="text", text="  "),
        6: _record(status="parse_error", extraction_mode="error"),
        7: _record(status="unprocessed", extraction_mode="none"),
    }
    summary = _evidence(records)["summary"]

    assert summary["counts"] == {
        "text": 1,
        "ocr": 1,
        "ocr_failed": 1,
        "ocr_candidate": 1,
        "blank": 1,
        "parse_error": 1,
        "unprocessed": 1,
    }
    assert summary["ocr_pages"] == [2]
    assert summary["ocr_failed_pages"] == [3]
    assert summary["scanned_pages"] == [2, 3, 4]
    assert summary["parse_error_pages"] == [6]
    assert summary["not_processed_pages"] == [7]


def test_unknown_status_is_counted_as_unprocessed():
    evidence = _evidence(
        {1: _record(status="teleported"), 2: _record(status="text")},
        total_page_count=2,
    )

    assert evidence["pages"][0]["status"] == "unprocessed"
    assert evidence["summary"]["counts"]["unprocessed"] == 1
    assert evidence["summary"]["counts"]["text"] == 1
    assert sum(evidence["summary"]["counts"].values()) == 2


# ---------------------------------------------------------------- 枚举与阅读顺序


def test_status_and_extraction_mode_stay_inside_the_contract_enums():
    records = {
        1: _record(status="text", extraction_mode="text"),
        2: _record(status="ocr", extraction_mode="ocr"),
        3: _record(status="ocr_failed", extraction_mode="none"),
        4: _record(status="ocr_candidate", extraction_mode="none"),
        5: _record(status="blank", extraction_mode="text", text=""),
        6: _record(status="parse_error", extraction_mode="error"),
        7: _record(status="unprocessed", extraction_mode="none"),
    }
    pages = _evidence(records)["pages"]

    assert {page["status"] for page in pages} <= CONTRACT_STATUSES
    assert {page["extraction_mode"] for page in pages} <= CONTRACT_MODES


def test_reading_order_defaults_to_columns_for_layout_pages_and_stream_otherwise():
    records = {
        1: _record(extraction_mode="layout", reading_order=None),
        2: _record(extraction_mode="text", reading_order=None),
        3: _record(extraction_mode="ocr", reading_order="stream"),
    }
    pages = _evidence(records)["pages"]

    assert pages[0]["reading_order"] == "columns"
    assert pages[1]["reading_order"] == "stream"
    assert pages[2]["reading_order"] == "stream"


def test_layout_entry_is_trimmed_to_the_contract_keys():
    notes = [f"note {index}" for index in range(9)]
    records = {
        1: _record(
            extraction_mode="layout",
            reading_order="columns",
            layout={
                "columns": 2,
                "lines": 12,
                "lines_reordered": True,
                "table_like_rows": 0,
                "layout_score": 0.98765,
                "notes": notes,
                "header_footer_lines": 2,
                "unknown_key": "dropped",
            },
            flags=["layout_reordered"],
        )
    }
    page = _evidence(records, total_page_count=1)["pages"][0]
    layout = page["layout"]

    assert set(layout) == {
        "columns",
        "lines",
        "lines_reordered",
        "table_like_rows",
        "layout_score",
        "notes",
        "header_footer_lines",
    }
    assert layout["layout_score"] == 0.988
    assert layout["notes"] == notes[:5]
    assert layout["header_footer_lines"] == 2
    assert page["reading_order"] == "columns"
    assert page["extraction_mode"] == "layout"


# ---------------------------------------------------------------- 端到端挂载


def _build_pdf(pages: list[str]) -> bytes:
    """最小文本层 PDF，pypdf 可直接 extract_text。"""

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


def _pdf_source(data: bytes) -> dict:
    return {
        "url": "https://example.com/paper",
        "data": data,
        "mime_type": "application/pdf",
        "input_kind": "remote_pdf",
    }


def test_pdf_read_attaches_page_evidence_with_matching_citations(monkeypatch):
    stored: list[dict] = []

    def capture(_material_id, material):
        stored.append(material)

    monkeypatch.setattr(reader, "_store_material", capture)
    data = _build_pdf(["First page body", "Second page body", "Third page body"])
    response = reader._parse_pdf_source(
        ReadRequest(
            request_id="req-pdf-evidence",
            source=SourceRef(url="https://example.com/paper", kind="document"),
        ),
        _pdf_source(data),
    )
    details = response.coverage.modalities["text"].details
    evidence = details["page_evidence"]

    assert evidence["schema"] == "envoy.page-evidence/1"
    assert evidence["page_count"] == 3
    assert evidence["indexed_page_count"] == 3
    assert evidence["pages_truncated"] is False
    assert len(evidence["pages"]) == 3
    assert [page["status"] for page in evidence["pages"]] == ["text", "text", "text"]

    delivered_pages = {
        block.locator.page_number: block
        for block in response.blocks
        if block.id.startswith("blk-pdf-page-")
    }
    assert stored, "the PDF text was expected to be retained as a material"
    spans = {span["page_number"]: span for span in stored[-1]["page_spans"]}
    for page in evidence["pages"]:
        number = page["page_number"]
        assert number in delivered_pages, f"page {number} was not delivered"
        assert page["citation"]["block_ids"] == [reader._pdf_page_block_id(number)]
        assert page["citation"]["text_offset_start"] == spans[number]["start"]
        assert page["citation"]["text_offset_end"] == spans[number]["end"]
        assert page["chars"] == spans[number]["end"] - spans[number]["start"]


def test_page_window_limits_the_indexed_pages_but_not_the_window_count():
    data = _build_pdf(["First page body", "Second page body", "Third page body"])
    response = reader._parse_pdf_source(
        ReadRequest(
            request_id="req-pdf-window-evidence",
            source=SourceRef(url="https://example.com/paper", kind="document"),
            scope={"page_start": 2, "page_end": 2},
        ),
        _pdf_source(data),
    )
    evidence = response.coverage.modalities["text"].details["page_evidence"]

    assert evidence["page_count"] == 3
    assert evidence["window"] == {"page_start": 2, "page_end": 2}
    assert evidence["indexed_page_count"] == 1
    assert [page["page_number"] for page in evidence["pages"]] == [2]
    assert evidence["summary"]["counts"]["text"] == 1
    assert evidence["summary"]["counts"]["unprocessed"] == 0
    # 窗口外的页不进入 pages，也不算 unprocessed。
    assert evidence["summary"]["not_processed_pages"] == []


def test_page_evidence_is_pdf_only(monkeypatch):
    html = (
        "<html><head><title>Plain page</title></head><body><article>"
        "<h1>Plain page</h1><p>Body text for a non-PDF source.</p>"
        "</article></body></html>"
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)
    monkeypatch.setattr(
        reader, "_fetch_html", lambda source_url, *_a, **_k: (html, source_url)
    )
    monkeypatch.setattr(reader, "public_api_fallback", lambda *_a, **_k: None)

    response = reader.read_public_page(
        ReadRequest(
            request_id="req-html-evidence",
            source=SourceRef(url="https://example.com/article", kind="article"),
        )
    )

    details = response.coverage.modalities["text"].details
    assert "page_evidence" not in details
    assert response.result.value in {"ready", "partial"}


def test_ocr_page_cap_defaults_to_forty_and_honours_the_environment(monkeypatch):
    monkeypatch.delenv("ENVOY_PDF_OCR_MAX_PAGES", raising=False)
    assert reader._pdf_ocr_page_cap(100) == 40
    assert reader._pdf_ocr_page_cap(3) == 3

    monkeypatch.setenv("ENVOY_PDF_OCR_MAX_PAGES", "7")
    assert reader._pdf_ocr_page_cap(100) == 7
    assert reader._pdf_ocr_page_cap(3) == 3

    monkeypatch.setenv("ENVOY_PDF_OCR_MAX_PAGES", "not-a-number")
    assert reader._pdf_ocr_page_cap(100) == 40

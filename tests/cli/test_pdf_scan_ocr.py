"""扫描页 OCR 与 page_evidence 的确定性检查（契约 §1/§2）。

除最后一个用真实 tesseract 的集成用例外，全部离线：渲染器、OCR 引擎和
PDF 都在测试里用桩或合成数据代替。因此这里验证的是「失败必须说出来」
和「证据结构符合契约」，不代表真实扫描件识别质量。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest


ROOT_DIR = Path(__file__).resolve().parents[2]
for _path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


from envoy_core.schemas.read_v1 import Budget, ErrorCode, ReadRequest, SourceRef


_PNG_STUB = b"\x89PNG\r\n\x1a\n" + b"page-image-stub"


def _build_pdf(page_streams: list[bytes]) -> bytes:
    """按给定内容流拼一个最小 PDF：内容流决定它是文本页还是扫描页。"""

    objects: list[bytes | str] = []
    font_id = 3 + 2 * len(page_streams)
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(len(page_streams)))
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_streams)} >>")
    for index, stream in enumerate(page_streams):
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


def _text_page_stream(text: str) -> bytes:
    return f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")


def _scanned_page_stream() -> bytes:
    # 只有图形指令、没有文字算子：pypdf 的 extract_text 返回空，正是扫描页。
    return b"q 0 0 1 rg 72 72 240 240 re f Q"


def _request(**budget) -> ReadRequest:
    return ReadRequest(
        request_id="req-pdf-ocr",
        source=SourceRef(url="https://example.com/scan.pdf", kind="document"),
        budget=Budget(**budget),
    )


def _pdf_source(data: bytes) -> dict:
    return {
        "url": "https://example.com/scan.pdf",
        "data": data,
        "mime_type": "application/pdf",
        "input_kind": "remote_pdf",
    }


def _stub_render(monkeypatch, *, calls: list[int]) -> None:
    """渲染后端可用，但只记调用次数、返回固定 PNG 字节。"""

    from envoy_cli import reader

    monkeypatch.setattr(
        reader,
        "render_backend_status",
        lambda: {
            "available": True,
            "backend": "pypdfium2",
            "backend_version": "9.9.9-test",
            "install_hint": reader.PDF_RENDER_INSTALL_HINT,
            "reason": None,
        },
    )

    def fake_render(pdf_bytes, page_number, *, dpi=200):
        calls.append(page_number)
        return _PNG_STUB, {
            "backend": "pypdfium2",
            "backend_version": "9.9.9-test",
            "dpi": dpi,
            "page_number": page_number,
            "sha256": hashlib.sha256(_PNG_STUB).hexdigest(),
            "bytes": len(_PNG_STUB),
        }

    monkeypatch.setattr(reader, "render_page_to_png", fake_render)


def _stub_ocr_backend(monkeypatch, *, texts: list[str] | None = None) -> list[Path]:
    """OCR 引擎可用；返回每次收到的图片路径，便于断言有没有重跑。"""

    from envoy_cli import reader

    calls: list[Path] = []
    queue = list(texts or [])

    monkeypatch.setattr(reader, "available_ocr_backends", lambda: ["tesseract"])
    monkeypatch.setattr(
        reader, "select_ocr_backend", lambda requested, available=None: "tesseract"
    )
    monkeypatch.setattr(
        reader, "resolve_ocr_language", lambda backend, language=None: "eng"
    )
    monkeypatch.setattr(reader, "_engine_version", lambda backend: "tesseract-test/5")

    def fake_extract(path, *, profile, language, backend, available=None):
        calls.append(Path(path))
        text = queue.pop(0) if queue else "Scanned page text from OCR"
        return {
            "backend": "tesseract",
            "engine_version": "tesseract-test/5",
            "profile": profile,
            "language": "eng",
            "regions": [{"text": text, "confidence": 0.91}],
            "text": text,
            "source_sha256": "0" * 64,
            "source_path": str(path),
        }

    monkeypatch.setattr(reader, "extract_image_ocr", fake_extract)
    return calls


def test_scanned_page_text_enters_the_material_and_reports_ocr(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf([_scanned_page_stream()])
    _stub_render(monkeypatch, calls=[])
    _stub_ocr_backend(monkeypatch)

    response = reader._parse_pdf_source(_request(), _pdf_source(data))

    blocks = [b for b in response.blocks if b.id.startswith("blk-pdf-page-")]
    assert [b.locator.page_number for b in blocks] == [1]
    assert "Scanned page text from OCR" in blocks[0].content.text

    details = response.coverage.modalities["text"].details
    evidence = details["page_evidence"]
    page = evidence["pages"][0]
    assert page["page_number"] == 1
    assert page["status"] == "ocr"
    assert page["extraction_mode"] == "ocr"
    assert page["chars"] == len(blocks[0].content.text)
    assert page["ocr"]["backend"] == "tesseract"
    assert page["ocr"]["confidence"] == 0.91
    assert page["ocr"]["regions"] == 1
    assert page["ocr"]["failed_reason"] is None
    assert page["ocr"]["cache_hit"] is False
    assert page["ocr"]["dpi"] == reader._PDF_OCR_DPI
    assert "ocr_applied" in page["flags"]
    assert evidence["summary"]["ocr_pages"] == [1]
    assert evidence["summary"]["scanned_pages"] == [1]
    assert evidence["summary"]["counts"]["ocr"] == 1

    # citation 偏移必须能直接在 material 正文里切出这一页的文本。
    stored = json.loads(
        (reader._material_root() / f"{response.material_id}.json").read_text(encoding="utf-8")
    )
    citation = page["citation"]
    assert citation["text_offset_start"] == 0
    assert citation["text_offset_end"] == len(blocks[0].content.text)
    assert stored["text"][citation["text_offset_start"] : citation["text_offset_end"]] == (
        blocks[0].content.text
    )
    assert stored["page_evidence"] == evidence
    assert citation["block_ids"] == ["blk-pdf-page-0001"]
    assert stored["page_spans"][0]["start"] == citation["text_offset_start"]
    assert stored["page_spans"][0]["end"] == citation["text_offset_end"]


def test_missing_render_backend_names_the_install_command(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf([_scanned_page_stream()])
    _stub_ocr_backend(monkeypatch)
    monkeypatch.setattr(
        reader,
        "render_backend_status",
        lambda: {
            "available": False,
            "backend": None,
            "backend_version": None,
            "install_hint": reader.PDF_RENDER_INSTALL_HINT,
            "reason": "pypdfium2 is not installed",
        },
    )

    def explode(*_args, **_kwargs):
        raise AssertionError("渲染器不可用时不该再尝试渲染")

    monkeypatch.setattr(reader, "render_page_to_png", explode)

    with pytest.raises(reader.PublicPageError) as caught:
        reader._parse_pdf_source(_request(), _pdf_source(data))

    assert caught.value.code == ErrorCode.MISSING_CAPABILITY
    message = str(caught.value)
    assert reader.PDF_RENDER_INSTALL_HINT in message
    assert "rendering backend" in message


def test_ocr_page_cache_skips_the_second_render(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf([_scanned_page_stream()])
    render_calls: list[int] = []
    _stub_render(monkeypatch, calls=render_calls)
    ocr_calls = _stub_ocr_backend(monkeypatch)

    first = reader._parse_pdf_source(_request(), _pdf_source(data))
    assert first.coverage.modalities["text"].details["page_evidence"]["pages"][0]["ocr"][
        "cache_hit"
    ] is False
    assert render_calls == [1]
    assert len(ocr_calls) == 1

    second = reader._parse_pdf_source(_request(), _pdf_source(data))
    page = second.coverage.modalities["text"].details["page_evidence"]["pages"][0]
    assert page["status"] == "ocr"
    assert page["ocr"]["cache_hit"] is True
    # 命中缓存意味着既没有重新渲染，也没有再跑一遍 OCR。
    assert render_calls == [1]
    assert len(ocr_calls) == 1
    assert "Scanned page text from OCR" in next(
        block.content.text
        for block in second.blocks
        if block.id.startswith("blk-pdf-page-")
    )


def test_page_evidence_matches_the_contract_shape_for_text_pages():
    from envoy_cli import reader

    data = _build_pdf([_text_page_stream("Alpha page text"), _text_page_stream("Beta page text")])
    response = reader._parse_pdf_source(_request(), _pdf_source(data))
    details = response.coverage.modalities["text"].details
    evidence = details["page_evidence"]

    assert evidence["schema"] == "envoy.page-evidence/1"
    assert evidence["page_count"] == 2
    assert evidence["window"] == {"page_start": 1, "page_end": 2}
    assert evidence["indexed_page_count"] == 2
    assert evidence["pages_truncated"] is False
    assert [page["page_number"] for page in evidence["pages"]] == [1, 2]

    expected_keys = {
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
    for page in evidence["pages"]:
        assert expected_keys <= set(page)
        assert page["status"] == "text"
        assert page["ocr"] is None
        assert page["chars"] > 0
        block_text = next(
            b.content.text
            for b in response.blocks
            if b.id == f"blk-pdf-page-{page['page_number']:04d}"
        )
        assert page["text_sha256"] == hashlib.sha256(block_text.encode("utf-8")).hexdigest()

    summary = evidence["summary"]
    assert summary["counts"]["text"] == 2
    assert summary["counts"]["ocr"] == 0
    assert summary["ocr_pages"] == []
    assert summary["scanned_pages"] == []
    # 旧的逐页覆盖字段必须保留，不能为了 page_evidence 改名或删除。
    assert details["page_count"] == 2
    assert details["text_pages"] == 2
    assert details["page_window"]["page_start"] == 1
    assert details["page_window"]["page_end"] == 2


def test_page_evidence_truncates_pages_but_keeps_true_counts():
    from envoy_cli import reader

    records = {
        number: {
            "status": "text",
            "extraction_mode": "text",
            "text": f"page {number}",
            "reading_order": "stream",
            "layout": None,
            "ocr": None,
            "flags": [],
        }
        for number in range(1, 251)
    }
    evidence = reader._pdf_page_evidence(
        total_page_count=250,
        window_start=1,
        window_end=250,
        page_records=records,
        page_spans=[],
    )

    assert evidence["pages_truncated"] is True
    assert len(evidence["pages"]) == 200
    assert evidence["pages"][-1]["page_number"] == 200
    # 计数按窗口真实值算，截断只能截 pages，不能截 summary。
    assert evidence["indexed_page_count"] == 250
    assert evidence["summary"]["counts"]["text"] == 250
    assert evidence["summary"]["counts"]["unprocessed"] == 0


def test_all_scanned_pages_failing_ocr_reports_a_specific_reason(monkeypatch):
    from envoy_cli import reader

    data = _build_pdf([_scanned_page_stream(), _scanned_page_stream()])
    _stub_render(monkeypatch, calls=[])
    # 两页都跑了 OCR 但一个字都没认出来。
    _stub_ocr_backend(monkeypatch, texts=["", ""])

    with pytest.raises(reader.PublicPageError) as caught:
        reader._parse_pdf_source(_request(), _pdf_source(data))

    assert caught.value.code == ErrorCode.MISSING_CAPABILITY
    message = str(caught.value)
    assert "scanned-page OCR was attempted and failed" in message
    assert "OCR produced no text" in message
    # 失败必须可诊断：说清是哪几页，而不是笼统的「需要 OCR」。
    assert "1" in message and "2" in message


def _real_scan_pdf(tmp_path: Path) -> bytes:
    """用 reportlab 画一个只有图片、没有文字层的 PDF 页。"""

    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    image = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=72)
    except TypeError:  # Pillow < 10 只支持默认小字号
        font = ImageFont.load_default()
    draw.text((30, 80), "SCANNED PAGE", fill="black", font=font)
    image_path = tmp_path / "scan.png"
    image.save(image_path)

    pdf_path = tmp_path / "scan.pdf"
    pdf = canvas.Canvas(str(pdf_path), pagesize=(612, 792))
    pdf.drawImage(ImageReader(str(image_path)), 56, 500, width=500, height=144)
    pdf.showPage()
    pdf.save()
    return pdf_path.read_bytes()


@pytest.mark.skipif(
    shutil.which("tesseract") is None
    or not all(
        importlib.util.find_spec(name) is not None
        for name in ("pypdfium2", "PIL", "reportlab")
    ),
    reason="需要 tesseract 与 pypdfium2/Pillow/reportlab 才能跑真实扫描件",
)
def test_real_tesseract_reads_a_scanned_page(tmp_path):
    from envoy_cli import reader

    status = reader.render_backend_status()
    assert status["available"] is True

    response = reader._parse_pdf_source(_request(), _pdf_source(_real_scan_pdf(tmp_path)))

    blocks = [b for b in response.blocks if b.id.startswith("blk-pdf-page-")]
    assert len(blocks) == 1
    details = response.coverage.modalities["text"].details
    page = details["page_evidence"]["pages"][0]
    assert page["status"] == "ocr"
    assert page["ocr"]["backend"]
    assert page["ocr"]["confidence"] is not None
    # 真实 OCR 的容错很小：只要求认出大写词，不比对整段排版。
    assert "SCANNED" in blocks[0].content.text.upper()

"""Local-file dispatch: a local PDF must never be mistaken for a video."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
for _path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))


from test_pdf_reader import _build_pdf


def test_pdf_magic_wins_over_a_misleading_suffix(tmp_path):
    from envoy_cli import local_source

    path = tmp_path / "paper.bin"
    path.write_bytes(_build_pdf(["Hidden extension"]))

    decision = local_source.classify_local_source(path)

    assert decision.kind == "pdf"
    assert decision.detected_by == "magic"


def test_video_magic_is_kept_on_the_video_path(tmp_path):
    from envoy_cli import local_source

    path = tmp_path / "clip.dat"
    path.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)

    decision = local_source.classify_local_source(path)

    assert decision.kind == "video"
    assert decision.detected_by == "magic"


def test_explicit_document_kind_rejects_a_non_pdf(tmp_path):
    from envoy_cli import local_source

    path = tmp_path / "notes.txt"
    path.write_text("not a pdf", encoding="utf-8")

    decision = local_source.classify_local_source(path, explicit_kind="document")

    assert decision.kind == "unsupported"
    assert decision.detected_by == "explicit_kind"
    assert "not a PDF" in decision.message


def test_local_image_states_the_ocr_boundary(tmp_path):
    from envoy_cli import local_source

    path = tmp_path / "photo.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

    decision = local_source.classify_local_source(path)

    assert decision.kind == "unsupported"
    assert "OCR" in decision.message


def test_local_byte_cap_ignores_an_explicit_zero_download_budget():
    from envoy_cli import local_source, reader

    assert local_source.local_pdf_max_bytes(0) == reader.MAX_PDF_BYTES
    assert local_source.local_pdf_max_bytes(1024) == 1024
    assert (
        local_source.local_pdf_max_bytes(reader.MAX_PDF_BYTES + 1)
        == reader.MAX_PDF_BYTES
    )


def test_chinese_path_with_spaces_is_read_as_a_pdf(tmp_path):
    from envoy_cli import local_source, mcp

    folder = tmp_path / "我的 文档"
    folder.mkdir()
    path = folder / "报告 一.pdf"
    path.write_bytes(_build_pdf(["Chinese path text"]))

    request = local_source.local_read_request(
        {"source": {"url": path.as_uri()}, "goal": "read"}, str(path), kind="document",
    )
    response = local_source.read_local_pdf(request, path)

    assert response.result.value in {"ready", "partial"}
    assert "Chinese path text" in "".join(b.content.text for b in response.blocks)
    assert request.idempotency_key.startswith("local-")
    assert request.source.url.startswith("file://")


def test_windows_file_url_loses_the_url_only_leading_slash():
    from envoy_cli import mcp

    assert mcp._local_source_path({
        "source": {"url": "file:///C:/Users/me/My%20Report.pdf"},
    }) == "C:/Users/me/My Report.pdf"


def test_unsupported_local_type_returns_a_clear_boundary(tmp_path):
    from envoy_cli import mcp

    path = tmp_path / "data.xlsx"
    path.write_bytes(b"PK\x03\x04" + b"\x00" * 16)
    buffer = io.StringIO()
    original = sys.stdout
    sys.stdout = buffer
    try:
        mcp.run_lines([
            {
                "jsonrpc": "2.0", "id": "init", "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            {
                "jsonrpc": "2.0", "id": "call", "method": "tools/call",
                "params": {
                    "name": "envoy_read",
                    "arguments": {"source": {"url": path.as_uri()}},
                },
            },
        ])
    finally:
        sys.stdout = original

    payload = json.loads(buffer.getvalue().splitlines()[1])["result"]["structuredContent"]
    assert payload["result"] == "blocked"
    assert payload["errors"][0]["code"] == "unsupported_source"
    details = payload["errors"][0]["details"]
    assert details["detected_kind"] == "unsupported"
    assert details["uploaded"] is False

"""Share links point at a viewer, not at a file.

`drive.google.com/file/d/<id>/view` and `docs.google.com/.../edit` answer with a
JavaScript shell, so a reader that fetches the link as written gets an empty
page. The export endpoints return the bytes under the same permissions; these
tests pin that rewrite and prove the read actually uses it.
"""

import io
import zipfile

import httpx

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate({"request_id": "share", "source": {"url": url}})


def _docx() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="w"><w:body>'
            "<w:p><w:r><w:t>Shared report body</w:t></w:r></w:p>"
            "</w:body></w:document>",
        )
    return buffer.getvalue()


def _text(response) -> str:
    return "\n".join(block.content.text or "" for block in response.blocks)


def _serve(monkeypatch, handler) -> None:
    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(reader, "_is_public_http_url", lambda *_a, **_k: True)


def test_a_drive_file_link_becomes_the_download_endpoint():
    assert reader.share_export_url(
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
    ) == f"https://drive.google.com/uc?export=download&id={FILE_ID}"


def test_a_google_doc_link_becomes_an_export_of_its_own_format():
    assert reader.share_export_url(
        f"https://docs.google.com/document/d/{FILE_ID}/edit"
    ) == f"https://docs.google.com/document/d/{FILE_ID}/export?format=docx"
    assert reader.share_export_url(
        f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit#gid=0"
    ) == f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=xlsx"
    assert reader.share_export_url(
        f"https://docs.google.com/presentation/d/{FILE_ID}/edit"
    ) == f"https://docs.google.com/presentation/d/{FILE_ID}/export?format=pptx"


def test_a_dropbox_link_asks_for_the_bytes():
    assert reader.share_export_url(
        "https://www.dropbox.com/s/abc/report.xlsx?raw=1"
    ) == "https://www.dropbox.com/s/abc/report.xlsx?raw=1&dl=1"


def test_an_ordinary_link_is_left_alone():
    for url in (
        "https://example.com/report.docx",
        "https://drive.google.com/uc?export=download&id=" + FILE_ID,
        "https://dl.dropboxusercontent.com/s/abc/report.xlsx",
        "https://docs.google.com/forms/d/e/abc/viewform",
    ):
        assert reader.share_export_url(url) == url


def test_a_shared_document_link_is_read_as_text(monkeypatch):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, headers={"content-type": DOCX_TYPE}, content=_docx())

    _serve(monkeypatch, handler)

    response = reader._read_public_page_once(_request(
        f"https://docs.google.com/document/d/{FILE_ID}/edit"
    ))

    assert response.result == reader.ReadResult.READY
    assert "Shared report body" in _text(response)
    assert seen == [f"https://docs.google.com/document/d/{FILE_ID}/export?format=docx"]
    assert response.coverage.modalities["text"].details["document_format"] == "docx"
    # The response names the material by the link the user handed in, not by the
    # export endpoint that was actually fetched.
    assert response.blocks[0].locator.source_url == (
        f"https://docs.google.com/document/d/{FILE_ID}/edit"
    )
    assert response.blocks[0].content.text == "docs.google.com"


def test_a_onedrive_link_asks_for_the_download():
    assert reader.share_export_url(
        "https://1drv.ms/u/s!AgsM3hkLexJ-hvJIR_epSz7JH8hB7g?e=abc"
    ) == "https://1drv.ms/u/s!AgsM3hkLexJ-hvJIR_epSz7JH8hB7g?e=abc&download=1"
    assert reader.share_export_url(
        "https://onedrive.live.com/?resid=ABC!123&cid=ABC"
    ) == "https://onedrive.live.com/?resid=ABC!123&cid=ABC&download=1"


def test_a_tsv_link_is_read_as_text_rows():
    response = reader._parse_text_source(
        _request("https://example.com/data.tsv"),
        {"url": "https://example.com/data.tsv", "data": b"a\tb\n1\t2\n", "mime_type": "text/tab-separated-values"},
    )
    assert response.result == reader.ReadResult.READY
    assert "1\t2" in _text(response)


def test_a_text_file_served_as_a_binary_download_is_read(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content="第一行\nsecond line\n".encode(),
        )

    _serve(monkeypatch, handler)

    response = reader._read_public_page_once(_request("https://example.com/notes"))

    assert response.result == reader.ReadResult.READY
    assert "第一行" in _text(response)


def test_a_binary_download_stays_refused(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01\x02binary",
        )

    _serve(monkeypatch, handler)

    response = reader._read_public_page_once(_request("https://example.com/blob"))

    assert response.result != reader.ReadResult.READY
    assert response.errors[0].code == reader.ErrorCode.UNSUPPORTED_SOURCE

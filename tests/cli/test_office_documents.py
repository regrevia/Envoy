"""ZIP container documents (.docx/.pptx/.epub/.odt) are read with the stdlib only.

A link that serves a document must come back as text, not as an unsupported
source or a pile of binary bytes.
"""

import io
import zipfile

import httpx
import pytest

from envoy_cli import reader
from envoy_core.schemas.read_v1 import ReadRequest

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _zip(parts: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in parts.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _request(url: str) -> ReadRequest:
    return ReadRequest.model_validate({"request_id": "office", "source": {"url": url}})


def _read(url: str, data: bytes, mime_type: str):
    return reader._parse_office_source(
        _request(url), {"url": url, "data": data, "mime_type": mime_type}
    )


def _text(response) -> str:
    return "\n".join(block.content.text or "" for block in response.blocks)


def test_a_word_document_is_read_as_paragraphs():
    data = _zip({
        "[Content_Types].xml": "<Types/>",
        "word/document.xml": (
            '<w:document xmlns:w="w"><w:body>'
            "<w:p><w:r><w:t>第一段 &amp; 标题</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Second</w:t></w:r><w:r><w:t> paragraph</w:t></w:r></w:p>"
            "</w:body></w:document>"
        ),
    })

    response = _read("https://example.com/report.docx", data, DOCX_TYPE)

    assert response.result == reader.ReadResult.READY
    assert "第一段 & 标题" in _text(response)
    assert "Second paragraph" in _text(response)
    assert response.coverage.modalities["text"].details["document_format"] == "docx"


def test_a_presentation_is_read_slide_by_slide():
    data = _zip({
        "ppt/slides/slide1.xml": '<p:sld xmlns:a="a"><a:t>Opening slide</a:t></p:sld>',
        "ppt/slides/slide2.xml": '<p:sld xmlns:a="a"><a:t>Second</a:t><a:t> slide</a:t></p:sld>',
    })

    response = _read("https://example.com/talk.pptx", data, PPTX_TYPE)

    text = _text(response)
    assert "Slide 1" in text and "Opening slide" in text
    assert "Slide 2" in text and "Second slide" in text


def test_an_epub_is_read_through_its_package_spine():
    data = _zip({
        "META-INF/container.xml": (
            '<container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'
        ),
        "OEBPS/content.opf": (
            '<package><manifest>'
            '<item id="c1" href="chapter1.xhtml"/>'
            '<item id="c2" href="chapter2.xhtml"/>'
            "</manifest><spine><itemref idref=\"c1\"/><itemref idref=\"c2\"/></spine></package>"
        ),
        "OEBPS/chapter1.xhtml": "<html><body><h1>第一章</h1><p>开端。</p></body></html>",
        "OEBPS/chapter2.xhtml": "<html><body><p>Second chapter.</p></body></html>",
    })

    response = _read("https://example.com/book.epub", data, "application/epub+zip")

    text = _text(response)
    assert "第一章" in text and "开端。" in text
    assert "Second chapter." in text


def test_an_opendocument_text_is_read_from_content_xml():
    data = _zip({
        "content.xml": (
            '<office:document-content xmlns:text="text"><text:p>第一段</text:p>'
            "<text:p>Second <text:s/>paragraph</text:p></office:document-content>"
        ),
    })

    response = _read("https://example.com/notes.odt", data, "application/vnd.oasis.opendocument.text")

    text = _text(response)
    assert "第一段" in text
    assert "Second paragraph" in text


def test_a_plain_zip_is_not_called_a_document():
    data = _zip({"payload.csv": "a,b\n1,2\n"})

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._office_document_text(data, "https://example.com/archive.zip", "application/zip")

    assert excinfo.value.code == reader.ErrorCode.UNSUPPORTED_SOURCE


def test_a_document_without_text_says_so():
    data = _zip({"word/document.xml": '<w:document xmlns:w="w"><w:body/></w:document>'})

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._office_document_text(data, "https://example.com/empty.docx", DOCX_TYPE)

    assert excinfo.value.code == reader.ErrorCode.EMPTY_CONTENT


def test_a_docx_link_is_detected_by_the_fetch_layer(monkeypatch):
    data = _zip({
        "word/document.xml": (
            '<w:document xmlns:w="w"><w:body>'
            "<w:p><w:r><w:t>Downloaded body</w:t></w:r></w:p></w:body></w:document>"
        ),
    })

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=data,
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.DetectedOffice) as excinfo:
        reader._fetch_html("https://example.com/report.docx", 1024 * 1024, {})

    detected = excinfo.value.source
    assert detected["data"].startswith(b"PK\x03\x04")
    response = reader._parse_office_source(_request("https://example.com/report.docx"), detected)
    assert "Downloaded body" in _text(response)


def test_a_document_link_without_a_zip_body_is_not_mistaken(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"<html><body>not a document</body></html>",
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._fetch_html("https://example.com/report.docx", 1024 * 1024, {})

    assert excinfo.value.code == reader.ErrorCode.UNSUPPORTED_SOURCE


def _xlsx(sheet_name: str, rows: list[list[str]]) -> bytes:
    """Minimal workbook: shared strings, one sheet, and the rels that link them."""

    ordered = [value for row in rows for value in row]
    shared = {value: index for index, value in enumerate(dict.fromkeys(ordered))}
    body = "".join(
        f'<row r="{number}">'
        + "".join(
            f'<c r="{chr(ord("A") + index)}{number}" t="s"><v>{shared[value]}</v></c>'
            for index, value in enumerate(row)
        )
        + "</row>"
        for number, row in enumerate(rows, start=1)
    )
    strings = "".join(f"<si><t>{value}</t></si>" for value in shared)
    return _zip({
        "[Content_Types].xml": "<Types/>",
        "xl/sharedStrings.xml": f"<sst>{strings}</sst>",
        "xl/workbook.xml": (
            f'<workbook><sheets><sheet name="{sheet_name}" sheetId="1" r:id="rId1"/>'
            "</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            '<Relationships><Relationship Id="rId1" '
            'Target="worksheets/sheet1.xml"/></Relationships>'
        ),
        "xl/worksheets/sheet1.xml": f"<worksheet><sheetData>{body}</sheetData></worksheet>",
    })


def test_an_excel_workbook_is_read_as_rows():
    data = _xlsx("Data", [["Name", "Score"], ["alice", "3"]])

    response = _read(
        "https://example.com/report.xlsx",
        data,
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

    assert response.result == reader.ReadResult.READY
    assert "Data" in _text(response)
    assert "Name | Score" in _text(response)
    assert "alice | 3" in _text(response)
    assert response.coverage.modalities["text"].details["document_format"] == "xlsx"


def test_an_ods_sheet_is_read_as_rows():
    content = (
        '<office:document-content xmlns:office="o" xmlns:table="t" xmlns:text="x">'
        "<office:body><office:spreadsheet>"
        '<table:table table:name="预算">'
        "<table:table-row>"
        '<table:table-cell office:value-type="string"><text:p>项目</text:p></table:table-cell>'
        '<table:table-cell office:value-type="string"><text:p>金额</text:p></table:table-cell>'
        "</table:table-row>"
        "<table:table-row>"
        '<table:table-cell office:value-type="string"><text:p>服务器</text:p></table:table-cell>'
        '<table:table-cell office:value-type="float" office:value="1200"/>'
        "</table:table-row>"
        "</table:table></office:spreadsheet></office:body></office:document-content>"
    )
    data = _zip({"content.xml": content})

    response = _read(
        "https://example.com/budget.ods",
        data,
        "application/vnd.oasis.opendocument.spreadsheet",
    )

    assert response.result == reader.ReadResult.READY
    assert "预算" in _text(response)
    assert "项目 | 金额" in _text(response)
    assert "服务器 | 1200" in _text(response)


def test_an_rtf_link_is_read_as_text(monkeypatch):
    rtf = (
        "{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Helvetica;}}"
        "\\f0 Hello \\b world\\b0\\par Second paragraph\\par}"
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/rtf"}, content=rtf.encode("latin-1")
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.DetectedText) as excinfo:
        reader._fetch_html("https://example.com/notes.rtf", 1024 * 1024, {})

    response = reader._parse_text_source(
        _request("https://example.com/notes.rtf"), excinfo.value.source
    )
    assert "Hello world" in _text(response)
    assert "Second paragraph" in _text(response)


def test_a_legacy_doc_link_says_which_format_to_use(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "application/msword"}, content=b"\xd0\xcf\x11\xe0"
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._fetch_html("https://example.com/old.doc", 1024 * 1024, {})

    assert excinfo.value.code == reader.ErrorCode.UNSUPPORTED_SOURCE
    assert ".docx" in str(excinfo.value)


def test_an_apple_iwork_file_answers_with_a_conversion_hint(monkeypatch):
    from envoy_cli import reader

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"PK\x03\x04iwork-index-binary",
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._fetch_html("https://example.com/deck.pages", 1024 * 1024, {})

    message = str(excinfo.value)
    assert "iWork" in message
    assert ".pptx" in message


def test_an_xlsb_workbook_answers_with_a_conversion_hint(monkeypatch):
    from envoy_cli import reader

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "application/octet-stream"},
            content=b"\x00\x01xlsb-binary",
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        reader.httpx,
        "Client",
        lambda **_kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(reader.PublicPageError) as excinfo:
        reader._fetch_html("https://example.com/book.xlsb", 1024 * 1024, {})

    message = str(excinfo.value)
    assert ".xlsb" in message
    assert ".xlsx" in message

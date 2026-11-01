import json
import sys
from pathlib import Path

import httpx

ROOT_DIR = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT_DIR / "packages" / "envoy-core" / "src"
CLI_DIR = ROOT_DIR / "packages" / "envoy-cli" / "src"
for path in (CORE_DIR, CLI_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

SAMPLE_HTML = """
<html><head>
<title>Release notes</title>
<link rel="canonical" href="/releases/1.2">
<meta name="description" content="  Fixes   for 1.2 ">
<meta property="og:title" content="Release 1.2">
</head><body>
<nav><a href="/nav">Navigation</a><h2>Menu</h2></nav>
<h1 id="top">Release 1.2</h1>
<p>See <a href="/next">the next steps</a> and <a href="https://other.example.net/x">an external page</a>.</p>
<h2>Fixes</h2>
<h3>Details</h3>
<table>
<caption>Changed files</caption>
<tr><th>File</th><th>Change</th></tr>
<tr><td>a.py</td><td>fix</td></tr>
<tr><td>b.py</td><td>refactor</td></tr>
</table>
<p><a href="javascript:void(0)">script link</a><a href="#top">back to top</a> <a href="/next">the next steps</a></p>
</body></html>
"""

BASE_URL = "https://docs.example.com/releases"


def _parse(html: str = SAMPLE_HTML):
    from envoy_cli.reader import _PageParser

    parser = _PageParser()
    parser.feed(html)
    return parser


def _builders():
    from envoy_cli import reader

    return (
        reader._page_heading_blocks,
        reader._page_structured_links,
        reader._page_table_blocks,
        reader._page_structure_details,
    )


def test_headings_become_blocks_with_levels_parents_and_anchors():
    from envoy_cli.reader import _clean_text

    heading_blocks, _links, _tables, _details = _builders()
    parser = _parse()
    blocks = heading_blocks(parser, BASE_URL)

    assert [block.kind.value for block in blocks] == ["heading", "heading", "heading"]
    assert [block.content.text for block in blocks] == ["Release 1.2", "Fixes", "Details"]
    assert [(block.locator.selector, block.parent_id) for block in blocks] == [
        ("h1#top", "blk-title"),
        ("h2", "blk-heading-0001"),
        ("h3", "blk-heading-0002"),
    ]
    assert [block.order for block in blocks] == [1, 2, 3]
    # Navigation headings are not page structure.
    assert "Menu" not in _clean_text(parser.text_parts)


def test_heading_text_never_repeats_in_body_text():
    from envoy_cli.reader import _clean_text

    parser = _parse()
    text = _clean_text(parser.text_parts)

    assert "Release 1.2" not in text
    assert "Fixes" not in text
    assert "Details" not in text
    assert "the next steps" in text


def test_links_are_absolutized_classified_and_deduplicated():
    _headings, structured_links, _tables, _details = _builders()
    parser = _parse()
    links, truncated = structured_links(parser, BASE_URL)

    assert truncated is False
    assert links == [
        {"text": "the next steps", "url": "https://docs.example.com/next", "kind": "internal"},
        {"text": "an external page", "url": "https://other.example.net/x", "kind": "external"},
        {"text": "back to top", "url": "https://docs.example.com/releases", "kind": "anchor"},
    ]


def test_link_hrefs_never_appear_in_body_text():
    from envoy_cli.reader import _clean_text

    parser = _parse()
    text = _clean_text(parser.text_parts)

    assert "https://docs.example.com/next" not in text
    assert "/next" not in text
    assert "javascript:" not in text


def test_links_are_capped_and_flagged():
    from envoy_cli.reader import MAX_PAGE_LINKS, _PageParser

    anchors = "".join(f'<a href="/page-{index}">page {index}</a>' for index in range(MAX_PAGE_LINKS + 25))
    parser = _PageParser()
    parser.feed(f"<html><body><p>{anchors}</p></body></html>")

    _headings, structured_links, _tables, _details = _builders()
    links, truncated = structured_links(parser, BASE_URL)

    assert len(links) == MAX_PAGE_LINKS
    assert truncated is True


def test_headings_are_capped_and_flagged():
    from envoy_cli.reader import MAX_PAGE_HEADINGS, _PageParser

    markup = "".join(f"<h2>Section {index}</h2><p>body {index}</p>" for index in range(MAX_PAGE_HEADINGS + 25))
    parser = _PageParser()
    parser.feed(f"<html><body>{markup}</body></html>")

    heading_blocks, _links, _tables, details = _builders()
    blocks = heading_blocks(parser, BASE_URL)

    assert len(blocks) == MAX_PAGE_HEADINGS
    assert parser.headings_truncated is True
    assert details(parser, BASE_URL, blocks, [], [], False)["headings_truncated"] is True


def test_table_rows_columns_and_caption_are_preserved():
    _headings, _links, table_blocks, _details = _builders()
    parser = _parse()
    blocks = table_blocks(parser, BASE_URL, 10)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind.value == "table"
    assert block.content.columns == ["File", "Change"]
    assert block.content.rows == [["a.py", "fix"], ["b.py", "refactor"]]
    assert block.content.caption == "Changed files"
    assert block.locator.selector == "table"
    assert block.parent_id == "blk-heading-0003"
    assert block.order == 10
    assert block.quality.unresolved == []


def test_table_limits_are_flagged_in_quality():
    from envoy_cli.reader import (
        MAX_PAGE_TABLE_COLUMNS,
        MAX_PAGE_TABLE_ROWS,
        _PageParser,
    )

    header = "".join(f"<th>c{index}</th>" for index in range(MAX_PAGE_TABLE_COLUMNS + 4))
    body = "".join(
        "<tr>" + "".join(f"<td>v{index}</td>" for index in range(MAX_PAGE_TABLE_COLUMNS + 4)) + "</tr>"
        for index in range(MAX_PAGE_TABLE_ROWS + 5)
    )
    parser = _PageParser()
    parser.feed(f"<html><body><table><tr>{header}</tr>{body}</table></body></html>")

    _headings, _links, table_blocks, details = _builders()
    blocks = table_blocks(parser, BASE_URL, 0)
    block = blocks[0]

    assert len(block.content.columns) == MAX_PAGE_TABLE_COLUMNS
    assert len(block.content.rows) == MAX_PAGE_TABLE_ROWS
    assert any("rows beyond" in note for note in block.quality.unresolved)
    assert any("columns beyond" in note for note in block.quality.unresolved)
    assert details(parser, BASE_URL, [], blocks, [], False)["tables_truncated"] is True


def test_metadata_is_structured_and_absent_metadata_stays_null():
    from envoy_cli.reader import _PageParser

    _headings, _links, _tables, details = _builders()
    parser = _parse()
    page = details(parser, BASE_URL, [], [], [], False)

    assert page["canonical_url"] == "https://docs.example.com/releases/1.2"
    assert page["description"] == "Fixes for 1.2"
    assert page["og_title"] == "Release 1.2"

    bare = _PageParser()
    bare.feed("<html><head><title>Only a title</title></head><body><p>Body.</p></body></html>")
    # A page with no structure and no head metadata keeps the previous shape.
    assert details(bare, BASE_URL, [], [], [], False) == {}

    structured_only = _PageParser()
    structured_only.feed("<html><body><h2>Section</h2><p>Body.</p></body></html>")
    from envoy_cli.reader import _page_heading_blocks

    blocks = _page_heading_blocks(structured_only, BASE_URL)
    page = details(structured_only, BASE_URL, blocks, [], [], False)

    assert page["canonical_url"] is None
    assert page["description"] is None
    assert page["og_title"] is None


def test_static_read_exposes_headings_links_and_tables(monkeypatch, capsys):
    from envoy_cli import cli

    html = SAMPLE_HTML.encode("utf-8")

    class Response:
        status_code = 200
        headers = {"content-type": "text/html; charset=utf-8"}
        url = "https://example.org/releases"
        content = html
        text = html.decode("utf-8")

        def raise_for_status(self):
            return None

        def iter_bytes(self):
            yield self.content

    class StreamContext:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self.response

        def __exit__(self, *_args):
            return False

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return StreamContext(Response())

    monkeypatch.setattr(httpx, "Client", Client)
    request = {
        "protocol_version": "envoy.read/1",
        "request_id": "req-page-structure-1",
        "source": {"url": "https://example.org/releases"},
        "goal": "Extract the release notes",
    }

    exit_code = cli.main(["read", "--request-json", "-"], stdin=json.dumps(request))

    assert exit_code == 0
    response = json.loads(capsys.readouterr().out)
    assert response["result"] == "ready"

    blocks = {block["id"]: block for block in response["blocks"]}
    assert blocks["blk-title"]["kind"] == "heading"
    assert blocks["blk-heading-0001"]["kind"] == "heading"
    assert blocks["blk-heading-0001"]["content"]["text"] == "Release 1.2"
    assert blocks["blk-heading-0001"]["locator"]["selector"] == "h1#top"
    assert blocks["blk-table-0001"]["kind"] == "table"
    assert blocks["blk-table-0001"]["content"]["columns"] == ["File", "Change"]
    assert [block["order"] for block in response["blocks"]] == list(range(len(response["blocks"])))

    body = blocks["blk-body-0001"]["content"]["text"]
    assert "Release 1.2" not in body
    assert "https://docs.example.com/next" not in body
    assert "#" not in body

    details = response["coverage"]["modalities"]["text"]["details"]
    assert [entry["block_id"] for entry in details["headings"]] == ["blk-heading-0001", "blk-heading-0002", "blk-heading-0003"]
    assert details["page_links"][0] == {
        "text": "the next steps",
        "url": "https://example.org/next",
        "kind": "internal",
    }
    assert details["canonical_url"] == "https://example.org/releases/1.2"
    assert details["description"] == "Fixes for 1.2"

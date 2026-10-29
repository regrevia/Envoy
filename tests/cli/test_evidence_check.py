"""A citation check must settle what is checkable and refuse to fake the rest.

The failure this pins: Envoy used to answer "is this citation real?" by
searching for a substring and calling a hit "supported". A hit proves the words
are in the material; it never proves they back the claim. These tests hold the
deterministic half (lease, block, quote, locator) to exact answers and hold the
semantic half to `not_evaluated`, and they pin that a PDF locator with no page
span says so instead of silently reporting page 1.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
for _path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import pytest

from envoy_cli import cli, reader
from envoy_cli import storage as state_storage
from envoy_cli.local_source import read_local_pdf
from envoy_core.schemas.read_v1 import (
    Budget,
    EvidenceCheckRequest,
    EvidenceCheckResponse,
    MaterialSearchRequest,
    PdfLocator,
    ReadRequest,
    SourceRef,
)


def _build_pdf(pages: list[str]) -> bytes:
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


def _read_local_pdf(tmp_path: Path, pages: list[str] | None = None, max_chars: int | None = None):
    # The root is cached per process; each test gets its own tmp data dir.
    state_storage.reset_root_cache()
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(
        _build_pdf(
            pages
            or [
                "Retained evidence states its own page.",
                "The second page carries a different sentence.",
            ]
        )
    )
    request = ReadRequest(
        request_id="req-evidence",
        source=SourceRef(url=pdf.as_uri(), kind="document"),
        goal="check a citation",
        budget=Budget(max_response_chars=max_chars) if max_chars else Budget(),
    )
    return read_local_pdf(request, pdf)


def _check(**payload) -> EvidenceCheckResponse:
    return reader.check_evidence(EvidenceCheckRequest.model_validate(payload))


def _first_text_block(response) -> tuple[str, str]:
    for block in response.blocks:
        text = getattr(block.content, "text", None)
        if text:
            return block.id, text
    raise AssertionError("the read produced no text block")


def _page_block(response) -> tuple[str, str]:
    for block in response.blocks:
        text = getattr(block.content, "text", None)
        locator = getattr(block, "locator", None)
        if text and getattr(locator, "type", None) == "pdf_page":
            return block.id, text
    raise AssertionError("the read produced no page block")


def test_a_quote_present_in_the_block_matches_and_support_is_not_claimed(tmp_path):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id
    block_id, text = _page_block(response)
    quote = " ".join(text.split()[:5]).rstrip(".")

    result = _check(material_id=material_id, block_id=block_id, quote=quote)

    assert result.resolution == "matched"
    assert result.quote_match in {"exact", "normalized"}
    assert result.locator_valid is True
    # The words occurring is not evidence they support anything.
    assert result.semantic_support == "not_evaluated"
    assert result.source_revision


def test_whitespace_differences_normalize_but_wording_does_not(tmp_path):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id
    block_id, text = _page_block(response)
    words = " ".join(text.split()[:4])
    spaced = "   ".join(words.split())

    normalized = _check(material_id=material_id, block_id=block_id, quote=spaced)
    assert normalized.resolution == "matched"
    assert normalized.quote_match in {"exact", "normalized"}

    absent = _check(
        material_id=material_id,
        block_id=block_id,
        quote="this sentence never appeared in the material",
    )
    assert absent.resolution == "unmatched"
    assert absent.reason == "quote_missing"
    assert absent.quote_match == "none"


def test_a_title_block_matches_but_is_reported_as_unlocatable(tmp_path):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id

    result = _check(material_id=material_id, block_id="blk-title", quote="paper")

    assert result.resolution == "matched"
    assert result.quote_match in {"exact", "normalized"}
    assert result.locator_valid is False
    assert result.reason == "no_locator"
    assert "no locator" in " ".join(result.notes)


def test_evidence_id_is_stable_across_repeated_checks(tmp_path):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id
    block_id, text = _page_block(response)
    quote = " ".join(text.split()[:5])

    first = _check(material_id=material_id, block_id=block_id, quote=quote)
    second = _check(material_id=material_id, block_id=block_id, quote=quote)

    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("ev-")


def test_search_ids_name_the_passage_not_the_query(tmp_path):
    response = _read_local_pdf(
        tmp_path,
        pages=[
            "Opening page of the report.",
            "Second page restates the scope.",
            "The third page mentions the quokka once.",
            "The fourth page mentions the zephyr twice, zephyr.",
        ],
        max_chars=300,
    )
    material_id = response.material_id
    stored = json.loads(
        (reader._material_root() / f"{material_id}.json").read_text(encoding="utf-8")
    )
    total_text = str(stored["text"])

    def start_offset(content: str) -> int:
        offset = total_text.find(content)
        assert offset >= 0, "the searched passage must come from the retained text"
        return offset

    first = reader.search_material(
        MaterialSearchRequest(
            request_id="search-1", material_id=material_id, query="quokka"
        )
    )
    second = reader.search_material(
        MaterialSearchRequest(
            request_id="search-2", material_id=material_id, query="zephyr"
        )
    )
    blocks = [(block, entry) for entry in (first, second) for block in entry.blocks]
    assert blocks, "the unseen part of the material must be searchable"
    ids = []
    for block, _entry in blocks:
        content = block.content.text
        expected = "blk-search-" + reader._stable_evidence_digest(
            material_id, start_offset(content), content
        )
        assert block.id == expected
        ids.append(block.id)
    # The old failure was a per-response counter (`blk-search-0001`), so two
    # different passages could share one id; a digest of the passage cannot.
    assert all(not block_id.startswith("blk-search-000") for block_id in ids)
    assert len(set(ids)) == len(ids)


def test_missing_and_expired_material_stay_unmatched_and_say_why(tmp_path):
    missing = _check(material_id="mat-000000000000000000000000")
    assert missing.resolution == "unmatched"
    assert missing.reason == "material_missing"
    assert missing.semantic_support == "not_evaluated"

    not_issued = _check(material_id="not-a-material-id")
    assert not_issued.reason == "material_missing"
    assert "not one Envoy issued" in " ".join(not_issued.notes)

    response = _read_local_pdf(tmp_path)
    material_id = response.material_id
    block_id, text = _page_block(response)
    expires = reader._material_root() / f"{material_id}.expires"
    expires.write_text(str(time.time() - 60), encoding="ascii")

    expired = _check(material_id=material_id, block_id=block_id, quote=text[:20])
    assert expired.resolution == "unmatched"
    assert expired.reason == "material_expired"
    assert "re-read" in " ".join(expired.notes)


def test_unknown_block_id_is_reported_as_block_missing(tmp_path):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id

    result = _check(material_id=material_id, block_id="blk-body-9999")

    assert result.resolution == "unmatched"
    assert result.reason == "block_missing"
    assert result.block_id == "blk-body-9999"


def test_pdf_locator_without_a_page_span_is_invalid_instead_of_page_one():
    # A valid locator must name a page; the old code reported page 1 whenever
    # the text offset did not fall inside an extracted page span.
    with pytest.raises(ValueError):
        PdfLocator(type="pdf_page", page_number=None, locator_valid=True)

    unspanned = PdfLocator(type="pdf_page", page_number=None, locator_valid=False)
    assert unspanned.page_number is None
    assert unspanned.locator_valid is False


def test_unspanned_pdf_text_says_the_locator_is_unverifiable(tmp_path):
    root = reader._material_root()
    material_id = "mat-" + "a" * 24
    (root / f"{material_id}.json").write_text(
        json.dumps(
            {
                "source_url": "https://example.com/paper.pdf",
                "source_revision": "sha256:abc",
                "session_id": "session-1",
                "source_kind": "pdf",
                "blocks": [
                    {
                        "id": "blk-body-0001",
                        "kind": "paragraph",
                        "content": {"type": "text", "text": "A sentence outside any page span."},
                        "origin": "extracted",
                        "locator": {
                            "type": "pdf_page",
                            "page_number": None,
                            "locator_valid": False,
                        },
                        "order": 1,
                    }
                ],
                "page_spans": [{"page_number": 2, "start": 0, "end": 10}],
            }
        ),
        encoding="utf-8",
    )
    (root / f"{material_id}.expires").write_text(str(time.time() + 600), encoding="ascii")

    result = _check(
        material_id=material_id,
        block_id="blk-body-0001",
        quote="sentence outside any page span",
    )

    assert result.resolution == "matched"
    assert result.quote_match in {"exact", "normalized"}
    assert result.locator_valid is False
    assert result.reason == "locator_unverifiable"
    assert "re-read with page_start/page_end" in " ".join(result.notes)


def test_cli_reports_matched_and_unmatched_exit_codes(tmp_path, capsys):
    response = _read_local_pdf(tmp_path)
    material_id = response.material_id
    block_id, text = _page_block(response)

    good = tmp_path / "good.json"
    good.write_text(
        json.dumps({"material_id": material_id, "block_id": block_id, "quote": text[:12]}),
        encoding="utf-8",
    )
    assert cli.main(["evidence", "check", "--request-json", str(good), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["resolution"] == "matched"
    assert payload["semantic_support"] == "not_evaluated"

    bad = tmp_path / "bad.json"
    bad.write_text(
        json.dumps({"material_id": material_id, "block_id": block_id, "quote": "absent wording"}),
        encoding="utf-8",
    )
    assert cli.main(["evidence", "check", "--request-json", str(bad), "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "quote_missing"


def test_mcp_exposes_verify_only_outside_the_core_toolset():
    from envoy_cli import mcp

    assert "envoy_verify" in mcp.TOOLSETS["full"]
    assert "envoy_verify" not in mcp.TOOLSETS["core"]
    tool = next(item for item in mcp.TOOLS if item["name"] == "envoy_verify")
    assert "not_evaluated" in tool["description"]
    assert tool["inputSchema"]["required"] == ["material_id"]


def test_revision_kind_names_what_the_digest_covers():
    from envoy_cli.reader import _evidence_revision_kind

    # A PDF revision is the file's byte digest.
    assert _evidence_revision_kind({"source_kind": "pdf"}, "a" * 64) == "byte_digest"
    # A media read without a revision hashes the URL; that string is not content.
    url = "https://example.com/paper.pdf"
    url_digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    assert _evidence_revision_kind({"source_kind": "video", "source_url": url}, url_digest) == "url_digest"
    # Anything else stays unclaimed rather than being passed off as a byte digest.
    assert _evidence_revision_kind({"source_kind": "text", "source_url": url}, "b" * 64) == "unknown"
    assert _evidence_revision_kind({"source_kind": "pdf"}, None) == "unknown"


def test_a_pdf_check_reports_the_revision_it_traces_to(tmp_path):
    response = _read_local_pdf(tmp_path)
    check = _check(material_id=response.material_id)
    assert check.source_revision == response.source_revision
    assert check.source_revision_kind == "byte_digest"

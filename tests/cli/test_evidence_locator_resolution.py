"""Any block id a receipt prints must resolve against the stored material.

The failure this pins: a citation only resolved when read had stored an explicit
``blocks`` list, so web, subtitle and ASR materials (which keep flat text plus
spans) answered ``block_missing`` for a block the host had just been shown, and
an expand receipt's ``-range-{offset}`` id resolved for nothing at all. One
index now answers read, search, expand, replay and verify alike, and an id that
encodes a character range is rebuilt from the stored text instead of trusted.
"""

from __future__ import annotations

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

from envoy_cli import reader
from envoy_cli import storage as state_storage
from envoy_core.schemas.read_v1 import EvidenceCheckRequest

TEXT = (
    "First sentence about otters and rivers. "
    "Second sentence about herons and reeds. "
    "Third sentence about beavers and dams."
)
REVISION = "b" * 64


def _material(material_id: str = "mat-" + "a" * 24, **overrides) -> str:
    state_storage.reset_root_cache()
    data = {
        "source_url": "https://example.com/story",
        "source_revision": REVISION,
        "source_kind": "web",
        "text": TEXT,
        "title_text": "A story",
        "expires_at": time.time() + 600,
    }
    data.update(overrides)
    reader._store_material(material_id, data)
    return material_id


def _check(**payload):
    return reader.check_evidence(EvidenceCheckRequest.model_validate(payload))


def _slice_of(needle: str) -> tuple[int, int]:
    start = TEXT.index(needle)
    return start, start + len(needle)


def test_a_web_read_block_resolves_without_a_stored_blocks_list():
    material_id = _material()

    result = _check(material_id=material_id, block_id="blk-body-0001", quote="herons and reeds")

    assert result.resolution == "matched"
    assert result.quote_match == "exact"
    assert result.locator_valid is True
    assert result.reason == "quote_found"


def test_a_search_block_id_resolves_to_its_character_range():
    material_id = _material()
    start, end = _slice_of("herons and reeds")
    block_id = f"blk-search-{start}-{end}-4f0b9d2c77aa10e5"

    result = _check(material_id=material_id, block_id=block_id, quote="herons and reeds")

    assert result.resolution == "matched"
    assert result.quote_match == "exact"
    assert result.locator_valid is True
    # The range is what the id means: words from elsewhere in the material
    # must not match this block.
    outside = _check(material_id=material_id, block_id=block_id, quote="otters and rivers")
    assert outside.resolution == "unmatched"
    assert outside.reason == "quote_missing"


def test_an_expand_range_id_resolves_from_the_whole_text_block():
    material_id = _material(
        budget_state={
            "returned_ranges": [[0, 40], [40, 80]],
            "returned_chars": 80,
        }
    )
    block_id = "blk-body-0001-range-40"

    result = _check(material_id=material_id, block_id=block_id, quote=TEXT[40:60])

    assert result.resolution == "matched"
    assert result.quote_match in {"exact", "normalized"}
    assert result.locator_valid is True
    # The range ends where the earlier delivery ended, so text beyond it is
    # not part of the block the receipt named.
    beyond = _check(material_id=material_id, block_id=block_id, quote=TEXT[80:95])
    assert beyond.resolution == "unmatched"


def test_a_video_cue_id_resolves_to_the_transcript_cue():
    first = "Opening remark about the harbour."
    second = "Second remark about the tide."
    text = first + " " + second
    second_start = text.index(second)
    material_id = _material(
        source_kind="video",
        source_url="https://example.com/talk",
        text=text,
        title_text="",
        video_spans=[
            {
                "start_offset": 0,
                "end_offset": len(first),
                "start_seconds": 0.0,
                "end_seconds": 1.5,
            },
            {
                "start_offset": second_start,
                "end_offset": len(text),
                "start_seconds": 1.5,
                "end_seconds": 3.0,
            },
        ],
    )

    result = _check(material_id=material_id, block_id="blk-video-asr-0002", quote="about the tide")

    assert result.resolution == "matched"
    assert result.quote_match == "exact"
    assert result.locator_valid is True


def test_a_pdf_page_id_resolves_from_page_spans():
    page_one = "Retained evidence states its own page."
    page_two = "The second page carries a different sentence."
    text = page_one + "\n" + page_two
    second_start = text.index(page_two)
    material_id = _material(
        source_kind="pdf",
        source_url="https://example.com/paper.pdf",
        text=text,
        title_text="",
        page_spans=[
            {"page_number": 1, "start": 0, "end": len(page_one)},
            {"page_number": 2, "start": second_start, "end": len(text)},
        ],
    )

    result = _check(
        material_id=material_id,
        block_id="blk-pdf-page-0002",
        quote="a different sentence",
    )

    assert result.resolution == "matched"
    assert result.quote_match == "exact"
    assert result.locator_valid is True


def test_a_video_transcript_range_id_resolves_after_partial_delivery():
    material_id = _material(
        source_kind="video",
        source_url="https://example.com/long-talk",
        title_text="",
        budget_state={"returned_ranges": [[0, 48]], "returned_chars": 48},
        video_spans=[
            {
                "start_offset": 0,
                "end_offset": len(TEXT),
                "start_seconds": 0.0,
                "end_seconds": 12.0,
            }
        ],
    )

    result = _check(
        material_id=material_id,
        block_id="blk-video-transcript-range-0",
        quote="otters and rivers",
    )

    assert result.resolution == "matched"
    assert result.quote_match == "exact"
    assert result.locator_valid is True


def test_an_unknown_block_id_is_still_reported_missing():
    material_id = _material()

    result = _check(material_id=material_id, block_id="blk-body-0002", quote="herons")

    assert result.resolution == "unmatched"
    assert result.reason == "block_missing"
    assert result.locator_valid is False


def test_a_block_id_from_another_material_does_not_resolve():
    material_id = _material()

    result = _check(material_id=material_id, block_id="blk-pdf-page-0002", quote="herons")

    assert result.resolution == "unmatched"
    assert result.reason == "block_missing"


def test_a_search_range_past_the_text_does_not_resolve():
    material_id = _material()

    result = _check(
        material_id=material_id,
        block_id=f"blk-search-{len(TEXT) + 10}-{len(TEXT) + 40}-4f0b9d2c77aa10e5",
        quote="herons",
    )

    assert result.resolution == "unmatched"
    assert result.reason == "block_missing"


def test_a_position_based_search_id_is_not_accepted():
    material_id = _material()

    result = _check(material_id=material_id, block_id="blk-search-0001", quote="herons")

    assert result.resolution == "unmatched"
    assert result.reason == "block_missing"
    assert "not portable" in " ".join(result.notes)


def test_an_expired_lease_says_to_read_the_source_again():
    material_id = _material(expires_at=time.time() - 1)

    result = _check(material_id=material_id, block_id="blk-body-0001", quote="herons")

    assert result.resolution == "unmatched"
    assert result.reason == "material_expired"
    assert "envoy_read" in " ".join(result.notes)


def test_a_well_formed_id_with_no_stored_material_is_reported_missing():
    state_storage.reset_root_cache()

    result = _check(material_id="mat-" + "f" * 24, block_id="blk-body-0001", quote="herons")

    assert result.resolution == "unmatched"
    assert result.reason == "material_missing"
    assert "envoy_read" in " ".join(result.notes)


def test_a_scoped_miss_says_the_rest_of_the_material_was_not_checked():
    material_id = _material()
    start, end = _slice_of("herons")
    block_id = "blk-search-%d-%d-0123456789abcdef" % (start, end)

    result = _check(material_id=material_id, block_id=block_id, quote="beavers")

    assert result.reason == "quote_missing"
    assert "not its whole text" in " ".join(result.notes)


def test_a_whole_text_miss_does_not_claim_the_check_was_partial():
    material_id = _material()

    result = _check(material_id=material_id, block_id="blk-body-0001", quote="wombats")

    assert result.reason == "quote_missing"
    assert "not its whole text" not in " ".join(result.notes)

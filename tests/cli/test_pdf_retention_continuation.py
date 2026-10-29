import json
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
for path in (
    ROOT_DIR / "packages" / "envoy-core" / "src",
    ROOT_DIR / "packages" / "envoy-cli" / "src",
):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def test_reuse_reports_the_offset_of_the_first_page_it_could_not_return():
    from envoy_cli import reader, storage
    from envoy_core.schemas.read_v1 import ReadRequest

    material_id = "mat-" + "b" * 24
    page_one = "1" * 200
    page_two = "2" * 200
    stored = {
        "source_kind": "pdf",
        "source_url": reader._redact_url("https://example.org/report.pdf"),
        "page_spans": [
            {"page_number": 1, "start": 0, "end": len(page_one)},
            {"page_number": 2, "start": len(page_one), "end": len(page_one) + len(page_two)},
        ],
        "page_coverage": {"page_count": 2},
        "page_count": 2,
        "text": page_one + page_two,
        "source_revision": "rev-1",
        "session_id": "session-a",
        "expires_at": time.time() + 600,
    }
    root = storage.resolve_material_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{material_id}.json").write_text(json.dumps(stored), encoding="utf-8")
    (root / f"{material_id}.expires").write_text(str(time.time() + 600), encoding="ascii")

    request = ReadRequest.model_validate({
        "protocol_version": "envoy.read/1",
        "request_id": "r-1",
        "session_id": "session-a",
        "source": {"url": "https://example.org/report.pdf"},
        "goal": "the appendix",
        "budget": {"max_response_chars": 300, "max_total_response_chars": 300},
    })
    response = reader._reuse_pdf_material(request, "https://example.org/report.pdf")

    assert response is not None
    details = response.coverage.modalities["text"].details
    pages = [block.locator.page_number for block in response.blocks if block.locator is not None]
    assert pages == [1]
    assert details["continuation_available"] is True
    assert details["next_offset"] == len(page_one), "the caller needs the offset expand starts from"


def test_one_expand_hands_over_a_window_of_pages_instead_of_a_single_page():
    from envoy_cli import reader, storage
    from envoy_core.schemas.read_v1 import ExpandRequest

    material_id = "mat-" + "c" * 24
    pages = [str(index % 10) * 200 for index in range(20)]
    spans = []
    offset = 0
    for number, body in enumerate(pages, start=1):
        spans.append({"page_number": number, "start": offset, "end": offset + len(body)})
        offset += len(body)
    stored = {
        "source_kind": "pdf",
        "source_url": reader._redact_url("https://example.org/report.pdf"),
        "page_spans": spans,
        "page_coverage": {"page_count": len(pages)},
        "page_count": len(pages),
        "text": "".join(pages),
        "source_revision": "rev-2",
        "session_id": "session-b",
        "expires_at": time.time() + 600,
        "budget_state": {
            "max_total_response_chars": 48_000,
            "max_total_response_tokens": None,
            "max_expansion_rounds": 3,
            "returned_chars": 0,
            "returned_tokens": 0,
            "expansion_rounds": 0,
            "returned_ranges": [],
        },
    }
    root = storage.resolve_material_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{material_id}.json").write_text(json.dumps(stored), encoding="utf-8")
    (root / f"{material_id}.expires").write_text(str(time.time() + 600), encoding="ascii")

    response = reader.expand_material(ExpandRequest(
        protocol_version="envoy.read/1",
        request_id="r-2",
        material_id=material_id,
        session_id="session-b",
        offset=0,
        max_chars=4000,
    ))

    numbers = [block.locator.page_number for block in response.blocks]
    assert numbers == list(range(1, 21)), "a window that fits must be handed over in one answer"
    details = response.coverage.modalities["text"].details
    assert details["pages_returned"] == 20
    assert details["continuation_available"] is False
    assert details["next_offset"] is None

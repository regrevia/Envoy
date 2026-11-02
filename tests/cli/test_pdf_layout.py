"""Unit tests for :mod:`envoy_cli.pdf_layout` and its synthetic PDF fixtures.

The fixtures live in ``tests/fixtures/pdf`` and are regenerated with
``tests/fixtures/pdf/make_pdf_fixtures.py``. The two-column / three-column /
table-grid / mixed fixtures carry a real text layer; ``scanned-text.pdf`` is
raster only, so these tests never need OCR or tesseract.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[2]
for package_src in (
    ROOT_DIR / "packages/envoy-core/src",
    ROOT_DIR / "packages/envoy-cli/src",
):
    if str(package_src) not in sys.path:
        sys.path.insert(0, str(package_src))

pypdf = pytest.importorskip("pypdf")

from envoy_cli.pdf_layout import analyse_runs  # noqa: E402

FIXTURE_DIR = ROOT_DIR / "tests/fixtures/pdf"
CONTRACT_KEYS = {
    "columns",
    "lines",
    "lines_reordered",
    "table_like_rows",
    "layout_score",
    "ordered_text",
    "header_footer_lines",
    "notes",
}


def tokens(text: str) -> Counter:
    return Counter(text.split())


def load_pages(name: str) -> list:
    return list(pypdf.PdfReader(str(FIXTURE_DIR / name)).pages)


def page_runs(page) -> list[dict]:
    """Collect positioned runs exactly the way the reader does."""

    collected: list[dict] = []

    def visitor(text, cm, tm, font_dict, font_size):
        collapsed = " ".join(str(text).split())
        if not collapsed or tm is None or len(tm) < 6:
            return
        collected.append(
            {
                "x": float(tm[4]),
                "y": float(tm[5]),
                "text": collapsed,
                "size": float(font_size) if font_size else None,
            }
        )

    page.extract_text(visitor_text=visitor)
    return collected


def analyse_page(page) -> dict:
    runs = page_runs(page)
    return analyse_runs(runs, page_width=float(page.mediabox.width))


def column_lines(prefix: str, count: int, x: float, y_top: float, step: float = 24.0) -> list[dict]:
    return [
        {"x": x, "y": y_top - index * step, "text": f"{prefix}{index + 1:02d}", "size": 9.0}
        for index in range(count)
    ]


def test_contract_keys_and_empty_page() -> None:
    result = analyse_runs([])
    assert set(result) == CONTRACT_KEYS
    assert result["columns"] == 0
    assert result["lines"] == 0
    assert result["lines_reordered"] is False
    assert result["table_like_rows"] == 0
    assert result["ordered_text"] == ""
    assert result["header_footer_lines"] == 0
    assert result["notes"], "an empty page must say why nothing was reordered"


@pytest.mark.parametrize("page_index", [0, 1])
def test_two_column_pdf_reading_order(page_index: int) -> None:
    page = load_pages("two-column.pdf")[page_index]
    runs = page_runs(page)
    assert tokens(" ".join(run["text"] for run in runs))  # sanity: text layer exists

    result = analyse_page(page)

    assert result["columns"] == 2
    assert result["lines_reordered"] is True
    assert result["layout_score"] >= 0.6
    assert result["table_like_rows"] == 0
    # Bag of words is conserved, so no token was renamed or dropped.
    assert tokens(result["ordered_text"]) == tokens(
        " ".join(run["text"] for run in runs)
    )
    ordered = result["ordered_text"].split()
    # The left column is read to completion before the right column starts.
    assert ordered.index("L12") < ordered.index("R01")
    assert ordered.index("L01") < ordered.index("L12")
    assert ordered.index("R01") < ordered.index("R12")


def test_two_column_stream_order_is_not_reading_order() -> None:
    page = load_pages("two-column.pdf")[0]
    runs = page_runs(page)
    stream = [run["text"] for run in runs]
    # The writer emitted the whole left column before the right one.
    assert stream.index("L01") < stream.index("L12") < stream.index("R01")

    ordered = analyse_page(page)["ordered_text"].split()
    assert ordered.index("R01") > ordered.index("L12")


def test_three_column_pdf_reading_order() -> None:
    page = load_pages("three-column.pdf")[0]
    runs = page_runs(page)
    result = analyse_page(page)

    assert result["columns"] == 3
    assert result["lines_reordered"] is True
    assert tokens(result["ordered_text"]) == tokens(
        " ".join(run["text"] for run in runs)
    )
    ordered = result["ordered_text"].split()
    assert ordered.index("C08") < ordered.index("C09")
    assert ordered.index("C16") < ordered.index("C17")


def test_table_grid_pdf_reports_signal_without_reordering() -> None:
    page = load_pages("table-grid.pdf")[0]
    runs = page_runs(page)
    result = analyse_page(page)

    assert result["table_like_rows"] == 3
    assert result["lines_reordered"] is False
    assert result["columns"] == 4  # the grid geometry is still reported
    assert tokens(result["ordered_text"]) == tokens(
        " ".join(run["text"] for run in runs)
    )
    assert any("table-like" in note for note in result["notes"])


def test_scanned_pdf_has_no_text_layer() -> None:
    for page in load_pages("scanned-text.pdf"):
        assert page.extract_text() in ("", None)
        result = analyse_page(page)
        assert result["columns"] == 0
        assert result["lines"] == 0
        assert result["lines_reordered"] is False
        assert result["header_footer_lines"] == 0


def test_mixed_pdf_text_page_and_scan_page() -> None:
    text_page, scan_page = load_pages("mixed.pdf")

    text_result = analyse_page(text_page)
    assert text_result["columns"] == 1
    assert text_result["lines"] == 7
    assert text_result["lines_reordered"] is False
    assert any("single column" in note for note in text_result["notes"])
    assert "MIX01" in text_result["ordered_text"].split()

    scan_result = analyse_page(scan_page)
    assert scan_result["lines_reordered"] is False
    assert scan_result["lines"] == 0


def test_single_column_stays_in_stream_order() -> None:
    lines = column_lines("S", 5, x=72.0, y_top=700.0)
    runs = lines + [{"x": 72.0, "y": 300.0, "text": "tail", "size": 9.0}]
    result = analyse_runs(runs, page_width=595.0)

    assert result["columns"] == 1
    assert result["lines_reordered"] is False
    assert result["lines"] == 6
    assert result["ordered_text"].split() == [run["text"] for run in runs]
    assert any("single column" in note for note in result["notes"])


def test_two_column_tokens_conserved_with_shuffled_stream() -> None:
    left = column_lines("L", 6, x=60.0, y_top=720.0)
    right = column_lines("R", 6, x=320.0, y_top=720.0)
    scrambled = left + right
    scrambled.sort(key=lambda run: run["text"], reverse=True)  # arbitrary stream order

    result = analyse_runs(scrambled, page_width=595.0)
    assert result["columns"] == 2
    assert result["lines_reordered"] is True
    assert tokens(result["ordered_text"]) == tokens(
        " ".join(run["text"] for run in scrambled)
    )


def test_three_column_synthetic_order() -> None:
    filler = "column body text sample for layout"
    columns = []
    for column_index, x in enumerate((60.0, 250.0, 440.0)):
        for row in range(4):
            columns.append(
                {
                    "x": x,
                    "y": 700.0 - row * 26.0,
                    "text": "C%02d %s" % (column_index * 4 + row + 1, filler),
                    "size": 9.0,
                }
            )
    runs = columns[:]
    runs.reverse()  # content stream order is irrelevant to the geometry

    result = analyse_runs(runs, page_width=595.0)
    assert result["columns"] == 3
    assert result["lines_reordered"] is True
    ordered = result["ordered_text"].split()
    assert ordered.index("C04") < ordered.index("C05")
    assert ordered.index("C08") < ordered.index("C09")
    assert tokens(result["ordered_text"]) == tokens(
        " ".join(run["text"] for run in runs)
    )


def test_low_confidence_gap_keeps_stream_order() -> None:
    # The two starts are more than the 8% threshold apart, but the split is
    # only ~0.56 confident, below the 0.60 bar, so nothing is reordered.
    runs = column_lines("A", 3, x=60.0, y_top=700.0) + column_lines(
        "B", 3, x=150.0, y_top=700.0
    )
    result = analyse_runs(runs, page_width=1000.0)

    assert result["columns"] == 1
    assert result["lines_reordered"] is False
    assert result["layout_score"] < 0.6
    assert any("confidence" in note for note in result["notes"])
    assert result["ordered_text"].split() == [run["text"] for run in runs]


def test_header_line_does_not_become_a_column() -> None:
    body = column_lines("L", 4, x=60.0, y_top=700.0) + column_lines(
        "R", 4, x=320.0, y_top=700.0
    )
    header = [{"x": 200.0, "y": 780.0, "text": "centred title", "size": 12.0}]
    runs = header + body

    result = analyse_runs(runs, page_width=595.0)
    # The centred title aligns with no column, so the page is not reordered.
    assert result["columns"] == 1
    assert result["lines_reordered"] is False
    assert any("align" in note for note in result["notes"])


def test_y_tolerance_merges_nearby_baselines() -> None:
    runs = [
        {"x": 60.0, "y": 700.0, "text": "alpha", "size": 10.0},
        {"x": 90.0, "y": 700.4, "text": "beta", "size": 10.0},
        {"x": 60.0, "y": 680.0, "text": "gamma", "size": 10.0},
    ]
    result = analyse_runs(runs, page_width=595.0)

    assert result["lines"] == 2
    assert result["ordered_text"].split() == ["alpha", "beta", "gamma"]


def test_synthetic_table_signal() -> None:
    runs = []
    for row, y in enumerate((700.0, 680.0, 660.0), start=1):
        for column, x in enumerate((60.0, 200.0, 340.0, 480.0), start=1):
            runs.append({"x": x, "y": y, "text": "T%d%d" % (row, column), "size": 10.0})

    result = analyse_runs(runs, page_width=612.0)
    assert result["table_like_rows"] == 3
    assert result["lines_reordered"] is False
    assert result["ordered_text"].split() == [run["text"] for run in runs]


def test_analysis_is_deterministic_and_does_not_mutate_input() -> None:
    page = load_pages("two-column.pdf")[0]
    runs = page_runs(page)
    snapshot = [dict(run) for run in runs]

    first = analyse_page(page)
    second = analyse_page(page)

    assert first == second
    assert runs == snapshot


def test_missing_size_and_odd_input_are_tolerated() -> None:
    runs = [
        {"x": 60.0, "y": 700.0, "text": "one"},
        {"x": 60.0, "y": 676.0, "text": "two", "size": None},
        {"x": 60.0, "y": 652.0, "text": "   ", "size": 9.0},
    ]
    result = analyse_runs(runs)
    assert result["lines"] == 2
    assert result["lines_reordered"] is False
    assert result["ordered_text"].split() == ["one", "two"]

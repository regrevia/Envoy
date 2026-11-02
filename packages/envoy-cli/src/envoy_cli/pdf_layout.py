"""Pure geometry helpers that recover the reading order of a PDF text page.

``pypdf``'s ``page.extract_text(visitor_text=callback)`` reports every
text-showing operation with its text matrix: ``tm[4]`` / ``tm[5]`` are the
user-space x / y of the baseline and ``font_size`` is the effective size.
That stream order is the order the file was written in, not the order a person
reads: two- and three-column pages, tables and side bars come out interleaved
or column-major.

This module turns the runs of one page into a small layout verdict and, when
the geometry is unambiguous, into reading-order text. It is intentionally
conservative: when the column split is not clear the original stream order is
kept and ``notes`` says why. A wrong reorder is worse than no reorder.

Constraints (see the frozen contract, section 3):

* ``analyse_runs`` is a pure function - no IO, no clock, no randomness, so the
  same input always returns the same verdict.
* Tokens are never invented or dropped: ``ordered_text.split()`` is a
  permutation of the tokens of the input runs.
* Table pages only report a signal (``table_like_rows``); their text is not
  rebuilt into cells or reordered.
* Page headers and footers that repeat across pages are counted by the reader
  across pages; ``header_footer_lines`` is therefore always 0 here.

Geometry summary:

* lines are clustered by baseline ``y`` with a tolerance of half the median
  font size;
* a line is split into segments where the gap between run starts exceeds four
  times the median character width (a space is far smaller);
* columns are clustered from segment start ``x`` values, splitting where the
  gap exceeds 8% of the page width (or four character widths when the width is
  unknown);
* a candidate column must be used by at least two lines, and every segment on
  the page must align with a candidate column start, otherwise the page is
  treated as a single column.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

# Character width is estimated from the median font size; Helvetica averages
# roughly 0.5 em per glyph, which is close enough for gap clustering.
_CHAR_WIDTH_PER_POINT = 0.5
_DEFAULT_FONT_SIZE = 10.0
_COLUMN_WIDTH_FRACTION = 0.08
_CONFIDENT_LAYOUT_SCORE = 0.6
# A table row is a run of >= 3 segments whose cell text is short; a wrapped
# prose line in a column is much longer than a table cell.
_TABLE_MAX_CELL_CHARS = 24
_TABLE_MIN_ROWS = 3


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _normalise(runs: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop empty runs and collapse whitespace without changing the tokens."""

    cleaned: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        text = " ".join(str(run.get("text") or "").split())
        if not text:
            continue
        try:
            x = float(run.get("x"))
            y = float(run.get("y"))
        except (TypeError, ValueError):
            continue
        size = run.get("size")
        try:
            size_value = float(size) if size is not None else None
        except (TypeError, ValueError):
            size_value = None
        if size_value is not None and size_value <= 0:
            size_value = None
        cleaned.append({"x": x, "y": y, "text": text, "size": size_value})
    return cleaned


def _cluster_lines(runs: list[dict[str, Any]], tolerance: float) -> list[list[dict[str, Any]]]:
    """Cluster runs into visual lines, top to bottom, each line left to right."""

    ordered = sorted(runs, key=lambda run: (-run["y"], run["x"]))
    lines: list[dict[str, Any]] = []
    for run in ordered:
        if lines and abs(lines[-1]["y"] - run["y"]) <= tolerance:
            line = lines[-1]
            line["y"] = (line["y"] * line["_count"] + run["y"]) / (line["_count"] + 1)
            line["_count"] += 1
            line["runs"].append(run)
        else:
            lines.append({"y": run["y"], "_count": 1, "runs": [run]})
    for line in lines:
        line["runs"].sort(key=lambda run: run["x"])
    return lines


def _segments(line: list[dict[str, Any]], gap: float) -> list[list[dict[str, Any]]]:
    """Split one visual line where the horizontal gap looks like a column gap."""

    segments: list[list[dict[str, Any]]] = []
    for run in line:
        if segments and run["x"] - segments[-1][-1]["x"] <= gap:
            segments[-1].append(run)
        else:
            segments.append([run])
    return segments


def _cluster_starts(starts: Sequence[float], gap: float) -> list[list[float]]:
    clusters: list[list[float]] = []
    for x in sorted(starts):
        if clusters and x - clusters[-1][-1] <= gap:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return clusters


def _table_like_rows(
    lines: list[dict[str, Any]],
    char_width: float,
) -> int:
    """Count rows that belong to a run of >= 3 geometrically stable short cells."""

    tolerance = max(2.0 * char_width, 6.0)
    rows = 0
    streak = 0
    previous: tuple[float, ...] | None = None
    for line in lines:
        segments = line["segments"]
        cell_texts = [" ".join(run["text"] for run in segment) for segment in segments]
        median_chars = _median([float(len(text)) for text in cell_texts])
        stable = (
            len(segments) >= 3
            and median_chars <= _TABLE_MAX_CELL_CHARS
            and previous is not None
            and len(previous) == len(segments)
            and all(
                abs(start - prior) <= tolerance
                for start, prior in zip(
                    (segment[0]["x"] for segment in segments), previous
                )
            )
        )
        if stable:
            streak += 1
        elif len(segments) >= 3 and median_chars <= _TABLE_MAX_CELL_CHARS:
            streak = 1
        else:
            if streak >= _TABLE_MIN_ROWS:
                rows += streak
            streak = 0
        previous = (
            tuple(segment[0]["x"] for segment in segments)
            if len(segments) >= 3 and median_chars <= _TABLE_MAX_CELL_CHARS
            else None
        )
    if streak >= _TABLE_MIN_ROWS:
        rows += streak
    return rows


def _reorder(
    lines: list[dict[str, Any]],
    column_starts: Sequence[float],
) -> str:
    """Column-major reading order: columns left to right, lines top to bottom."""

    buckets: list[list[str]] = [[] for _ in column_starts]
    for line in lines:
        row: list[list[str]] = [[] for _ in column_starts]
        for segment in line["segments"]:
            run_text = " ".join(run["text"] for run in segment)
            column = min(
                range(len(column_starts)),
                key=lambda index: abs(column_starts[index] - segment[0]["x"]),
            )
            row[column].append(run_text)
        for index, texts in enumerate(row):
            if texts:
                buckets[index].append(" ".join(texts))
    columns = ["\n".join(bucket) for bucket in buckets if bucket]
    return "\n\n".join(columns)


def _stream_text(runs: list[dict[str, Any]]) -> str:
    return " ".join(run["text"] for run in runs)


def analyse_runs(
    runs: list[dict[str, Any]],
    *,
    page_width: float | None = None,
) -> dict[str, Any]:
    """Recover the reading order of one page from its positioned text runs.

    ``runs`` holds ``{"x", "y", "text", "size"}`` entries in content-stream
    order. Returns ``columns``, ``lines``, ``lines_reordered``,
    ``table_like_rows``, ``layout_score``, ``ordered_text``,
    ``header_footer_lines`` and ``notes``; ``ordered_text`` always keeps the
    input token multiset.
    """

    cleaned = _normalise(runs)
    if not cleaned:
        return {
            "columns": 0,
            "lines": 0,
            "lines_reordered": False,
            "table_like_rows": 0,
            "layout_score": 0.0,
            "ordered_text": "",
            "header_footer_lines": 0,
            "notes": ["no text runs on the page"],
        }

    sizes = [run["size"] for run in cleaned if run["size"] is not None]
    median_size = _median(sizes) if sizes else _DEFAULT_FONT_SIZE
    char_width = max(median_size * _CHAR_WIDTH_PER_POINT, 0.5)

    line_tolerance = median_size * 0.5
    line_gap = max(4.0 * char_width, 10.0)
    lines = _cluster_lines(cleaned, line_tolerance)
    for line in lines:
        line["segments"] = _segments(line["runs"], line_gap)

    if page_width is not None and page_width > 0:
        page_width_value = float(page_width)
    else:
        widest = max(run["x"] for run in cleaned)
        page_width_value = max(widest, 1.0) + 2.0 * char_width
    column_gap = max(page_width_value * _COLUMN_WIDTH_FRACTION, 2.0 * char_width)
    align_tolerance = max(2.0 * char_width, 8.0)

    segment_starts = [segment[0]["x"] for line in lines for segment in line["segments"]]
    raw_columns = _cluster_starts(segment_starts, column_gap)
    # A real column has at least two lines starting on it; a lone centred
    # title or a page number does not make a column.
    used_lines: list[set[int]] = [set() for _ in raw_columns]
    for line_index, line in enumerate(lines):
        for segment in line["segments"]:
            for cluster_index, cluster in enumerate(raw_columns):
                if abs(segment[0]["x"] - cluster[-1]) <= align_tolerance:
                    used_lines[cluster_index].add(line_index)
                    break
    column_starts = [
        cluster[0]
        for cluster, lines_used in zip(raw_columns, used_lines)
        if len(lines_used) >= 2
    ]
    column_starts.sort()

    layout_score = 0.0
    notes: list[str] = []
    if len(column_starts) < 2:
        deviation = max(
            (abs(start - column_starts[0]) for start in segment_starts),
            default=0.0,
        ) if column_starts else 0.0
        columns = 1
        layout_score = 1.0 if deviation <= align_tolerance else 0.5
        notes.append(
            "single column of text; stream order already reads top to bottom"
        )
    else:
        gaps = [
            right - left for left, right in zip(column_starts, column_starts[1:])
        ]
        layout_score = min(1.0, min(gaps) / (2.0 * column_gap))
        columns = len(column_starts)
        misaligned = any(
            min(abs(start - candidate) for candidate in column_starts)
            > align_tolerance
            for start in segment_starts
        )
        if misaligned:
            columns = 1
            layout_score = 0.0
            notes.append(
                "some lines do not align with the detected columns; "
                "reading order left unchanged"
            )
        elif layout_score < _CONFIDENT_LAYOUT_SCORE:
            columns = 1
            notes.append(
                "column split confidence %.2f is below %.2f; "
                "reading order left unchanged"
                % (layout_score, _CONFIDENT_LAYOUT_SCORE)
            )

    table_like_rows = _table_like_rows(lines, char_width)

    reorder = (
        columns >= 2
        and layout_score >= _CONFIDENT_LAYOUT_SCORE
        and table_like_rows == 0
    )
    if table_like_rows:
        notes.append(
            "table-like rows detected (%d); reading order left unchanged"
            % table_like_rows
        )
    if reorder:
        notes.append(
            "reordered %d visual lines into %d columns" % (len(lines), columns)
        )
        ordered_text = _reorder(lines, column_starts)
    else:
        ordered_text = _stream_text(cleaned)

    return {
        "columns": columns,
        "lines": len(lines),
        "lines_reordered": bool(reorder),
        "table_like_rows": table_like_rows,
        "layout_score": round(float(layout_score), 4),
        "ordered_text": ordered_text,
        "header_footer_lines": 0,
        "notes": notes,
    }

"""Regenerate the synthetic PDF fixtures used by ``tests/cli/test_pdf_layout.py``.

Every fixture is synthetic and deterministic: reportlab runs with
``invariant=1`` and the raster pages are rendered by Pillow into JPEG bytes
without timestamps, so regenerating normally produces no diff. Each file stays
well below 150 KB.

    packages/envoy-cli/.venv/bin/python tests/fixtures/pdf/make_pdf_fixtures.py

Page contents and sentinels:

- ``two-column.pdf`` - A4, 2 pages. Each page has two text columns: the left
  column starts at x=60 with sentinels ``L01``..``L12``, the right column starts
  at x=320 with sentinels ``R01``..``R12``; both columns share the same y rows
  (750, 726, ... going down by 24). A page header sits at y=800. The content
  stream writes the whole left column first, then the whole right column, so
  stream order is NOT reading order: L01..L12 must be followed by R01..R12.

- ``three-column.pdf`` - A4, 1 page. Three columns at x=60 / x=250 / x=440 with
  8 shared y rows. Sentinels ``C01``..``C08`` (left), ``C09``..``C16`` (middle),
  ``C17``..``C24`` (right). Each cell carries filler prose so the page is not
  mistaken for a short-cell grid.

- ``table-grid.pdf`` - A4, 1 page. A 4-column x 3-row grid at x=60/200/340/480
  and y=700/680/660, with short cell sentinels ``T11``..``T14`` (row 1),
  ``T21``..``T24`` (row 2), ``T31``..``T34`` (row 3).

- ``scanned-text.pdf`` - A4, 2 pages. Purely raster pages (200 dpi grayscale
  PNG, no text layer, so ``extract_text()`` returns ""), with large sentinels
  ``SCAN01``..``SCAN03`` on page 1 and ``SCAN04``..``SCAN06`` on page 2. The
  glyphs are large enough for 200 dpi OCR.

- ``mixed.pdf`` - 2 pages. Page 1 has a real text layer with sentinels
  ``MIX01``..``MIX06``; page 2 is a raster image with sentinel ``MIXP2`` only,
  simulating a scan appended to a born-digital document.
"""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
PAGE_WIDTH, PAGE_HEIGHT = A4
RASTER_DPI = 200
RASTER_SIZE = (int(PAGE_WIDTH / 72 * RASTER_DPI), int(PAGE_HEIGHT / 72 * RASTER_DPI))


def _raster_png(lines: list[str]) -> bytes:
    """Render ``lines`` as black text on white at 200 dpi, as PNG bytes."""

    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("L", RASTER_SIZE, 255)
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.load_default(size=150)
    except TypeError:  # Pillow < 10 has no sized default font.
        font = ImageFont.load_default()
    y = 200
    for line in lines:
        draw.text((140, y), line, fill=0, font=font)
        y += 320
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _draw_raster_page(sheet: canvas.Canvas, lines: list[str]) -> None:
    data = _raster_png(lines)
    sheet.drawImage(
        ImageReader(io.BytesIO(data)),
        0,
        0,
        width=PAGE_WIDTH,
        height=PAGE_HEIGHT,
    )


def build_two_column(target: Path) -> Path:
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy two-column PDF fixture")
    for page in (1, 2):
        sheet.setFont("Helvetica", 8)
        sheet.drawString(60, PAGE_HEIGHT - 40, "Envoy two-column fixture - page %d" % page)
        sheet.setFont("Helvetica", 9)
        y = PAGE_HEIGHT - 92
        for index in range(1, 13):
            sheet.drawString(60, y, "L%02d" % index)
            y -= 24
        y = PAGE_HEIGHT - 92
        for index in range(1, 13):
            sheet.drawString(320, y, "R%02d" % index)
            y -= 24
        sheet.showPage()
    sheet.save()
    return target


def build_three_column(target: Path) -> Path:
    filler = "column text sample for layout detection"
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy three-column PDF fixture")
    sheet.setFont("Helvetica", 8)
    sheet.drawString(60, PAGE_HEIGHT - 40, "Envoy three-column fixture")
    sheet.setFont("Helvetica", 9)
    y = PAGE_HEIGHT - 100
    for row in range(8):
        sheet.drawString(60, y, "C%02d %s" % (row + 1, filler))
        sheet.drawString(250, y, "C%02d %s" % (row + 9, filler))
        sheet.drawString(440, y, "C%02d %s" % (row + 17, filler))
        y -= 26
    sheet.showPage()
    sheet.save()
    return target


def build_table_grid(target: Path) -> Path:
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy table-grid PDF fixture")
    sheet.setFont("Helvetica", 8)
    sheet.drawString(60, PAGE_HEIGHT - 40, "Envoy table grid fixture")
    sheet.setFont("Helvetica", 10)
    for row, y in enumerate((700, 680, 660), start=1):
        for column, x in enumerate((60, 200, 340, 480), start=1):
            sheet.drawString(x, y, "T%d%d" % (row, column))
    sheet.showPage()
    sheet.save()
    return target


def build_scanned_text(target: Path) -> Path:
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy scanned-text PDF fixture")
    _draw_raster_page(sheet, ["SCAN01", "SCAN02", "SCAN03"])
    sheet.showPage()
    _draw_raster_page(sheet, ["SCAN04", "SCAN05", "SCAN06"])
    sheet.showPage()
    sheet.save()
    return target


def build_mixed(target: Path) -> Path:
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy mixed text and scan PDF fixture")
    sheet.setFont("Helvetica", 11)
    sheet.drawString(60, PAGE_HEIGHT - 80, "Envoy mixed fixture - born digital page")
    y = PAGE_HEIGHT - 120
    for index in range(1, 7):
        sheet.drawString(60, y, "MIX%02d text layer line" % index)
        y -= 22
    sheet.showPage()
    _draw_raster_page(sheet, ["MIXP2"])
    sheet.showPage()
    sheet.save()
    return target


BUILDERS = {
    "two-column.pdf": build_two_column,
    "three-column.pdf": build_three_column,
    "table-grid.pdf": build_table_grid,
    "scanned-text.pdf": build_scanned_text,
    "mixed.pdf": build_mixed,
}


def build_all(directory: Path = HERE) -> list[Path]:
    built = []
    for name, builder in BUILDERS.items():
        target = directory / name
        builder(target)
        built.append(target)
    return built


def digest(target: Path) -> str:
    return hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == "__main__":
    for path in build_all():
        print(
            "wrote %s (%d bytes, sha256 %s)"
            % (path.name, path.stat().st_size, digest(path))
        )

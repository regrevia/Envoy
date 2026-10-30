"""Regenerate the 120-page PDF fixture used by the Windows read acceptance.

The fixture is committed so the acceptance never depends on a third-party
document that may move or change. It is served to the real reader through the
public raw URL, because `envoy read` only accepts public HTTP(S) sources.

    python tests/fixtures/pdf/make_long_pdf.py

Requires reportlab, which is a dev-only dependency. `invariant=1` keeps the
bytes stable for a given reportlab version, so regenerating normally produces
no diff.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

PAGES = 120
LINES_PER_PAGE = 46
TARGET = Path(__file__).with_name("long-120-pages.pdf")


def build(target: Path = TARGET) -> Path:
    sheet = canvas.Canvas(str(target), pagesize=A4, invariant=1)
    sheet.setTitle("Envoy long PDF fixture (120 pages)")
    sheet.setAuthor("Envoy acceptance fixture")
    width, height = A4
    for page in range(1, PAGES + 1):
        sheet.setFont("Helvetica-Bold", 13)
        sheet.drawString(56, height - 60, "Envoy long document fixture - section %03d" % page)
        sheet.setFont("Helvetica", 8.5)
        y = height - 90
        for line in range(1, LINES_PER_PAGE + 1):
            sheet.drawString(
                56,
                y,
                (
                    "page %03d line %02d: envoy fixture text used to verify that a long PDF "
                    "is read page by page without losing its place." % (page, line)
                ),
            )
            y -= 12.5
        sheet.setFont("Helvetica", 7)
        sheet.drawString(56, 40, "fixture page %d of %d" % (page, PAGES))
        sheet.showPage()
    sheet.save()
    return target


def digest(target: Path = TARGET) -> str:
    return hashlib.sha256(target.read_bytes()).hexdigest()


if __name__ == "__main__":
    built = build()
    print("wrote %s (%d bytes, sha256 %s)" % (built, built.stat().st_size, digest(built)))

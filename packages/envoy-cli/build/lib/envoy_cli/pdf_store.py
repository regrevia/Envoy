"""Page-level checkpoint store for long PDF reads.

A 500-page PDF used to be a single all-or-nothing operation: extract every
page, then save the material. A read that ran out of time or crashed lost all
the page work and the next attempt started at page one again.

This store keeps one append-only record per processed page plus a small state
file. The rules that matter:

* a page record reaches disk (append + ``fsync``) **before** the cursor moves,
  so a crash can leave a page committed but never a cursor past missing text;
* the cursor is the contiguous committed prefix, recomputed from the records,
  so an out-of-order or duplicated record cannot skip a page;
* every page carries an explicit status from :data:`PAGE_STATUSES`, so a
  reader can tell "text was extracted" from "needs OCR" from "never processed".

Nothing here writes outside the material root the caller passes in.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

PARSER_VERSION = "pdf-page-store/1"
PAGE_STATUSES = (
    "text",
    "ocr_candidate",
    "blank",
    "parse_error",
    "not_processed",
)
_COMMITTED_STATUSES = frozenset({"text", "ocr_candidate", "blank", "parse_error"})


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def source_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PdfPageStore:
    """Crash-safe per-page records for one PDF source digest."""

    def __init__(self, root: str | Path, digest: str):
        self.root = Path(root)
        self.digest = digest
        self.directory = self.root / "pdf-pages" / digest
        self.records_path = self.directory / "pages.jsonl"
        self.state_path = self.directory / "state.json"

    # -- reading ---------------------------------------------------------
    @property
    def exists(self) -> bool:
        return self.records_path.is_file()

    def committed(self) -> dict[int, dict]:
        """Last record wins per page; a truncated final line is ignored."""

        pages: dict[int, dict] = {}
        try:
            with self.records_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    try:
                        page_number = int(record["page_number"])
                    except (KeyError, TypeError, ValueError):
                        continue
                    status = str(record.get("status") or "")
                    if status not in PAGE_STATUSES:
                        continue
                    pages[page_number] = record
        except OSError:
            return {}
        return pages

    def state(self) -> dict:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def contiguous_cursor(self, pages: dict[int, dict] | None = None) -> int:
        pages = self.committed() if pages is None else pages
        cursor = 0
        while (cursor + 1) in pages:
            cursor += 1
        return cursor

    # -- writing ---------------------------------------------------------
    def commit_page(
        self,
        page_number: int,
        *,
        status: str,
        text: str = "",
        extraction_mode: str = "text",
        page_count: int | None = None,
        parser_version: str = PARSER_VERSION,
        material_id: str | None = None,
    ) -> bool:
        """Append one page record, then advance the cursor.

        Returns ``False`` when the record could not be written; callers must
        treat that page as uncommitted rather than assume progress.
        """

        if status not in _COMMITTED_STATUSES:
            raise ValueError(f"commit_page needs a completed status, got {status!r}")
        record = {
            "page_number": int(page_number),
            "status": status,
            "extraction_mode": extraction_mode,
            "chars": len(text),
            "text": text,
            "parser_version": parser_version,
            "source_digest": self.digest,
            "written_at": time.time(),
        }
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with self.records_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            return False
        pages = self.committed()
        if int(page_number) not in pages:
            return False
        self._write_state(pages, page_count=page_count, material_id=material_id)
        return True

    def mark_not_processed(
        self,
        page_numbers: list[int],
        *,
        page_count: int | None = None,
        material_id: str | None = None,
        reason: str = "work budget exhausted",
    ) -> None:
        pages = self.committed()
        state = self.state()
        state.update({
            "source_digest": self.digest,
            "parser_version": PARSER_VERSION,
            "page_count": page_count if page_count is not None else state.get("page_count"),
            "material_id": material_id or state.get("material_id"),
            "cursor": self.contiguous_cursor(pages),
            "unprocessed_pages": sorted({int(item) for item in page_numbers}),
            "not_processed_reason": reason,
            "updated_at": time.time(),
        })
        _atomic_write_json(self.state_path, state)

    def _write_state(
        self,
        pages: dict[int, dict],
        *,
        page_count: int | None,
        material_id: str | None,
    ) -> None:
        state = self.state()
        state.update({
            "source_digest": self.digest,
            "parser_version": PARSER_VERSION,
            "page_count": page_count if page_count is not None else state.get("page_count"),
            "material_id": material_id or state.get("material_id"),
            "cursor": self.contiguous_cursor(pages),
            "committed_pages": len(pages),
            "unprocessed_pages": [],
            "updated_at": time.time(),
        })
        _atomic_write_json(self.state_path, state)


def committed_text_pages(store: PdfPageStore, limit: int | None = None) -> list[tuple[int, str, str]]:
    """(page_number, status, text) for committed pages, in page order."""

    pages = store.committed()
    numbers = sorted(pages)
    if limit is not None:
        numbers = numbers[:limit]
    return [
        (number, str(pages[number].get("status") or ""), str(pages[number].get("text") or ""))
        for number in numbers
    ]

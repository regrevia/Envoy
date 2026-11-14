"""Export one retained material as an Evidence Pack.

The pack is derived, never authoritative: the material record stays where the
reader wrote it, and deleting a pack changes nothing about a later ``expand``,
``search`` or ``replay``. Every citation in the pack resolves through the same
block view the reader already uses for citation checks, so an exported quote is
the quote the material holds.

Determinism is a feature, not a nicety: the same material and the same
revision produce the same pack id and the same bytes, which is what lets a
caller tell "I exported twice" from "the source changed under me". No wall
clock, pid or absolute path goes into the manifest.

JSON schemas written here (see ``docs/architecture/evidence-pack-v2.md``):
``envoy.evidence-pack/2`` for ``manifest.json``,
``envoy.source-document/2`` for ``source/document.json``,
``envoy.source-metadata/2`` for ``source/metadata.json`` and
``envoy.evidence-index/2`` for ``references/evidence-index.json``.

Version 2 adds what version 1 left implicit: ``manifest.json`` names every file
in the pack and carries a digest for each file that cannot contain the pack id
itself, so ``verify`` can report an added or removed file instead of only a
changed one; and every evidence block says where its text came from
(``origin``, ``extractor``, ``extractor_version``) instead of leaving a
converter to infer it. Version 1 packs are still read and verified.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from . import storage

PACK_SCHEMA = "envoy.evidence-pack/2"
DOCUMENT_SCHEMA = "envoy.source-document/2"
COVERAGE_SCHEMA = "envoy.evidence-coverage/2"
METADATA_SCHEMA = "envoy.source-metadata/2"
INDEX_SCHEMA = "envoy.evidence-index/2"
V1_PACK_SCHEMA = "envoy.evidence-pack/1"
# Where the text of a block came from. `source` is text the source carried;
# the rest is model-produced and must never be shown as original wording.
ORIGINS = ("source", "asr", "ocr", "vision", "derived")

MATERIAL_ID = re.compile(r"^mat-[a-f0-9]{24}$")
KIND_DIRS = {
    "web": "text",
    "pdf_page": "text",
    "office_location": "text",
    "video_time": "transcript",
    "audio_time": "transcript",
    "image_region": "visual",
}
WHOLE_BLOCK_IDS = frozenset(
    {"blk-body-0001", "blk-office-0001", "blk-image-0001", "blk-video-transcript", "blk-video-0001"}
)
MAX_EVIDENCE_CHARS = 200_000
MAX_TOTAL_EVIDENCE_CHARS = 2_000_000
# A section is a place in the source, not a guess: only spans the material
# actually stored become sections. A flat text with no spans yields one
# whole-document section, and `structure/sections.json` says so.
VIDEO_SECTION_SECONDS = 120.0


class PackError(RuntimeError):
    """A pack cannot be built; the message is meant to reach the user."""

    def __init__(self, code: str, message: str, **details) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def material_root() -> Path:
    return storage.resolve_material_root()


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _digest(payload) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_text(path: Path, text: str) -> None:
    """Write UTF-8 exactly as built, without newline translation.

    The manifest stores the digest of the text as it existed in memory, so the
    file has to hold those same bytes everywhere. Windows text mode rewrites
    ``\n`` to ``\r\n``, which makes a freshly exported pack fail its own
    manifest on that platform.
    """

    path.write_text(text, encoding="utf-8", newline="")


def load_material(material_id: str, *, root: Path | None = None) -> dict:
    """Return a material only while its lease is still live."""

    if not isinstance(material_id, str) or not MATERIAL_ID.fullmatch(material_id):
        raise PackError("invalid_material_id", f"not a material id: {material_id!r}")
    base = root or material_root()
    path = base / f"{material_id}.json"
    lease = base / f"{material_id}.expires"
    if path.is_symlink() or lease.is_symlink() or not path.is_file():
        raise PackError("material_missing", f"material {material_id} is not on disk")
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
        expires_at = float(lease.read_text(encoding="ascii").strip())
    except (OSError, ValueError, TypeError) as exc:
        raise PackError("material_unreadable", f"material {material_id} cannot be read: {exc}") from exc
    if not isinstance(stored, dict):
        raise PackError("material_unreadable", f"material {material_id} is not an object")
    if expires_at <= time.time():
        raise PackError(
            "material_expired",
            f"material {material_id} was retained only until its lease ran out; read the source again",
        )
    return stored


def _evidence_blocks(stored: dict) -> dict[str, dict]:
    # Imported lazily so an extension can never sit on the import path of a read.
    from .reader import _material_evidence_blocks

    return _material_evidence_blocks(stored)


def _source_title(stored: dict) -> str:
    title = str(stored.get("title_text") or "").strip()
    if title:
        return title
    return str(stored.get("source_url") or "").strip() or "Untitled source"


def _source_type(stored: dict) -> str:
    kind = str(stored.get("source_kind") or "").strip()
    return kind or "web"


def _locator_kind(locator: dict | None) -> str:
    if not isinstance(locator, dict):
        return "web"
    kind = str(locator.get("type") or "").strip()
    return kind if kind in KIND_DIRS else "web"


def _modality(locator: dict | None) -> str:
    """The kind of evidence this is, named the way its directory is named."""

    return KIND_DIRS.get(_locator_kind(locator), "text")


def _evidence_id(material_id: str, block_id: str, order: int) -> str:
    identity = f"{material_id}\0{block_id}\0{order}"
    return "ev-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _section_id(material_id: str, key: str) -> str:
    return "sec-" + hashlib.sha256(f"{material_id}\0{key}".encode("utf-8")).hexdigest()[:12]


def _page_of(locator: dict | None) -> int | None:
    if isinstance(locator, dict) and isinstance(locator.get("page_number"), int):
        return locator["page_number"]
    return None


def _seconds(locator: dict | None) -> tuple[float, float] | None:
    if not isinstance(locator, dict):
        return None
    start, end = locator.get("start_seconds"), locator.get("end_seconds")
    if isinstance(start, (int, float)):
        return (float(start), float(end) if isinstance(end, (int, float)) else float(start))
    if isinstance(locator.get("seconds"), (int, float)):
        return (float(locator["seconds"]), float(locator["seconds"]))
    return None


def _pdf_page_records(stored: dict) -> dict[int, dict]:
    """The per-page record the reader kept, keyed by page number."""

    evidence = stored.get("page_evidence")
    if not isinstance(evidence, dict):
        return {}
    pages = evidence.get("pages")
    if not isinstance(pages, list):
        return {}
    records: dict[int, dict] = {}
    for page in pages:
        if isinstance(page, dict) and isinstance(page.get("page_number"), int):
            records[page["page_number"]] = page
    return records


SOURCE_PROVENANCE = {"origin": "source", "extractor": None, "extractor_version": None}


def _ocr_provenance(record: dict) -> dict | None:
    ocr = record.get("ocr")
    if not isinstance(ocr, dict):
        return None
    version = ocr.get("engine_version")
    return {
        "origin": "ocr",
        "extractor": str(ocr.get("backend") or "ocr"),
        "extractor_version": str(version) if version else None,
    }


def _is_ocr_page(record: dict) -> bool:
    if str(record.get("extraction_mode") or "") == "ocr":
        return True
    return str(record.get("status") or "") in {"ocr", "ocr_failed"}


def _image_ocr_provenance(stored: dict) -> dict | None:
    spans = stored.get("ocr_spans")
    if not isinstance(spans, list) or not spans:
        return None
    for span in spans:
        if isinstance(span, dict) and str(span.get("engine") or "").strip():
            return {"origin": "ocr", "extractor": str(span["engine"]).strip(), "extractor_version": None}
    return {"origin": "ocr", "extractor": "ocr", "extractor_version": None}


def _provenance(
    block_id: str,
    locator: dict,
    stored: dict,
    pages: dict[int, dict],
) -> dict:
    """Say which text a model produced, without guessing from the locator.

    A locator records where text was found, not who wrote it: a scanned page and
    a page with a text layer cite the same page number. A converter that has to
    mark model output as inferred needs the answer here, not a heuristic.
    """

    if block_id == "blk-title":
        return dict(SOURCE_PROVENANCE)
    source_kind = str(stored.get("source_kind") or "")
    if source_kind == "pdf":
        page = _page_of(locator)
        record = pages.get(page) if isinstance(page, int) else None
        if isinstance(record, dict) and _is_ocr_page(record):
            return _ocr_provenance(record) or {
                "origin": "ocr",
                "extractor": "ocr",
                "extractor_version": None,
            }
        return dict(SOURCE_PROVENANCE)
    if source_kind == "video":
        if str(stored.get("transcript_source") or "") == "asr":
            return {"origin": "asr", "extractor": "asr", "extractor_version": None}
        return dict(SOURCE_PROVENANCE)
    if source_kind == "image":
        ocr = _image_ocr_provenance(stored)
        if ocr is not None:
            return ocr
        if stored.get("visual_resources"):
            # The store keeps the observation but not the model that made it.
            return {"origin": "vision", "extractor": None, "extractor_version": None}
        return dict(SOURCE_PROVENANCE)
    return dict(SOURCE_PROVENANCE)


def _sections(material_id: str, stored: dict, blocks: list[dict]) -> list[dict]:
    """Sections come only from spans the material stored; otherwise one section."""

    sections: list[dict] = []
    page_spans = stored.get("page_spans") if isinstance(stored.get("page_spans"), list) else []
    if page_spans:
        for span in page_spans:
            if not isinstance(span, dict):
                continue
            page = span.get("page_number")
            if not isinstance(page, int) or page < 1:
                continue
            evidence_ids = [
                block["evidence_id"] for block in blocks if _page_of(block["locator"]) == page
            ]
            sections.append(
                {
                    "section_id": _section_id(material_id, f"page-{page}"),
                    "title": f"Page {page}",
                    "start": int(span.get("start") or 0),
                    "end": int(span.get("end") or 0),
                    "locator": {"type": "pdf_page", "page_number": page},
                    "evidence_ids": evidence_ids,
                }
            )
    if not sections:
        timed = [(block, _seconds(block["locator"])) for block in blocks]
        timed = [(block, span) for block, span in timed if span is not None]
        if timed:
            window_start = None
            window: list[dict] = []
            for block, span in timed:
                if window_start is None or span[0] - window_start > VIDEO_SECTION_SECONDS:
                    if window:
                        sections.append(
                            _timed_section(material_id, window_start, window)
                        )
                    window_start = span[0]
                    window = []
                window.append(block)
            if window:
                sections.append(_timed_section(material_id, window_start, window))
    if not sections:
        text = str(stored.get("text") or "")
        sections.append(
            {
                "section_id": _section_id(material_id, "document"),
                "title": _source_title(stored),
                "start": 0,
                "end": len(text),
                "locator": {"type": "web", "source_url": str(stored.get("source_url") or "")},
                "evidence_ids": [block["evidence_id"] for block in blocks],
            }
        )
    return sections


def _timed_section(material_id: str, window_start: float, window: list[dict]) -> dict:
    end = window_start
    for block in window:
        span = _seconds(block["locator"])
        if span is not None and span[1] > end:
            end = span[1]
    if end <= window_start:
        end = window_start + VIDEO_SECTION_SECONDS
    return {
        "section_id": _section_id(material_id, f"time-{int(window_start)}"),
        "title": f"{_clock(window_start)}-{_clock(end)}",
        "start": 0,
        "end": 0,
        "locator": {"type": "video_time", "start_seconds": window_start, "end_seconds": end},
        "evidence_ids": [block["evidence_id"] for block in window],
    }


def _clock(seconds: float) -> str:
    whole = max(0, int(seconds))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:d}:{secs:02d}"


def build_source_document(stored: dict, material_id: str) -> dict:
    """Build the document, the evidence files and the index in one pass.

    A citation is only worth exporting when it names a place in the source. The
    reader synthesises whole-document blocks so that a ``-range-`` id has a base;
    those repeat the entire text and are dropped once finer blocks exist. Blocks
    with no locator at all (a title is not a place) are counted, not exported.
    """

    records = _evidence_blocks(stored)
    pages = _pdf_page_records(stored)
    fine_grained = any(block_id not in WHOLE_BLOCK_IDS for block_id in records)
    blocks: list[dict] = []
    seen: set[str] = set()
    unpinned: list[str] = []
    ordered = sorted(records.items(), key=lambda item: (item[1].get("order", 0), item[0]))
    for index, (block_id, record) in enumerate(ordered):
        text = str(record.get("text") or "")
        if not text.strip():
            continue
        if fine_grained and block_id in WHOLE_BLOCK_IDS:
            continue
        locator = record.get("locator") if isinstance(record.get("locator"), dict) else None
        if locator is None:
            unpinned.append(block_id)
            continue
        order = int(record.get("order") or index)
        evidence_id = _evidence_id(material_id, block_id, order)
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        provenance = _provenance(block_id, locator, stored, pages)
        blocks.append(
            {
                "evidence_id": evidence_id,
                "block_id": block_id,
                "text": text,
                "order": order,
                "locator": locator,
                "kind": _modality(locator),
                "locator_kind": _locator_kind(locator),
                "origin": provenance["origin"],
                "extractor": provenance["extractor"],
                "extractor_version": provenance["extractor_version"],
            }
        )

    coverage = stored.get("page_coverage") if isinstance(stored.get("page_coverage"), dict) else {}
    document = {
        "schema_version": DOCUMENT_SCHEMA,
        "source": {
            "source_id": f"src-{material_id.removeprefix('mat-')}",
            "material_id": material_id,
            "type": _source_type(stored),
            "title": _source_title(stored),
            "url": str(stored.get("source_url") or ""),
            "revision": str(stored.get("source_revision") or ""),
        },
        "sections": _sections(material_id, stored, blocks),
        "coverage": {
            "text": bool(str(stored.get("text") or "").strip()),
            "transcript": any(block["kind"] == "transcript" for block in blocks),
            "ocr": any(block["origin"] == "ocr" for block in blocks),
            "visual": bool(stored.get("visual_resources")),
            "page_count": coverage.get("page_count"),
            "indexed_page_count": coverage.get("indexed_page_count"),
            "unpinned_blocks": unpinned,
        },
    }
    index = {block["evidence_id"]: block for block in blocks}
    return {"document": document, "blocks": blocks, "index": index}


def _pack_id_input(manifest: dict, document: dict, evidence: list[dict]) -> dict:
    """What the pack id covers: the manifest header, the document, the index.

    Neither ``pack_id`` nor ``inventory`` is an input. The inventory has to name
    the digest of ``source/metadata.json``, and that file carries the pack id, so
    including it would be circular. The inventory is therefore at the same level
    of trust as ``manifest.json`` itself: both detect damage, neither resists a
    deliberate rewrite of the pack.
    """

    header = {key: value for key, value in manifest.items() if key not in {"pack_id", "inventory"}}
    return {"manifest": header, "document": document, "evidence": evidence}


def build_pack(material_id: str, *, root: Path | None = None) -> dict:
    """Everything a writer needs: manifest fields plus the text to write."""

    stored = load_material(material_id, root=root)
    built = build_source_document(stored, material_id)
    document = built["document"]
    blocks = built["blocks"]

    written: dict[str, str] = {}
    total = 0
    truncated: list[str] = []
    for block in blocks:
        text = block["text"]
        if total + len(text) > MAX_TOTAL_EVIDENCE_CHARS:
            remaining = max(0, MAX_TOTAL_EVIDENCE_CHARS - total)
            text = text[:remaining]
            truncated.append(block["evidence_id"])
        if len(text) > MAX_EVIDENCE_CHARS:
            text = text[:MAX_EVIDENCE_CHARS]
            if block["evidence_id"] not in truncated:
                truncated.append(block["evidence_id"])
        body = text if text.endswith("\n") else text + "\n"
        written[block["evidence_id"]] = body
        total += len(text)

    evidence_index = []
    for block in blocks:
        entry = {
            "evidence_id": block["evidence_id"],
            "block_id": block["block_id"],
            "kind": block["kind"],
            "file": f"evidence/{block['kind']}/{block['evidence_id']}.txt",
            "locator": block["locator"],
            "sha256": hashlib.sha256(written[block["evidence_id"]].encode("utf-8")).hexdigest(),
            # The quote is the exported text itself; a second copy would let the
            # two disagree. Locator + file digest is the citation contract.
            "chars": len(written[block["evidence_id"]].rstrip("\n")),
            "origin": block["origin"],
            "extractor": block["extractor"],
            "extractor_version": block["extractor_version"],
        }
        evidence_index.append(entry)

    gaps = [str(item) for item in (stored.get("source_coverage_unresolved") or []) if str(item).strip()]
    if truncated:
        gaps.append(
            f"Evidence export was capped; {len(truncated)} block(s) carry only their leading text"
        )
    if stored.get("delivery_gap"):
        gaps.append("The material holds a delivery gap: some of the source text was never returned")

    coverage = {
        "schema_version": COVERAGE_SCHEMA,
        "material_id": material_id,
        "source_type": _source_type(stored),
        "unresolved": gaps,
        "truncated_evidence_ids": truncated,
        "evidence_bytes": sum(len(body.encode("utf-8")) for body in written.values()),
    }

    outline = _outline(document)
    # The pack id is fixed before the inventory exists, because the inventory
    # has to name the digest of every file and one of them carries the pack id.
    manifest = {
        "schema_version": PACK_SCHEMA,
        "material_id": material_id,
        "source_revision": document["source"]["revision"],
        "source_type": document["source"]["type"],
        "source_title": document["source"]["title"],
        "source_url": document["source"]["url"],
        "counts": {
            "sections": len(document["sections"]),
            "evidence": len(evidence_index),
        },
        "files": {
            "manifest": "manifest.json",
            "readme": "README.md",
            "document": "source/document.json",
            "metadata": "source/metadata.json",
            "sections": "structure/sections.json",
            "outline": "structure/outline.md",
            "evidence_index": "references/evidence-index.json",
            "coverage": "coverage.json",
        },
        "coverage_unresolved": gaps,
    }
    origins = sorted({block["origin"] for block in blocks})
    metadata = {
        "schema_version": METADATA_SCHEMA,
        "material_id": material_id,
        "source_revision": document["source"]["revision"],
        "source_type": document["source"]["type"],
        "source_title": document["source"]["title"],
        "source_url": document["source"]["url"],
        "session_id": stored.get("session_id"),
        "material_expires_at": stored.get("expires_at"),
        # Which parts of the text a model produced. A converter must be able
        # to say "inferred" without guessing from the locator alone.
        "transcript_source": stored.get("transcript_source"),
        "ocr_applied": "ocr" in origins,
        "origins": origins,
        "pack_id": "",
    }
    manifest["pack_id"] = _digest(
        _pack_id_input(manifest, document, evidence_index)
    )
    metadata["pack_id"] = manifest["pack_id"]
    texts = {
        "README.md": _readme(manifest, metadata),
        "coverage.json": _canonical(coverage),
        "references/evidence-index.json": _canonical(evidence_index),
        "source/document.json": _canonical(document),
        "source/metadata.json": _canonical(metadata),
        "structure/outline.md": outline,
        "structure/sections.json": _canonical(document["sections"]),
    }
    for entry in evidence_index:
        texts[entry["file"]] = written[entry["evidence_id"]]
    manifest["inventory"] = [
        {"path": path, "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(), "bytes": len(body.encode("utf-8"))}
        for path, body in sorted(texts.items())
    ]
    return {
        "manifest": manifest,
        "document": document,
        "sections": document["sections"],
        "evidence": evidence_index,
        "evidence_text": written,
        "coverage": coverage,
        "outline": outline,
        "metadata": metadata,
        "files": texts,
    }


def _outline(document: dict) -> str:
    lines = [f"# {document['source']['title']}", ""]
    lines.append(f"Source: {document['source']['url']}")
    lines.append("")
    for section in document["sections"]:
        locator = section.get("locator") or {}
        suffix = ""
        if locator.get("type") == "pdf_page":
            suffix = f" (p.{locator.get('page_number')})"
        elif locator.get("type") in {"video_time", "audio_time"}:
            suffix = f" ({_clock(float(locator.get('start_seconds') or 0))})"
        lines.append(f"- {section['title']}{suffix} {section['section_id']}")
    lines.append("")
    return "\n".join(lines)


def write_pack(material_id: str, out_dir: str | Path, *, root: Path | None = None, overwrite: bool = False) -> dict:
    """Write the pack atomically; a failure leaves the material and any old pack alone."""

    built = build_pack(material_id, root=root)
    target = Path(out_dir).expanduser()
    if target.exists() and any(target.iterdir()):
        if not overwrite:
            raise PackError(
                "pack_exists",
                f"{target} already holds files; pass --overwrite to replace it",
            )
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(parent)))
    try:
        _emit(temporary, built)
        if target.exists():
            if any(target.iterdir()) and overwrite:
                shutil.rmtree(target)
            else:
                target.rmdir()
        os.replace(temporary, target)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {"pack_dir": str(target), **built["manifest"]}


def _emit(root: Path, built: dict) -> None:
    (root / "source").mkdir(parents=True)
    (root / "structure").mkdir()
    (root / "references").mkdir()
    for kind_dir in sorted(set(KIND_DIRS.values())):
        (root / "evidence" / kind_dir).mkdir(parents=True)
    for entry in built["evidence"]:
        _write_text(root / entry["file"], built["evidence_text"][entry["evidence_id"]])
    for path, body in built["files"].items():
        if path == "manifest.json":
            continue
        _write_text(root / path, body)
    # The manifest names every other file with a digest, so it is written last.
    _write_text(root / "manifest.json", _canonical(built["manifest"]))


def _readme(manifest: dict, metadata: dict) -> str:
    return "\n".join(
        [
            f"# Evidence Pack: {manifest['source_title']}",
            "",
            f"- material: {manifest['material_id']}",
            f"- revision: {manifest['source_revision']}",
            f"- pack id: {manifest['pack_id']}",
            f"- origins: {', '.join(metadata['origins']) or 'source'}",
            "",
            "This directory is derived from a retained material. It is not the",
            "source of truth: the material record keeps that role, and deleting",
            "this pack does not change what a read already returned.",
            "",
            "Entries:",
            "",
            "- `manifest.json` lists every other file with its digest.",
            "- `source/document.json` is the Source Document contract.",
            "- `source/metadata.json` records the session, the expiry and the pack id.",
            "- `structure/` holds sections and a human-readable outline.",
            "- `evidence/` holds the exact text of every citation, one file each.",
            "- `references/evidence-index.json` maps evidence ids to locators and files.",
            "- `coverage.json` says what the material does not cover.",
            "",
            "Every evidence entry names the origin of its text (source, asr, ocr,",
            "vision or derived) so that text a model produced is never presented",
            "as original source text.",
            "",
        ]
    )


def _inventory_of(root: Path, inventory: list, files: dict) -> list[str]:
    """Every file the pack holds that the manifest does not name."""

    named = {str(value) for value in files.values() if isinstance(value, str)}
    named |= {"manifest.json"}
    for item in inventory:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            named.add(item["path"])
    extra: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in named:
            extra.append(relative)
    return extra


def verify_pack(pack_dir: str | Path) -> dict:
    """Recompute the pack id and every digest; report what does not match."""

    root = Path(pack_dir).expanduser()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise PackError("pack_unreadable", f"{root} has no manifest.json")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        document = json.loads((root / "source" / "document.json").read_text(encoding="utf-8"))
        evidence = json.loads((root / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PackError("pack_unreadable", f"{root} is not a readable pack: {exc}") from exc
    problems: list[str] = []
    recomputed = _digest(_pack_id_input(manifest, document, evidence))
    if recomputed != manifest.get("pack_id"):
        problems.append("pack id does not match the files it names")
    for entry in evidence:
        path = root / str(entry.get("file") or "")
        if not path.is_file():
            problems.append(f"missing evidence file: {entry.get('file')}")
            continue
        if _file_digest(path) != entry.get("sha256"):
            problems.append(f"evidence text changed: {entry.get('file')}")
    inventory = manifest.get("inventory")
    files = manifest.get("files")
    if isinstance(inventory, list):
        # v2 names every file, so a file that appeared or disappeared is
        # damage too, not just a file whose bytes changed. Evidence files are
        # skipped here because the loop above names them more precisely.
        cited = {str(entry.get("file") or "") for entry in evidence}
        for item in inventory:
            if not isinstance(item, dict):
                continue
            relative = str(item.get("path") or "")
            if not relative or relative.startswith("/") or ".." in Path(relative).parts:
                problems.append(f"manifest names an unusable path: {relative or '(empty)'}")
                continue
            if relative in cited:
                continue
            path = root / relative
            if not path.is_file():
                problems.append(f"missing file: {relative}")
                continue
            if _file_digest(path) != item.get("sha256"):
                problems.append(f"file changed: {relative}")
        if isinstance(files, dict):
            for relative in _inventory_of(root, inventory, files):
                problems.append(f"unexpected file: {relative}")
    return {"pack_dir": str(root), "pack_id": manifest.get("pack_id"), "valid": not problems, "problems": problems}


def render_summary(result: dict) -> str:
    if "pack_dir" in result and "manifest" not in result:
        return f"{result['pack_id']} {result['pack_dir']}"
    return "\n".join(
        [
            f"pack {result['pack_id']}  {result.get('pack_dir', '')}",
            f"  material {result['material_id']} ({result['source_type']})",
            f"  sections {result['counts']['sections']}, evidence {result['counts']['evidence']}",
            f"  unresolved: {len(result['coverage_unresolved'])}",
        ]
    )

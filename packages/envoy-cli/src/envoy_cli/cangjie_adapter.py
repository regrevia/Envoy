"""Convert an Evidence Pack into another tool's material format.

An adapter is a pure function of a *verified* pack. It never reads the material
record, never fetches, never calls a model and never edits the pack, so a broken
adapter cannot damage evidence or a read. This module holds the interface and one
example (Cangjie); other integrations follow the same two functions and stay out
of the core read path.

The corpus adapter contract:

- ``convert(pack_dir)`` returns a JSON-serialisable dict, or raises ``AdapterError``.
- ``write(result, out_path)`` writes that dict atomically and returns the path.
- A conversion carries ``pack_id``, every locator unchanged, and labels text a
  model produced (ASR transcript, OCR) as inferred rather than original.
- Nothing is distilled here. Selecting the claims worth keeping is the caller's
  job, and it is the one step that costs model calls.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import evidence_pack

SCHEMA = "envoy.cangjie-material/1"
ADAPTER_NAME = "cangjie"

# Locator kinds whose text a model produced. Original captions and page text keep
# ``source``; this set only decides what may be claimed as verbatim source text.
_INFERRED_KINDS = {"image_region"}
_TIMED_KINDS = {"video_time", "audio_time"}


class AdapterError(RuntimeError):
    """A pack cannot be converted; nothing was written."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical(payload) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AdapterError("pack_unreadable", f"{path} cannot be read: {exc}") from exc
    if not isinstance(payload, dict):
        raise AdapterError("pack_unreadable", f"{path} does not hold a JSON object")
    return payload


def _read_index(pack_dir: Path) -> list[dict]:
    payload = json.loads((pack_dir / "references" / "evidence-index.json").read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise AdapterError("pack_unreadable", "evidence-index.json must be a list")
    return [entry for entry in payload if isinstance(entry, dict)]


def _locator_kind(entry: dict) -> str:
    locator = entry.get("locator")
    return str(locator.get("type") or "") if isinstance(locator, dict) else ""


def _origin(entry: dict, metadata: dict) -> str:
    kind = _locator_kind(entry)
    if kind in _TIMED_KINDS and str(metadata.get("transcript_source") or "") == "asr":
        return "inferred"
    if kind in _INFERRED_KINDS and metadata.get("ocr_applied"):
        return "inferred"
    return "source"


def _coverage(pack: dict) -> dict:
    return {
        "unresolved": list(pack.get("unresolved") or []),
        "truncated_evidence_ids": list(pack.get("truncated_evidence_ids") or []),
    }


def to_cangjie_material(pack_dir: str | Path) -> dict:
    """Turn one verified pack into a Cangjie-shaped material document.

    Deliberately flat: segments in reading order, each with its original locator
    and origin, plus the coverage gap. Cangjie may slice this differently; the
    text and the places are what must survive.
    """

    root = Path(pack_dir).expanduser()
    report = evidence_pack.verify_pack(root)
    if not report["valid"]:
        raise AdapterError(
            "pack_invalid",
            "the pack does not match its own manifest; convert a fresh export instead",
        )
    manifest = _read_json(root / "manifest.json")
    document = _read_json(root / "source" / "document.json")
    metadata = _read_json(root / "source" / "metadata.json")
    coverage = _read_json(root / "coverage.json")

    sections = {section.get("section_id"): section for section in document.get("sections") or []}
    section_ids = [section.get("section_id") for section in document.get("sections") or []]
    rank = {section_id: index for index, section_id in enumerate(section_ids)}

    segments: list[dict] = []
    for entry in _read_index(root):
        evidence_id = str(entry.get("evidence_id") or "")
        relative = str(entry.get("file") or "")
        if not evidence_id or not relative:
            raise AdapterError("pack_unreadable", "an evidence entry is missing its id or file")
        try:
            raw = (root / relative).read_text(encoding="utf-8")
        except OSError as exc:
            raise AdapterError("pack_unreadable", f"{relative} cannot be read: {exc}") from exc
        # Evidence files end with one newline for readable diffs; ``chars`` is the
        # length of the quote itself, so the segment carries the quote, not the file
        # formatting. The digest still covers the file as written.
        text = raw[: int(entry.get("chars") or len(raw))] if entry.get("chars") else raw.rstrip("\n")
        section_id = _owning_section(entry, sections)
        segments.append(
            {
                "segment_id": evidence_id,
                "section_id": section_id,
                "order": (rank.get(section_id, len(rank)), int(entry.get("order") or 0)),
                "text": text,
                "locator": entry.get("locator"),
                "locator_kind": _locator_kind(entry) or None,
                "origin": _origin(entry, metadata),
                "evidence_id": evidence_id,
                "sha256": entry.get("sha256"),
            }
        )
    segments.sort(key=lambda segment: (segment["order"][0], segment["order"][1], segment["segment_id"]))
    for segment in segments:
        del segment["order"]

    return {
        "schema_version": SCHEMA,
        "pack_id": manifest.get("pack_id"),
        "source": {
            **{key: document.get("source", {}).get(key) for key in ("source_id", "type", "title", "url", "revision")},
            "material_id": document.get("source", {}).get("material_id"),
        },
        "segments": segments,
        "coverage": _coverage(coverage),
        "adapter": {
            "name": ADAPTER_NAME,
            "distilled": False,
            "note": "Verbatim segments only; no claims were extracted and no model was called.",
        },
    }


def _owning_section(entry: dict, sections: dict) -> str | None:
    """The first section whose evidence list names this entry."""

    evidence_id = entry.get("evidence_id")
    for section_id, section in sections.items():
        if evidence_id in (section.get("evidence_ids") or []):
            return section_id
    return None


def write(result: dict, out_path: str | Path) -> Path:
    """Write the conversion atomically; an existing file is replaced, not merged."""

    target = Path(out_path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(_canonical(result))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def render_summary(result: dict, path: str | Path) -> str:
    segments = result.get("segments") or []
    inferred = sum(1 for segment in segments if segment.get("origin") == "inferred")
    gaps = len(result.get("coverage", {}).get("unresolved") or [])
    return "\n".join(
        [
            f"Wrote {path}",
            f"  source: {result['source'].get('title')} ({result['source'].get('type')})",
            f"  segments: {len(segments)} ({inferred} inferred)",
            f"  coverage gaps: {gaps}",
            "  distilled: no (no model was called)",
        ]
    )

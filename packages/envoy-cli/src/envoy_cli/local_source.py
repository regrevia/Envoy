"""Decide what a file the caller already has actually is, before reading it.

`envoy_read` used to hand every local path to the local-video pipeline. A PDF
the user downloaded was therefore probed for a video stream and refused with
"not a readable video", even though Envoy can read its text layer perfectly
well. The file type is now decided from three signals, in order:

1. the caller's explicit ``source.kind`` (a host that already knows says so),
2. the file's magic bytes (``%PDF-``, container signatures for video),
3. the filename suffix, as a last resort for files whose head could not be read.

Classification only decides *which* reader runs. It never reads the whole
file, never uploads it, and never guesses at a capability Envoy does not have:
an unknown local type becomes a blocked response that names the boundary.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from envoy_core.schemas.read_v1 import (
    ErrorCode,
    ReadRequest,
    ReadResponse,
    SourceKind,
)

from . import reader


PDF_SIGNATURE = b"%PDF-"
PDF_SUFFIXES = (".pdf",)
TEXT_SUFFIXES = (
    ".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".log", ".rst", ".html", ".htm",
)
IMAGE_SUFFIXES = (
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif",
)
VIDEO_SUFFIXES = (
    ".mp4", ".m4v", ".mov", ".webm", ".m3u8", ".ogv", ".avi", ".mkv",
    ".mpeg", ".mpg", ".3gp", ".ts", ".flv", ".wmv",
)
AUDIO_SUFFIXES = (
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".amr", ".wma",
)
# First bytes of the container formats the local-video pipeline can probe.
_VIDEO_SIGNATURES = (
    b"ftyp",      # MP4 / M4V / MOV: size-prefixed, so match the box type below
    b"RIFF",      # AVI (and WAV, which the video probe rejects)
    b"\x1a\x45\xdf\xa3",  # Matroska / WebM
    b"\x00\x00\x01\xba",  # MPEG program stream
    b"\x00\x00\x01\xb3",  # MPEG video
    b"OggS",
    b"FLV\x01",
)

LOCAL_PDF_SCHEME = "envoy-local-pdf://"


@dataclass(frozen=True)
class LocalSource:
    """One local file, classified for the reader that can actually handle it."""

    path: Path
    kind: str  # "pdf" | "video" | "unsupported"
    detected_by: str  # "explicit_kind" | "magic" | "suffix"
    explicit_kind: str | None = None
    message: str = ""


def _head_bytes(path: Path, count: int = 16) -> bytes:
    try:
        with path.open("rb") as handle:
            return handle.read(count)
    except OSError:
        return b""


def _looks_like_video(head: bytes) -> bool:
    if head[4:8] == b"ftyp":
        return True
    return any(head.startswith(signature) for signature in _VIDEO_SIGNATURES)


def _unsupported_message(path: Path) -> str:
    suffix = path.suffix.lower() or "no extension"
    return (
        f"Envoy cannot read this local file type ({suffix}) directly. "
        "Local reading supports PDF documents and video/audio that FFmpeg can "
        "probe; other formats need a public URL or a host-side converter. "
        "Nothing was uploaded."
    )


def classify_local_source(
    path: str | Path,
    *,
    explicit_kind: str | None = None,
) -> LocalSource:
    """Classify a local file without reading beyond its first bytes."""

    source = Path(path).expanduser()
    suffix = source.suffix.lower()
    kind = (explicit_kind or SourceKind.AUTO.value).strip().lower()
    head = _head_bytes(source)

    if kind == SourceKind.DOCUMENT.value:
        if head.startswith(PDF_SIGNATURE) or suffix in PDF_SUFFIXES:
            return LocalSource(source, "pdf", "explicit_kind", explicit_kind)
        return LocalSource(
            source,
            "unsupported",
            "explicit_kind",
            explicit_kind,
            f"Envoy can only read PDF documents locally, and this file is not a PDF ({suffix or 'no extension'}). "
            "Nothing was uploaded.",
        )
    if kind == SourceKind.VIDEO.value:
        return LocalSource(source, "video", "explicit_kind", explicit_kind)
    if kind == SourceKind.AUDIO.value:
        # Local audio has no reader of its own; the video pipeline is the one
        # that runs FFprobe/Whisper, and it rejects a file with no video stream
        # with a clear message rather than pretending to transcribe it.
        return LocalSource(source, "video", "explicit_kind", explicit_kind)
    if kind == SourceKind.IMAGE.value:
        return LocalSource(
            source,
            "unsupported",
            "explicit_kind",
            explicit_kind,
            "Local image OCR is not available in this build; a local image needs a host that can "
            "render it or a public URL. Nothing was uploaded.",
        )

    # AUTO / ARTICLE / COLLECTION: inspect what the file really is.
    if head.startswith(PDF_SIGNATURE):
        return LocalSource(source, "pdf", "magic", explicit_kind)
    if _looks_like_video(head):
        return LocalSource(source, "video", "magic", explicit_kind)
    if suffix in PDF_SUFFIXES:
        # The head could not be read (empty/unreadable), or the file is a PDF
        # whose signature sits behind junk. Let the PDF reader give the real
        # parse error instead of calling it a video.
        return LocalSource(source, "pdf", "suffix", explicit_kind)
    if suffix in VIDEO_SUFFIXES or suffix in AUDIO_SUFFIXES:
        return LocalSource(source, "video", "suffix", explicit_kind)
    if suffix in IMAGE_SUFFIXES:
        return LocalSource(
            source,
            "unsupported",
            "suffix",
            explicit_kind,
            "Local image OCR is not available in this build; a local image needs a host that can "
            "render it or a public URL. Nothing was uploaded.",
        )
    return LocalSource(
        source,
        "unsupported",
        "suffix",
        explicit_kind,
        _unsupported_message(source),
    )


def local_pdf_max_bytes(configured: int) -> int:
    """Byte cap for a local PDF.

    ``max_download_bytes == 0`` means "do not download". A local file is not a
    download, so the cap stays at the PDF ceiling; it must never turn the read
    into a silent no-op.
    """
    if int(configured) > 0:
        return min(reader.MAX_PDF_BYTES, int(configured))
    return reader.MAX_PDF_BYTES


def validate_local_source_path(path: str | Path, *, max_bytes: int) -> Path:
    """Same guards the local-video reader uses, worded for any local file."""

    source = Path(path).expanduser()
    if source.is_symlink():
        raise ValueError("Local input must not be a symbolic link")
    resolved = source.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError("Local input must be a regular file")
    if resolved.stat().st_size <= 0:
        raise ValueError("Local input is empty")
    if resolved.stat().st_size > int(max_bytes):
        raise ValueError("Local input exceeds the input byte budget")
    return resolved


def _local_file_identity(path: str | Path) -> str:
    digest = hashlib.sha256()
    digest.update(str(Path(path).expanduser().resolve()).encode("utf-8", "surrogatepass"))
    try:
        stat = Path(path).expanduser().stat()
    except OSError:
        return digest.hexdigest()[:32]
    digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:32]


def local_read_request(arguments: dict, path: str, *, kind: str) -> ReadRequest:
    """Turn one `envoy_read` call with a local path into a ReadRequest."""

    allowed = set(ReadRequest.model_fields)
    converted = {key: value for key, value in arguments.items() if key in allowed}
    converted["source"] = {
        "url": Path(path).expanduser().as_uri(),
        "kind": kind,
        "label": Path(path).name,
    }
    converted.pop("material_id", None)
    if not converted.get("idempotency_key"):
        converted["idempotency_key"] = f"local-{_local_file_identity(path)}"
    if not converted.get("request_id"):
        converted["request_id"] = f"local-{_local_file_identity(path)[:16]}"
    return ReadRequest.model_validate(converted)


def read_local_pdf(request: ReadRequest, path: str | Path) -> ReadResponse:
    """Read a local PDF through the same PDF pipeline public PDFs use."""

    configured = int(request.budget.max_download_bytes)
    try:
        resolved = validate_local_source_path(path, max_bytes=local_pdf_max_bytes(configured))
    except (OSError, ValueError) as exc:
        return reader._blocked(
            request,
            ErrorCode.INVALID_REQUEST,
            str(exc) if isinstance(exc, ValueError) else "Could not access the selected local PDF",
        )

    deadline = None
    try:
        deadline = reader._pdf_deadline(request)
    except reader.MediaBudgetExceeded as exc:
        return reader._blocked(request, ErrorCode.BUDGET_EXCEEDED, str(exc))

    digest = hashlib.sha256()
    data = bytearray()
    try:
        with resolved.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                if deadline is not None and reader.time.monotonic() >= deadline:
                    return reader._blocked(
                        request,
                        ErrorCode.BUDGET_EXCEEDED,
                        "Reading the local PDF exhausted max_work_seconds",
                    )
                digest.update(chunk)
                data.extend(chunk)
    except OSError:
        return reader._blocked(
            request,
            ErrorCode.INVALID_REQUEST,
            "Could not read the selected local PDF",
        )

    source = {
        "url": (
            f"{LOCAL_PDF_SCHEME}sha256/{digest.hexdigest()}/"
            f"{quote(resolved.name, safe='')}"
        ),
        "data": bytes(data),
        "mime_type": "application/pdf",
        "input_kind": "local_file",
    }
    try:
        response = reader._parse_pdf_source(request, source, deadline=deadline)
    except reader.PublicPageError as exc:
        return reader._blocked(request, exc.code, str(exc), details=exc.details)
    details = dict((response.coverage.modalities.get("text") or reader.CoverageItem()).details)
    details.update({
        "input_kind": "local_file",
        "filename": resolved.name,
        "sha256": digest.hexdigest(),
        "downloaded_bytes": 0,
    })
    text_coverage = response.coverage.modalities.get("text") or reader.CoverageItem()
    modalities = dict(response.coverage.modalities)
    modalities["text"] = text_coverage.model_copy(update={"details": details})
    return response.model_copy(update={
        "coverage": response.coverage.model_copy(update={"modalities": modalities}),
    })

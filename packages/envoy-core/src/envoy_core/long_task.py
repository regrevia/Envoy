"""Durable checkpoints for long reading tasks.

A long task keeps three things apart on purpose:

- the material lease: short-lived, borrowed, and never persisted as identity;
- the checkpoint: durable, enough to resume in a new process or another model;
- the model session: only used to keep replayed material isolated.

The checkpoint records what was actually delivered: source revision, material
id, and the character ranges already returned. Resuming therefore never
downloads the material again and never re-delivers a range that was already
paid for. Raising the budget only asks for ranges that are still unseen.

Checkpoint v2 adds the media axis. For video and audio the character ranges say
nothing about how much of the timeline was actually transcribed, so a receipt
whose ASR stopped at 120 s of a 3600 s video is recorded through ``kind``,
``total_seconds``, ``processed_seconds``, ``completed_seconds``,
``transcription_status`` and ``continuation_start_seconds``. The checkpoint
stays PARTIAL while ``media_pending()`` is true and the resume request becomes
an ``envoy.read/1`` range read over the untranscribed tail instead of an
``ExpandRequest``. A media resume is only cheap because the local ASR work is
itself content-addressed and cached: a re-read of the same bytes with the same
model and range reuses the earlier transcription instead of paying for it
again (``packages/envoy-cli/src/envoy_cli/media.py:1491`` builds that identity,
``:1738`` loads it). ``downloaded_bytes`` and ``media_calls`` are per folded
receipt, not deduplicated: folding the same receipt twice counts it twice,
because a checkpoint cannot tell a retry from a real second delivery.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

TASK_PROTOCOL_VERSION = "envoy.task/1"
CHECKPOINT_VERSION = 2
#: v1 checkpoints predate the media fields and still load with defaults.
SUPPORTED_CHECKPOINT_VERSIONS = (1, 2)

MEDIA_KINDS = frozenset({"video", "audio"})
TEXT_KINDS = frozenset({"text", "pdf"})

CREATED = "created"
DISCOVERING = "discovering"
READING = "reading"
PARTIAL = "partial"
CHECKPOINTED = "checkpointed"
RESUMING = "resuming"
COMPLETED = "completed"
BLOCKED = "blocked"
FAILED = "failed"
CANCELLED = "cancelled"

#: A material in one of these states has nothing left to deliver.
FINISHED_STATES = frozenset({COMPLETED, FAILED, CANCELLED})
TERMINAL_TASK_STATES = frozenset({COMPLETED, BLOCKED, FAILED, CANCELLED})
OPEN_STATES = frozenset({CREATED, DISCOVERING, READING, PARTIAL, CHECKPOINTED, RESUMING})


class TaskCheckpointError(ValueError):
    """The checkpoint cannot be trusted, so the task must not resume from it."""


class TaskBlocked(RuntimeError):
    """A resume would be wrong, for example after the source revision moved."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def merge_ranges(ranges: Iterable[Sequence[int]]) -> list[list[int]]:
    """Merge overlapping or touching character ranges, sorted by start."""
    ordered = sorted((int(start), int(end)) for start, end in ranges if end > start)
    merged: list[list[int]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return merged


def coverage_of(ranges: Iterable[Sequence[int]]) -> int:
    """How many characters a set of ranges actually covers."""
    return sum(end - start for start, end in merge_ranges(ranges))


def unseen_ranges(
    start: int, end: int, delivered: Iterable[Sequence[int]]
) -> list[list[int]]:
    """The parts of ``[start, end)`` that were never delivered."""
    if end <= start:
        return []
    gaps: list[list[int]] = []
    cursor = start
    for done_start, done_end in merge_ranges(delivered):
        if done_end <= cursor:
            continue
        if done_start >= end:
            break
        if done_start > cursor:
            gaps.append([cursor, min(done_start, end)])
        cursor = max(cursor, done_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append([cursor, end])
    return gaps


def merge_seconds(ranges: Iterable[Sequence[float]]) -> list[list[float]]:
    """Merge overlapping or touching timeline ranges, sorted by start.

    The media twin of :func:`merge_ranges`. Seconds are rounded to three
    decimals here so a folded checkpoint compares and serializes stably.
    """
    ordered = sorted(
        (round(float(start), 3), round(float(end), 3))
        for start, end in ranges
        if float(end) > float(start)
    )
    merged: list[list[float]] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = round(max(merged[-1][1], end), 3)
        else:
            merged.append([start, end])
    return merged


def coverage_seconds(ranges: Iterable[Sequence[float]]) -> float:
    """How many seconds of timeline a set of ranges actually covers."""
    return round(sum(end - start for start, end in merge_seconds(ranges)), 3)


def unseen_seconds(
    start: float, end: float, completed: Iterable[Sequence[float]]
) -> list[list[float]]:
    """The parts of ``[start, end)`` that were never transcribed."""
    if end <= start:
        return []
    gaps: list[list[float]] = []
    cursor = float(start)
    for done_start, done_end in merge_seconds(completed):
        if done_end <= cursor:
            continue
        if done_start >= end:
            break
        if done_start > cursor:
            gaps.append([cursor, min(done_start, end)])
        cursor = max(cursor, done_end)
        if cursor >= end:
            break
    if cursor < end:
        gaps.append([cursor, end])
    return gaps


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _modality_details(receipt: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    coverage = receipt.get("coverage")
    if not isinstance(coverage, Mapping):
        return {}
    modalities = coverage.get("modalities")
    if not isinstance(modalities, Mapping):
        return {}
    item = modalities.get(name)
    if not isinstance(item, Mapping):
        return {}
    details = item.get("details")
    return details if isinstance(details, Mapping) else {}


def _audio_sampled_ranges(receipt: Mapping[str, Any]) -> list[tuple[float, float]]:
    """Audio ranges the reader says it actually sampled.

    Only audio counts: a sampled ``text`` or ``video`` range says nothing about
    how much speech was transcribed, which is the whole point of v2.
    """
    coverage = receipt.get("coverage")
    if not isinstance(coverage, Mapping):
        return []
    sampled = coverage.get("sampled_ranges")
    if not isinstance(sampled, list):
        return []
    ranges: list[tuple[float, float]] = []
    for item in sampled:
        if not isinstance(item, Mapping) or item.get("modality") != "audio":
            continue
        start = _as_float(item.get("start_seconds"))
        end = _as_float(item.get("end_seconds"))
        if start is None or end is None or end <= start:
            continue
        ranges.append((start, end))
    return ranges


def _text_details(receipt: Mapping[str, Any]) -> Mapping[str, Any]:
    coverage = receipt.get("coverage")
    if not isinstance(coverage, Mapping):
        return {}
    modalities = coverage.get("modalities")
    if not isinstance(modalities, Mapping):
        return {}
    text = modalities.get("text")
    if not isinstance(text, Mapping):
        return {}
    details = text.get("details")
    return details if isinstance(details, Mapping) else {}


def _page_numbers(blocks: Any) -> list[int]:
    import re

    pattern = re.compile(r"^blk-pdf-page-(\d+)(?:-range-\d+)?$")
    pages: list[int] = []
    if not isinstance(blocks, list):
        return pages
    for block in blocks:
        if not isinstance(block, Mapping):
            continue
        match = pattern.match(str(block.get("id") or ""))
        if match:
            page = int(match.group(1))
            if page not in pages:
                pages.append(page)
    return pages


# A pause, not an ending: the same material can be read again once the
# condition clears. Marking these FAILED put them in FINISHED_STATES, so resume
# answered "already_finished" and the only way forward was a brand-new task.
_RECOVERABLE_CODES = frozenset({
    "auth_required",
    "challenge_required",
    "budget_exceeded",
    "network_error",
    "rate_limited",
})


def _recoverable(codes: Sequence[str], details: Mapping[str, Any]) -> bool:
    if any(code in _RECOVERABLE_CODES for code in codes):
        return True
    retry_after = details.get("retry_after_seconds")
    return isinstance(retry_after, (int, float)) and retry_after > 0


def _error_codes(receipt: Mapping[str, Any]) -> list[str]:
    errors = receipt.get("errors")
    if not isinstance(errors, list):
        return []
    codes = []
    for error in errors:
        if isinstance(error, Mapping):
            code = error.get("code")
            codes.append(str(code) if code else "error")
        elif error:
            codes.append(str(error))
    return codes


@dataclass(frozen=True)
class MaterialCheckpoint:
    """What one material has actually delivered so far."""

    url: str
    state: str = CREATED
    material_id: str | None = None
    source_revision: str | None = None
    total_chars: int | None = None
    next_offset: int | None = None
    delivered_ranges: tuple[tuple[int, int], ...] = ()
    page_count: int | None = None
    pages_seen: tuple[int, ...] = ()
    errors: tuple[str, ...] = ()
    # --- v2 media axis -------------------------------------------------
    # Character ranges cannot describe a timeline. For video and audio these
    # fields record how much of the material was actually transcribed, so a
    # partial ASR pass is never mistaken for a finished material.
    kind: str = ""
    total_seconds: float | None = None
    processed_seconds: float | None = None
    completed_seconds: tuple[tuple[float, float], ...] = ()
    transcription_status: str | None = None
    transcript_source: str | None = None
    continuation_start_seconds: float | None = None
    unprocessed_seconds: float | None = None
    downloaded_bytes: int = 0
    media_calls: int = 0

    @property
    def delivered_chars(self) -> int:
        return coverage_of(self.delivered_ranges)

    @property
    def has_material(self) -> bool:
        return bool(self.material_id)

    def remaining_chars(self) -> int | None:
        if self.total_chars is None:
            return None
        return max(self.total_chars - self.delivered_chars, 0)

    def media_pending(self) -> bool:
        """True when a media material still has untranscribed speech.

        Text and PDF materials have no media axis, so this is always False for
        them. The three signals are deliberately independent: an explicit
        partial status, an explicit unprocessed remainder, and a numeric
        comparison of processed against total seconds. Any one of them is
        enough, because each has been observed to be the only honest field in
        some receipt.
        """
        if self.kind not in MEDIA_KINDS:
            return False
        if self.transcription_status == "partial":
            return True
        if self.unprocessed_seconds is not None and self.unprocessed_seconds > 0:
            return True
        if self.total_seconds is not None and self.processed_seconds is not None:
            return self.processed_seconds < self.total_seconds - 1.0
        return False

    def remaining_seconds(self) -> float | None:
        """Seconds of timeline still to transcribe, or None when unknown.

        ``None`` when there is no transcription evidence at all: a subtitle-only
        video has a duration and a finished transcript but no per-second
        progress, and reporting its whole duration as remaining would tell the
        caller a finished material still has an hour of ASR left.
        """
        if self.kind not in MEDIA_KINDS or self.total_seconds is None:
            return None
        if self.processed_seconds is None and not self.completed_seconds:
            return None
        covered = (
            self.processed_seconds
            if self.processed_seconds is not None
            else coverage_seconds(self.completed_seconds)
        )
        return round(max(self.total_seconds - float(covered), 0.0), 3)

    def resume_kind(self) -> str:
        """``read`` when the next step is another range read, else ``expand``."""
        return "read" if self.media_pending() else "expand"

    def media_progress(self) -> dict[str, Any] | None:
        """A reportable media summary, or None for text and PDF materials."""
        if self.kind not in MEDIA_KINDS:
            return None
        return {
            "processed_seconds": self.processed_seconds,
            "total_seconds": self.total_seconds,
            "remaining_seconds": self.remaining_seconds(),
            "completed_seconds": [list(pair) for pair in self.completed_seconds],
            "transcription_status": self.transcription_status,
            "transcript_source": self.transcript_source,
            "downloaded_bytes": self.downloaded_bytes,
            "media_calls": self.media_calls,
        }

    def is_finished(self) -> bool:
        if self.media_pending():
            return False
        if self.state in FINISHED_STATES:
            return True
        remaining = self.remaining_chars()
        return remaining == 0 and self.state in {PARTIAL, CHECKPOINTED, RESUMING}

    def with_delivery(
        self,
        receipt: Mapping[str, Any],
        *,
        offset: int,
        state: str | None = None,
    ) -> "MaterialCheckpoint":
        """Fold one real receipt into the checkpoint.

        Nothing is invented here: ranges grow only by the characters the
        receipt says it returned.
        """
        details = _text_details(receipt)
        codes = _error_codes(receipt)
        returned = _as_int(details.get("returned_chars"))
        start = max(int(offset), 0)
        ranges = list(self.delivered_ranges)
        if returned is None or returned < 0:
            ranges = list(self.delivered_ranges)
        elif returned > 0:
            ranges.append((start, start + returned))
        merged = tuple((a, b) for a, b in merge_ranges(ranges))
        next_offset = _as_int(details.get("next_offset"))
        revision = receipt.get("source_revision")
        material_id = receipt.get("material_id")
        if revision is not None and self.source_revision not in (None, revision):
            raise TaskBlocked(
                "source_revision_changed",
                "source revision moved from %s to %s" % (self.source_revision, revision),
            )
        if material_id is not None and self.material_id not in (None, material_id):
            raise TaskBlocked(
                "material_mismatch",
                "receipt belongs to %s, not %s" % (material_id, self.material_id),
            )
        pages = list(self.pages_seen)
        for page in _page_numbers(receipt.get("blocks")):
            if page not in pages:
                pages.append(page)
        result = receipt.get("result")
        if codes:
            new_state = BLOCKED if _recoverable(codes, details) else FAILED
        elif result == "complete":
            new_state = COMPLETED
        elif result == "blocked":
            new_state = BLOCKED
        else:
            new_state = state or PARTIAL
        # --- v2 media folding -----------------------------------------
        # Two receipt dialects exist in the reader: the local-video path writes
        # plain ``processed_until_seconds``, the browser-video path writes the
        # ``transcript_*`` prefixed keys. Both are read here. In the browser
        # dialect ``transcript_processed_until_seconds`` is the honest cutoff and
        # must be preferred over ``transcript_delivered_until_seconds``, which
        # only says how much of the transcript reached the caller: a
        # budget-truncated response delivered less than it processed, and
        # reading the delivered key alone makes processed too small, remaining
        # too large and the resume range start too early. Reading only one of
        # them (or only the text modality) was the bug that let a 120 s
        # transcript of a 3600 s video count as finished.
        video_details = _modality_details(receipt, "video")
        coverage = receipt.get("coverage")
        modalities = coverage.get("modalities") if isinstance(coverage, Mapping) else None
        modalities = modalities if isinstance(modalities, Mapping) else {}
        media_seen = "video" in modalities or "audio" in modalities
        duration = _as_float(video_details.get("duration_seconds"))
        if duration is None:
            duration = _as_float(details.get("duration_seconds"))
        if duration is not None:
            duration = round(duration, 3)
        audio_ranges = _audio_sampled_ranges(receipt)
        candidates = [
            _as_float(video_details.get("processed_until_seconds")),
            _as_float(video_details.get("transcript_processed_until_seconds")),
            _as_float(video_details.get("transcript_delivered_until_seconds")),
        ]
        candidates.extend(end for _, end in audio_ranges)
        candidates = [value for value in candidates if value is not None and value >= 0]
        processed = round(max(candidates), 3) if candidates else None
        if processed is not None and duration is not None:
            processed = round(min(processed, duration + 1.0), 3)
        requested_start = _as_float(video_details.get("requested_start_seconds"))
        if requested_start is None:
            requested = video_details.get("requested_range")
            if isinstance(requested, Mapping):
                requested_start = _as_float(requested.get("start_seconds"))
        if requested_start is None and audio_ranges:
            requested_start = min(pair_start for pair_start, _ in audio_ranges)
        if requested_start is None:
            requested_start = 0.0
        completed = list(self.completed_seconds)
        if processed is not None and processed > requested_start:
            completed.append((requested_start, processed))
        completed_seconds = tuple((a, b) for a, b in merge_seconds(completed))
        per_call = _as_float(video_details.get("media_downloaded_bytes"))
        if per_call is None:
            usage = receipt.get("usage")
            local_usage = usage.get("local") if isinstance(usage, Mapping) else None
            if isinstance(local_usage, Mapping):
                per_call = _as_float(local_usage.get("downloaded_bytes"))
        downloaded = self.downloaded_bytes + (
            int(per_call) if per_call is not None and per_call > 0 else 0
        )
        kind = self.kind
        if media_seen:
            kind = "video" if "video" in modalities else "audio"
        elif details:
            kind = "pdf" if _page_numbers(receipt.get("blocks")) else "text"
        transcription_status = video_details.get("transcription_status")
        transcript_source = video_details.get("transcript_source")
        for candidate in (
            "transcription_status" if not isinstance(transcription_status, str) else "",
            "transcript_source" if not isinstance(transcript_source, str) else "",
        ):
            if not candidate:
                continue
            fallback = details.get(candidate)
            if isinstance(fallback, str) and fallback:
                if candidate == "transcription_status":
                    transcription_status = fallback
                else:
                    transcript_source = fallback
        if not isinstance(transcription_status, str) or not transcription_status:
            transcription_status = self.transcription_status
        if not isinstance(transcript_source, str) or not transcript_source:
            transcript_source = self.transcript_source
        continuation = _as_float(video_details.get("transcript_continuation_start_seconds"))
        if continuation is None:
            continuation = self.continuation_start_seconds
        unprocessed = _as_float(video_details.get("transcript_unprocessed_seconds"))
        if unprocessed is None:
            unprocessed = self.unprocessed_seconds
        updated = replace(
            self,
            state=new_state,
            material_id=self.material_id or (material_id if isinstance(material_id, str) else None),
            source_revision=self.source_revision
            or (revision if isinstance(revision, str) else None),
            total_chars=_as_int(details.get("total_chars")) or self.total_chars,
            next_offset=next_offset if next_offset is not None else self.next_offset,
            delivered_ranges=merged,
            page_count=_as_int(details.get("page_count")) or self.page_count,
            pages_seen=tuple(pages),
            errors=tuple(codes),
            kind=kind,
            total_seconds=duration if duration is not None else self.total_seconds,
            processed_seconds=processed if processed is not None else self.processed_seconds,
            completed_seconds=completed_seconds,
            transcription_status=transcription_status,
            transcript_source=transcript_source,
            continuation_start_seconds=continuation,
            unprocessed_seconds=unprocessed,
            downloaded_bytes=downloaded,
            media_calls=self.media_calls + (1 if media_seen else 0),
        )
        if updated.state == COMPLETED and updated.media_pending():
            # A "complete" receipt result only means this call returned all the
            # text it was asked for. It says nothing about the timeline, so an
            # unfinished transcription downgrades the material instead of
            # claiming completion. Other states are left untouched.
            updated = replace(updated, state=PARTIAL)
        return updated

    def resume_offset(self) -> int | None:
        """Where to continue from, derived from what is still unseen."""
        if self.next_offset is not None:
            return self.next_offset
        if self.total_chars is not None:
            gaps = unseen_ranges(0, self.total_chars, self.delivered_ranges)
            if gaps:
                return gaps[0][0]
        return None

    def resume_request(
        self,
        *,
        request_id: str,
        max_chars: int = 4000,
        max_total_response_chars: int = 48000,
        observed_revision: str | None = None,
        goal: str = "",
    ) -> dict[str, Any]:
        """Build the next request, without paying for anything twice.

        ``goal`` is only read by request shapes that carry one (a read of the
        source URL); an expand request has no goal field and ignores it.
        """
        if observed_revision is not None and self.source_revision not in (
            None,
            observed_revision,
        ):
            raise TaskBlocked(
                "source_revision_changed",
                "source revision is %s, checkpoint has %s"
                % (observed_revision, self.source_revision),
            )
        if self.is_finished():
            raise TaskBlocked("already_finished", "material %s has nothing left" % self.url)
        if self.kind in MEDIA_KINDS and self.media_pending():
            # Media resume is a range read, not an expand: the reader has to be
            # pointed at the part of the timeline that was never transcribed.
            # It carries no material_id and no source_revision, because those
            # keys are not part of a read request.
            start = self.continuation_start_seconds
            if start is None:
                start = self.processed_seconds
            start = round(float(start if start is not None else 0.0), 3)
            media_request: dict[str, Any] = {
                "protocol_version": "envoy.read/1",
                "request_id": request_id,
                "source": {"url": self.url},
                "goal": goal,
                "budget": {
                    "max_response_chars": max_chars,
                    "max_total_response_chars": max_total_response_chars,
                },
                # Keeping the raw media is what lets the next call reuse the
                # frozen ASR checkpoint instead of decoding the same audio
                # again.
                "retention": {"raw_media": "persistent", "derived_ttl_seconds": 86400},
            }
            # A range scope is only honest when the total length is known: the
            # read protocol refuses an end-only or start-only scope, and it
            # refuses end_seconds <= start_seconds.
            if self.total_seconds is not None and self.total_seconds > start:
                media_request["scope"] = {
                    "kind": "range",
                    "start_seconds": start,
                    "end_seconds": round(float(self.total_seconds), 3),
                }
            return media_request
        if not self.has_material:
            request: dict[str, Any] = {
                "protocol_version": "envoy.read/1",
                "request_id": request_id,
                "source": {"url": self.url},
                "budget": {
                    "max_response_chars": max_chars,
                    "max_total_response_chars": max_total_response_chars,
                },
            }
            if goal:
                request["goal"] = goal
            return request
        offset = self.resume_offset()
        if offset is None:
            raise TaskBlocked(
                "no_resume_offset", "material %s has no delivered ranges to resume from" % self.url
            )
        # This is an expand request, so it carries no source and no revision:
        # the retained material is the source. The revision travels beside the
        # request so the caller can still compare it with what it observes.
        return {
            "protocol_version": "envoy.read/1",
            "request_id": request_id,
            "material_id": self.material_id,
            "offset": offset,
            "max_chars": max_chars,
            "max_total_response_chars": max_total_response_chars,
        }


@dataclass(frozen=True)
class LongTask:
    """A stable task identity that survives process and model boundaries."""

    task_id: str
    goal: str
    materials: tuple[MaterialCheckpoint, ...]
    state: str = CREATED
    created_at: str | None = None
    updated_at: str | None = None
    errors: tuple[str, ...] = ()
    # Receipt keys already folded in. A caller that retries an apply after a
    # timeout must not charge the same delivery twice.
    applied_receipts: tuple[str, ...] = ()

    def material(self, url: str) -> MaterialCheckpoint:
        for item in self.materials:
            if item.url == url:
                return item
        raise KeyError(url)

    def with_material(self, checkpoint: MaterialCheckpoint) -> "LongTask":
        items = tuple(
            checkpoint if item.url == checkpoint.url else item for item in self.materials
        )
        if all(item.url != checkpoint.url for item in self.materials):
            items = items + (checkpoint,)
        revised = replace(self, materials=items)
        return replace(revised, state=overall_state(revised))

    def record_error(self, message: str) -> "LongTask":
        return replace(self, errors=self.errors + (message,))

    def record_receipt(self, receipt_id: str) -> "LongTask":
        if not receipt_id or receipt_id in self.applied_receipts:
            return self
        kept = (self.applied_receipts + (receipt_id,))[-64:]
        return replace(self, applied_receipts=kept)

    def finished(self) -> bool:
        return self.state in TERMINAL_TASK_STATES or all(
            item.is_finished() for item in self.materials
        )


def overall_state(task: LongTask) -> str:
    """The task state implied by its materials, never guessed past the evidence.

    The ``state`` word of a material is not the whole truth: a video can come
    back with ``result: "partial"`` while its transcript already covers the
    entire timeline. When every material reports ``is_finished()`` the task is
    done, even if the last receipt called itself partial, so ``get`` and
    ``next`` cannot disagree. Cancellation still outranks that evidence, because
    a cancelled material is finished without being complete.
    """
    if not task.materials:
        return task.state if task.state in OPEN_STATES else CREATED
    states = [item.state for item in task.materials]
    if any(state == BLOCKED for state in states):
        return BLOCKED
    if any(state == FAILED for state in states):
        return FAILED
    if any(state == CANCELLED for state in states):
        return CANCELLED
    if task.state == CANCELLED:
        return CANCELLED
    if all(item.is_finished() for item in task.materials):
        return COMPLETED
    if all(state in FINISHED_STATES for state in states):
        return COMPLETED if all(state == COMPLETED for state in states) else CANCELLED
    if any(state in {PARTIAL, CHECKPOINTED, RESUMING, READING} for state in states):
        return PARTIAL
    return CREATED


def new_task(
    task_id: str,
    goal: str,
    urls: Sequence[str],
    *,
    now: str | None = None,
) -> LongTask:
    """Create a task whose identity does not depend on a process or a model."""
    if not task_id:
        raise TaskCheckpointError("a long task needs a stable task_id")
    if not urls:
        raise TaskCheckpointError("a long task needs at least one source")
    materials = tuple(MaterialCheckpoint(url=url) for url in urls)
    task = LongTask(task_id=task_id, goal=goal, materials=materials, created_at=now, updated_at=now)
    return replace(task, state=overall_state(task))


def to_checkpoint(task: LongTask) -> dict[str, Any]:
    """Serialize the durable part only.

    A lease, an expiry, and a model session id are deliberately absent: they
    are borrowed or isolated state, not checkpoint identity.
    """
    return {
        "checkpoint_version": CHECKPOINT_VERSION,
        "protocol_version": TASK_PROTOCOL_VERSION,
        "task_id": task.task_id,
        "goal": task.goal,
        "state": task.state,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "errors": list(task.errors),
        "applied_receipts": list(task.applied_receipts),
        "materials": [
            {
                "url": item.url,
                "state": item.state,
                "material_id": item.material_id,
                "source_revision": item.source_revision,
                "total_chars": item.total_chars,
                "next_offset": item.next_offset,
                "delivered_ranges": [list(pair) for pair in item.delivered_ranges],
                "page_count": item.page_count,
                "pages_seen": list(item.pages_seen),
                "errors": list(item.errors),
                "kind": item.kind,
                "total_seconds": item.total_seconds,
                "processed_seconds": item.processed_seconds,
                "completed_seconds": [list(pair) for pair in item.completed_seconds],
                "transcription_status": item.transcription_status,
                "transcript_source": item.transcript_source,
                "continuation_start_seconds": item.continuation_start_seconds,
                "unprocessed_seconds": item.unprocessed_seconds,
                "downloaded_bytes": item.downloaded_bytes,
                "media_calls": item.media_calls,
            }
            for item in task.materials
        ],
    }


def from_checkpoint(payload: Mapping[str, Any]) -> LongTask:
    """Restore a task, refusing a checkpoint it cannot interpret."""
    if not isinstance(payload, Mapping):
        raise TaskCheckpointError("checkpoint must be an object")
    version = payload.get("checkpoint_version")
    if version not in SUPPORTED_CHECKPOINT_VERSIONS:
        raise TaskCheckpointError(
            "unsupported checkpoint_version %r (this build reads %s and writes %d)"
            % (
                version,
                "/".join(str(item) for item in SUPPORTED_CHECKPOINT_VERSIONS),
                CHECKPOINT_VERSION,
            )
        )
    task_id = payload.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise TaskCheckpointError("checkpoint has no task_id")
    raw_materials = payload.get("materials")
    if not isinstance(raw_materials, list) or not raw_materials:
        raise TaskCheckpointError("checkpoint has no materials")
    materials = []
    for raw in raw_materials:
        if not isinstance(raw, Mapping):
            raise TaskCheckpointError("checkpoint material must be an object")
        url = raw.get("url")
        if not isinstance(url, str) or not url:
            raise TaskCheckpointError("checkpoint material has no url")
        ranges = raw.get("delivered_ranges") or []
        if not isinstance(ranges, list):
            raise TaskCheckpointError("delivered_ranges must be a list")
        parsed = []
        for pair in ranges:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or _as_int(pair[0]) is None
                or _as_int(pair[1]) is None
                or pair[1] <= pair[0]
            ):
                raise TaskCheckpointError("delivered_ranges holds an invalid range")
            parsed.append((int(pair[0]), int(pair[1])))
        seconds = raw.get("completed_seconds") or []
        if not isinstance(seconds, list):
            raise TaskCheckpointError("completed_seconds must be a list")
        completed = []
        for pair in seconds:
            if (
                not isinstance(pair, (list, tuple))
                or len(pair) != 2
                or _as_float(pair[0]) is None
                or _as_float(pair[1]) is None
                or float(pair[1]) <= float(pair[0])
            ):
                raise TaskCheckpointError("completed_seconds holds an invalid range")
            completed.append((float(pair[0]), float(pair[1])))
        downloaded = _as_int(raw.get("downloaded_bytes"))
        media_calls = _as_int(raw.get("media_calls"))
        materials.append(
            MaterialCheckpoint(
                url=url,
                state=str(raw.get("state") or CREATED),
                material_id=raw.get("material_id") if isinstance(raw.get("material_id"), str) else None,
                source_revision=raw.get("source_revision")
                if isinstance(raw.get("source_revision"), str)
                else None,
                total_chars=_as_int(raw.get("total_chars")),
                next_offset=_as_int(raw.get("next_offset")),
                delivered_ranges=tuple((a, b) for a, b in merge_ranges(parsed)),
                page_count=_as_int(raw.get("page_count")),
                pages_seen=tuple(sorted(set(int(p) for p in raw.get("pages_seen") or []))),
                errors=tuple(str(e) for e in raw.get("errors") or []),
                # v1 checkpoints have none of the media keys and load with the
                # declared defaults, which is what keeps old files resumable.
                kind=str(raw.get("kind") or ""),
                total_seconds=_as_float(raw.get("total_seconds")),
                processed_seconds=_as_float(raw.get("processed_seconds")),
                completed_seconds=tuple((a, b) for a, b in merge_seconds(completed)),
                transcription_status=raw.get("transcription_status")
                if isinstance(raw.get("transcription_status"), str)
                else None,
                transcript_source=raw.get("transcript_source")
                if isinstance(raw.get("transcript_source"), str)
                else None,
                continuation_start_seconds=_as_float(raw.get("continuation_start_seconds")),
                unprocessed_seconds=_as_float(raw.get("unprocessed_seconds")),
                downloaded_bytes=max(downloaded or 0, 0),
                media_calls=max(media_calls or 0, 0),
            )
        )
    task = LongTask(
        task_id=task_id,
        goal=str(payload.get("goal") or ""),
        materials=tuple(materials),
        state=str(payload.get("state") or CREATED),
        created_at=payload.get("created_at") if isinstance(payload.get("created_at"), str) else None,
        updated_at=payload.get("updated_at") if isinstance(payload.get("updated_at"), str) else None,
        errors=tuple(str(e) for e in payload.get("errors") or []),
        # v1 checkpoints have no receipt log and load with an empty one.
        applied_receipts=tuple(
            str(item) for item in payload.get("applied_receipts") or [] if str(item)
        ),
    )
    return replace(task, state=overall_state(task))

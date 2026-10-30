"""Durable checkpoints for long reading tasks.

A long task keeps three things apart on purpose:

- the material lease: short-lived, borrowed, and never persisted as identity;
- the checkpoint: durable, enough to resume in a new process or another model;
- the model session: only used to keep replayed material isolated.

The checkpoint records what was actually delivered: source revision, material
id, and the character ranges already returned. Resuming therefore never
downloads the material again and never re-delivers a range that was already
paid for. Raising the budget only asks for ranges that are still unseen.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence

TASK_PROTOCOL_VERSION = "envoy.task/1"
CHECKPOINT_VERSION = 1

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


def _as_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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

    def is_finished(self) -> bool:
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
            new_state = FAILED
        elif result == "complete":
            new_state = COMPLETED
        elif result == "blocked":
            new_state = BLOCKED
        else:
            new_state = state or PARTIAL
        return replace(
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
        )

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
    ) -> dict[str, Any]:
        """Build the next request, without paying for anything twice."""
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
        if not self.has_material:
            return {
                "protocol_version": "envoy.read/1",
                "request_id": request_id,
                "source": {"url": self.url},
                "budget": {
                    "max_response_chars": max_chars,
                    "max_total_response_chars": max_total_response_chars,
                },
            }
        offset = self.resume_offset()
        if offset is None:
            raise TaskBlocked(
                "no_resume_offset", "material %s has no delivered ranges to resume from" % self.url
            )
        return {
            "protocol_version": "envoy.read/1",
            "request_id": request_id,
            "material_id": self.material_id,
            "source_revision": self.source_revision,
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

    def finished(self) -> bool:
        return self.state in TERMINAL_TASK_STATES or all(
            item.is_finished() for item in self.materials
        )


def overall_state(task: LongTask) -> str:
    """The task state implied by its materials, never guessed past the evidence."""
    if not task.materials:
        return task.state if task.state in OPEN_STATES else CREATED
    states = [item.state for item in task.materials]
    if any(state == BLOCKED for state in states):
        return BLOCKED
    if any(state == FAILED for state in states):
        return FAILED
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
            }
            for item in task.materials
        ],
    }


def from_checkpoint(payload: Mapping[str, Any]) -> LongTask:
    """Restore a task, refusing a checkpoint it cannot interpret."""
    if not isinstance(payload, Mapping):
        raise TaskCheckpointError("checkpoint must be an object")
    version = payload.get("checkpoint_version")
    if version != CHECKPOINT_VERSION:
        raise TaskCheckpointError(
            "unsupported checkpoint_version %r (this build writes %d)"
            % (version, CHECKPOINT_VERSION)
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
    )
    return replace(task, state=overall_state(task))

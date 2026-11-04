"""Domain types: job state machine and transcript/translation segments.

Plain dataclasses keep the deterministic core dependency-free. Pydantic is used
at the API boundary and to validate untrusted Gemini output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from app.core.errors import ValidationError


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}

_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED},
    # RUNNING -> QUEUED is used only for crash recovery (worker restart).
    JobState.RUNNING: {JobState.WAITING_FOR_REVIEW, JobState.COMPLETED, JobState.FAILED,
                       JobState.CANCELLED, JobState.QUEUED},
    JobState.WAITING_FOR_REVIEW: {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED},
    JobState.COMPLETED: set(),
    JobState.FAILED: set(),
    JobState.CANCELLED: set(),
}


def can_transition(current: JobState, new: JobState) -> bool:
    return new in _TRANSITIONS[current]


_LANG_RE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})?$")


def validate_language_code(code: str, *, allow_auto: bool = False) -> str:
    if allow_auto and code == "auto":
        return code
    if not isinstance(code, str) or not _LANG_RE.match(code):
        raise ValidationError(f"Invalid language code: {code!r}", code="invalid_language")
    return code


def base_language(code: str) -> str:
    return code.split("-")[0].lower()


@dataclass(frozen=True)
class Segment:
    segment_id: str
    start_ms: int
    end_ms: int
    text: str
    speaker_id: str | None = None
    language: str | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValidationError("segment_id required", code="invalid_segment")
        if self.start_ms < 0 or self.end_ms <= self.start_ms:
            raise ValidationError(
                f"{self.segment_id}: need 0 <= start_ms < end_ms", code="invalid_segment")
        if not self.text or not self.text.strip():
            raise ValidationError(f"{self.segment_id}: empty text", code="invalid_segment")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValidationError(f"{self.segment_id}: confidence out of range", code="invalid_segment")


@dataclass(frozen=True)
class LocalizedSegment:
    segment_id: str
    source_text: str
    translated_text: str
    target_language: str
    start_ms: int
    end_ms: int
    speaker_id: str | None = None
    # Phase 1 has no quality agent yet, so segments are honestly "unchecked".
    quality_status: str = "unchecked"
    quality_findings: tuple = ()

    def __post_init__(self) -> None:
        if not self.translated_text or not self.translated_text.strip():
            raise ValidationError(f"{self.segment_id}: empty translation", code="invalid_segment")
        if self.end_ms <= self.start_ms or self.start_ms < 0:
            raise ValidationError(f"{self.segment_id}: invalid timing", code="invalid_segment")


def validate_segments(segments: list[Segment]) -> None:
    """Ids unique, ordered by start time, no overlaps."""
    seen: set[str] = set()
    prev_end = 0
    for s in segments:
        if s.segment_id in seen:
            raise ValidationError(f"duplicate segment_id {s.segment_id}", code="invalid_transcript")
        seen.add(s.segment_id)
        if s.start_ms < prev_end:
            raise ValidationError(
                f"{s.segment_id} overlaps or is out of order", code="invalid_transcript")
        prev_end = s.end_ms

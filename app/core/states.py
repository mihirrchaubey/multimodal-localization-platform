"""Job state machine (pure Python, no dependencies)."""
from enum import Enum

from app.core.errors import InvalidTransition


class JobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_REVIEW = "waiting_for_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ALLOWED: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}),
    # running -> queued is used only for crash recovery on startup
    JobState.RUNNING: frozenset(
        {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED,
         JobState.WAITING_FOR_REVIEW, JobState.QUEUED}
    ),
    JobState.WAITING_FOR_REVIEW: frozenset(
        {JobState.RUNNING, JobState.CANCELLED, JobState.FAILED}
    ),
    JobState.COMPLETED: frozenset(),
    JobState.FAILED: frozenset(),
    JobState.CANCELLED: frozenset(),
}
TERMINAL = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})


def can_transition(current: JobState, new: JobState) -> bool:
    return new in ALLOWED[current]


def assert_transition(current: JobState, new: JobState) -> None:
    if not can_transition(current, new):
        raise InvalidTransition(f"Cannot move job from {current.value} to {new.value}")

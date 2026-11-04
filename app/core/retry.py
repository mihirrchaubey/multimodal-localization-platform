"""Bounded retry with exponential backoff. Only retries errors flagged retryable."""
from __future__ import annotations

import time
from typing import Callable, TypeVar

T = TypeVar("T")


def with_retries(fn: Callable[[], T], *, max_attempts: int, base_delay_s: float,
                 max_delay_s: float = 30.0,
                 is_retryable: Callable[[BaseException], bool] = lambda e: bool(getattr(e, "retryable", False)),
                 on_retry: Callable[[int, BaseException], None] | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> T:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    attempt = 1
    while True:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - re-raised below when not retryable
            if attempt >= max_attempts or not is_retryable(exc):
                raise
            if on_retry:
                on_retry(attempt, exc)
            sleep(min(base_delay_s * (2 ** (attempt - 1)), max_delay_s))
            attempt += 1

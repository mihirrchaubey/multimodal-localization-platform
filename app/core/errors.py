"""Typed errors. Every failure surfaced to a user carries a stable `code`."""
from __future__ import annotations


class LocalizationError(Exception):
    code = "internal_error"
    retryable = False

    def __init__(self, message: str, *, code: str | None = None,
                 retryable: bool | None = None, details: dict | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or {}


class ValidationError(LocalizationError):
    code = "validation_error"


class UnsupportedMediaError(ValidationError):
    code = "unsupported_media"


class MediaProcessingError(LocalizationError):
    code = "media_processing_error"


class ProviderError(LocalizationError):
    """External provider (ASR/translation/TTS) failure. Retryable by default."""
    code = "provider_error"
    retryable = True


class NotFoundError(LocalizationError):
    code = "not_found"


class InvalidTransitionError(LocalizationError):
    code = "invalid_transition"


class JobCancelled(Exception):
    """Raised inside the pipeline when cancellation was requested."""

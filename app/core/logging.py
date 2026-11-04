"""Structured JSON logging with redaction of sensitive fields."""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

# Raw transcripts / translations / secrets must never reach logs by default.
REDACT_KEYS = {"text", "source_text", "translated_text", "transcript",
               "api_key", "authorization", "gemini_api_key", "prompt"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        fields = getattr(record, "fields", None)
        if fields:
            payload.update(redact(fields))
        if record.exc_info:
            payload["exc_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(payload, default=str)


def redact(fields: dict) -> dict:
    return {k: ("[REDACTED]" if k.lower() in REDACT_KEYS else v) for k, v in fields.items()}


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger("loc")
    if any(getattr(h, "_loc", False) for h in root.handlers):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    handler._loc = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"loc.{name}")


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})

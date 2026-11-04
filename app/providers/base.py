from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from app.core.models import LocalizedSegment, Segment


@dataclass(frozen=True)
class TranscriptionResult:
    language: str
    segments: list[Segment]


class ASRProvider(Protocol):
    name: str

    def transcribe(self, audio_path: Path, language: str | None) -> TranscriptionResult: ...


class Translator(Protocol):
    name: str

    def translate(self, segments: Sequence[Segment], source_language: str,
                  target_language: str) -> list[LocalizedSegment]: ...

"""ASR adapters. `mock` needs no credentials; `whisper` uses faster-whisper (optional extra)."""
from __future__ import annotations

import wave
from pathlib import Path

from app.core.errors import ProviderError
from app.core.models import Segment
from app.providers.base import TranscriptionResult

_CANNED = ["This is mock transcript line {n}.", "Mock speech recognition produced this sentence {n}.",
           "Segment {n} exists only to exercise the pipeline."]


class MockASR:
    """Deterministic fake transcript laid out over the real audio duration.
    It does NOT recognise speech; it exists so the pipeline is testable offline."""
    name = "mock"

    def __init__(self, segment_ms: int = 2500, gap_ms: int = 500, max_segments: int = 50):
        self.segment_ms, self.gap_ms, self.max_segments = segment_ms, gap_ms, max_segments

    def transcribe(self, audio_path: Path, language: str | None) -> TranscriptionResult:
        try:
            with wave.open(str(audio_path), "rb") as w:
                dur_ms = int(w.getnframes() * 1000 / w.getframerate())
        except (wave.Error, EOFError, OSError) as exc:
            raise ProviderError("Mock ASR needs a PCM WAV file", code="bad_audio", retryable=False) from exc
        lang = language or "en"
        segs, t, n = [], self.gap_ms, 1
        while t + 1000 <= dur_ms and n <= self.max_segments:
            end = min(t + self.segment_ms, dur_ms)
            segs.append(Segment(f"seg_{n:04d}", t, end, _CANNED[(n - 1) % 3].format(n=n),
                                language=lang, confidence=1.0))
            t, n = end + self.gap_ms, n + 1
        if not segs:
            raise ProviderError("Audio too short for mock transcript", code="audio_too_short",
                                retryable=False)
        return TranscriptionResult(language=lang, segments=segs)


class WhisperASR:
    """faster-whisper adapter. NOTE: written against the library's documented API but not
    executed in the build sandbox (no network/model download) — treat as untested."""
    name = "whisper"

    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "default"):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ProviderError("faster-whisper not installed (pip install '.[whisper]')",
                                code="provider_missing", retryable=False) from exc
        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def transcribe(self, audio_path: Path, language: str | None) -> TranscriptionResult:
        try:
            raw, info = self._model.transcribe(str(audio_path), language=language)
            segs = []
            for i, s in enumerate(raw, start=1):
                text = (s.text or "").strip()
                start, end = int(round(s.start * 1000)), int(round(s.end * 1000))
                if text and end > start:
                    segs.append(Segment(f"seg_{i:04d}", start, end, text, language=info.language))
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Whisper transcription failed: {type(exc).__name__}",
                                retryable=False) from exc
        if not segs:
            raise ProviderError("No speech detected", code="no_speech", retryable=False)
        return TranscriptionResult(language=info.language, segments=segs)

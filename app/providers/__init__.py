from __future__ import annotations

from app.core.config import Settings
from app.core.errors import ValidationError


def build_asr(settings: Settings):
    if settings.asr_provider == "mock":
        from app.providers.asr import MockASR
        return MockASR()
    if settings.asr_provider == "whisper":
        from app.providers.asr import WhisperASR
        return WhisperASR(settings.whisper_model)
    raise ValidationError(f"Unknown ASR provider {settings.asr_provider!r}", code="bad_config")


def build_translator(settings: Settings):
    if settings.translator_provider == "mock":
        from app.providers.translation import MockTranslator
        return MockTranslator()
    if settings.translator_provider == "gemini":
        from app.providers.gemini import GeminiTranslator
        return GeminiTranslator(settings.gemini_api_key, settings.gemini_model)
    raise ValidationError(f"Unknown translator provider {settings.translator_provider!r}", code="bad_config")

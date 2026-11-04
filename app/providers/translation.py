"""Translation adapters. Model output is untrusted: ids/texts are validated before use."""
from __future__ import annotations

from typing import Sequence

from app.core.errors import ProviderError
from app.core.models import LocalizedSegment, Segment


def build_localized(source: Sequence[Segment], translations: dict[str, str],
                    target_language: str) -> list[LocalizedSegment]:
    """Join translations to source segments by id. Any mismatch is a (retryable) model error."""
    expected = {s.segment_id for s in source}
    got = set(translations)
    if expected != got:
        raise ProviderError("Model returned mismatched segment ids", code="invalid_model_output",
                            retryable=True,
                            details={"missing": sorted(expected - got)[:5], "extra": sorted(got - expected)[:5]})
    out = []
    for s in source:
        text = translations[s.segment_id]
        if not isinstance(text, str) or not text.strip():
            raise ProviderError(f"Empty translation for {s.segment_id}", code="invalid_model_output",
                                retryable=True)
        out.append(LocalizedSegment(s.segment_id, s.text, text.strip(), target_language,
                                    s.start_ms, s.end_ms, s.speaker_id))
    return out


class MockTranslator:
    name = "mock"

    def translate(self, segments, source_language, target_language):
        return build_localized(segments, {s.segment_id: f"[{target_language}] {s.text}" for s in segments},
                               target_language)

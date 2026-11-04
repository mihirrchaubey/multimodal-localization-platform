"""SRT generation with line-length constraints. Never drops text; reports warnings."""
from __future__ import annotations

import textwrap
from typing import Sequence

from app.core.models import LocalizedSegment


def format_srt_time(ms: int) -> str:
    if ms < 0:
        raise ValueError("negative timestamp")
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, milli = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{milli:03d}"


def wrap_text(text: str, max_chars: int) -> list[str]:
    lines: list[str] = []
    for para in text.strip().splitlines():
        para = para.strip()
        if para:
            lines.extend(textwrap.wrap(para, width=max_chars, break_long_words=True,
                                       break_on_hyphens=False))
    return lines or [text.strip()]


def segments_to_srt(segments: Sequence[LocalizedSegment], *, max_chars: int = 42,
                    max_lines: int = 2) -> tuple[str, list[str]]:
    """Return (srt_text, warnings). Text is never truncated; overflow is reported."""
    blocks: list[str] = []
    warnings: list[str] = []
    prev_end = 0
    for i, seg in enumerate(segments, start=1):
        if seg.start_ms < prev_end:
            warnings.append(f"{seg.segment_id}: overlaps previous cue")
        prev_end = seg.end_ms
        # '-->' inside cue text would corrupt the cue timing line for some parsers
        clean = seg.translated_text.replace("-->", "→")
        lines = wrap_text(clean, max_chars)
        if len(lines) > max_lines:
            warnings.append(f"{seg.segment_id}: {len(lines)} lines exceeds max_lines={max_lines}")
        blocks.append(f"{i}\n{format_srt_time(seg.start_ms)} --> {format_srt_time(seg.end_ms)}\n"
                      + "\n".join(lines) + "\n")
    return "\n".join(blocks), warnings

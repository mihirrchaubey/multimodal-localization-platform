"""Deterministic media tools: upload validation, FFprobe metadata, audio extraction.

FFmpeg/FFprobe are invoked with argument lists (never a shell string), with timeouts.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.errors import MediaProcessingError, UnsupportedMediaError

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4a", ".mkv", ".webm", ".wav", ".mp3", ".ogg", ".flac"}


def sniff_format(head: bytes) -> str | None:
    """Identify a container family from magic bytes (not from the filename)."""
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:3] == b"ID3" or (len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "mp3"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"fLaC":
        return "flac"
    return None


_EXT_FAMILY = {".mp4": "mp4", ".mov": "mp4", ".m4a": "mp4", ".mkv": "matroska", ".webm": "matroska",
               ".wav": "wav", ".mp3": "mp3", ".ogg": "ogg", ".flac": "flac"}


def validate_upload_header(filename: str, head: bytes) -> str:
    """Return the safe lowercase extension, or raise UnsupportedMediaError."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UnsupportedMediaError(f"Unsupported file extension {ext or '(none)'}",
                                    code="unsupported_extension")
    family = sniff_format(head)
    if family is None:
        raise UnsupportedMediaError("File signature is not a recognised media container",
                                    code="bad_signature")
    if family != _EXT_FAMILY[ext]:
        raise UnsupportedMediaError("File signature does not match its extension",
                                    code="signature_mismatch")
    return ext


@dataclass(frozen=True)
class MediaInfo:
    format_name: str
    duration_s: float
    size_bytes: int | None
    has_video: bool
    video: dict | None
    audio_streams: list

    def to_dict(self) -> dict:
        return asdict(self)


def _rate(value: str | None) -> float | None:
    if not value or "/" not in value:
        return None
    num, _, den = value.partition("/")
    try:
        n, d = float(num), float(den)
    except ValueError:
        return None
    return round(n / d, 3) if d else None


def parse_ffprobe(data: dict) -> MediaInfo:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and s.get("disposition", {}).get("attached_pic", 0) != 1), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio:
        raise UnsupportedMediaError("Media has no audio stream", code="no_audio_stream")

    durations = []
    for raw in [fmt.get("duration")] + [s.get("duration") for s in streams]:
        try:
            if raw is not None:
                durations.append(float(raw))
        except (TypeError, ValueError):
            pass
    if not durations or max(durations) <= 0:
        raise MediaProcessingError("Could not determine media duration", code="no_duration")

    size = fmt.get("size")
    return MediaInfo(
        format_name=str(fmt.get("format_name", "unknown")),
        duration_s=round(max(durations), 3),
        size_bytes=int(size) if size is not None and str(size).isdigit() else None,
        has_video=video is not None,
        video=None if video is None else {
            "codec": video.get("codec_name"), "width": video.get("width"),
            "height": video.get("height"),
            "fps": _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")),
        },
        audio_streams=[{"codec": a.get("codec_name"), "channels": a.get("channels"),
                        "sample_rate": int(a["sample_rate"]) if str(a.get("sample_rate", "")).isdigit() else None,
                        "language": (a.get("tags") or {}).get("language")} for a in audio],
    )


def _run(cmd: list[str], timeout_s: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    except FileNotFoundError as exc:
        raise MediaProcessingError(f"{cmd[0]} not found on PATH", code="ffmpeg_missing") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaProcessingError(f"{cmd[0]} timed out after {timeout_s}s", code="ffmpeg_timeout",
                                   retryable=True) from exc


def probe(path: Path, *, ffprobe_bin: str = "ffprobe", timeout_s: int = 60) -> MediaInfo:
    cmd = [ffprobe_bin, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(Path(path).resolve())]
    res = _run(cmd, timeout_s)
    if res.returncode != 0:
        raise UnsupportedMediaError("FFprobe could not read this file as media",
                                    code="unreadable_media", details={"stderr": res.stderr[-300:]})
    try:
        return parse_ffprobe(json.loads(res.stdout))
    except json.JSONDecodeError as exc:
        raise MediaProcessingError("FFprobe returned invalid JSON", code="ffprobe_bad_output") from exc


def check_limits(info: MediaInfo, *, max_duration_s: int) -> None:
    if info.duration_s > max_duration_s:
        raise UnsupportedMediaError(
            f"Media is {info.duration_s:.0f}s; limit is {max_duration_s}s", code="too_long")


def extract_audio(src: Path, dst: Path, *, ffmpeg_bin: str = "ffmpeg", timeout_s: int = 300) -> Path:
    """Mono 16 kHz PCM WAV — fixed, repeatable settings. Written atomically."""
    src, dst = Path(src).resolve(), Path(dst).resolve()
    tmp = dst.with_name(dst.name + ".part")
    cmd = [ffmpeg_bin, "-y", "-nostdin", "-v", "error", "-i", str(src), "-vn",
           "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(tmp)]
    try:
        res = _run(cmd, timeout_s)
        if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
            raise MediaProcessingError("Audio extraction failed", code="audio_extraction_failed",
                                       details={"stderr": res.stderr[-300:]})
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst

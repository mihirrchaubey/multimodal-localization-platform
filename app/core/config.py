"""Settings loaded from environment variables (never from source)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw not in (None, "") else default


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path("./data")
    max_upload_mb: int = 200
    max_duration_s: int = 600
    max_target_languages: int = 10
    asr_provider: str = "mock"          # mock | whisper
    translator_provider: str = "mock"   # mock | gemini
    whisper_model: str = "small"
    gemini_api_key: str | None = None
    gemini_model: str = "gemini-2.5-flash"
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    subprocess_timeout_s: int = 300
    max_stage_attempts: int = 3
    retry_base_delay_s: float = 1.0
    api_key: str | None = None          # if set, API requires X-API-Key
    log_level: str = "INFO"
    subtitle_max_chars: int = 42
    subtitle_max_lines: int = 2
    run_worker_in_api: bool = True

    @property
    def db_path(self) -> Path:
        return self.data_dir / "localization.db"

    @property
    def upload_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.upload_dir, self.jobs_dir):
            d.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            data_dir=Path(e("LOC_DATA_DIR", "./data")),
            max_upload_mb=_int("LOC_MAX_UPLOAD_MB", 200),
            max_duration_s=_int("LOC_MAX_DURATION_S", 600),
            max_target_languages=_int("LOC_MAX_TARGET_LANGUAGES", 10),
            asr_provider=e("LOC_ASR_PROVIDER", "mock"),
            translator_provider=e("LOC_TRANSLATOR_PROVIDER", "mock"),
            whisper_model=e("LOC_WHISPER_MODEL", "small"),
            gemini_api_key=e("GEMINI_API_KEY") or e("GOOGLE_API_KEY") or None,
            gemini_model=e("GEMINI_MODEL", "gemini-2.5-flash"),
            ffmpeg_bin=e("FFMPEG_BIN", "ffmpeg"),
            ffprobe_bin=e("FFPROBE_BIN", "ffprobe"),
            subprocess_timeout_s=_int("LOC_SUBPROCESS_TIMEOUT_S", 300),
            max_stage_attempts=_int("LOC_MAX_STAGE_ATTEMPTS", 3),
            retry_base_delay_s=_float("LOC_RETRY_BASE_DELAY_S", 1.0),
            api_key=e("LOC_API_KEY") or None,
            log_level=e("LOC_LOG_LEVEL", "INFO"),
            subtitle_max_chars=_int("LOC_SUBTITLE_MAX_CHARS", 42),
            subtitle_max_lines=_int("LOC_SUBTITLE_MAX_LINES", 2),
            run_worker_in_api=e("LOC_RUN_WORKER_IN_API", "1") not in ("0", "false", "False"),
        )

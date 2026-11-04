import shutil
import subprocess
import tempfile
from pathlib import Path

from app.core.config import Settings

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def make_sample(path: Path, seconds: int = 10, video: bool = True) -> Path:
    """Generate a tiny synthetic clip with FFmpeg (no external media needed)."""
    if video:
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=160x120:rate=10",
               "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-c:v", "mpeg4", "-c:a", "aac",
               "-shortest", str(path)]
    else:
        cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", str(path)]
    subprocess.run(cmd, check=True)
    return path


def temp_settings(**overrides) -> tuple[Settings, tempfile.TemporaryDirectory]:
    tmp = tempfile.TemporaryDirectory()
    s = Settings(data_dir=Path(tmp.name), retry_base_delay_s=0.0, **overrides)
    s.ensure_dirs()
    return s, tmp

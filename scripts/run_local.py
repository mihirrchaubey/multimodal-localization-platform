"""Run one file through the full pipeline without the HTTP layer.

    python scripts/run_local.py sample.mp4 --targets hi,es [--source en]

Uses providers from env (LOC_ASR_PROVIDER / LOC_TRANSLATOR_PROVIDER); defaults are mock.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.db.store import JobStore  # noqa: E402
from app.providers import build_asr, build_translator  # noqa: E402
from app.tools.media import validate_upload_header  # noqa: E402
from app.workflows.pipeline import run_job  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--source", default="auto")
    ap.add_argument("--targets", required=True, help="comma-separated, e.g. hi,es")
    a = ap.parse_args()

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    src = Path(a.file).resolve()
    with open(src, "rb") as f:
        validate_upload_header(src.name, f.read(32))
    store = JobStore(settings.db_path)
    store.init()
    jid = store.create_job(source_path=str(src), original_filename=src.name, source_language=a.source,
                           target_languages=[t.strip() for t in a.targets.split(",") if t.strip()])
    store.claim_next_queued()
    state = run_job(jid, store, settings, build_asr(settings), build_translator(settings))
    job = store.get_job(jid)
    print(f"job {jid}: {state.value}")
    for s in job["stages"]:
        print(f"  {s['name']:<14}{s['target_language']:<5}{s['status']:<10}{s['duration_ms']} ms  retries={s['retries']}")
    if job["error"]:
        print("error:", job["error"])
    for art in store.list_artifacts(jid):
        print(f"  artifact {art['kind']:<14}{art['target_language']:<4} {settings.data_dir / art['rel_path']}")
    return 0 if state.value == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

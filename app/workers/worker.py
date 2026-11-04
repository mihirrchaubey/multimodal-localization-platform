"""Background worker: polls the durable job store, runs claimed jobs."""
from __future__ import annotations

import logging
import threading

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger, log_event
from app.db.store import JobStore
from app.providers import build_asr, build_translator
from app.workflows.pipeline import run_job

log = get_logger("worker")


class Worker(threading.Thread):
    def __init__(self, store: JobStore, settings: Settings, asr, translator, poll_interval_s: float = 0.5):
        super().__init__(daemon=True, name="localization-worker")
        self.store, self.settings, self.asr, self.translator = store, settings, asr, translator
        self.poll_interval_s = poll_interval_s
        self._stop_evt = threading.Event()

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        while not self._stop_evt.is_set():
            try:
                job_id = self.store.claim_next_queued()
            except Exception:  # noqa: BLE001
                log_event(log, "claim_failed", logging.ERROR)
                self._stop_evt.wait(self.poll_interval_s * 4)
                continue
            if job_id is None:
                self._stop_evt.wait(self.poll_interval_s)
                continue
            run_job(job_id, self.store, self.settings, self.asr, self.translator)


def main() -> None:  # standalone worker process: python -m app.workers.worker
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    store = JobStore(settings.db_path)
    store.init()
    store.requeue_stale_running()
    w = Worker(store, settings, build_asr(settings), build_translator(settings))
    w.start()
    try:
        w.join()
    except KeyboardInterrupt:
        w.stop()


if __name__ == "__main__":
    main()

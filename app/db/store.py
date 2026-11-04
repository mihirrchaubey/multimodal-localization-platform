"""SQLite-backed durable job store (Phase 1 local prototype; PostgreSQL is a Phase 4 swap).

All state changes go through short IMMEDIATE transactions so the API thread and the
worker thread/process can share one database safely.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from app.core.errors import InvalidTransitionError, NotFoundError
from app.core.models import TERMINAL_STATES, JobState, can_transition

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs(
  id TEXT PRIMARY KEY, state TEXT NOT NULL, source_path TEXT NOT NULL, original_filename TEXT,
  source_language TEXT NOT NULL, target_languages TEXT NOT NULL, options TEXT NOT NULL DEFAULT '{}',
  error TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state, created_at);
CREATE TABLE IF NOT EXISTS stages(
  job_id TEXT NOT NULL, name TEXT NOT NULL, target_language TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, retries INTEGER NOT NULL DEFAULT 0,
  started_at TEXT, finished_at TEXT, duration_ms INTEGER, detail TEXT,
  PRIMARY KEY(job_id, name, target_language));
CREATE TABLE IF NOT EXISTS artifacts(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, kind TEXT NOT NULL,
  target_language TEXT NOT NULL DEFAULT '', rel_path TEXT NOT NULL, size_bytes INTEGER,
  created_at TEXT NOT NULL, UNIQUE(job_id, kind, target_language));
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL, ts TEXT NOT NULL,
  type TEXT NOT NULL, payload TEXT);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def init(self) -> None:
        c = sqlite3.connect(self.db_path)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(_SCHEMA)
            c.commit()
        finally:
            c.close()

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def ping(self) -> bool:
        with self._tx() as c:
            return c.execute("SELECT 1").fetchone()[0] == 1

    # ---- jobs -------------------------------------------------------------------
    def create_job(self, *, source_path: str, original_filename: str | None, source_language: str,
                   target_languages: list[str], options: dict | None = None) -> str:
        job_id = uuid.uuid4().hex
        now = _now()
        with self._tx() as c:
            c.execute("INSERT INTO jobs(id,state,source_path,original_filename,source_language,"
                      "target_languages,options,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (job_id, JobState.QUEUED.value, source_path, original_filename, source_language,
                       json.dumps(target_languages), json.dumps(options or {}), now, now))
            self._event(c, job_id, "job_created", {"targets": target_languages})
        return job_id

    @staticmethod
    def _event(c, job_id: str, type_: str, payload: dict | None = None) -> None:
        c.execute("INSERT INTO events(job_id,ts,type,payload) VALUES(?,?,?,?)",
                  (job_id, _now(), type_, json.dumps(payload or {})))

    @staticmethod
    def _job_dict(row) -> dict:
        d = dict(row)
        d["target_languages"] = json.loads(d["target_languages"])
        d["options"] = json.loads(d["options"] or "{}")
        d["error"] = json.loads(d["error"]) if d["error"] else None
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def get_job(self, job_id: str) -> dict:
        with self._tx() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Job {job_id} not found", code="job_not_found")
            job = self._job_dict(row)
            job["stages"] = [self._stage_dict(r) for r in c.execute(
                "SELECT * FROM stages WHERE job_id=? ORDER BY rowid", (job_id,))]
            return job

    def transition(self, job_id: str, new: JobState, *, error: dict | None = None) -> None:
        with self._tx() as c:
            row = c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Job {job_id} not found", code="job_not_found")
            cur = JobState(row["state"])
            if not can_transition(cur, new):
                raise InvalidTransitionError(f"Cannot move job from {cur.value} to {new.value}")
            c.execute("UPDATE jobs SET state=?, error=?, updated_at=? WHERE id=?",
                      (new.value, json.dumps(error) if error else None, _now(), job_id))
            self._event(c, job_id, f"state:{new.value}", error)

    def claim_next_queued(self) -> str | None:
        with self._tx() as c:
            row = c.execute("SELECT id FROM jobs WHERE state=? ORDER BY created_at LIMIT 1",
                            (JobState.QUEUED.value,)).fetchone()
            if row is None:
                return None
            c.execute("UPDATE jobs SET state=?, updated_at=? WHERE id=?",
                      (JobState.RUNNING.value, _now(), row["id"]))
            self._event(c, row["id"], "state:running")
            return row["id"]

    def requeue_stale_running(self) -> int:
        """Crash recovery: jobs left 'running' by a dead worker go back to the queue.
        Completed stages are skipped on re-run, so no expensive work is repeated."""
        with self._tx() as c:
            rows = c.execute("SELECT id FROM jobs WHERE state=?", (JobState.RUNNING.value,)).fetchall()
            for r in rows:
                c.execute("UPDATE jobs SET state=?, updated_at=? WHERE id=?",
                          (JobState.QUEUED.value, _now(), r["id"]))
                self._event(c, r["id"], "requeued_after_restart")
            return len(rows)

    def request_cancel(self, job_id: str) -> JobState:
        with self._tx() as c:
            row = c.execute("SELECT state FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"Job {job_id} not found", code="job_not_found")
            cur = JobState(row["state"])
            if cur in TERMINAL_STATES:
                return cur
            if cur == JobState.QUEUED:
                c.execute("UPDATE jobs SET state=?, cancel_requested=1, updated_at=? WHERE id=?",
                          (JobState.CANCELLED.value, _now(), job_id))
                self._event(c, job_id, "state:cancelled")
                return JobState.CANCELLED
            c.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?", (_now(), job_id))
            self._event(c, job_id, "cancel_requested")
            return cur

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._tx() as c:
            row = c.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()
            return bool(row and row["cancel_requested"])

    # ---- stages -----------------------------------------------------------------
    @staticmethod
    def _stage_dict(r) -> dict:
        d = dict(r)
        d["detail"] = json.loads(d["detail"]) if d["detail"] else None
        return d

    def stage_get(self, job_id: str, name: str, lang: str = "") -> dict | None:
        with self._tx() as c:
            r = c.execute("SELECT * FROM stages WHERE job_id=? AND name=? AND target_language=?",
                          (job_id, name, lang)).fetchone()
            return self._stage_dict(r) if r else None

    def stage_begin(self, job_id: str, name: str, lang: str = "") -> int:
        with self._tx() as c:
            c.execute(
                "INSERT INTO stages(job_id,name,target_language,status,attempts,started_at) "
                "VALUES(?,?,?, 'running', 1, ?) ON CONFLICT(job_id,name,target_language) DO UPDATE SET "
                "status='running', attempts=attempts+1, started_at=excluded.started_at, finished_at=NULL",
                (job_id, name, lang, _now()))
            return c.execute("SELECT attempts FROM stages WHERE job_id=? AND name=? AND target_language=?",
                             (job_id, name, lang)).fetchone()[0]

    def stage_end(self, job_id: str, name: str, lang: str, status: str, duration_ms: int,
                  retries: int = 0, detail: dict | None = None) -> None:
        with self._tx() as c:
            c.execute("UPDATE stages SET status=?, finished_at=?, duration_ms=?, retries=?, detail=? "
                      "WHERE job_id=? AND name=? AND target_language=?",
                      (status, _now(), duration_ms, retries, json.dumps(detail) if detail else None,
                       job_id, name, lang))

    # ---- artifacts --------------------------------------------------------------
    def add_artifact(self, job_id: str, kind: str, lang: str, rel_path: str, size_bytes: int) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO artifacts(job_id,kind,target_language,rel_path,size_bytes,created_at) "
                      "VALUES(?,?,?,?,?,?) ON CONFLICT(job_id,kind,target_language) DO UPDATE SET "
                      "rel_path=excluded.rel_path, size_bytes=excluded.size_bytes",
                      (job_id, kind, lang, rel_path, size_bytes, _now()))

    def list_artifacts(self, job_id: str) -> list[dict]:
        with self._tx() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM artifacts WHERE job_id=? ORDER BY id", (job_id,))]

    def get_artifact(self, job_id: str, artifact_id: int) -> dict:
        with self._tx() as c:
            r = c.execute("SELECT * FROM artifacts WHERE job_id=? AND id=?", (job_id, artifact_id)).fetchone()
            if r is None:
                raise NotFoundError("Artifact not found", code="artifact_not_found")
            return dict(r)

    def events(self, job_id: str) -> list[dict]:
        with self._tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,))]

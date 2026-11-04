"""FastAPI application. NOTE: written against FastAPI/Pydantic v2 but NOT executed in the build
sandbox (packages unavailable offline) — run `pytest tests/api` after installing to verify."""
from __future__ import annotations

import hmac
import os
import re
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from app.core.config import Settings
from app.core.errors import LocalizationError, NotFoundError, ValidationError
from app.core.logging import configure_logging
from app.core.models import validate_language_code
from app.db.store import JobStore
from app.providers import build_asr, build_translator
from app.tools.media import validate_upload_header
from app.workers.worker import Worker

CHUNK = 1024 * 1024
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def parse_languages(raw: list[str], *, max_count: int) -> list[str]:
    seen: list[str] = []
    for item in raw:
        for part in item.split(","):
            part = part.strip()
            if part and part not in seen:
                seen.append(validate_language_code(part))
    if not seen:
        raise ValidationError("At least one target language is required", code="no_targets")
    if len(seen) > max_count:
        raise ValidationError(f"At most {max_count} target languages allowed", code="too_many_targets")
    return seen


def safe_display_name(name: str | None) -> str:
    base = os.path.basename((name or "").replace("\\", "/"))
    return _CTRL.sub("", base)[:120] or "upload"


def create_app(settings: Settings | None = None, *, start_worker: bool | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    settings.ensure_dirs()
    store = JobStore(settings.db_path)
    store.init()
    run_worker = settings.run_worker_in_api if start_worker is None else start_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = None
        if run_worker:
            store.requeue_stale_running()
            worker = Worker(store, settings, build_asr(settings), build_translator(settings))
            worker.start()
        yield
        if worker:
            worker.stop()

    app = FastAPI(title="Multimodal AI Localization Platform", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.settings = store, settings

    def require_auth(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_key and not (x_api_key and hmac.compare_digest(x_api_key, settings.api_key)):
            raise HTTPException(status_code=401, detail="Invalid or missing API key")

    @app.exception_handler(LocalizationError)
    async def _handle(_, exc: LocalizationError):
        status = 404 if isinstance(exc, NotFoundError) else 422 if isinstance(exc, ValidationError) else 400
        return JSONResponse(status_code=status, content={"error": {"code": exc.code, "message": exc.message}})

    @app.get("/health")
    def health():
        ok_db = True
        try:
            store.ping()
        except Exception:  # noqa: BLE001
            ok_db = False
        ffmpeg = bool(shutil.which(settings.ffmpeg_bin) and shutil.which(settings.ffprobe_bin))
        body = {"status": "ok" if ok_db and ffmpeg else "degraded", "db": ok_db, "ffmpeg": ffmpeg}
        return JSONResponse(status_code=200 if ok_db and ffmpeg else 503, content=body)

    @app.post("/api/v1/jobs", status_code=202, dependencies=[Depends(require_auth)])
    def create_job(file: UploadFile = File(...), source_language: str = Form("auto"),
                   target_languages: list[str] = Form(...)):
        src = validate_language_code(source_language, allow_auto=True)
        targets = parse_languages(target_languages, max_count=settings.max_target_languages)

        head = file.file.read(32)
        ext = validate_upload_header(file.filename or "", head)
        dest = settings.upload_dir / f"{uuid.uuid4().hex}{ext}"   # generated name; user filename never used
        written = 0
        try:
            with open(dest, "wb") as out:
                chunk = head
                while chunk:
                    written += len(chunk)
                    if written > settings.max_upload_bytes:
                        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")
                    out.write(chunk)
                    chunk = file.file.read(CHUNK)
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        job_id = store.create_job(source_path=str(dest), original_filename=safe_display_name(file.filename),
                                  source_language=src, target_languages=targets)
        return {"job_id": job_id, "state": "queued"}

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(require_auth)])
    def get_job(job_id: str):
        j = store.get_job(job_id)
        done = sum(1 for s in j["stages"] if s["status"] == "completed")
        return {"job_id": j["id"], "state": j["state"], "source_language": j["source_language"],
                "target_languages": j["target_languages"], "error": j["error"],
                "cancel_requested": j["cancel_requested"], "created_at": j["created_at"],
                "updated_at": j["updated_at"], "stages_completed": done,
                "stages": [{"name": s["name"], "target_language": s["target_language"], "status": s["status"],
                            "attempts": s["attempts"], "retries": s["retries"], "duration_ms": s["duration_ms"]}
                           for s in j["stages"]]}

    @app.get("/api/v1/jobs/{job_id}/results", dependencies=[Depends(require_auth)])
    def get_results(job_id: str):
        j = store.get_job(job_id)
        return {"job_id": job_id, "state": j["state"], "artifacts": [
            {"id": a["id"], "kind": a["kind"], "target_language": a["target_language"],
             "size_bytes": a["size_bytes"], "download_url": f"/api/v1/jobs/{job_id}/artifacts/{a['id']}"}
            for a in store.list_artifacts(job_id)]}

    @app.get("/api/v1/jobs/{job_id}/artifacts/{artifact_id}", dependencies=[Depends(require_auth)])
    def download(job_id: str, artifact_id: int):
        a = store.get_artifact(job_id, artifact_id)
        path = (settings.data_dir / a["rel_path"]).resolve()
        if not path.is_relative_to(settings.job_dir(job_id).resolve()) or not path.is_file():
            raise NotFoundError("Artifact not found", code="artifact_not_found")
        return FileResponse(path, filename=path.name)

    @app.post("/api/v1/jobs/{job_id}/cancel", dependencies=[Depends(require_auth)])
    def cancel(job_id: str):
        return {"job_id": job_id, "state": store.request_cancel(job_id).value}

    # Phase 2 (human review): GET/POST /api/v1/jobs/{job_id}/review are intentionally not implemented yet.
    return app


def app_factory() -> FastAPI:  # uvicorn --factory app.api.main:app_factory
    return create_app()

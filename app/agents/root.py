"""Phase 1 ADK root agent.

Verification status: written against the google-adk Python API as I know it
(`google.adk.agents.LlmAgent`, plain-function tools auto-wrapped). It was NOT executed in the
build sandbox (no network / no ADK install). Before relying on it, run `adk web adk_agents`
and check imports against the installed version's docs.

Scope: the *pipeline itself* stays deterministic Python (app/workflows/pipeline.py). This agent is
the conversational front door: it submits jobs and reports status through typed tools. Phase 2
introduces ADK workflow agents (Sequential/Parallel/Loop) for the stages that need LLM judgement.
"""
from __future__ import annotations

from pathlib import Path

from google.adk.agents import LlmAgent

from app.core.config import Settings
from app.core.errors import LocalizationError
from app.core.models import validate_language_code
from app.db.store import JobStore
from app.tools.media import validate_upload_header

_settings = Settings.from_env()
_settings.ensure_dirs()
_store = JobStore(_settings.db_path)
_store.init()


def submit_localization_job(file_path: str, source_language: str, target_languages: list[str]) -> dict:
    """Queue a localization job for a local media file.

    Args:
        file_path: Absolute path of an audio/video file on the server.
        source_language: BCP-47 code such as "en", or "auto" to detect.
        target_languages: Language codes to localize into, e.g. ["hi", "es"].
    Returns:
        {"status": "success", "job_id": ...} or {"status": "error", "message": ...}.
    """
    try:
        p = Path(file_path).resolve()
        with open(p, "rb") as f:
            validate_upload_header(p.name, f.read(32))
        validate_language_code(source_language, allow_auto=True)
        for t in target_languages:
            validate_language_code(t)
        job_id = _store.create_job(source_path=str(p), original_filename=p.name,
                                   source_language=source_language, target_languages=list(target_languages))
        return {"status": "success", "job_id": job_id}
    except (LocalizationError, OSError) as exc:
        return {"status": "error", "message": getattr(exc, "message", str(exc))}


def get_job_status(job_id: str) -> dict:
    """Return the state and stage progress of a localization job.

    Args:
        job_id: Identifier returned by submit_localization_job.
    """
    try:
        j = _store.get_job(job_id)
    except LocalizationError as exc:
        return {"status": "error", "message": exc.message}
    return {"status": "success", "state": j["state"], "error": j["error"],
            "stages": [{"name": s["name"], "language": s["target_language"], "status": s["status"]}
                       for s in j["stages"]]}


def list_job_outputs(job_id: str) -> dict:
    """List the output artifacts (subtitles, transcript, report) of a job."""
    try:
        return {"status": "success", "artifacts": [
            {"kind": a["kind"], "language": a["target_language"], "path": a["rel_path"]}
            for a in _store.list_artifacts(job_id)]}
    except LocalizationError as exc:
        return {"status": "error", "message": exc.message}


root_agent = LlmAgent(
    name="localization_orchestrator",
    model=_settings.gemini_model,
    description="Submits and tracks media localization jobs (transcribe, translate, subtitle).",
    instruction=(
        "You help users localize audio/video. Use submit_localization_job to start a job, "
        "get_job_status to report progress, and list_job_outputs to show results. "
        "Never invent job IDs or file paths; ask the user if something is missing."),
    tools=[submit_localization_job, get_job_status, list_job_outputs],
)

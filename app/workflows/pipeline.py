"""Phase 1 localization pipeline: inspect -> extract audio -> transcribe -> translate -> SRT.

Deterministic orchestration in plain Python. Each stage is idempotent: if it already
completed and its output file exists it is skipped, so retries/restarts never redo
expensive work or create conflicting artifacts. (Phase 2 re-expresses this as ADK
workflow agents; see docs/architecture.md.)
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from app.core.config import Settings
from app.core.errors import JobCancelled, LocalizationError, ValidationError
from app.core.logging import get_logger, log_event
from app.core.models import JobState, LocalizedSegment, Segment, base_language, validate_segments
from app.core.retry import with_retries
from app.db.store import JobStore
from app.tools import media, subtitles

log = get_logger("pipeline")


def _write_json(path: Path, data) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_segments(path: Path) -> tuple[str, list[Segment]]:
    d = json.loads(path.read_text(encoding="utf-8"))
    return d["language"], [Segment(**s) for s in d["segments"]]


def _load_localized(path: Path) -> list[LocalizedSegment]:
    out = []
    for s in json.loads(path.read_text(encoding="utf-8")):
        s["quality_findings"] = tuple(s.get("quality_findings", ()))
        out.append(LocalizedSegment(**s))
    return out


def run_job(job_id: str, store: JobStore, settings: Settings, asr, translator,
            sleep: Callable[[float], None] = time.sleep) -> JobState:
    """Run a job already claimed (state == running). Returns the final state."""
    job = store.get_job(job_id)
    jdir = settings.job_dir(job_id)
    jdir.mkdir(parents=True, exist_ok=True)
    src = Path(job["source_path"])
    warnings: list[str] = []

    def check_cancel() -> None:
        if store.is_cancel_requested(job_id):
            raise JobCancelled()

    def stage(name: str, lang: str, output: Path, fn: Callable[[], dict | None]) -> bool:
        """Run fn as an idempotent, timed, retried stage. Returns True if it actually ran."""
        check_cancel()
        prev = store.stage_get(job_id, name, lang)
        if prev and prev["status"] == "completed" and output.exists():
            log_event(log, "stage_skipped", job_id=job_id, stage=name, lang=lang)
            return False
        store.stage_begin(job_id, name, lang)
        retries = 0

        def on_retry(attempt: int, exc: BaseException) -> None:
            nonlocal retries
            retries += 1
            log_event(log, "stage_retry", logging.WARNING, job_id=job_id, stage=name, attempt=attempt,
                      error_code=getattr(exc, "code", type(exc).__name__))

        t0 = time.perf_counter()
        try:
            detail = with_retries(fn, max_attempts=settings.max_stage_attempts,
                                  base_delay_s=settings.retry_base_delay_s, on_retry=on_retry, sleep=sleep)
        except BaseException:
            store.stage_end(job_id, name, lang, "failed", int((time.perf_counter() - t0) * 1000), retries)
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        store.stage_end(job_id, name, lang, "completed", ms, retries, detail)
        log_event(log, "stage_completed", job_id=job_id, stage=name, lang=lang, duration_ms=ms, retries=retries)
        return True

    def register(kind: str, lang: str, path: Path) -> None:
        store.add_artifact(job_id, kind, lang, str(path.relative_to(settings.data_dir)), path.stat().st_size)

    try:
        # 1. inspect ------------------------------------------------------------
        info_path = jdir / "media_info.json"

        def do_inspect():
            info = media.probe(src, ffprobe_bin=settings.ffprobe_bin, timeout_s=settings.subprocess_timeout_s)
            media.check_limits(info, max_duration_s=settings.max_duration_s)
            _write_json(info_path, info.to_dict())
            return {"duration_s": info.duration_s, "has_video": info.has_video}

        stage("inspect", "", info_path, do_inspect)

        # 2. audio extraction -----------------------------------------------------
        audio_path = jdir / "audio.wav"
        transcript_path = jdir / "transcript.json"
        t_prev = store.stage_get(job_id, "transcribe")
        if not (t_prev and t_prev["status"] == "completed" and transcript_path.exists()):
            # The extracted audio is only needed to transcribe; skip it when a transcript exists
            # (audio.wav is deleted after a successful run, so its absence must not force rework).
            stage("extract_audio", "", audio_path, lambda: (media.extract_audio(
                src, audio_path, ffmpeg_bin=settings.ffmpeg_bin, timeout_s=settings.subprocess_timeout_s), None)[1])

        # 3. transcription --------------------------------------------------------

        def do_transcribe():
            lang = None if job["source_language"] == "auto" else job["source_language"]
            res = asr.transcribe(audio_path, lang)
            validate_segments(res.segments)
            _write_json(transcript_path, {"language": res.language, "provider": asr.name,
                                          "segments": [asdict(s) for s in res.segments]})
            return {"provider": asr.name, "segments": len(res.segments), "language": res.language}

        stage("transcribe", "", transcript_path, do_transcribe)
        register("transcript", "", transcript_path)
        source_lang, segments = _load_segments(transcript_path)

        # 4 + 5. translate and write subtitles per target language ----------------
        for target in job["target_languages"]:
            tr_path = jdir / f"translation.{target}.json"

            def do_translate(target=target, tr_path=tr_path):
                if base_language(target) == base_language(source_lang):
                    warnings.append(f"{target}: same as source language; text copied unchanged")
                    loc = [LocalizedSegment(s.segment_id, s.text, s.text, target, s.start_ms, s.end_ms,
                                            s.speaker_id) for s in segments]
                else:
                    loc = translator.translate(segments, source_lang, target)
                _write_json(tr_path, [asdict(s) for s in loc])
                return {"provider": translator.name, "segments": len(loc)}

            stage("translate", target, tr_path, do_translate)
            localized = _load_localized(tr_path)

            srt_path = jdir / f"subtitles.{target}.srt"

            def do_srt(localized=localized, srt_path=srt_path, target=target):
                text, warns = subtitles.segments_to_srt(
                    localized, max_chars=settings.subtitle_max_chars, max_lines=settings.subtitle_max_lines)
                srt_path.write_text(text, encoding="utf-8")
                warnings.extend(f"{target}: {w}" for w in warns)
                return {"cues": len(localized), "warnings": len(warns)}

            stage("subtitles", target, srt_path, do_srt)
            register("subtitles_srt", target, srt_path)

        # 6. report ---------------------------------------------------------------
        report_path = jdir / "report.json"
        final = store.get_job(job_id)
        _write_json(report_path, {
            "job_id": job_id, "source_language": source_lang, "target_languages": job["target_languages"],
            "providers": {"asr": asr.name, "translator": translator.name},
            "stages": [{k: s[k] for k in ("name", "target_language", "status", "attempts", "retries",
                                          "duration_ms")} for s in final["stages"]],
            "warnings": warnings,
            "quality": "Quality review not implemented in Phase 1; segments are 'unchecked'.",
        })
        register("report", "", report_path)
        audio_path.unlink(missing_ok=True)  # intermediate; retention: deleted on success
        store.transition(job_id, JobState.COMPLETED)
        return JobState.COMPLETED

    except JobCancelled:
        store.transition(job_id, JobState.CANCELLED)
        log_event(log, "job_cancelled", job_id=job_id)
        return JobState.CANCELLED
    except LocalizationError as exc:
        store.transition(job_id, JobState.FAILED, error={"code": exc.code, "message": exc.message})
        log_event(log, "job_failed", logging.ERROR, job_id=job_id, error_code=exc.code)
        return JobState.FAILED
    except Exception as exc:  # noqa: BLE001 - unknown failure: generic message to the user
        store.transition(job_id, JobState.FAILED,
                         error={"code": "internal_error", "message": "Unexpected internal error"})
        log_event(log, "job_crashed", logging.ERROR, job_id=job_id, exc_type=type(exc).__name__)
        return JobState.FAILED

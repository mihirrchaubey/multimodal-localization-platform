# Architecture (Phase 1)

```
client ──POST /api/v1/jobs──▶ FastAPI (validate upload, store under generated name, create job)
                                   │ returns 202 + job_id immediately
                                   ▼
                           SQLite job store  ◀── status/results/cancel endpoints
                                   ▲
                 worker thread polls, claims (atomic) ──▶ pipeline.run_job
                                                              │
   inspect ▶ extract_audio ▶ transcribe ▶ [ translate ▶ subtitles ] per target language
   (ffprobe)  (ffmpeg)        (ASR adapter)  (Translator adapter)    (SRT writer)
```

## Decisions
- **Deterministic work stays plain Python** (FFmpeg, SRT, validation, state machine). LLMs are for
  translation (and, in Phase 2, quality review) only.
- **Idempotent stages.** A stage is skipped if it already completed and its output exists, so
  retries and worker restarts never repeat expensive work. `requeue_stale_running()` recovers jobs
  orphaned by a crash.
- **Model output is untrusted.** Gemini responses are schema-validated (Pydantic) and joined to
  source segments by id; any mismatch is a retryable `invalid_model_output`.
- **Adapters for ASR/translation** with a `mock` implementation so everything runs offline.
- **SQLite now, PostgreSQL later.** The `JobStore` interface is the seam for Phase 4.

## Where ADK fits today — and next
Phase 1 includes one ADK `LlmAgent` (`app/agents/root.py`) exposing job submit/status/outputs as
typed tools. It is **unverified** (ADK could not be installed offline). Phase 2 re-expresses the
pipeline as ADK `SequentialAgent` / `ParallelAgent` (per-language translation) / `LoopAgent`
(bounded revision, max N iterations) with callbacks for logging; the durable store remains the
source of truth, ADK session state is execution context only.

## Retention
Uploads live in `data/uploads/`, artifacts in `data/jobs/<id>/`. The extracted `audio.wav` is deleted
when a job completes. No automatic deletion of uploads/outputs yet — a documented gap.

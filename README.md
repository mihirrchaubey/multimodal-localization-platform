# Multimodal AI Localization Platform

Upload audio/video → transcript → translated subtitles (SRT) per target language, via an
asynchronous job API. **Status: Phase 0 + Phase 1 vertical slice** (see checklist). Built from
`Multimodal_AI_Localization_Project_Report_for_Grok.docx`.

## Quick start
Requires Python 3.11+ and FFmpeg/FFprobe on PATH.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"            # add ,gemini / ,adk / ,whisper as needed
cp .env.example .env               # defaults use mock providers: no keys required

uvicorn --factory app.api.main:app_factory --reload
```

```bash
sh scripts/make_sample.sh sample.mp4
curl -F file=@sample.mp4 -F source_language=en -F target_languages=hi,es localhost:8000/api/v1/jobs
curl localhost:8000/api/v1/jobs/<job_id>
curl localhost:8000/api/v1/jobs/<job_id>/results
```

No server? `python scripts/run_local.py sample.mp4 --targets hi,es --source en`

**Real providers:** `LOC_TRANSLATOR_PROVIDER=gemini` + `GEMINI_API_KEY` (and `pip install '.[gemini]'`);
`LOC_ASR_PROVIDER=whisper` (and `pip install '.[whisper]'`).
> The mock ASR does **not** recognise speech — it lays placeholder lines over the audio duration.

## API
`POST /api/v1/jobs` · `GET /api/v1/jobs/{id}` · `GET /api/v1/jobs/{id}/results` ·
`GET /api/v1/jobs/{id}/artifacts/{artifact_id}` · `POST /api/v1/jobs/{id}/cancel` · `GET /health`.
Optional `X-API-Key` auth via `LOC_API_KEY`.

## Tests
```bash
python -m unittest discover -s tests -t . -v     # stdlib only; uses real ffmpeg
pytest                                           # same tests, plus API tests once deps are installed
```

## Status checklist (honest)
| Item | Status |
|---|---|
| Config, structured/redacting logs, errors, state machine, retry/backoff | ✅ implemented, unit-tested |
| Upload validation (extension + magic bytes), FFprobe metadata, FFmpeg audio extraction | ✅ implemented, tested against real FFmpeg |
| Durable job store, atomic claim, cancel, crash-recovery requeue, idempotent stages | ✅ implemented, tested |
| Pipeline: transcript + translation + SRT, multi-language | ✅ end-to-end tested with **mock** ASR/translator |
| FastAPI endpoints + API tests | ⚠️ written, **not run** (deps unavailable in build sandbox) |
| Gemini translator | ⚠️ response parsing/validation written; live call **not run** (parsing tests need pydantic) |
| Whisper ASR adapter | ⚠️ written, **not run** |
| ADK root agent | ⚠️ written, **not run**; imports must be verified against installed ADK |
| Docker | ⚠️ written, **not built** |
| Quality agent, revision loop, human review (Phase 2) | ❌ not started |
| TTS, alignment, rendering (Phase 3) | ❌ not started |
| GCS / Pub/Sub / Postgres / Cloud Run (Phase 4) | ❌ not started |
| Evaluation, load tests, cost/latency numbers (Phase 5) | ❌ not started — no performance/accuracy claims are made |

## Known limitations
Dependency versions are ranges, not verified pins. No auth beyond optional API key. Uploads and
outputs are not auto-deleted. Diarization not implemented. `quality_status` is always `unchecked`.

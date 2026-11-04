"""End-to-end local pipeline with REAL ffmpeg/ffprobe and mock ASR/translator."""
import json
import unittest
from pathlib import Path

from app.core.errors import ProviderError
from app.db.store import JobStore
from app.providers.asr import MockASR
from app.providers.translation import MockTranslator
from app.workflows.pipeline import run_job
from tests.helpers import HAVE_FFMPEG, make_sample, temp_settings


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg/ffprobe not available")
class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.settings, self._tmp = temp_settings()
        self.addCleanup(self._tmp.cleanup)
        self.store = JobStore(self.settings.db_path)
        self.store.init()
        self.src = make_sample(self.settings.upload_dir / "sample.mp4", seconds=10)

    def submit(self, src=None, targets=("hi", "es"), source="en"):
        jid = self.store.create_job(source_path=str(src or self.src), original_filename="sample.mp4",
                                    source_language=source, target_languages=list(targets))
        self.assertEqual(self.store.claim_next_queued(), jid)
        return jid

    def run_it(self, jid, translator=None):
        return run_job(jid, self.store, self.settings, MockASR(), translator or MockTranslator(),
                       sleep=lambda s: None)

    def test_happy_path_produces_valid_srt_for_each_language(self):
        jid = self.submit()
        self.assertEqual(self.run_it(jid).value, "completed")
        job = self.store.get_job(jid)
        self.assertEqual(job["state"], "completed")
        arts = {(a["kind"], a["target_language"]): a for a in self.store.list_artifacts(jid)}
        self.assertEqual(set(arts), {("transcript", ""), ("subtitles_srt", "hi"), ("subtitles_srt", "es"),
                                    ("report", "")})
        srt = (self.settings.data_dir / arts[("subtitles_srt", "hi")]["rel_path"]).read_text(encoding="utf-8")
        self.assertIn("-->", srt)
        self.assertIn("[hi] This is mock transcript line 1.", srt)
        names = [(s["name"], s["target_language"], s["status"]) for s in job["stages"]]
        self.assertIn(("translate", "es", "completed"), names)
        self.assertTrue(all(s["status"] == "completed" for s in job["stages"]))
        self.assertFalse((self.settings.job_dir(jid) / "audio.wav").exists())  # intermediate cleaned up

    def test_source_media_is_untouched(self):
        before = self.src.read_bytes()
        self.run_it(self.submit())
        self.assertEqual(self.src.read_bytes(), before)

    def test_rerun_skips_completed_stages(self):
        jid = self.submit()
        calls = []

        class Counting(MockTranslator):
            def translate(self, *a):
                calls.append(1)
                return super().translate(*a)

        self.run_it(jid, Counting())
        self.assertEqual(len(calls), 2)
        # Simulate a worker crash after completion of all stages but before the final state write.
        import sqlite3
        c = sqlite3.connect(self.settings.db_path)
        c.execute("UPDATE jobs SET state='running' WHERE id=?", (jid,))
        c.commit()
        c.close()
        self.run_it(jid, Counting())
        self.assertEqual(len(calls), 2, "translation must not be re-run")
        attempts = {(s["name"], s["target_language"]): s["attempts"] for s in self.store.get_job(jid)["stages"]}
        self.assertTrue(all(a == 1 for a in attempts.values()), attempts)

    def test_transient_provider_failure_is_retried(self):
        jid = self.submit(targets=("hi",))
        state = {"n": 0}

        class Flaky(MockTranslator):
            def translate(self, *a):
                state["n"] += 1
                if state["n"] < 3:
                    raise ProviderError("timeout")
                return super().translate(*a)

        self.assertEqual(self.run_it(jid, Flaky()).value, "completed")
        st = next(s for s in self.store.get_job(jid)["stages"] if s["name"] == "translate")
        self.assertEqual(st["retries"], 2)

    def test_persistent_failure_marks_job_failed_with_code(self):
        jid = self.submit(targets=("hi",))

        class Down(MockTranslator):
            def translate(self, *a):
                raise ProviderError("down")

        self.assertEqual(self.run_it(jid, Down()).value, "failed")
        job = self.store.get_job(jid)
        self.assertEqual(job["error"]["code"], "provider_error")
        self.assertEqual(next(s for s in job["stages"] if s["name"] == "translate")["status"], "failed")

    def test_invalid_media_fails_cleanly(self):
        bad = self.settings.upload_dir / "bad.mp4"
        bad.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"garbage" * 50)
        jid = self.submit(src=bad)
        self.assertEqual(self.run_it(jid).value, "failed")
        self.assertEqual(self.store.get_job(jid)["error"]["code"], "unreadable_media")

    def test_duration_limit_enforced(self):
        from dataclasses import replace
        self.settings = replace(self.settings, max_duration_s=5)
        jid = self.submit()
        self.assertEqual(self.run_it(jid).value, "failed")
        self.assertEqual(self.store.get_job(jid)["error"]["code"], "too_long")

    def test_cancellation_stops_between_stages(self):
        jid = self.submit()
        self.store.request_cancel(jid)
        self.assertEqual(self.run_it(jid).value, "cancelled")
        self.assertEqual(self.store.list_artifacts(jid), [])

    def test_auto_source_and_same_language_copy(self):
        jid = self.submit(targets=("en",), source="auto")
        self.assertEqual(self.run_it(jid).value, "completed")
        report = json.loads(next(
            (self.settings.data_dir / a["rel_path"]).read_text() for a in self.store.list_artifacts(jid)
            if a["kind"] == "report"))
        self.assertTrue(any("same as source" in w for w in report["warnings"]))

    def test_audio_only_input(self):
        wav = make_sample(self.settings.upload_dir / "a.wav", seconds=6, video=False)
        self.assertEqual(self.run_it(self.submit(src=wav, targets=("fr",))).value, "completed")

    def test_logs_do_not_contain_transcript_text(self):
        with self.assertLogs("loc", level="DEBUG") as cm:
            self.run_it(self.submit(targets=("hi",)))
        self.assertFalse(any("mock transcript line" in line for line in cm.output))


if __name__ == "__main__":
    unittest.main()

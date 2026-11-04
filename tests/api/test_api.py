"""API tests. Require fastapi + httpx (pip install '.[dev]'). NOT run in the build sandbox."""
import io
import time
import unittest
from dataclasses import replace

try:
    from fastapi.testclient import TestClient
    HAVE = True
except ImportError:
    HAVE = False

from tests.helpers import HAVE_FFMPEG, make_sample, temp_settings


@unittest.skipUnless(HAVE and HAVE_FFMPEG, "fastapi/httpx/ffmpeg not available")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from app.api.main import create_app
        self.create_app = create_app
        self.settings, self._tmp = temp_settings(max_upload_mb=1)
        self.addCleanup(self._tmp.cleanup)
        self.client = TestClient(create_app(self.settings, start_worker=False))
        self.mp4 = make_sample(self.settings.data_dir / "s.mp4", seconds=4).read_bytes()

    def post(self, client=None, data=None, name="s.mp4", content=None):
        c = client or self.client
        return c.post("/api/v1/jobs",
                      data=data or {"source_language": "en", "target_languages": "hi,es"},
                      files={"file": (name, io.BytesIO(self.mp4 if content is None else content), "video/mp4")})

    def test_health(self):
        self.assertEqual(self.client.get("/health").json()["status"], "ok")

    def test_create_returns_job_id_immediately(self):
        r = self.post()
        self.assertEqual(r.status_code, 202)
        job = self.client.get(f"/api/v1/jobs/{r.json()['job_id']}").json()
        self.assertEqual((job["state"], job["target_languages"]), ("queued", ["hi", "es"]))

    def test_rejects_bad_signature_extension_and_languages(self):
        self.assertEqual(self.post(content=b"MZ" + b"\0" * 64).status_code, 422)
        self.assertEqual(self.post(name="x.exe").status_code, 422)
        self.assertEqual(self.post(data={"source_language": "en", "target_languages": "not a lang"}).status_code, 422)

    def test_rejects_oversize(self):
        self.assertEqual(self.post(content=self.mp4[:32] + b"\0" * (2 * 1024 * 1024)).status_code, 413)

    def test_path_traversal_filename_is_not_used_for_storage(self):
        self.assertEqual(self.post(name="../../evil.mp4").status_code, 202)
        self.assertEqual([p.name for p in self.settings.data_dir.rglob("evil*")], [])

    def test_unknown_job_404_and_cancel(self):
        self.assertEqual(self.client.get("/api/v1/jobs/nope").status_code, 404)
        jid = self.post().json()["job_id"]
        self.assertEqual(self.client.post(f"/api/v1/jobs/{jid}/cancel").json()["state"], "cancelled")

    def test_end_to_end_with_worker(self):
        with TestClient(self.create_app(self.settings, start_worker=True)) as c:
            jid = self.post(client=c, data={"source_language": "en", "target_languages": "hi"}).json()["job_id"]
            st = "queued"
            for _ in range(100):
                st = c.get(f"/api/v1/jobs/{jid}").json()["state"]
                if st in ("completed", "failed"):
                    break
                time.sleep(0.1)
            self.assertEqual(st, "completed")
            arts = c.get(f"/api/v1/jobs/{jid}/results").json()["artifacts"]
            srt = next(a for a in arts if a["kind"] == "subtitles_srt" and a["target_language"] == "hi")
            self.assertIn("-->", c.get(srt["download_url"]).text)

    def test_api_key_enforced_when_configured(self):
        c = TestClient(self.create_app(replace(self.settings, api_key="k"), start_worker=False))
        self.assertEqual(c.get("/api/v1/jobs/x").status_code, 401)
        self.assertEqual(c.get("/api/v1/jobs/x", headers={"X-API-Key": "k"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.core.errors import InvalidTransitionError, NotFoundError
from app.core.models import JobState
from app.db.store import JobStore
from tests.helpers import temp_settings


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.settings, self._tmp = temp_settings()
        self.store = JobStore(self.settings.db_path)
        self.store.init()
        self.addCleanup(self._tmp.cleanup)

    def new(self):
        return self.store.create_job(source_path="/x/y.mp4", original_filename="y.mp4",
                                     source_language="auto", target_languages=["hi", "es"])

    def test_create_and_get(self):
        j = self.store.get_job(self.new())
        self.assertEqual(j["state"], "queued")
        self.assertEqual(j["target_languages"], ["hi", "es"])

    def test_missing_job(self):
        with self.assertRaises(NotFoundError):
            self.store.get_job("nope")

    def test_claim_is_atomic_and_ordered(self):
        a, b = self.new(), self.new()
        self.assertEqual(self.store.claim_next_queued(), a)
        self.assertEqual(self.store.claim_next_queued(), b)
        self.assertIsNone(self.store.claim_next_queued())

    def test_invalid_transition_rejected(self):
        j = self.new()
        with self.assertRaises(InvalidTransitionError):
            self.store.transition(j, JobState.COMPLETED)

    def test_cancel_queued_is_immediate_running_is_flagged(self):
        q = self.new()
        self.assertEqual(self.store.request_cancel(q), JobState.CANCELLED)
        r = self.new()
        self.store.claim_next_queued()
        self.assertEqual(self.store.request_cancel(r), JobState.RUNNING)
        self.assertTrue(self.store.is_cancel_requested(r))

    def test_cancel_terminal_is_noop(self):
        j = self.new()
        self.store.request_cancel(j)
        self.assertEqual(self.store.request_cancel(j), JobState.CANCELLED)

    def test_stage_attempts_and_artifact_upsert(self):
        j = self.new()
        self.assertEqual(self.store.stage_begin(j, "inspect"), 1)
        self.store.stage_end(j, "inspect", "", "completed", 5)
        self.assertEqual(self.store.stage_begin(j, "inspect"), 2)
        self.store.add_artifact(j, "report", "", "a", 1)
        self.store.add_artifact(j, "report", "", "b", 2)
        arts = self.store.list_artifacts(j)
        self.assertEqual((len(arts), arts[0]["rel_path"]), (1, "b"))

    def test_requeue_stale_running(self):
        j = self.new()
        self.store.claim_next_queued()
        self.assertEqual(self.store.requeue_stale_running(), 1)
        self.assertEqual(self.store.get_job(j)["state"], "queued")


if __name__ == "__main__":
    unittest.main()

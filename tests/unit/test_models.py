import unittest

from app.core.errors import ValidationError
from app.core.models import (JobState, Segment, can_transition, validate_language_code,
                             validate_segments)


class ModelTests(unittest.TestCase):
    def test_valid_transitions(self):
        self.assertTrue(can_transition(JobState.QUEUED, JobState.RUNNING))
        self.assertTrue(can_transition(JobState.RUNNING, JobState.WAITING_FOR_REVIEW))
        self.assertTrue(can_transition(JobState.WAITING_FOR_REVIEW, JobState.RUNNING))

    def test_terminal_states_are_final(self):
        for t in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
            for n in JobState:
                self.assertFalse(can_transition(t, n))

    def test_cannot_skip_to_completed_from_queued(self):
        self.assertFalse(can_transition(JobState.QUEUED, JobState.COMPLETED))

    def test_segment_validation(self):
        Segment("seg_1", 0, 10, "ok")
        for bad in [dict(start_ms=-1, end_ms=5, text="x"), dict(start_ms=5, end_ms=5, text="x"),
                    dict(start_ms=0, end_ms=5, text="  "), dict(start_ms=0, end_ms=5, text="x", confidence=2)]:
            with self.assertRaises(ValidationError):
                Segment("seg_1", **bad)

    def test_overlap_and_duplicates_detected(self):
        a, b = Segment("a", 0, 100, "x"), Segment("b", 50, 200, "y")
        with self.assertRaises(ValidationError):
            validate_segments([a, b])
        with self.assertRaises(ValidationError):
            validate_segments([a, Segment("a", 100, 200, "y")])
        validate_segments([a, Segment("b", 100, 200, "y")])  # touching is fine

    def test_language_codes(self):
        for ok in ("en", "hi", "pt-BR", "zh-Hans"):
            validate_language_code(ok)
        for bad in ("", "english", "e", "en_US", "en; rm -rf /", "../x"):
            with self.assertRaises(ValidationError):
                validate_language_code(bad)
        validate_language_code("auto", allow_auto=True)
        with self.assertRaises(ValidationError):
            validate_language_code("auto")


if __name__ == "__main__":
    unittest.main()

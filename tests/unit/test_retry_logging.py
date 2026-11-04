import json
import logging
import unittest

from app.core.errors import ProviderError, ValidationError
from app.core.logging import JsonFormatter, redact
from app.core.retry import with_retries


class RetryTests(unittest.TestCase):
    def test_retries_then_succeeds_with_backoff(self):
        calls, sleeps = [], []

        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ProviderError("flaky")
            return "ok"

        self.assertEqual(with_retries(fn, max_attempts=3, base_delay_s=1, sleep=sleeps.append), "ok")
        self.assertEqual(sleeps, [1, 2])

    def test_gives_up_after_max_attempts(self):
        calls = []

        def fn():
            calls.append(1)
            raise ProviderError("down")

        with self.assertRaises(ProviderError):
            with_retries(fn, max_attempts=3, base_delay_s=0, sleep=lambda s: None)
        self.assertEqual(len(calls), 3)

    def test_non_retryable_not_retried(self):
        calls = []

        def fn():
            calls.append(1)
            raise ValidationError("bad")

        with self.assertRaises(ValidationError):
            with_retries(fn, max_attempts=5, base_delay_s=0, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)


class LoggingTests(unittest.TestCase):
    def test_sensitive_fields_redacted(self):
        out = redact({"job_id": "j1", "text": "secret words", "api_key": "k", "translated_text": "t"})
        self.assertEqual(out["job_id"], "j1")
        self.assertEqual(out["text"], "[REDACTED]")
        self.assertEqual(out["api_key"], "[REDACTED]")

    def test_json_line(self):
        rec = logging.LogRecord("loc.x", logging.INFO, "f", 1, "evt", None, None)
        rec.fields = {"job_id": "j1", "text": "hello"}
        data = json.loads(JsonFormatter().format(rec))
        self.assertEqual(data["event"], "evt")
        self.assertEqual(data["text"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()

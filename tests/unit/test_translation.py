import unittest

from app.core.errors import ProviderError
from app.core.models import Segment
from app.providers.translation import MockTranslator, build_localized

SEGS = [Segment("seg_0001", 0, 1000, "Hello"), Segment("seg_0002", 1000, 2000, "World")]

try:
    import pydantic  # noqa: F401
    HAVE_PYDANTIC = True
except ImportError:
    HAVE_PYDANTIC = False


class TranslationTests(unittest.TestCase):
    def test_mock_preserves_ids_and_timing(self):
        out = MockTranslator().translate(SEGS, "en", "hi")
        self.assertEqual([s.segment_id for s in out], ["seg_0001", "seg_0002"])
        self.assertEqual((out[0].start_ms, out[0].end_ms), (0, 1000))
        self.assertEqual(out[0].translated_text, "[hi] Hello")
        self.assertEqual(out[0].quality_status, "unchecked")

    def test_missing_extra_and_empty_rejected(self):
        for bad in ({"seg_0001": "a"}, {"seg_0001": "a", "seg_0002": "b", "seg_9": "c"},
                    {"seg_0001": "a", "seg_0002": "   "}):
            with self.assertRaises(ProviderError) as ctx:
                build_localized(SEGS, bad, "hi")
            self.assertTrue(ctx.exception.retryable)


@unittest.skipUnless(HAVE_PYDANTIC, "pydantic not installed in this environment")
class GeminiParsingTests(unittest.TestCase):
    def test_valid_and_invalid_model_output(self):
        from app.providers.gemini import parse_batch_response
        ok = parse_batch_response('{"items":[{"segment_id":"a","translated_text":"x"}]}')
        self.assertEqual(ok, {"a": "x"})
        for bad in (None, "", "not json", '{"items":[{"segment_id":"a"}]}',
                    '{"items":[{"segment_id":"a","translated_text":"x"},{"segment_id":"a","translated_text":"y"}]}'):
            with self.assertRaises(ProviderError):
                parse_batch_response(bad)

    def test_batches_with_stubbed_model(self):
        from app.providers.gemini import GeminiTranslator
        t = GeminiTranslator.__new__(GeminiTranslator)  # bypass SDK/client construction
        t._batch_size = 1
        calls = []

        def fake(prompt):
            calls.append(prompt)
            sid = "seg_0001" if "seg_0001" in prompt else "seg_0002"
            return '{"items":[{"segment_id":"%s","translated_text":"T-%s"}]}' % (sid, sid)

        t._call_model = fake
        out = t.translate(SEGS, "en", "hi")
        self.assertEqual(len(calls), 2)
        self.assertEqual([s.translated_text for s in out], ["T-seg_0001", "T-seg_0002"])


if __name__ == "__main__":
    unittest.main()

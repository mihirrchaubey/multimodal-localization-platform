import unittest

from app.core.models import LocalizedSegment
from app.tools.subtitles import format_srt_time, segments_to_srt, wrap_text


def seg(i, start, end, text):
    return LocalizedSegment(f"seg_{i:04d}", "src", text, "hi", start, end)


class SubtitleTests(unittest.TestCase):
    def test_time_format(self):
        self.assertEqual(format_srt_time(0), "00:00:00,000")
        self.assertEqual(format_srt_time(3_723_456), "01:02:03,456")

    def test_negative_time_rejected(self):
        with self.assertRaises(ValueError):
            format_srt_time(-1)

    def test_srt_structure(self):
        srt, warns = segments_to_srt([seg(1, 1200, 3450, "Hello"), seg(2, 4000, 5000, "World")])
        self.assertEqual(srt.split("\n\n")[0], "1\n00:00:01,200 --> 00:00:03,450\nHello")
        self.assertIn("2\n00:00:04,000 --> 00:00:05,000\nWorld", srt)
        self.assertEqual(warns, [])

    def test_wrap_never_drops_text_and_warns(self):
        long = "word " * 40
        srt, warns = segments_to_srt([seg(1, 0, 5000, long)], max_chars=20, max_lines=2)
        self.assertEqual(srt.count("word"), 40)
        self.assertEqual(len(warns), 1)

    def test_unbroken_cjk_is_hard_wrapped(self):
        self.assertTrue(all(len(l) <= 10 for l in wrap_text("字" * 35, 10)))

    def test_arrow_in_text_is_neutralised(self):
        srt, _ = segments_to_srt([seg(1, 0, 1000, "a --> b")])
        self.assertEqual(srt.count("-->"), 1)

    def test_overlap_warning(self):
        _, warns = segments_to_srt([seg(1, 0, 2000, "a"), seg(2, 1000, 3000, "b")])
        self.assertTrue(any("overlaps" in w for w in warns))


if __name__ == "__main__":
    unittest.main()

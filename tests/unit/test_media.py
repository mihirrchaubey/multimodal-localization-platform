import unittest

from app.core.errors import MediaProcessingError, UnsupportedMediaError
from app.tools.media import (MediaInfo, check_limits, parse_ffprobe, sniff_format,
                             validate_upload_header)

MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "


class MediaTests(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(sniff_format(MP4), "mp4")
        self.assertEqual(sniff_format(WAV), "wav")
        self.assertEqual(sniff_format(b"\x1a\x45\xdf\xa3" + b"0" * 8), "matroska")
        self.assertIsNone(sniff_format(b"MZ\x90\x00 not media at all"))

    def test_upload_validation(self):
        self.assertEqual(validate_upload_header("clip.MP4", MP4), ".mp4")
        with self.assertRaises(UnsupportedMediaError):
            validate_upload_header("evil.exe", MP4)
        with self.assertRaises(UnsupportedMediaError):
            validate_upload_header("clip.mp4", b"MZ" + b"\x00" * 20)      # exe renamed to .mp4
        with self.assertRaises(UnsupportedMediaError):
            validate_upload_header("clip.wav", MP4)                       # signature/extension mismatch
        with self.assertRaises(UnsupportedMediaError):
            validate_upload_header("../../etc/passwd", MP4)

    PROBE = {"format": {"format_name": "mov,mp4", "duration": "12.5", "size": "1000"},
             "streams": [{"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
                          "avg_frame_rate": "30000/1001", "disposition": {}},
                         {"codec_type": "audio", "codec_name": "aac", "channels": 2, "sample_rate": "48000",
                          "tags": {"language": "eng"}}]}

    def test_parse_ffprobe(self):
        info = parse_ffprobe(self.PROBE)
        self.assertEqual(info.duration_s, 12.5)
        self.assertEqual(info.video["fps"], 29.97)
        self.assertEqual(info.video["width"], 1920)
        self.assertEqual(info.audio_streams[0]["sample_rate"], 48000)

    def test_audio_only_and_zero_fps_denominator(self):
        data = {"format": {"duration": "3"}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}
        info = parse_ffprobe(data)
        self.assertFalse(info.has_video)
        v = {"format": {"duration": "3"}, "streams": [
            {"codec_type": "video", "avg_frame_rate": "0/0", "r_frame_rate": "25/1"},
            {"codec_type": "audio"}]}
        self.assertEqual(parse_ffprobe(v).video["fps"], 25.0)

    def test_no_audio_or_duration_rejected(self):
        with self.assertRaises(UnsupportedMediaError):
            parse_ffprobe({"format": {"duration": "3"}, "streams": [{"codec_type": "video"}]})
        with self.assertRaises(MediaProcessingError):
            parse_ffprobe({"format": {}, "streams": [{"codec_type": "audio"}]})

    def test_duration_limit(self):
        info = MediaInfo("x", 100.0, None, False, None, [])
        check_limits(info, max_duration_s=100)
        with self.assertRaises(UnsupportedMediaError):
            check_limits(info, max_duration_s=99)


if __name__ == "__main__":
    unittest.main()

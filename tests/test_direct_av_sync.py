import importlib.util
import math
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import wave


REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO_ROOT / "lib" / "direct_av_sync.py"
SPEC = importlib.util.spec_from_file_location("direct_av_sync", MODULE_PATH)
assert SPEC and SPEC.loader
direct_av_sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = direct_av_sync
SPEC.loader.exec_module(direct_av_sync)


class DirectAVSyncTests(unittest.TestCase):
    def test_config_is_strict_loopback_only_and_redacted(self) -> None:
        config = direct_av_sync.load_config({"SOREN_STREAM_BACKEND": "ffmpeg"})
        public = config.public_dict()
        self.assertEqual(public["tone_hz"], 17000)
        self.assertNotIn("rtmp", " ".join(public))
        with self.assertRaises(direct_av_sync.AVSyncError):
            direct_av_sync.load_config(
                {
                    "SOREN_STREAM_BACKEND": "ffmpeg",
                    "SOREN_DIRECT_STREAM_LOCAL_URL": "rtmp://example.invalid/app/secret",
                }
            )

    def test_probe_html_uses_absolute_schedule_and_transparent_idle(self) -> None:
        html = direct_av_sync.render_probe_html([1000, 2000], 180)
        self.assertIn("const events=[1000,2000]", html)
        self.assertIn("Date.now()", html)
        self.assertIn("'transparent'", html)
        self.assertIn("'#fff'", html)
        self.assertIn("lastEventEnd", html)
        self.assertIn("location.reload()", html)
        self.assertIn("lastEventEnd-Date.now()+5000", html)

    def test_generated_tone_is_48khz_mono_pcm(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "tone.wav"
            direct_av_sync.generate_tone_wav(
                path,
                frequency_hz=17000,
                duration_ms=180,
            )
            with wave.open(str(path), "rb") as source:
                self.assertEqual(source.getframerate(), 48000)
                self.assertEqual(source.getnchannels(), 1)
                self.assertEqual(source.getsampwidth(), 2)
                self.assertEqual(source.getnframes(), 8640)

    def test_tone_detector_finds_high_frequency_pulses_over_silence(self) -> None:
        sample_rate = 48000
        duration = 5
        pulses = (1.0, 3.0)
        values = []
        for index in range(sample_rate * duration):
            at = index / sample_rate
            active = any(start <= at < start + 0.18 for start in pulses)
            value = 0.4 * math.sin(2 * math.pi * 17000 * at) if active else 0.0
            values.append(struct.pack("<h", round(value * 32767)))
        events = direct_av_sync.detect_tone_events(
            b"".join(values),
            sample_rate=sample_rate,
            frequency_hz=17000,
        )
        self.assertEqual(len(events), 2)
        self.assertAlmostEqual(events[0], 1.0, delta=0.02)
        self.assertAlmostEqual(events[1], 3.0, delta=0.02)

    def test_event_match_accepts_under_100ms_without_drift(self) -> None:
        result = direct_av_sync.match_events(
            [1, 3, 5, 7, 9, 11],
            [1.06, 3.05, 5.06, 7.05, 9.06, 11.05],
            expected_count=6,
            max_abs_offset_ms=100,
            max_drift_ms=50,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["pair_count"], 6)
        self.assertLessEqual(result["max_abs_offset_ms"], 60)

    def test_event_match_rejects_large_offset_or_drift(self) -> None:
        result = direct_av_sync.match_events(
            [1, 3, 5, 7, 9, 11],
            [1.02, 3.06, 5.10, 7.14, 9.18, 11.22],
            expected_count=6,
            max_abs_offset_ms=100,
            max_drift_ms=50,
        )
        self.assertFalse(result["ok"])
        self.assertGreater(result["drift_ms"], 50)


if __name__ == "__main__":
    unittest.main()

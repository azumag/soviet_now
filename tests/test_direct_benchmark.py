import unittest

from lib.direct_benchmark import build_comparison


class DirectBenchmarkComparisonTests(unittest.TestCase):
    def test_four_cpu_result_passes_on_stream_process_cpu_reduction(self) -> None:
        result = build_comparison(
            {
                "obs_cpu_pct": 152.33,
                "system_busy_pct": 97.21,
                "game_fps_mean": 29.847,
            },
            {
                "encoder_cpu_pct": 47.0,
                "system_busy_pct": 84.77,
                "ffmpeg_fps": 29.97,
                "ffmpeg_speed": 0.999,
                "game_fps_mean": 29.9,
                "drop_frames": 0,
                "dup_frames": 2,
                "encoded_frames": 900,
                "exit_code": 0,
            },
            "native4",
        )

        self.assertEqual(result["stream_process_cpu_reduction_pct"], 69.146)
        self.assertEqual(result["system_busy_reduction_pct"], 12.797)
        self.assertTrue(result["cpu_acceptance_20pct"])
        self.assertTrue(result["direct_720p30_acceptance"])
        self.assertTrue(result["short_benchmark_acceptance"])

    def test_two_cpu_result_fails_content_and_drop_dup_gates(self) -> None:
        result = build_comparison(
            {"obs_cpu_pct": 85.95, "system_busy_pct": 99.97},
            {
                "encoder_cpu_pct": 45.0,
                "system_busy_pct": 99.45,
                "ffmpeg_fps": 29.81,
                "ffmpeg_speed": 0.995,
                "game_fps_mean": 25.353,
                "drop_frames": 33,
                "dup_frames": 35,
                "encoded_frames": 899,
                "exit_code": 0,
            },
            "actual2",
        )

        self.assertTrue(result["cpu_acceptance_20pct"])
        self.assertTrue(result["output_720p30_acceptance"])
        self.assertFalse(result["content_30fps_acceptance"])
        self.assertFalse(result["drop_dup_1pct_acceptance"])
        self.assertFalse(result["short_benchmark_acceptance"])

    def test_missing_or_boolean_metrics_fail_closed(self) -> None:
        result = build_comparison(
            {"obs_cpu_pct": True, "system_busy_pct": None},
            {
                "encoder_cpu_pct": 1,
                "ffmpeg_fps": True,
                "ffmpeg_speed": 1.0,
                "game_fps_mean": True,
                "drop_frames": True,
                "dup_frames": 0,
                "encoded_frames": 900,
                "exit_code": 0,
            },
            "native4",
        )

        self.assertFalse(result["cpu_acceptance_20pct"])
        self.assertFalse(result["output_720p30_acceptance"])
        self.assertFalse(result["content_30fps_acceptance"])
        self.assertFalse(result["drop_dup_1pct_acceptance"])
        self.assertFalse(result["short_benchmark_acceptance"])


if __name__ == "__main__":
    unittest.main()

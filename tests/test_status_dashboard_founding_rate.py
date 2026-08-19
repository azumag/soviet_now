import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import status_dashboard as sd


class CalcRussiaFoundingRateTest(unittest.TestCase):
    def test_window_math_and_delta(self):
        # recent window = games [401, 500]; prior window = games [301, 400]
        founding_games = [50, 310, 340, 450]
        result = sd.calc_russia_founding_rate(founding_games, 500, window=100)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["rate"], 1.0)
        self.assertEqual(result["prev_count"], 2)
        self.assertEqual(result["prev_rate"], 2.0)
        self.assertLess(result["delta"], 0)
        self.assertAlmostEqual(result["delta"], -1.0)

    def test_tie_delta_is_zero_not_none(self):
        # recent window = games [401, 500]; prior window = games [301, 400]
        founding_games = [350, 450]
        result = sd.calc_russia_founding_rate(founding_games, 500, window=100)
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["prev_count"], 1)
        self.assertEqual(result["delta"], 0.0)

    def test_none_history_yields_none(self):
        self.assertIsNone(sd.calc_russia_founding_rate(None, 500, window=100))

    def test_empty_history_is_not_none_and_yields_zero_rate(self):
        # [] (履歴ファイルはあるが建国0件) は None (履歴ファイル自体が無い) と区別される。
        result = sd.calc_russia_founding_rate([], 500, window=100)
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["rate"], 0.0)

    def test_zero_or_negative_window_yields_none(self):
        self.assertIsNone(sd.calc_russia_founding_rate([50], 500, window=0))
        self.assertIsNone(sd.calc_russia_founding_rate([50], 500, window=-1))

    def test_below_window_yields_none(self):
        self.assertIsNone(sd.calc_russia_founding_rate([50], 99, window=100))

    def test_exactly_at_window_boundary_is_shown(self):
        result = sd.calc_russia_founding_rate([1], 100, window=100)
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 1)

    def test_below_double_window_hides_trend_only(self):
        result = sd.calc_russia_founding_rate([120], 150, window=100)
        self.assertIsNotNone(result)
        self.assertEqual(result["count"], 1)
        self.assertIsNone(result["prev_rate"])
        self.assertIsNone(result["delta"])


class LoadRussiaFoundingGamesTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def test_missing_file_returns_none(self):
        def _test():
            self.assertIsNone(sd.load_russia_founding_games())
        self._run_in_tempdir(_test)

    def test_empty_file_returns_empty_list(self):
        def _test():
            Path("tmp/history").mkdir(parents=True)
            Path(sd.RUSSIA_CREATION_HISTORY_FILE).write_text("", encoding="utf-8")
            self.assertEqual(sd.load_russia_founding_games(), [])
        self._run_in_tempdir(_test)

    def test_dedups_and_skips_malformed_lines(self):
        def _test():
            Path("tmp/history").mkdir(parents=True)
            lines = [
                "2026-05-21T00:00:00+09:00\t2026-05-21 00:00 JST\t100\t9000\t250",
                "2026-05-21T00:00:00+09:00\t2026-05-21 00:00 JST\t100\t9000\t250",
                "2026-05-22T00:00:00+09:00\t2026-05-22 00:00 JST\t150\t8000\t200",
                "bad\tline\ttoo\tshort",
                "2026-05-23T00:00:00+09:00\t2026-05-23 00:00 JST\tNOTANUMBER\t8000\t200",
            ]
            Path(sd.RUSSIA_CREATION_HISTORY_FILE).write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertEqual(sd.load_russia_founding_games(), [100, 150])
        self._run_in_tempdir(_test)


class LoadGameCounterTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def test_reads_game_count_file(self):
        def _test():
            Path(sd.GAME_COUNT_FILE).write_text("742\n", encoding="utf-8")
            self.assertEqual(sd.load_game_counter(fallback=1), 742)
        self._run_in_tempdir(_test)

    def test_falls_back_when_missing(self):
        def _test():
            self.assertEqual(sd.load_game_counter(fallback=17), 17)
        self._run_in_tempdir(_test)

    def test_falls_back_when_zero_or_garbage(self):
        def _test():
            Path(sd.GAME_COUNT_FILE).write_text("not-a-number\n", encoding="utf-8")
            self.assertEqual(sd.load_game_counter(fallback=5), 5)
            Path(sd.GAME_COUNT_FILE).write_text("0\n", encoding="utf-8")
            self.assertEqual(sd.load_game_counter(fallback=5), 5)
        self._run_in_tempdir(_test)


class CurrentStrategyRunEntryRussiaCountTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def test_exposes_russia_count_for_matching_hash(self):
        def _test():
            Path("tmp/state").mkdir(parents=True)
            Path(sd.CURRENT_STRATEGY_RUN_FILE).write_text(
                json.dumps({"hash": "abc123", "scores": [100, 200, 300], "russia_count": 2}),
                encoding="utf-8",
            )
            entry = sd.get_current_strategy_run_entry("abc123")
            self.assertEqual(entry["russia_count"], 2)
            self.assertEqual(entry["n_roll"], 3)
        self._run_in_tempdir(_test)

    def test_hash_mismatch_yields_zero(self):
        def _test():
            Path("tmp/state").mkdir(parents=True)
            Path(sd.CURRENT_STRATEGY_RUN_FILE).write_text(
                json.dumps({"hash": "old_hash", "scores": [100], "russia_count": 5}),
                encoding="utf-8",
            )
            entry = sd.get_current_strategy_run_entry("new_hash")
            self.assertEqual(entry["russia_count"], 0)
        self._run_in_tempdir(_test)

    def test_missing_file_yields_zero(self):
        def _test():
            entry = sd.get_current_strategy_run_entry("some_hash")
            self.assertEqual(entry["russia_count"], 0)
        self._run_in_tempdir(_test)


class RenderHeaderFoundingRateTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def _assert_all_lines_fit(self, lines):
        for line in lines:
            width = sd.ansi_display_width(line)
            self.assertLessEqual(width, sd.W, msg=f"line exceeds W={sd.W} ({width}): {line!r}")

    def _plain(self, lines):
        # ANSI色コードが値の途中(例: "R:" と "6%" の間)に挟まることがあるため、
        # 文字列内容の assertion は色コードを剥がしてから行う。
        return sd.ANSI_RE.sub("", "\n".join(lines))

    def test_header_shows_both_founding_rate_fields_and_fits_width(self):
        def _test():
            Path("tmp/state").mkdir(parents=True)
            Path(sd.CURRENT_STRATEGY_RUN_FILE).write_text(
                json.dumps({"hash": "a1b2c3d4e5f6", "scores": list(range(1, 48)), "russia_count": 3}),
                encoding="utf-8",
            )
            russia_rate = {"window": 100, "count": 4, "rate": 4.0, "prev_count": 6, "prev_rate": 6.0, "delta": -2.0}
            lines = sd.render_header(
                [100] * 40,
                {"state": "MOVE", "score": 500, "pieces": [1, 2, 3]},
                "",
                "a1b2c3d4e5f6",
                "v1200",
                1400,
                0,
                0,
                {},
                {},
                russia_rate=russia_rate,
            )
            joined = self._plain(lines)
            # delta=-2.0 (rate 4% < prev 6%) → must render the down arrow, not just the number
            self.assertIn("Rus:4%▼", joined)
            # bare count, not a fraction/percent (see status_dashboard.py comment on run_rus_raw:
            # russia_count and n_roll can measure different populations, so no rate is shown here)
            self.assertIn("R:3", joined)
            self.assertNotIn("R:3%", joined)
            self._assert_all_lines_fit(lines)
        self._run_in_tempdir(_test)

    def test_no_arrow_when_delta_is_none(self):
        def _test():
            russia_rate = {"window": 100, "count": 1, "rate": 1.0, "prev_count": None, "prev_rate": None, "delta": None}
            lines = sd.render_header(
                [100] * 40,
                {"state": "MOVE", "score": 500, "pieces": []},
                "",
                "?",
                "?",
                0,
                0,
                0,
                {},
                {},
                russia_rate=russia_rate,
            )
            joined = self._plain(lines)
            # exact match: no trailing arrow char when delta is None (unknown data, not a real tie)
            self.assertIn("Rus:1%", joined)
            for arrow in ("▲", "▼", "="):
                self.assertNotIn(f"Rus:1%{arrow}", joined)
            self._assert_all_lines_fit(lines)
        self._run_in_tempdir(_test)

    def test_flat_trend_renders_equals_arrow(self):
        def _test():
            russia_rate = {"window": 100, "count": 2, "rate": 2.0, "prev_count": 2, "prev_rate": 2.0, "delta": 0.0}
            lines = sd.render_header(
                [100] * 40,
                {"state": "MOVE", "score": 500, "pieces": []},
                "",
                "?",
                "?",
                0,
                0,
                0,
                {},
                {},
                russia_rate=russia_rate,
            )
            joined = self._plain(lines)
            self.assertIn("Rus:2%=", joined)
        self._run_in_tempdir(_test)

    def test_strategy_row_drops_founding_rate_while_improve_is_active(self):
        def _test():
            # "alive" は既存コードで Imp サフィックスが最短になるケースで、
            # Strategy本体+Impだけで inner ちょうど(=54)を使い切る(実測済み)。
            # このケースで R: が正しく落ちることを確認する。
            # ("state_activity_fresh"/"stale" は Imp サフィックス自体が長く、
            # 本変更前から inner を超過することがある既存の別問題であり対象外)
            Path("tmp/state").mkdir(parents=True)
            Path(sd.CURRENT_STRATEGY_RUN_FILE).write_text(
                json.dumps({"hash": "a1b2c3d4e5f6", "scores": list(range(1, 48)), "russia_count": 3}),
                encoding="utf-8",
            )
            improve = {
                "status": "running",
                "alive": True,
                "phase": "wildcard_parallel",
                "progress": 100,
            }
            lines = sd.render_header(
                [100] * 300,
                {"state": "MOVE", "score": 500, "pieces": [1] * 20},
                "",
                "a1b2c3d4e5f6",
                "v1200",
                1400,
                999,
                0,
                improve,
                {},
                russia_rate=None,
            )
            strategy_line = next(l for l in lines if "Strategy:" in l)
            plain_strategy_line = sd.ANSI_RE.sub("", strategy_line)
            self.assertNotIn("R:", plain_strategy_line)
            self.assertIn("Imp:", plain_strategy_line)
            self._assert_all_lines_fit(lines)
        self._run_in_tempdir(_test)

    def test_backward_compatible_with_ten_positional_args(self):
        def _test():
            lines = sd.render_header(
                [],
                {"state": "STOP", "score": 0, "pieces": []},
                "",
                "?",
                "?",
                0,
                0,
                0,
                {},
                {},
            )
            self.assertTrue(lines)
        self._run_in_tempdir(_test)


if __name__ == "__main__":
    unittest.main()

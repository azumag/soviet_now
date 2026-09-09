import json
import os
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import status_dashboard as sd


def _game_row(idx, arm, eval_score=None, score=None, tainted=False):
    row = {"idx": idx, "arm": arm, "tainted": tainted}
    if eval_score is not None:
        row["eval"] = eval_score
    if score is not None:
        row["score"] = score
    return row


class LoadAbProgressTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def _paths(self):
        return (
            str(Path("tmp/state/ab_state.json")),
            str(Path("tmp/state/ab_games.jsonl")),
            str(Path("tmp/state/ab_candidate/meta.json")),
        )

    def test_no_files_yields_none(self):
        def _test():
            state, games, meta = self._paths()
            self.assertIsNone(sd.load_ab_progress(state, games, meta))

        self._run_in_tempdir(_test)

    def test_candidate_only_without_state(self):
        def _test():
            state, games, meta = self._paths()
            Path("tmp/state/ab_candidate").mkdir(parents=True)
            Path(meta).write_text(
                json.dumps({"base_hash": "a" * 40, "cand_hash": "b" * 40}),
                encoding="utf-8",
            )
            result = sd.load_ab_progress(state, games, meta)
            self.assertIsNotNone(result)
            self.assertFalse(result["active"])
            self.assertEqual(result["candidate"]["cand"], "b" * 40)

        self._run_in_tempdir(_test)

    def test_active_counts_means_and_diff(self):
        def _test():
            state, games, meta = self._paths()
            Path("tmp/state").mkdir(parents=True)
            Path(state).write_text(
                json.dumps(
                    {
                        "a_hash": "a" * 40,
                        "b_hash": "b" * 40,
                        "pattern": "ABBA",
                        "games_recorded": 5,
                    }
                ),
                encoding="utf-8",
            )
            rows = [
                _game_row(0, "A", eval_score=1000),
                _game_row(1, "B", eval_score=1200),
                _game_row(2, "B", eval_score=1400),
                _game_row(3, "A", eval_score=1100),
                # tainted と idx 重複は tools/ab_report.py と同じ規則で除外する。
                _game_row(4, "B", eval_score=9999, tainted=True),
                _game_row(1, "B", eval_score=9999),
            ]
            Path(games).write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
            result = sd.load_ab_progress(state, games, meta)
            self.assertTrue(result["active"])
            self.assertEqual(result["n"], 4)
            self.assertEqual(result["n_a"], 2)
            self.assertEqual(result["n_b"], 2)
            self.assertAlmostEqual(result["mean_a"], 1050.0)
            self.assertAlmostEqual(result["mean_b"], 1300.0)
            self.assertAlmostEqual(result["diff"], 250.0)
            self.assertEqual(result["tainted"], 1)

        self._run_in_tempdir(_test)

    def test_score_fallback_when_eval_missing(self):
        def _test():
            state, games, meta = self._paths()
            Path("tmp/state").mkdir(parents=True)
            Path(state).write_text(
                json.dumps({"a_hash": "a", "b_hash": "b", "pattern": "AB"}),
                encoding="utf-8",
            )
            rows = [
                _game_row(0, "A", score=500),
                _game_row(1, "B", score=700),
            ]
            Path(games).write_text(
                "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
            )
            result = sd.load_ab_progress(state, games, meta)
            self.assertAlmostEqual(result["diff"], 200.0)

        self._run_in_tempdir(_test)

    def test_broken_state_yields_none(self):
        def _test():
            state, games, meta = self._paths()
            Path("tmp/state").mkdir(parents=True)
            Path(state).write_text(json.dumps({"a_hash": "a"}), encoding="utf-8")
            self.assertIsNone(sd.load_ab_progress(state, games, meta))
            Path(state).write_text("not json", encoding="utf-8")
            self.assertIsNone(sd.load_ab_progress(state, games, meta))

        self._run_in_tempdir(_test)


class RenderHeaderAbRowTest(unittest.TestCase):
    def _run_in_tempdir(self, fn):
        import tempfile

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
        return sd.ANSI_RE.sub("", "\n".join(lines))

    def _base_kwargs(self):
        Path("tmp/state").mkdir(parents=True)
        Path(sd.CURRENT_STRATEGY_RUN_FILE).write_text(
            json.dumps({"hash": "a1b2c3d4e5f6", "scores": list(range(1, 48))}),
            encoding="utf-8",
        )
        return dict(
            scores=[100] * 40,
            game_state={"state": "MOVE", "score": 500, "pieces": [1, 2, 3]},
            latest_drop="",
            strat_hash="a1b2c3d4e5f6",
            strat_ver="v1200",
            strat_lines=1400,
            rejected=0,
            accumulated=0,
            improve={},
            rolling={},
        )

    def test_active_ab_row_shows_hashes_counts_and_diff(self):
        def _test():
            kwargs = self._base_kwargs()
            ab_status = {
                "active": True,
                "a_hash": "3a9bd96b76a0",
                "b_hash": "015aa63973ac",
                "pattern": "ABBA",
                "n": 53,
                "n_a": 27,
                "n_b": 26,
                "mean_a": 1000.0,
                "mean_b": 1120.0,
                "diff": 120.0,
                "tainted": 0,
                "candidate": None,
            }
            lines = sd.render_header(russia_rate=None, ab_status=ab_status, **kwargs)
            joined = self._plain(lines)
            self.assertIn("A/B: A 3a9bd96b vs B 015aa639", joined)
            self.assertIn("n=53(A27/B26)", joined)
            self.assertIn("d=+120", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)

    def test_no_ab_status_renders_no_ab_row(self):
        def _test():
            kwargs = self._base_kwargs()
            lines = sd.render_header(russia_rate=None, ab_status=None, **kwargs)
            joined = self._plain(lines)
            self.assertNotIn("A/B:", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)

    def test_candidate_row_when_not_active(self):
        def _test():
            kwargs = self._base_kwargs()
            ab_status = {
                "active": False,
                "candidate": {"base": "a" * 12, "cand": "b" * 12},
            }
            lines = sd.render_header(russia_rate=None, ab_status=ab_status, **kwargs)
            joined = self._plain(lines)
            self.assertIn("A/B: candidate B bbbbbbbb ready", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)

    def test_header_still_shows_trend_and_russia_rate(self):
        # c1e000122 の退行防止: ヘッダーに Trend と全体建国率が残ること。
        def _test():
            kwargs = self._base_kwargs()
            russia_rate = {
                "window": 100,
                "count": 4,
                "rate": 4.0,
                "prev_count": 6,
                "prev_rate": 6.0,
                "delta": -2.0,
            }
            lines = sd.render_header(
                russia_rate=russia_rate, ab_status=None, **kwargs
            )
            joined = self._plain(lines)
            self.assertIn("Recent30:", joined)
            self.assertIn("Trend:", joined)
            self.assertIn("Rus:4%▼", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)


if __name__ == "__main__":
    unittest.main()

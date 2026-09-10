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

    def test_strategy_row_shows_played_arm_during_ab(self):
        """A/B 中は root ではなく実際に打っている腕のハッシュを出す。"""
        from unittest import mock

        def _test():
            kwargs = self._base_kwargs()
            kwargs["strat_hash"] = "3a9bd96b76a0"
            Path("strategy.py.game_snapshot").write_text("# snap\n")
            ab_status = {
                "active": True, "a_hash": "3a9bd96b76a0", "b_hash": "d0188f418f58",
                "n": 22, "n_a": 11, "n_b": 11, "mean_a": 13191.0, "mean_b": 14481.0,
                "diff": 899.0, "tainted": 0, "candidate": None,
            }
            with mock.patch.object(sd, "compute_decide_hash", return_value="d0188f418f58"):
                lines = sd.render_header(russia_rate=None, ab_status=ab_status, **kwargs)
            joined = self._plain(lines)
            self.assertIn("Strategy: d0188f41 [B]", joined)
            self.assertNotIn("Strategy: 3a9bd96b ", joined)
            self._assert_all_lines_fit(lines)

            with mock.patch.object(sd, "compute_decide_hash", return_value="3a9bd96b76a0"):
                lines = sd.render_header(russia_rate=None, ab_status=ab_status, **kwargs)
            self.assertIn("Strategy: 3a9bd96b [A]", self._plain(lines))
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)

    def test_strategy_row_unchanged_without_ab(self):
        """A/B 非稼働では従来どおり root のハッシュのみ (腕タグを出さない)。"""
        def _test():
            kwargs = self._base_kwargs()
            kwargs["strat_hash"] = "3a9bd96b76a0"
            lines = sd.render_header(russia_rate=None, ab_status=None, **kwargs)
            joined = self._plain(lines)
            self.assertIn("Strategy: 3a9bd96b", joined)
            self.assertNotIn("[A]", joined)
            self.assertNotIn("[B]", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)


class PlayedAbArmTest(unittest.TestCase):
    """A/B 中のヘッダーは「いま実際に打っている腕」を出す。

    root の strategy.py は B 腕の試合でも書き換わらないため、それだけを見ていると
    A/B 中ずっと A のハッシュが出て実際の対局と食い違う (2026-09-10 にユーザーが
    ステータスバーの Strategy が変わらないと指摘して発覚)。判定根拠は推定ではなく、
    試合ごとに書かれる game_snapshot の decide hash。
    """

    A = "3a9bd96b76a0"
    B = "d0188f418f58"

    def _ab(self, active=True):
        return {"active": active, "a_hash": self.A, "b_hash": self.B}

    def _with_snapshot(self, hash_value, ab=None, exists=True):
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / "strategy.py.game_snapshot"
            if exists:
                snap.write_text("# snapshot\n")
            with mock.patch.object(sd, "compute_decide_hash", return_value=hash_value):
                return sd.played_ab_arm(self._ab() if ab is None else ab, str(snap))

    def test_b_arm_is_reported_from_snapshot(self):
        self.assertEqual(self._with_snapshot(self.B), (self.B, "B"))

    def test_a_arm_is_reported_from_snapshot(self):
        self.assertEqual(self._with_snapshot(self.A), (self.A, "A"))

    def test_inactive_ab_reports_nothing(self):
        ab = {"active": False, "a_hash": self.A, "b_hash": self.B}
        self.assertEqual(self._with_snapshot(self.B, ab=ab), ("", ""))

    def test_missing_snapshot_reports_nothing(self):
        self.assertEqual(self._with_snapshot(self.B, exists=False), ("", ""))

    def test_unknown_hash_reports_nothing_rather_than_guessing(self):
        self.assertEqual(self._with_snapshot("deadbeefcafe"), ("", ""))

    def test_empty_hash_reports_nothing(self):
        self.assertEqual(self._with_snapshot(""), ("", ""))

    def test_non_dict_status_is_safe(self):
        self.assertEqual(sd.played_ab_arm(None), ("", ""))
        self.assertEqual(sd.played_ab_arm({}), ("", ""))


class TopPanelModeTest(unittest.TestCase):
    """トップパネルは表示面ごとに出し分ける。

    show-status-g のターミナル表示は AI backoff だけに絞る (c1e000122 の意図) が、
    配信オーバーレイは A/B の進捗を出したいので render_header のままにする。
    既定を header 側にして、絞る面だけが環境変数で opt-in する。
    """

    def test_default_is_header(self):
        import os
        from unittest import mock

        for value in ("", "   ", "bogus", "HEADER"):
            with mock.patch.dict(os.environ, {"STATUS_DASHBOARD_TOP_PANEL": value}):
                self.assertEqual(sd.top_panel_mode(), "header", value)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("STATUS_DASHBOARD_TOP_PANEL", None)
            self.assertEqual(sd.top_panel_mode(), "header")

    def test_ai_backoff_is_opt_in(self):
        import os
        from unittest import mock

        for value in ("ai_backoff", "AI_BACKOFF", " ai_backoff "):
            with mock.patch.dict(os.environ, {"STATUS_DASHBOARD_TOP_PANEL": value}):
                self.assertEqual(sd.top_panel_mode(), "ai_backoff", value)

    def test_show_status_g_opts_into_ai_backoff(self):
        """ターミナル側が確実に opt-in していること (ここが外れると元の指摘が再発する)。"""
        script = (REPO_ROOT / "show_status_g.sh").read_text()
        self.assertIn("STATUS_DASHBOARD_TOP_PANEL=ai_backoff python3 status_dashboard.py", script)

    def test_overlays_keep_the_header(self):
        """配信オーバーレイは既定のまま = A/B 進捗が出る。"""
        for name in ("generate_status_overlay.sh", "generate_soren_overlay.sh"):
            script = (REPO_ROOT / name).read_text()
            self.assertIn("python3 status_dashboard.py", script)
            self.assertNotIn("STATUS_DASHBOARD_TOP_PANEL", script)


class AbRemainingGamesTest(unittest.TestCase):
    """「あと何試合か」の算出。判定側 (tools/ab_decide.py) と同じ数でなければ嘘になる。"""

    def _run_in_tempdir(self, fn):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            old_cwd = os.getcwd()
            os.chdir(tmp)
            try:
                fn()
            finally:
                os.chdir(old_cwd)

    def _write_ab(self, rows, pattern="ABBA", env_text=None):
        Path("tmp/state").mkdir(parents=True, exist_ok=True)
        state = Path("tmp/state/ab_state.json")
        games = Path("tmp/state/ab_games.jsonl")
        state.write_text(
            json.dumps({"a_hash": "a" * 40, "b_hash": "b" * 40, "pattern": pattern}),
            encoding="utf-8",
        )
        games.write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
        )
        env = Path("tmp/state/env")
        if env_text is not None:
            env.write_text(env_text, encoding="utf-8")
        return str(state), str(games), "tmp/state/none.json", str(env)

    def _complete_blocks(self, count, pattern="ABBA"):
        rows = []
        for block in range(count):
            for offset, arm in enumerate(pattern):
                idx = block * len(pattern) + offset
                rows.append(_game_row(idx, arm, eval_score=1000, score=1000))
        return rows

    def test_defaults_match_ab_decide(self):
        """looks / max_blocks が tools/ab_decide.py の DEFAULTS と一致していること。

        ここがズレると残り試合数の表示だけが実際の判定と食い違う。
        """
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        try:
            import ab_decide
        finally:
            sys.path.pop(0)
        self.assertEqual(
            tuple(ab_decide.DEFAULTS["looks"]), tuple(sd.AB_DEFAULT_LOOKS)
        )
        self.assertEqual(
            int(ab_decide.DEFAULTS["max_blocks"]), int(sd.AB_DEFAULT_MAX_BLOCKS)
        )

    def test_block_count_matches_ab_decide_on_same_rows(self):
        """k が判定側と同じ数であること。

        「完全ブロック」の定義 (idx 重複除去・idx 連続・腕構成一致) は
        tools/ab_report.py にしかない。写して持つとズレるので同じ関数を呼ぶ。
        """
        sys.path.insert(0, str(REPO_ROOT / "tools"))
        try:
            import ab_report
        finally:
            sys.path.pop(0)
        cases = {
            "完全3ブロック": self._complete_blocks(3),
            "idx 重複": self._complete_blocks(3) + [_game_row(0, "A", score=1000)],
            "欠けブロック": self._complete_blocks(2) + [_game_row(8, "A", score=1000)],
            "tainted 混入": self._complete_blocks(2)
            + [_game_row(8, "A", score=1, tainted=True)],
            "腕構成が偏る (AAAB)": self._complete_blocks(1)
            + [
                _game_row(4, "A", score=1000),
                _game_row(5, "A", score=1000),
                _game_row(6, "A", score=1000),
                _game_row(7, "B", score=1000),
            ],
        }
        for name, rows in cases.items():
            expected = len(ab_report.blocks(rows, "ABBA", key="score"))
            self.assertEqual(sd.ab_complete_blocks(rows, "ABBA"), expected, msg=name)

    def test_block_count_is_none_when_ab_report_unavailable(self):
        """tools/ が読めない環境では残り試合数を出さない (でっち上げない)。"""
        saved = sd._AB_REPORT
        sd._AB_REPORT = None
        try:
            self.assertIsNone(sd.ab_complete_blocks(self._complete_blocks(3), "ABBA"))
        finally:
            sd._AB_REPORT = saved

    def test_progress_reports_next_look_and_max(self):
        def _test():
            state, games, meta, env = self._write_ab(self._complete_blocks(8))
            ab = sd.load_ab_progress(state, games, meta, env_path=env)
            self.assertEqual(ab["blocks"], 8)
            self.assertEqual(ab["next_look"], 19)
            self.assertEqual(ab["games_to_next_look"], (19 - 8) * 4)
            self.assertEqual(ab["games_to_max"], (37 - 8) * 4)

        self._run_in_tempdir(_test)

    def test_env_overrides_are_read_from_env_file_like_the_gate(self):
        """ゲート (strategy/ab_interleave.sh:_ab_env_value) と同じく .env を見る。"""

        def _test():
            state, games, meta, env = self._write_ab(
                self._complete_blocks(4),
                env_text="AB_GATE_LOOKS=10,20\nAB_GATE_MAX_BLOCKS=20\n",
            )
            ab = sd.load_ab_progress(state, games, meta, env_path=env)
            self.assertEqual(ab["looks"], (10, 20))
            self.assertEqual(ab["max_blocks"], 20)
            self.assertEqual(ab["next_look"], 10)
            self.assertEqual(ab["games_to_next_look"], (10 - 4) * 4)
            self.assertEqual(ab["games_to_max"], (20 - 4) * 4)

        self._run_in_tempdir(_test)

    def test_env_last_match_wins_and_quotes_stripped(self):
        def _test():
            state, games, meta, env = self._write_ab(
                self._complete_blocks(1),
                env_text='AB_GATE_MAX_BLOCKS=99\nAB_GATE_MAX_BLOCKS="12"\n',
            )
            ab = sd.load_ab_progress(state, games, meta, env_path=env)
            self.assertEqual(ab["max_blocks"], 12)

        self._run_in_tempdir(_test)

    def test_past_final_look_reports_no_adopt_look_left(self):
        def _test():
            state, games, meta, env = self._write_ab(self._complete_blocks(37))
            ab = sd.load_ab_progress(state, games, meta, env_path=env)
            self.assertEqual(ab["blocks"], 37)
            self.assertIsNone(ab["next_look"])
            self.assertIsNone(ab["games_to_next_look"])
            self.assertEqual(ab["games_to_max"], 0)
            self.assertIn("no adopt-look left", sd._ab_remaining_line(ab))

        self._run_in_tempdir(_test)

    def test_remaining_line_is_empty_when_not_active(self):
        self.assertEqual(sd._ab_remaining_line(None), "")
        self.assertEqual(sd._ab_remaining_line({"active": False}), "")
        self.assertEqual(sd._ab_remaining_line({"active": True}), "")


class RenderHeaderAbRemainingRowTest(RenderHeaderAbRowTest):
    """残り試合数の行が実際に枠内へ出ること (A/B 行に足すと W=57 を超えるため別行)。"""

    def _ab(self, **over):
        ab = {
            "active": True,
            "a_hash": "3a9bd96b76a0",
            "b_hash": "d0188f418f58",
            "pattern": "ABBA",
            "n": 35,
            "n_a": 17,
            "n_b": 18,
            "mean_a": 1000.0,
            "mean_b": 900.0,
            "diff": -100.0,
            "tainted": 0,
            "candidate": None,
            "blocks": 8,
            "looks": (19, 37),
            "max_blocks": 37,
            "next_look": 19,
            "games_to_next_look": 44,
            "games_to_max": 116,
        }
        ab.update(over)
        return ab

    def test_remaining_row_is_rendered_and_fits(self):
        def _test():
            kwargs = self._base_kwargs()
            lines = sd.render_header(russia_rate=None, ab_status=self._ab(), **kwargs)
            joined = self._plain(lines)
            self.assertIn("k8/19", joined)
            self.assertIn("adopt-look in 44g", joined)
            self.assertIn("max 116g", joined)
            # A/B 行の中身は落ちていない
            self.assertIn("A/B: A 3a9bd96b vs B d0188f41", joined)
            self.assertIn("n=35(A17/B18)", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)

    def test_no_remaining_row_when_only_candidate_is_pending(self):
        def _test():
            kwargs = self._base_kwargs()
            ab = {
                "active": False,
                "candidate": {"base": "a" * 12, "cand": "b" * 12},
            }
            lines = sd.render_header(russia_rate=None, ab_status=ab, **kwargs)
            joined = self._plain(lines)
            self.assertIn("candidate B bbbbbbbb ready", joined)
            self.assertNotIn("adopt-look", joined)
            self._assert_all_lines_fit(lines)

        self._run_in_tempdir(_test)


if __name__ == "__main__":
    unittest.main()

import json
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.docich_corner_stats import load_active_corner
import status_dashboard as sd


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _epoch(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


class DocichCornerStatsTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self):
        self.tempdir.cleanup()

    def _state(self, name: str, **extra) -> None:
        value = {"schema_version": 1, "status": "active", **extra}
        _write_json(self.root / name, value)

    def test_idle_corner_keeps_normal_mode_and_does_not_read_soren_history(self):
        (self.root / "score_history.txt").write_text("999999\n", encoding="utf-8")
        self._state("retro_corner.json", status="completed", game="robots")
        self.assertIsNone(load_active_corner(self.root))

    def test_retro_history_keeps_same_game_across_visits_and_counts_session(self):
        started = "2026-09-21T00:00:00+00:00"
        self._state(
            "retro_corner.json",
            game="ninvaders",
            started_at=started,
            target_matches=3,
        )
        log = self.root / "scores/ninvaders.jsonl"
        log.parent.mkdir(parents=True)
        rows = [
            {"ts": _epoch("2026-09-20T23:59:00"), "game": "ninvaders", "score": 100},
            {"ts": _epoch("2026-09-21T00:01:00"), "game": "ninvaders", "score": 10},
            {"ts": _epoch("2026-09-21T00:02:00"), "game": "ninvaders", "score": 20},
            {"ts": _epoch("2026-09-21T00:03:00"), "game": "ninvaders", "score": 30},
        ]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        _write_json(
            self.root / "resolver/unused.json",
            {"not": "used"},
        )
        improve = self.root / "resolver/improve_log.jsonl"
        improve.parent.mkdir(parents=True, exist_ok=True)
        improve.write_text(
            json.dumps(
                {
                    "game": "ninvaders",
                    "promoted": True,
                    "best_strategy_key": "not-used-directly",
                    "trials": [{"strategy": {"mode": "safe"}, "mean_score": 20}],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "retro")
        self.assertEqual([row["score"] for row in corner["scores"]], [100, 10, 20, 30])
        self.assertEqual(corner["session_matches"], 3)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Last8: 100 10 20 30", rendered)
        self.assertIn("this corner matches 3/3", rendered)
        self.assertIn("mean=40 median=25", rendered)
        self.assertIn("Strategy: #1", rendered)
        self.assertNotIn("999999", rendered)

    def test_paper_corner_exposes_fill_history_not_game_score(self):
        self._state("paper_corner.json")
        _write_json(
            self.root / "trading/status.json",
            {
                "worker_state": "running",
                "capital_reference": "10000",
                "deployed_reference": "3000",
                "open_positions": {"btc_jpy": "0.001"},
                "recent_fills": [
                    {"symbol": "btc_jpy", "side": "buy", "quote_notional": "3000"},
                    {"symbol": "btc_jpy", "side": "sell", "quote_notional": "3100"},
                ],
            },
        )
        corner = load_active_corner(self.root)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Funds: capital=10000 deployed=3000", rendered)
        self.assertIn("btc_jpy SELL 3100", rendered)
        self.assertIn("btc_jpy BUY 3000", rendered)
        self.assertNotIn("Score Timeline", rendered)

    def test_nethack_corner_uses_run_history(self):
        self._state(
            "nethack_corner.json",
            game="nethack",
            run_id="00000000-0000-4000-8000-000000000001",
            run_status="active",
            run_score=42,
            run_turns=120,
            run_max_depth=4,
        )
        _write_json(self.root / "nethack/current.json", {"schema_version": 1, "run_id": "00000000-0000-4000-8000-000000000001"})
        _write_json(self.root / "nethack/runs/00000000-0000-4000-8000-000000000001.json", {"run_id": "00000000-0000-4000-8000-000000000001", "expedition": 7, "status": "active", "score": 42, "turns": 120, "max_depth": 4})
        _write_json(self.root / "nethack/runs/old.json", {"score": 12, "status": "dead", "last_finished_at": "2026-09-20T00:00:00+00:00"})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["scores"], [12])
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("expedition 7", rendered)
        self.assertIn("score=42", rendered)

    def test_soren91_reads_confirmed_rank_history(self):
        self._state("soren91_corner.json", game="soren91", started_at=200)
        for i, rank in enumerate([12, 4, 1, None, -1, 92, True]):
            _write_json(self.root / f"soren91/tmp/summaries/game_{i:04}.json",
                        {"rank": rank, "timestamp": 100 + i * 100})
        corner = load_active_corner(self.root, soren_root=self.root)
        self.assertEqual([r["score"] for r in corner["scores"]], [12, 4, 1])
        self.assertEqual(corner["session_matches"], 2)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Rank Timeline", rendered)
        self.assertIn("Rank Distribution", rendered)
        self.assertIn("best=1", rendered)
        self.assertIn("wins=1 / lower rank is better", rendered)
        plot, colors = sd._render_timeline_grid([50, 1], 5, 3, 1, 50, lower_is_better=True)
        self.assertEqual(plot[0][-1], "*")
        self.assertEqual(colors[0][-1], sd.gradient_color(50, 1, 50))

    def test_jev_reads_own_reports_not_mixed_soren_history(self):
        self._state("jev_corner.json", game="sorengame", policy="jev", player_generation=3)
        (self.root / "score_history.txt").write_text("999999\n")
        _write_json(self.root / "tmp/jev_player/runs/run-1/report.json",
                    {"status": "completed", "finished_at": 100, "summary": {"score": 80}})
        corner = load_active_corner(self.root, soren_root=self.root)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("policy=jev generation=3", rendered)
        self.assertIn("1 reports / best=80", rendered)
        self.assertIn("may include interrupted runs", rendered)
        self.assertNotIn("999999", rendered)

    def test_multiple_active_corners_fail_closed(self):
        self._state("retro_corner.json", game="robots")
        self._state("paper_corner.json")
        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "conflict")
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("STATE CONFLICT", rendered)
        self.assertNotIn("Score Distribution", rendered)

    def test_retro_filters_other_games_and_bad_rows_but_keeps_long_history(self):
        self._state("retro_corner.json", game="robots", started_at=200)
        log = self.root / "scores/robots.jsonl"
        log.parent.mkdir()
        rows = [{"game": "robots", "score": i, "ts": i} for i in range(250)]
        rows += [{"game": "other", "score": 999999, "ts": 300},
                 {"game": "robots", "score": True}, {"game": "robots", "score": "NaN"}]
        log.write_text("\n".join(json.dumps(row) for row in rows) + "\n{partial")
        corner = load_active_corner(self.root)
        self.assertEqual(len(corner["scores"]), 250)
        self.assertEqual(corner["session_matches"], 50)
        text = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("250 results / best=249", text)
        self.assertIn("mean=124.5 median=124.5", text)
        self.assertIn("(last 100 games; old -> now)", text)
        self.assertNotIn("999999", text)

    def test_nethack_completed_current_run_is_counted_once(self):
        run_id = "00000000-0000-4000-8000-000000000001"
        self._state("nethack_corner.json", run_id=run_id)
        _write_json(self.root / "nethack/current.json", {"schema_version": 1, "run_id": run_id})
        _write_json(self.root / f"nethack/runs/{run_id}.json",
                    {"run_id": run_id, "expedition": 7, "status": "dead", "score": 42,
                     "last_finished_at": 200})
        _write_json(self.root / "nethack/runs/older.json",
                    {"status": "ascended", "score": 100, "last_finished_at": 100})
        _write_json(self.root / "nethack/runs/suspended.json",
                    {"status": "suspended", "score": 123456})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["scores"], [100, 42])
        self.assertEqual(corner["run"]["expedition"], 7)

    def test_nethack_does_not_read_a_different_current_run(self):
        self._state("nethack_corner.json", run_id="expected", run_score=10)
        _write_json(self.root / "nethack/current.json", {"run_id": "../../unrelated"})
        self.assertEqual(load_active_corner(self.root)["run"]["score"], 10)

    def test_distribution_covers_small_large_negative_and_constant_scores(self):
        for scores in ([0, 1, 2, 3, 4, 5], [100000, 200000], [-10, -5, 0], [7, 7]):
            with self.subTest(scores=scores):
                lines = sd.render_corner_distribution(scores)
                counts = [int(re.sub(r"\x1b\[[0-9;]*m", "", line).split()[-1]) for line in lines[1:]]
                self.assertEqual(sum(counts), len(scores))
                self.assertLessEqual(max(sd.ansi_display_width(line) for line in lines), sd.W)
        small = "\n".join(sd.render_corner_distribution([0, 1, 2, 3, 4, 5]))
        self.assertNotIn("500", small)

    def test_one_or_two_results_are_visible_and_empty_is_explicit(self):
        for scores in ([1], [1, 2]):
            text = "\n".join(sd._corner_score_panels(scores))
            self.assertIn("Score Timeline", text)
            self.assertNotIn("not enough data", text)
            self.assertIn("*", text)
        self.assertIn("no completed results", "\n".join(sd._corner_score_panels([])))

    def _render_html(self, raw):
        script = (REPO_ROOT / "generate_status_overlay.sh").read_text()
        python = script.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        output = self.root / "overlay.html"
        subprocess.run([sys.executable, "-", str(output), "560", "820"],
                       input=python, text=True, cwd=REPO_ROOT, check=True,
                       env={**os.environ, "STATUS_OVERLAY_RAW": raw})
        return output.read_text()

    def test_html_does_not_hide_corner_graph_after_closed_backoff_box(self):
        backoff = ["┌── AI BACKOFF ──┐", "│ no backoff    │", "└───────────────┘", ""]
        corner = {"kind": "retro", "label": "RETRO", "status": "active", "game": "robots",
                  "scores": [{"score": score} for score in [2, 10, 5, 30]],
                  "session_matches": 4, "target_matches": 4}
        raw = "\n".join(backoff + sd.render_docich_corner_stats(corner))
        html = self._render_html(raw)
        self.assertNotIn('<span class="rail-only">', html)
        self.assertIn("Score Timeline", html)
        self.assertIn("Stats: 4 results", html)

    def test_html_still_hides_regular_soren_header_box(self):
        html = self._render_html("┌───────────────┐\n│ SOREN/OBS    │\n└───────────────┘\nScore Timeline")
        self.assertIn('<span class="rail-only">', html)
        self.assertIn('</span>\nScore Timeline', html)

    def test_bad_active_schema_fails_closed(self):
        _write_json(self.root / "retro_corner.json", {"schema_version": 99, "status": "active", "game": "robots"})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "invalid")
        self.assertIn("STATE INVALID", "\n".join(sd.render_docich_corner_stats(corner)))

    def test_retro_render_stays_within_dashboard_width(self):
        self._state("retro_corner.json", game="moon-buggy", target_matches=3)
        log = self.root / "scores/moon-buggy.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text(
            "\n".join(json.dumps({"ts": 10 + i, "game": "moon-buggy", "score": i * 100}) for i in range(1, 8)),
            encoding="utf-8",
        )
        corner = load_active_corner(self.root)
        for line in sd.render_docich_corner_stats(corner):
            self.assertLessEqual(sd.ansi_display_width(line), sd.W)

    def test_dashboard_main_switches_to_corner_feed_before_soren_history(self):
        corner = {
            "kind": "soren91",
            "label": "SOREN91",
            "status": "active",
            "game": "soren91",
        }
        output = io.StringIO()
        with patch.object(sd, "load_active_corner", return_value=corner), patch.object(
            sd, "load_scores", side_effect=AssertionError("Soren history must not be loaded")
        ), patch.object(sd, "render_ai_backoff_header", return_value=[]), redirect_stdout(output):
            sd.main()
        self.assertIn("SOREN/CORNER: SOREN91", output.getvalue())


if __name__ == "__main__":
    unittest.main()

class HanjukuStatusTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = dict(game='hanjuku-hero', runtime_id='g1-abcdef12', generation=1, lease_id='lease')
        self.runtime = self.root / 'runtimes' / self.identity['runtime_id']
        self.state = dict(schema_version=1, status='active', game='hanjuku-hero', bot_identity=self.identity)
        self.write()

    def tearDown(self):
        self.temp.cleanup()

    def write(self):
        import time
        _write_json(self.root / 'retro_corner.json', self.state)
        _write_json(self.root / 'game_switch.json', dict(phase='ready', active=self.identity))
        _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, observed_at=time.time(), phase='field', actions_sent=7))
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=self.identity, screen_kind='world_map', policy=dict(chapter=2, gold=123, captured=['城'], active='F3', stats=dict(wins=2, losses=1))))

    def snapshot(self):
        return load_active_corner(self.root)

    def test_cached_observations_render_without_score_inference(self):
        corner = self.snapshot()
        self.assertEqual(corner['hanjuku']['availability'], 'fresh')
        rendered = '\n'.join(sd.render_docich_corner_stats(corner))
        self.assertIn('第2話', rendered)
        self.assertIn('123G', rendered)
        self.assertIn('現在の城数ではない', rendered)
        self.assertIn('計画段階: F3（完了未確認）', rendered)
        self.assertIn('実入力: 7回', rendered)
        self.assertIn('将軍HP・卵状態: 未確認', rendered)

    def test_previous_generation_is_unavailable(self):
        changed = dict(self.identity, generation=2, runtime_id='g2-abcdef12')
        _write_json(self.root / 'game_switch.json', dict(phase='ready', active=changed))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_stale_and_future_observation_are_not_live(self):
        import time
        for stamp in (time.time() - 60, time.time() + 60, True, float('nan')):
            with self.subTest(stamp=stamp):
                _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, observed_at=stamp))
                self.assertEqual(self.snapshot()['hanjuku']['availability'], 'stale')

    def test_terminal_malformed_and_symlink_fail_closed(self):
        _write_json(self.runtime / 'hanjuku_run.json', dict(self.identity, terminal_reason='game_over'))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        self.write()
        target = self.runtime / 'hanjuku_bot.json'
        target.write_text('x' * 262145)
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        target.unlink()
        target.symlink_to(self.root / 'retro_corner.json')
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_missing_numbers_are_unknown_not_zero(self):
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=self.identity, policy=dict(chapter=True, gold=-1)))
        result = self.snapshot()['hanjuku']
        self.assertIsNone(result['chapter'])
        self.assertIsNone(result['gold'])
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('所持金 不明G', text)
        self.assertNotIn('0勝', text)

    def test_transition_and_identity_mismatch_fail_closed(self):
        _write_json(self.root / 'game_switch.json', dict(phase='switching', active=self.identity))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')
        self.write()
        _write_json(self.runtime / 'hanjuku_bot.json', dict(decision_trace=dict(self.identity, lease_id='old')))
        self.assertEqual(self.snapshot()['hanjuku']['availability'], 'unavailable')

    def test_renderer_strips_control_characters_and_bounds_lines(self):
        text = sd.fit_dashboard_lines(sd.render_hanjuku_status(dict(availability='fresh', screen='\x1b[31mBAD\n' * 100)))
        self.assertNotIn('\x1b', '\n'.join(text))
        self.assertTrue(all(sd.ansi_display_width(line) <= sd.W for line in text))

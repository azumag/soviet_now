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
            target_matches=3, rotation_runtime_id="g1",
        )
        _write_json(self.root / "game_switch.json", {"phase":"ready", "active":{"game":"ninvaders","runtime_id":"g1"}})
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
        self.assertNotIn("Rank Distribution", rendered)
        self.assertIn("best=1", rendered)
        self.assertIn("Recent30=", rendered)
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
        self.assertIn("Recent30=80", rendered)
        self.assertIn("may include interrupted runs", rendered)
        self.assertNotIn("Score Distribution", rendered)
        self.assertNotIn("999999", rendered)

    def test_compact_corner_rank_trend_treats_lower_as_better(self):
        scores = ([10] * 30) + ([5] * 30)
        rendered = "\n".join(sd._corner_score_panels(scores, rank=True, compact=True))
        self.assertIn("Trend: -5.0 vs previous 30 / better", rendered)
        self.assertIn("Rank Timeline", rendered)
        self.assertNotIn("Rank Distribution", rendered)

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

    def test_measured_gap_is_fenced_and_observation_deadlines_are_preserved(self):
        import time
        now=time.time()
        projection=dict(align='left',viewport=[0,90,960,540],content=[0,0,721,540])
        _write_json(self.runtime / 'presentation.json', dict(status='ready', projection=projection))
        bot=json.loads((self.runtime / 'hanjuku_bot.json').read_text())
        names=['長い城の名前'+str(i) for i in range(10)]
        bot['policy'].update(captured=names, tick=100,
            garrison={'アルマムーン':['長い将軍の名前です','ゼウス'], '古い城':['どうし']},
            garrison_observed_at={'アルマムーン':now-5,'古い城':now-31},
            battle=dict(enemy='シェーブル',ally='どうし',enemy_hp=55,ally_hp=70,hp_observed_at=now-3))
        _write_json(self.runtime / 'hanjuku_bot.json',bot)
        value=self.snapshot()['hanjuku']; gap=value['gap']
        self.assertEqual((gap['left'],gap['width']),(721,239))
        self.assertEqual(gap['captured_names'],names)
        self.assertEqual([g['castle'] for g in gap['garrison']],['アルマムーン'])
        self.assertAlmostEqual(gap['garrison'][0]['until'],now+25)
        self.assertAlmostEqual(gap['hp']['until'],now+7)
        text='\n'.join(sd.fit_dashboard_lines(sd.render_hanjuku_status(value),width=20))
        self.assertIn(' / '.join(names),text)
        self.assertIn('長い将軍の名前です',text)
        # Projection failure, mismatched geometry, narrow gap and old generations
        # must never reserve an area over a different or unmeasured game plane.
        for changed in [dict(status='presentation_failed',projection=projection),
                        dict(status='ready',projection=dict(projection,content=[0,0,900,540])),
                        dict(status='ready',projection=dict(projection,viewport=[0,0,960,540])),
                        dict(status='ready',projection=dict(projection,content=[120,0,721,540]))]:
            _write_json(self.runtime / 'presentation.json',changed)
            self.assertIsNone(self.snapshot()['hanjuku']['gap'])

    def test_gap_hp_and_rosters_hide_missing_future_and_expired_observations(self):
        import time
        now=time.time()
        _write_json(self.runtime / 'presentation.json',dict(status='ready',projection=
            dict(align='left',viewport=[0,90,960,540],content=[0,0,721,540])))
        for stamp in [None, True, now+2, now-31]:
            bot=json.loads((self.runtime / 'hanjuku_bot.json').read_text())
            bot['policy'].update(garrison={'城':['将軍']},garrison_observed_at={'城':stamp},
                battle=dict(enemy='敵',ally='我',enemy_hp=10,ally_hp=20,hp_observed_at=stamp))
            _write_json(self.runtime / 'hanjuku_bot.json',bot)
            gap=self.snapshot()['hanjuku']['gap']
            self.assertEqual(gap['garrison'],[]); self.assertIsNone(gap['hp'])

    def test_cached_observations_render_without_score_inference(self):
        corner = self.snapshot()
        self.assertEqual(corner['hanjuku']['availability'], 'fresh')
        rendered = '\n'.join(sd.render_docich_corner_stats(corner))
        self.assertIn('第2話', rendered)
        self.assertIn('123G', rendered)
        self.assertIn('現在の城数ではない', rendered)
        self.assertIn('計画段階: F3（完了未確認）', rendered)
        self.assertIn('実入力: 7回', rendered)
        # No live battle record in this fixture: say so instead of inventing HP.
        self.assertIn('交戦HP: 戦闘記録なし', rendered)
        self.assertNotIn('将軍HP', rendered)

    def test_month_and_pending_plan_are_observations_not_roster_counts(self):
        path = self.runtime / 'hanjuku_bot.json'
        data = json.loads(path.read_text())
        data['policy'].update(month='1-11', house={'phase': 'castle_verify'},
                              house_eggs={'old_general': {'hp': 1}})
        _write_json(path, data)
        result = self.snapshot()['hanjuku']
        self.assertEqual((result['year'], result['month']), (1, 11))
        self.assertEqual(result['pending_plan'], 'repair')
        self.assertNotIn('general_count', result)
        rendered = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('1年11月（最終観測）', rendered)
        self.assertIn('保留計画: repair', rendered)

    def test_invalid_month_stays_unknown(self):
        path = self.runtime / 'hanjuku_bot.json'
        for month in ('1-13', '0-2', True, '1-2\n', '10000-2'):
            data = json.loads(path.read_text())
            data['policy']['month'] = month
            _write_json(path, data)
            result = self.snapshot()['hanjuku']
            self.assertIsNone(result['month'])
            self.assertIsNone(result['year'])

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

    def test_no_escape_fragment_survives_any_card_field(self):
        # isprintable() rejects ESC but keeps the printable "[31m" behind it, so
        # a per-character check would put a broken escape on the card. Cover
        # every field the renderer reads, including the nested name rows.
        esc = '\x1b[31m'
        value = dict(
            availability='fresh', chapter=2, gold=1,
            variant=esc + 'EVIL\n' * 50, chart_step='X' * 200,
            screen=esc + 'S\n', phase=esc + 'P\n', pending_plan=esc + 'N\n',
            captured_names=[esc + '[2J', 'Y' * 90, 'ok'],
            lost_names=[esc + '[9mZ'],
            garrison=[{'castle': esc + 'X', 'generals': ['\x07bell', esc + 'OK']}],
            marching=[{'general': esc + 'A', 'target': esc + '[2JB'}],
            eggs=[{'general': esc + 'E', 'uses': 2}],
            enemy=esc + 'EN', ally=esc + 'AL', enemy_hp=5, ally_hp=6)
        text = '\n'.join(sd.fit_dashboard_lines(sd.render_hanjuku_status(value)))
        # '\n' is the card's own line separator, so check per line instead.
        for fragment in ('\x1b', '\x07', '\r', '[31m', '[2J', '[1m', '[9m'):
            self.assertNotIn(fragment, text, f'{fragment!r} leaked onto the card')
        for line in text.split('\n'):
            self.assertNotIn('\n', line)
            self.assertTrue(sd.ansi_display_width(line) <= sd.W)

    def test_snapshot_cleaner_drops_whole_escape_runs(self):
        from lib.docich_corner_stats import _hanjuku_clean, _hanjuku_names
        self.assertEqual(_hanjuku_clean('\x1b[31mEVIL\n'), 'EVIL')
        self.assertEqual(_hanjuku_clean('\x1b]0;title\x07X'), 'X')
        self.assertEqual(_hanjuku_clean('\x07\x08\x00only'), 'only')
        self.assertEqual(_hanjuku_names(['\x1b[2J', 'plain', 'plain', '\x07b']), ['plain', 'b'])

    def _policy(self, **extra):
        data = json.loads((self.runtime / 'hanjuku_bot.json').read_text())
        data['policy'].update(extra)
        _write_json(self.runtime / 'hanjuku_bot.json', data)
        return data

    def test_observed_battle_hp_replaces_the_permanent_unknown_placeholder(self):
        self._policy(battle={'enemy': ' dragon', 'ally': 'ゼウス', 'enemy_hp': 31, 'ally_hp': 44})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('交戦HP: 敵 31（dragon） / 我 44（ゼウス）', text)
        # The old card ended on a permanent "将軍HP・卵状態: 未確認" line.
        self.assertNotIn('将軍HP', text)
        self.assertNotIn('交戦HP: 戦闘記録なし', text)

    def test_partial_battle_record_never_prints_half_measured_hp(self):
        cases = [
            {'enemy': ' dragon', 'ally_hp': 44},
            {'enemy': 'dragon', 'enemy_hp': 31, 'ally_hp': 44},
            {'ally': 'ゼウス', 'enemy_hp': 31, 'ally_hp': 44},
        ]
        for battle in cases:
            with self.subTest(battle=battle):
                self._policy(battle=battle)
                text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
                self.assertIn('交戦HP: 戦闘記録なし', text)
                self.assertNotIn('交戦HP: 敵', text)

    def test_roster_line_names_observed_castles_and_lost_ones(self):
        self._policy(captured=['カストーラ', '-travel'], lost=['ジョンリギ'], home_lost=False)
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('失った城: ジョンリギ', text)
        self.assertNotIn('本拠: 失陥', text)
        self._policy(home_lost=True)
        self.assertIn('本拠: 失陥（記録）', '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku'])))

    def test_marching_sortie_expires_with_the_policy_busy_window(self):
        self._policy(tick=5000, sorties={'J3': {'general': 'どうし', 'target': 'ナキューメラ',
                                                 'status': 'en_route', 'tick': 4990}})
        result = self.snapshot()['hanjuku']
        self.assertEqual(result['marching'], [{'general': 'どうし', 'target': 'ナキューメラ'}])
        self.assertIn('行軍中: どうし→ナキューメラ', '\n'.join(sd.render_hanjuku_status(result)))
        # One tick past the policy's own 400-observation busy window: no longer
        # marching, so it must not be displayed as one.
        self._policy(tick=5000 + 400, sorties={'J3': {'general': 'どうし', 'target': 'ナキューメラ',
                                                      'status': 'en_route', 'tick': 4990}})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertNotIn('行軍中', text)

    def test_settled_and_unread_sorties_are_not_shown_as_marching(self):
        self._policy(tick=5000, sorties={
            'J3': {'general': 'ゼウス', 'status': 'arrived', 'tick': 4999},
            'J4': {'general': 'ユイートル', 'target': 'X', 'status': 'launched_unconfirmed', 'tick': True},
            'J5': {'target': 'Y', 'status': 'en_route', 'tick': 4999},
            'J6': {'general': 'future', 'target': 'Z', 'status': 'en_route', 'tick': 5001},
        })
        self.assertEqual(self.snapshot()['hanjuku']['marching'], [])

    def test_marching_without_a_confirmed_target_says_so_instead_of_inventing_one(self):
        self._policy(tick=5000, sorties={'J4': {'general': 'ヴィーナス', 'status': 'en_route', 'tick': 4999}})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('行軍中: ヴィーナス→未確定', text)

    def test_garrison_and_eggs_are_bounded_observations(self):
        self._policy(garrison={'アルマムーン': ['ゼウス', 'ユイートル'], '空': []},
                     egg_uses={'ゼウス': 1, '不正': 'x', '範囲外': 9})
        text = '\n'.join(sd.render_hanjuku_status(self.snapshot()['hanjuku']))
        self.assertIn('駐留: アルマムーン=ゼウス/ユイートル', text)
        self.assertIn('卵: ゼウス 1回', text)
        self.assertNotIn('範囲外', text)
        self.assertNotIn('不正', text)

    def test_unread_values_stay_unknown_rather_than_zero(self):
        result = self.snapshot()['hanjuku']
        text = '\n'.join(sd.render_hanjuku_status(result))
        self.assertIn('兵力: 不明名', text)
        self.assertIn('停滞 不明秒', text)
        self.assertIn('戦闘: 開始 不明 / 終了 不明', text)
        self.assertNotIn('開始 0', text)
        self.assertNotIn('兵力: 0名', text)
        self.assertIn('出撃: 成立 不明 / 失敗 不明', text)
        self.assertNotIn('出撃: 成立 0 / 失敗 0', text)

    def test_hanjuku_corner_omits_permanently_empty_score_and_ranking_lines(self):
        rendered = sd.render_docich_corner_stats(self.snapshot())
        text = '\n'.join(rendered)
        self.assertNotIn('no completed results', text)
        self.assertNotIn('no corner ranking data', text)
        # The retro card header and the Hanjuku card itself stay in place.
        self.assertIn('SOREN/CORNER: RETRO / hanjuku-hero', text)
        self.assertIn('半熟英雄 / 最終観測・記録', text)

    def test_other_retro_game_without_scores_keeps_empty_score_panel(self):
        state = dict(self.state, game='ninvaders', bot_identity=None)
        _write_json(self.root / 'retro_corner.json', state)
        text = '\n'.join(sd.render_docich_corner_stats(load_active_corner(self.root)))
        self.assertIn('Stats: no completed results yet', text)
        self.assertIn('Strategy: no corner ranking data', text)

    def test_other_retro_games_keep_their_score_and_ranking_lines(self):
        state = dict(self.state, game='ninvaders', bot_identity=None)
        _write_json(self.root / 'retro_corner.json', state)
        log = self.root / 'scores/ninvaders.jsonl'
        log.parent.mkdir(parents=True)
        log.write_text('\n'.join(json.dumps({'ts': f'2026-10-01T0{i}:00:00+00:00',
                                            'game': 'ninvaders', 'score': 100 + i})
                                for i in range(4)), encoding='utf-8')
        text = '\n'.join(sd.render_docich_corner_stats(load_active_corner(self.root)))
        self.assertIn('Stats: 4 results', text)
        self.assertIn('Strategy: no corner ranking data', text)

class ConsoleProgressTest(unittest.TestCase):
    setUp = DocichCornerStatsTest.setUp
    tearDown = DocichCornerStatsTest.tearDown
    _state = DocichCornerStatsTest._state

    def console(self, *, status="active", game="nsnake", start=100, deadline=500, **extra):
        self._state("retro_corner.json", status=status, game=game, started_at=start,
                    ends_at=deadline, target_matches=3, rotation_runtime_id="g7", **extra)
        _write_json(self.root / "game_switch.json", {"phase": "ready", "active":
                    {"game": game, "runtime_id": "g7"}})
        log = self.root / f"scores/{game}.jsonl"
        log.parent.mkdir(exist_ok=True)
        log.write_text("\n".join(json.dumps(row) for row in [
            {"game": game, "score": 900, "ts": 90},
            {"game": game, "score": 10, "ts": 110},
            {"game": game, "score": 30, "ts": 160},
            {"game": game, "score": 999, "ts": 300},
            {"game": "other", "score": 888, "ts": 150}]))
        return load_active_corner(self.root, now=200)

    def test_session_excludes_previous_future_and_other_game_results(self):
        for game in ["ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"]:
            with self.subTest(game=game):
                c = self.console(game=game)
                p = c["console"]
                self.assertNotIn(999, [r["score"] for r in c["scores"]])
                self.assertEqual((p["session_count"], p["session_best"], p["session_mean"]), (2, 30, 20))
                self.assertEqual((p["remaining"], p["remaining_seconds"]), (1, 300))
                self.assertEqual(p["latest"], {"score": 30, "at": 160, "age": 40})
                text = "\n".join(sd.render_console_progress(p))
                self.assertIn("Session: n=2 / best=30 / mean=20", text)
                self.assertIn("1 matches left / limit 5:00", text)
                self.assertIn("40s ago", text)
                self.assertIn("live score unobserved", text)
                self.assertNotIn("999", text)
                self.assertNotIn("900", text)
                for line in sd.render_console_progress(p):
                    self.assertLessEqual(sd.ansi_display_width(line), sd.W)
                self.assertEqual(sd.fit_dashboard_lines(sd.render_console_progress(p)),
                                 sd.render_console_progress(p))

    def test_absent_invalid_and_undated_history_are_unknown_not_zero(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        for content in [None, "{partial", json.dumps({"game": "nsnake", "score": 42})]:
            with self.subTest(content=content):
                if content is None: path.unlink()
                else: path.write_text(content)
                p = load_active_corner(self.root, now=200)["console"]
                self.assertIsNone(p["session_count"])
                self.assertIsNone(p["remaining"])
                self.assertIsNone(p["latest"])
                self.assertIn("history unavailable", "\n".join(sd.render_console_progress(p)))
        path.write_text("")
        self.assertEqual(load_active_corner(self.root, now=200)["console"]["session_count"], 0)

    def test_unidentified_history_rows_make_session_unknown(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        valid = {"game": "nsnake", "score": 10, "ts": 110}
        for invalid in [None, [], {}, {"score": 42, "ts": 150},
                        {"game": None}, {"game": ""}, {"game": 7}]:
            for rows in [[invalid], [valid, invalid]]:
                with self.subTest(rows=rows):
                    path.write_text("\n".join(json.dumps(row) for row in rows))
                    p = load_active_corner(self.root, now=200)["console"]
                    self.assertEqual(p["history_status"], "partial")
                    self.assertIsNone(p["session_count"])
                    self.assertIsNone(p["remaining"])
                    self.assertIsNone(p["latest"])
                    text = "\n".join(sd.render_console_progress(p))
                    self.assertIn("history unavailable", text)
                    self.assertNotIn("n=0", text)
                    self.assertNotIn("matches left", text)

    def test_identified_other_game_rows_do_not_make_history_partial(self):
        self.console()
        path = self.root / "scores/nsnake.jsonl"
        other = {"game": "bastet", "score": 42, "ts": 150}
        valid = {"game": "nsnake", "score": 10, "ts": 110}
        for rows, count in [([other], 0), ([valid, other], 1)]:
            with self.subTest(rows=rows):
                path.write_text("\n".join(json.dumps(row) for row in rows))
                p = load_active_corner(self.root, now=200)["console"]
                self.assertEqual(p["history_status"], "readable")
                self.assertEqual(p["session_count"], count)
                self.assertEqual(p["remaining"], 3 - count)

    def test_old_same_game_runtime_and_switching_never_claim_current_plan(self):
        self.console()
        for canonical in [{"phase": "ready", "active": {"game": "nsnake", "runtime_id": "g8"}},
                          {"phase": "draining", "active": {"game": "nsnake", "runtime_id": "g7"}},
                          {"phase": "ready", "active": {"game": "bastet", "runtime_id": "g7"}}, {}]:
            _write_json(self.root / "game_switch.json", canonical)
            p = load_active_corner(self.root, now=200)["console"]
            self.assertEqual(p["next"], "unverified")
            if canonical.get("phase") != "draining":
                self.assertIsNone(p["session_count"])
                self.assertIsNone(p["latest"])
            self.assertNotIn("matches left", "\n".join(sd.render_console_progress(p)))

    def test_target_time_and_transition_plans_are_distinct(self):
        for status, start, end, expected in [
                ("starting", 100, 500, "switch-wait"), ("restoring", 100, 500, "restore-wait"),
                ("active", 100, 150, "deadline-passed"), ("active", 50, 500, "target-recorded")]:
            c = self.console(status=status, start=start, deadline=end)
            self.assertEqual(c["console"]["next"], expected)
            if status == "starting": self.assertIsNone(c["console"]["session_count"])
        c = self.console(start=300)
        self.assertIsNone(c["console"]["session_count"])
        self.assertIsNone(c["console"]["remaining"])

    def test_reasons_are_allowlisted_and_other_corners_are_unchanged(self):
        c = self.console(status="restoring", end_reason="manual_saved_stop", last_error="private detail")
        self.assertIn("Reason: saved stop (record)", "\n".join(sd.render_console_progress(c["console"])))
        self.assertNotIn("private", "\n".join(sd.render_docich_corner_stats(c)))
        c = self.console(end_reason=["arbitrary"], last_error_code="sentinel-secret")
        self.assertIsNone(c["console"]["reason"])
        c = self.console(game="robots")
        self.assertNotIn("console", c)

    def test_every_reason_keeps_live_score_unobserved_in_the_fitted_feed(self):
        for reason in ["game_over", "screen_stalled", "manual_saved_stop", "manual_forced_stop",
                       "switch-terminal-before-corner-active", "recovery_required", "quiesce_failed",
                       "readiness_timeout", "deadline_exceeded"]:
            with self.subTest(reason=reason):
                c = self.console(status="restoring", end_reason=reason, last_error_code=reason)
                lines = sd.render_console_progress(c["console"])
                self.assertEqual(len(lines), 4)
                self.assertIn("live score unobserved", lines[3])
                self.assertLessEqual(sd.ansi_display_width(lines[3]), sd.W)
                self.assertEqual(sd.fit_dashboard_lines(lines), lines)

import json
import io
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

    def test_retro_history_is_scoped_to_current_corner_start(self):
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
            {"ts": _epoch("2026-09-20T23:59:00"), "game": "ninvaders", "score": 999999},
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
        self.assertEqual([row["score"] for row in corner["scores"]], [10, 20, 30])
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("Recent30: 10 20 30", rendered)
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
            run_id="run-1",
            run_status="active",
            run_score=42,
            run_turns=120,
            run_max_depth=4,
        )
        _write_json(self.root / "nethack/current.json", {"expedition": 7, "status": "active", "score": 42, "turns": 120, "max_depth": 4})
        _write_json(self.root / "nethack/runs/old.json", {"score": 12, "finished_at": "2026-09-20T00:00:00+00:00"})
        corner = load_active_corner(self.root)
        self.assertEqual(corner["scores"], [12, 42])
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("expedition 7", rendered)
        self.assertIn("score=42", rendered)

    def test_soren91_and_jev_are_non_score_corner_views(self):
        self._state("soren91_corner.json", game="soren91")
        corner = load_active_corner(self.root)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("rank-based", rendered)
        self.assertIn("not applicable", rendered)

        (self.root / "soren91_corner.json").unlink()
        self._state("jev_corner.json", game="sorengame", policy="jev", player_generation=3)
        corner = load_active_corner(self.root)
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("policy=jev generation=3", rendered)
        self.assertIn("not applicable", rendered)

    def test_multiple_active_corners_fail_closed(self):
        self._state("retro_corner.json", game="robots")
        self._state("paper_corner.json")
        corner = load_active_corner(self.root)
        self.assertEqual(corner["kind"], "conflict")
        rendered = "\n".join(sd.render_docich_corner_stats(corner))
        self.assertIn("STATE CONFLICT", rendered)
        self.assertNotIn("Score Distribution", rendered)

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

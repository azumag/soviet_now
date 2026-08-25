"""strategy_runner の静止判定 (SOREN_SETTLE_REQUIRED / SOREN_SETTLE_MAX_SPEED2 / SOREN_SETTLE_MAX_AWAKE) と
wait_for_move_state の連続観測・記録を検証する。環境変数 (.env 実験中) に依存しないよう定数は必ず patch する。"""
import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import strategy_runner as sr  # noqa: E402

LEGACY = {"SETTLE_REQUIRED": 1, "SETTLE_MAX_SPEED2": 0.1, "SETTLE_MAX_AWAKE": -1}


def _gs(vs, awake=True, state="MOVE"):
    return {"state": state, "pieces": [{"id": i, "type": 3, "x": 0.0, "y": -3.0, "vx": v, "vy": 0.0, "awake": awake} for i, v in enumerate(vs)]}


class SettleWaitTest(unittest.TestCase):
    def test_defaults_match_legacy_without_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(sr._env_int("SOREN_SETTLE_REQUIRED", 1), 1)
            self.assertEqual(sr._env_float("SOREN_SETTLE_MAX_SPEED2", 0.1), 0.1)
            self.assertEqual(sr._env_int("SOREN_SETTLE_MAX_AWAKE", -1), -1)
        cfg = open(os.path.join(ROOT, "core", "config.sh"), encoding="utf-8").read()
        for line in ('SOREN_SETTLE_REQUIRED="${SOREN_SETTLE_REQUIRED:-1}"', 'SOREN_SETTLE_MAX_SPEED2="${SOREN_SETTLE_MAX_SPEED2:-0.1}"', 'SOREN_SETTLE_MAX_AWAKE="${SOREN_SETTLE_MAX_AWAKE:--1}"'):
            self.assertIn(line, cfg)

    def test_env_parsing_is_fail_safe(self):
        for raw, expect in (("4", 4), ("abc", 1), ("inf", 1), ("nan", 1), ("", 1), ("2.9", 2)):
            with mock.patch.dict(os.environ, {"SOREN_TEST_X": raw}):
                self.assertEqual(sr._env_int("SOREN_TEST_X", 1), expect, raw)
        for raw, expect in (("nan", 0.1), ("inf", 0.1), ("x", 0.1), ("0.05", 0.05)):
            with mock.patch.dict(os.environ, {"SOREN_TEST_Y": raw}):
                self.assertEqual(sr._env_float("SOREN_TEST_Y", 0.1), expect, raw)

    def test_speed_threshold_and_awake_cap(self):
        with mock.patch.multiple(sr, **LEGACY):
            self.assertTrue(sr.is_board_settled(_gs([0.3, 0.1])))      # 0.09 < 0.1
            self.assertFalse(sr.is_board_settled(_gs([0.4])))          # 0.16 >= 0.1
            self.assertTrue(sr.is_board_settled(_gs([0.0, -5000.0])))  # 落下待ちの next ピースは除外
            self.assertTrue(sr.is_board_settled(_gs([9.0]), force_after=1.0))  # 期限超過は強制 settled
        with mock.patch.multiple(sr, SETTLE_MAX_SPEED2=0.01, SETTLE_MAX_AWAKE=-1):
            self.assertFalse(sr.is_board_settled(_gs([0.3])))
        with mock.patch.multiple(sr, SETTLE_MAX_SPEED2=0.1, SETTLE_MAX_AWAKE=0):
            self.assertFalse(sr.is_board_settled(_gs([0.0, 0.0], awake=True)))
            self.assertTrue(sr.is_board_settled(_gs([0.0, 0.0], awake=False)))
        with mock.patch.multiple(sr, SETTLE_MAX_SPEED2=0.1, SETTLE_MAX_AWAKE=2):
            self.assertTrue(sr.is_board_settled(_gs([0.0, 0.0], awake=True)))

    def _run_wait(self, sequence, required, fast=False, **extra):
        seq = iter(sequence)
        patches = dict(LEGACY, SETTLE_REQUIRED=required, POLL_INTERVAL=0.0, STOP_FILE="/nonexistent/soren-stop-file", **extra)
        with mock.patch.multiple(sr, **patches), \
             mock.patch.object(sr, "load_game_state", lambda: next(seq, None)), \
             mock.patch.object(sr, "has_deadline_contact", lambda gs: fast):
            gs, ok = sr.wait_for_move_state(deadline_fast_drop_enabled=True)
        return gs, ok, dict(sr._LAST_SETTLE)

    def test_required_consecutive_samples(self):
        calm, moving = _gs([0.1]), _gs([0.9])
        gs, ok, rec = self._run_wait([calm, moving, calm, calm, calm, calm], 3)
        self.assertTrue(ok)
        self.assertEqual(rec["required"], 3)
        self.assertFalse(rec["fast_drop"])
        self.assertFalse(rec["forced"])
        self.assertIn("wait_sec", rec)
        self.assertEqual(rec["awake"], 1)
        self.assertAlmostEqual(rec["max_speed"], 0.1, places=3)
        gs, ok, rec = self._run_wait([calm, moving], 1)  # legacy: first calm sample wins
        self.assertTrue(ok)
        self.assertEqual(rec["required"], 1)

    def test_forced_drop_when_never_calm(self):
        moving = _gs([0.9])
        gs, ok, rec = self._run_wait([moving] * 50, 4, SETTLE_FORCE_TIMEOUT=0.0)
        self.assertTrue(ok)
        self.assertTrue(rec["forced"])
        self.assertEqual(rec["required"], 4)

    def test_fast_drop_records_flag(self):
        gs, ok, rec = self._run_wait([_gs([0.9])], 3, fast=True)
        self.assertTrue(ok)
        self.assertTrue(rec["fast_drop"])

    def test_move_timeout_leaves_no_settle_record(self):
        seq = iter([_gs([0.0], state="DROP")] * 5)
        with mock.patch.multiple(sr, POLL_INTERVAL=0.0, MOVE_TIMEOUT=0.0, STOP_FILE="/nonexistent/soren-stop-file"), \
             mock.patch.object(sr, "load_game_state", lambda: next(seq, _gs([0.0], state="DROP"))):
            gs, ok = sr.wait_for_move_state()
        self.assertTrue(ok)
        self.assertEqual(sr._LAST_SETTLE, {})

    def test_gameover_returns_false(self):
        seq = iter([_gs([0.0], state="GAMEOVER")])
        with mock.patch.multiple(sr, POLL_INTERVAL=0.0, STOP_FILE="/nonexistent/soren-stop-file"), \
             mock.patch.object(sr, "load_game_state", lambda: next(seq)):
            gs, ok = sr.wait_for_move_state()
        self.assertFalse(ok)

    def test_record_turn_includes_settle(self):
        sr._LAST_SETTLE.clear()
        sr._LAST_SETTLE.update({"wait_sec": 0.3, "awake": 5, "max_speed": 0.2, "fast_drop": False, "forced": False, "required": 3})
        buf = io.StringIO()
        gs = {"state": "MOVE", "score": 10, "pieces": [], "next": {"type": 3}, "nextNext": {"type": 4}}
        sr.record_turn(buf, 1, gs, {"x": 0.0, "reason": "t"}, {"results": [], "reactor": {}}, strategy_hash="h")
        rec = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(rec.get("settle", {}).get("required"), 3)


if __name__ == "__main__":
    unittest.main()

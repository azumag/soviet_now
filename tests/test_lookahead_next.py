"""v739: decide() の 2 手先読み (strategy_helpers/lookahead.rerank)。実履歴の flip 局面で LOOKAHEAD_NEXT が付き
選択が変わること、lam=0 / nextNext 無し / 締切余裕 <1.0 では不変、解析器が例外を投げても 1 手評価に戻ること、
v736 の fixture (保護タグ付き) が不変であること、呼び出し上限と所要時間を確認する。"""
import json
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
import analyze_board as ab  # noqa: E402
import strategy  # noqa: E402
import strategy_runner as sr  # noqa: E402
from strategy_helpers import lookahead  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
TAG = "LOOKAHEAD_NEXT"
FLIPS = ("lookahead_t9_nnt9_turn43.json", "lookahead_t6_nnt10_turn31.json", "lookahead_t8_nnt10_turn65.json")


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


def _gs(fx, with_nnt=True):
    nt = fx["next_type"]
    nnt = fx.get("next_next_type", 5)
    gs = {"state": "MOVE", "score": fx.get("score", 0), "pieces": [dict(p) for p in fx["pieces"]], "shapes": fx["shapes"],
          "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)}}
    if with_nnt:
        gs["nextNext"] = {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)}
    return gs


def _analysis(gs):
    an = sr.build_analysis(gs)
    sr.enrich_game_state_deadline_fields(gs, an)
    return an


def _pipeline(gs, an=None):
    an = an or _analysis(gs)
    d = strategy.decide(gs, an)
    d["x"] = max(-3.0, min(3.0, float(d.get("x", 0))))
    d = sr.enforce_deadline_safety(d, an, gs, strategy)
    d = sr.apply_strategy_final_decision(strategy, d, an, gs)
    x = max(-3.0, min(3.0, float(d.get("x", 0))))
    return x, str(d.get("reason", "")), min(an["results"], key=lambda r: abs(r["x"] - x)), an


def _ply1(gs, an=None):
    """先読みを無効化した 1 手評価の選択。"""
    with mock.patch.object(lookahead, "rerank", return_value=None):
        return _pipeline(gs, an)


class LookaheadNextTest(unittest.TestCase):
    def test_flip_fixtures_take_lookahead_pick(self):
        for name in FLIPS:
            with self.subTest(fixture=name):
                fx = _load(name)
                gs = _gs(fx)
                x, reason, chosen, an = _pipeline(gs)
                self.assertIn(TAG, reason)
                self.assertFalse(chosen["crosses_deadline"])
                self.assertFalse(chosen.get("merge_result_crosses_deadline"))
                x1, reason1, chosen1, _ = _ply1(gs, an)
                self.assertNotAlmostEqual(x, x1, delta=0.05, msg="%s: lookahead must change the pick" % name)
                self.assertNotIn(TAG, reason1)
                self.assertAlmostEqual(x1, float(fx["baseline_x"]), delta=0.05)
                # 併合手 (DIRECT/NEAR) を捨てていない
                self.assertFalse(chosen1["merge_grade"] in ("DIRECT", "NEAR") and chosen["merge_grade"] == "NO")

    def test_lambda_zero_never_flips(self):
        for name in FLIPS:
            fx = _load(name)
            gs = _gs(fx)
            an = _analysis(gs)
            # decide の候補を採取して helper を直接 lam=0 で呼ぶ
            captured = {}
            real = lookahead.rerank

            def spy(pieces, shapes, nt, nnt, cands, cfg=None):
                captured["cands"] = cands
                captured["cfg"] = dict(cfg or {})
                return real(pieces, shapes, nt, nnt, cands, cfg)
            with mock.patch.object(lookahead, "rerank", side_effect=spy):
                strategy.decide(gs, an)
            self.assertTrue(captured.get("cands"))
            cfg0 = dict(captured["cfg"])
            cfg0["lam"] = 0.0
            self.assertIsNone(real(gs["pieces"], gs["shapes"], fx["next_type"], fx["next_next_type"], captured["cands"], cfg0), name)

    def test_no_next_next_or_low_margin_disables(self):
        fx = _load(FLIPS[0])
        gs = _gs(fx, with_nnt=False)
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)
        gs = _gs(fx)
        an = _analysis(gs)
        an["reactor"]["deadline_margin"] = 0.99
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        an["reactor"]["deadline_margin"] = 1.0
        self.assertIn(TAG, str(strategy.decide(gs, an).get("reason", "")))

    def test_analyzer_exception_falls_back_to_ply1(self):
        fx = _load(FLIPS[1])
        gs = _gs(fx)
        an = _analysis(gs)
        x1, reason1, _, _ = _ply1(gs, an)
        with mock.patch.object(ab, "get_landing_info", side_effect=RuntimeError("boom")):
            d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))
        self.assertAlmostEqual(max(-3.0, min(3.0, float(d["x"]))), x1, delta=0.05)

    def test_protected_v736_pick_unchanged(self):
        fx = _load("probable_merge_t4_turn20.json")
        gs = _gs(fx)
        an = _analysis(gs)
        x, reason, chosen, _ = _pipeline(gs, an)
        x1, reason1, _, _ = _ply1(gs, an)
        self.assertIn("PROBABLE_MERGE_CONTACT", reason1)
        self.assertAlmostEqual(x, x1, delta=1e-6)
        self.assertNotIn(TAG, reason)

    def test_call_budget_and_latency(self):
        fx = _load("lookahead_t8_nnt10_turn65.json")
        self.assertGreaterEqual(len(fx["pieces"]), 30)
        gs = _gs(fx)
        an = _analysis(gs)
        calls = {"n": 0}
        real = lookahead.lite_results

        def counted(*a, **k):
            calls["n"] += 1
            return real(*a, **k)
        with mock.patch.object(lookahead, "lite_results", side_effect=counted):
            t0 = time.perf_counter()
            d = strategy.decide(gs, an)
            dt = time.perf_counter() - t0
        self.assertIn(TAG, str(d.get("reason", "")))
        self.assertLessEqual(calls["n"], 16)
        self.assertLess(dt, 0.6)

    def test_rerank_is_fail_closed(self):
        self.assertIsNone(lookahead.rerank(None, {}, 3, 4, [], None))
        self.assertIsNone(lookahead.rerank([], {}, 3, 4, [(0.0, 1.0, {}, [])], None))
        self.assertIsNone(lookahead.rerank([{"id": 1, "type": 3, "x": 0.0, "y": -4.0}], {}, "x", 4, [(0.0, 1.0, {}, []), (1.0, 0.5, {}, [])], None))


if __name__ == "__main__":
    unittest.main()

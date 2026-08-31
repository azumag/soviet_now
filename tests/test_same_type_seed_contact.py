"""v731: decide() の SAME_TYPE_SEED_CONTACT (非併合手で届かない同型相方の隣へ播種)。
実履歴の局面 (angle 付き) を fixture にし、フルパイプライン (build_analysis → decide →
enforce_deadline_safety → apply_strategy_final_decision) で v731 の着手が相方への接触ギャップを縮め、
併合候補がある手・危険域・ロシア在盤では発火しないことを確認する。"""
import json
import math
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
import analyze_board as ab  # noqa: E402
import strategy  # noqa: E402
import strategy_runner as sr  # noqa: E402
from strategy_helpers import board_stats  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
TAG = "SAME_TYPE_SEED_CONTACT"


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


def _gs(fx):
    nt = fx["next_type"]
    nnt = fx.get("next_next_type", 5)
    return {"state": "MOVE", "score": fx.get("score", 0), "pieces": [dict(p) for p in fx["pieces"]], "shapes": fx["shapes"],
            "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)}, "nextNext": {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)}}


def _pipeline(gs):
    an = sr.build_analysis(gs)
    sr.enrich_game_state_deadline_fields(gs, an)
    d = strategy.decide(gs, an)
    d["x"] = max(-3.0, min(3.0, float(d.get("x", 0))))
    d = sr.enforce_deadline_safety(d, an, gs, strategy)
    d = sr.apply_strategy_final_decision(strategy, d, an, gs)
    fx = max(-3.0, min(3.0, float(d.get("x", 0))))
    chosen = min(an["results"], key=lambda r: abs(r["x"] - fx))
    return fx, str(d.get("reason", "")), chosen, an


def _gap(chosen, pieces, nt):
    parts = [p for p in pieces if p["type"] == nt]
    sx = chosen["x"] + (chosen.get("drift_x") or 0.0)
    rn = board_stats.seed_horiz_radius(nt)
    return min(math.hypot(sx - p["x"], chosen["landing_y"] - p["y"]) - (rn + board_stats.seed_horiz_radius(p["type"])) for p in parts)


class SameTypeSeedContactTest(unittest.TestCase):
    def _assert_seeds(self, name):
        fx = _load(name)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(TAG, reason, name)
        self.assertEqual(chosen["merge_grade"], "NO")
        self.assertFalse(chosen["crosses_deadline"])
        self.assertFalse(chosen["merge_result_crosses_deadline"])
        gap_new = _gap(chosen, gs["pieces"], fx["next_type"])
        logged = min(an["results"], key=lambda r: abs(r["x"] - float(fx["logged_decision_x"])))
        gap_old = _gap(logged, gs["pieces"], fx["next_type"])
        self.assertLess(gap_new, gap_old, "%s: gap %.2f -> %.2f" % (name, gap_old, gap_new))
        self.assertLessEqual(gap_new, 1.3)
        return gap_old, gap_new

    def test_t10_real_turn_seeds_next_to_partner(self):
        g0, g1 = self._assert_seeds("seed_contact_t10_turn50.json")
        self.assertLessEqual(g1, 0.3)

    def test_t9_real_turn_seeds_next_to_partner(self):
        g0, g1 = self._assert_seeds("seed_contact_t9_turn41.json")
        self.assertLessEqual(g1, 0.3)

    def test_t11_real_turn_seeds_closer(self):
        self._assert_seeds("seed_contact_t11_turn15.json")

    def test_never_competes_with_available_merge(self):
        fx = _load("seed_contact_direct_available.json")
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertTrue(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertNotIn(TAG, reason)

    def test_fail_closed_in_danger_regimes(self):
        fx = _load("seed_contact_t10_turn50.json")
        # (a) ロシア在盤
        gs = _gs(fx)
        gs["pieces"].append({"id": 9901, "type": 15, "x": 2.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)
        # (b) deadline_crossed
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        gs["deadline_crossed"] = True
        d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))
        # (c) reactor margin < 1.0
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        an["reactor"]["deadline_margin"] = 0.5
        d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))

    def test_low_types_do_not_seed(self):
        fx = _load("seed_contact_t10_turn50.json")
        gs = _gs(fx)
        gs["next"] = {"type": 6, "r": ab.TYPE_RADII[6]}
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))

    def test_malformed_partner_geometry_does_not_raise(self):
        fx = _load("seed_contact_t10_turn50.json")
        gs = _gs(fx)
        for p in gs["pieces"]:
            if p["type"] == fx["next_type"]:
                p["x"] = None
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        d = strategy.decide(gs, an)
        self.assertIn("x", d)

    def test_helper_radius(self):
        self.assertAlmostEqual(board_stats.seed_horiz_radius(11), ab.UNITY_PREFAB_DEADLINE_RADII[11]["horiz"])
        self.assertEqual(board_stats.seed_horiz_radius(999), 0.5)


if __name__ == "__main__":
    unittest.main()

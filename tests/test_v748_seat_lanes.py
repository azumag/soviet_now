"""v748 SEAT_LANES: 型別定位置への誘導。実履歴の局面を fixture にし、フルパイプラインで
 (1) 非併合手で T1/T2（ジャンク）と T3-T8 が高型側の反対壁から割り当てた座席 x へ寄ること、
 (2) DIRECT 併合がある手は変わらないこと、
 (3) デッドライン余裕 <1.0 では発火しないこと、を確認する。"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
os.environ.setdefault("ANALYZE_BOARD_WALL_CLAMP", "1")
import analyze_board as ab  # noqa: E402
import strategy  # noqa: E402
import strategy_runner as sr  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")


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


class V748SeatLaneTests(unittest.TestCase):
    def test_seat_lane_moves_junk_and_mid_types_to_their_seats(self):
        for name in ("v748_seat_changed_junk_1_turn1.json", "v748_seat_changed_mid_1_turn19.json", "v748_seat_changed_mid_2_turn27.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertIn("SEAT_LANE", reason, name)
            self.assertLessEqual(abs(x - fx["seat_x"]), 0.6, "%s: x=%.2f seat=%.2f" % (name, x, fx["seat_x"]))
            self.assertGreater(abs(x - fx["base_x"]), 0.5, name)
            self.assertFalse(chosen.get("crosses_deadline"), name)

    def test_direct_merge_unchanged(self):
        fx = _load("v748_direct_unchanged_1_turn3.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertIn("DIRECT_MERGE", reason)
        self.assertNotIn("SEAT_LANE", reason)
        self.assertLessEqual(abs(x - fx["base_x"]), 0.011)

    def test_no_fire_when_deadline_margin_tight(self):
        fx = _load("v748_tight_margin_nofire_1_turn76.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertNotIn("SEAT_LANE", reason)
        self.assertLessEqual(abs(x - fx["base_x"]), 0.011)


if __name__ == "__main__":
    unittest.main()

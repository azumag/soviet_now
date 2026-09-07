"""v747: 終盤の併合優先。実試合 (2026-08-27 22:34, 152 手) の終盤局面を fixture にし、フルパイプライン
(decide → enforce_deadline_safety → apply_strategy_final_decision) で
 (1) 超過フラグ付きでも DIRECT 併合候補が選ばれ (DEADLINE_GUARD_DIRECT_MERGE_CROSSING)、ランナーが差し戻さないこと、
 (2) 非超過候補が無い局面で露出同型へ直落としすること (OPEN_TWIN_MERGE_DESPERATE)、
 (3) 併合候補も露出同型も無い局面では着手が変わらないこと、を確認する。"""
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


class V747EndgameMergeTests(unittest.TestCase):
    def test_strategy_declares_direct_cross_allowance(self):
        self.assertTrue(getattr(strategy, "DEADLINE_ALLOW_DIRECT_CROSS", False))

    def test_crossing_direct_merge_is_taken_and_survives_runtime(self):
        for name in ("v747_guard_direct_crossing_turn141.json", "v747_guard_direct_crossing_turn149.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertIn("DEADLINE_GUARD_DIRECT_MERGE_CROSSING", reason, name)
            self.assertEqual(chosen.get("merge_grade"), "DIRECT", name)
            self.assertLessEqual(abs(x - fx["expect_x"]), 0.3, "%s: x=%.2f" % (name, x))

    def test_desperate_open_twin_drop(self):
        fx = _load("v747_desperate_open_twin_turn148.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertIn("OPEN_TWIN_MERGE_DESPERATE", reason)
        twins = [p for p in fx["pieces"] if p["type"] == fx["next_type"]]
        self.assertLessEqual(min(abs(x - p["x"]) for p in twins), 0.4, "x=%.2f not above a twin" % x)

    def test_unchanged_when_no_merge_option(self):
        fx = _load("v747_unchanged_control_turn151.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertNotIn("DEADLINE_GUARD_DIRECT_MERGE_CROSSING", reason)
        self.assertNotIn("OPEN_TWIN_MERGE_DESPERATE", reason)
        self.assertLessEqual(abs(x - fx["expect_x"]), 0.3, "x=%.2f" % x)


if __name__ == "__main__":
    unittest.main()

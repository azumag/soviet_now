"""v732: decide() の ANCHOR_LANE_SEED_CONTACT (T(N+1) アンカーの横 / 開いた真上への播種)。
実履歴の局面 (angle 付き) を fixture にし、フルパイプラインで v732 の着地がアンカー近傍 (GOOD 級:
BESIDE / ABOVE_OPEN) になり、併合候補がある手・危険域・ロシア在盤・next T12 では発火しないこと、
埋もれたアンカーの真上には加点しないことを確認する。"""
import json
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
from strategy_helpers import board_stats as bs  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
TAG = "ANCHOR_LANE_SEED_CONTACT"


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


def _anchor_class(chosen, pieces, nt):
    """decide() と同じ軸別幾何 (縦ゲート込み): BESIDE / ABOVE_OPEN / ABOVE_COVERED / ABOVE_HIGH / FAR / None"""
    anchors = [p for p in pieces if p["type"] == nt + 1]
    if not anchors:
        return None
    sx = chosen["x"] + (chosen.get("drift_x") or 0.0)
    ly = chosen["landing_y"]
    rnh, rnb, rnt = bs.seed_horiz_radius(nt), bs.seed_bottom_radius(nt), bs.seed_top_radius(nt)
    ah, atr, abr = bs.seed_horiz_radius(nt + 1), bs.seed_top_radius(nt + 1), bs.seed_bottom_radius(nt + 1)
    cbot, ctop = ly - rnb, ly + rnt
    classes = []
    for a in anchors:
        atop, abot = a["y"] + atr, a["y"] - abr
        open_ = not any(o is not a and abs(o["x"] - a["x"]) <= ah and o["y"] - bs.seed_bottom_radius(o["type"]) >= atop - 0.25 for o in pieces)
        dx = abs(sx - a["x"])
        if dx <= ah and cbot >= atop - 0.25:
            if not open_:
                classes.append("ABOVE_COVERED")
            elif cbot <= atop + 0.25:
                classes.append("ABOVE_OPEN")
            else:
                classes.append("ABOVE_HIGH")
        elif dx - (rnh + ah) <= 0.15 and max(cbot - atop, abot - ctop, 0.0) <= 0.5:
            classes.append("BESIDE")
        else:
            classes.append("FAR")
    for good in ("ABOVE_OPEN", "BESIDE"):
        if good in classes:
            return good
    return "ABOVE_COVERED" if "ABOVE_COVERED" in classes else "FAR"


class AnchorLaneSeedContactTest(unittest.TestCase):
    def _assert_good(self, name, expect_class=None):
        fx = _load(name)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(TAG, reason, name)
        self.assertEqual(chosen["merge_grade"], "NO")
        self.assertFalse(chosen["crosses_deadline"])
        cls = _anchor_class(chosen, gs["pieces"], fx["next_type"])
        self.assertIn(cls, ("BESIDE", "ABOVE_OPEN"), "%s: class %s" % (name, cls))
        if expect_class:
            self.assertEqual(cls, expect_class)
        logged = min(an["results"], key=lambda r: abs(r["x"] - float(fx["logged_decision_x"])))
        self.assertNotIn(_anchor_class(logged, gs["pieces"], fx["next_type"]), ("BESIDE", "ABOVE_OPEN"), "logged v727/v731 choice should not already be GOOD class")
        return x, chosen, gs, an

    def test_real_turns_seed_beside_or_on_open_anchor(self):
        for name in ("anchor_lane_t9_beside_turn14.json", "anchor_lane_t9_ladder_turn47.json",
                     "anchor_lane_t10_beside_turn15.json", "anchor_lane_t10_ladder_turn16.json",
                     "anchor_lane_t11_beside_turn50.json", "anchor_lane_t11_ladder_turn18.json"):
            with self.subTest(fixture=name):
                self._assert_good(name, _load(name).get("expect_class"))

    def test_covered_anchor_top_is_not_rewarded(self):
        """ラダー fixture で開いた T12 の真上が選ばれる。T12 上端を小駒で塞ぐと、その x はもう
        加点されず (埋もれたアンカーの上)、決定は真上から離れるか、少なくとも埋もれたアンカーの上には乗らない。"""
        fx = _load("anchor_lane_t11_ladder_turn18.json")
        nt = fx["next_type"]
        gs0 = _gs(fx)
        x0, reason0, chosen0, an0 = _pipeline(gs0)
        self.assertIn(TAG, reason0)
        self.assertEqual(_anchor_class(chosen0, gs0["pieces"], nt), "ABOVE_OPEN")
        gs = _gs(fx)
        anchor = next(p for p in gs["pieces"] if p["type"] == nt + 1)
        cap = {"id": 9902, "type": 3, "x": anchor["x"], "y": anchor["y"] + bs.seed_top_radius(nt + 1) + 0.31, "r": 0.32, "angle": 0.0}
        gs["pieces"].append(cap)
        x, reason, chosen, an = _pipeline(gs)
        cls = _anchor_class(chosen, gs["pieces"], nt)
        # v732 は埋もれたアンカーの上に「加点しない」(他の軸がそこを選ぶこと自体は妨げない)
        self.assertFalse(cls == "ABOVE_COVERED" and TAG in reason, "no bonus for landing on a covered anchor")
        self.assertTrue(abs(x - x0) > 0.05 or TAG not in reason, "covered anchor must not keep the same rewarded landing")

    def test_guard_suppresses_bonus_over_other_open_high_top(self):
        """アンカーの横に着地する fixture (t11_beside_turn50) で、その着地点の下に上端の開いた T10 を置くと
        guard が効いて加点されず、決定は T10 の上から離れる (T10 を被覆しない)。"""
        fx = _load("anchor_lane_t11_beside_turn50.json")
        nt = fx["next_type"]
        gs0 = _gs(fx)
        x0, reason0, chosen0, an0 = _pipeline(gs0)
        self.assertIn(TAG, reason0)
        gs = _gs(fx)
        t10 = {"id": 9903, "type": 10, "x": x0, "y": chosen0["landing_y"] - bs.seed_bottom_radius(nt) - bs.seed_top_radius(10) + 0.05, "r": ab.TYPE_RADII[10], "angle": 0.0}
        gs["pieces"].append(t10)
        x, reason, chosen, an = _pipeline(gs)
        sx = chosen["x"] + (chosen.get("drift_x") or 0.0)
        covers = abs(sx - t10["x"]) <= bs.seed_horiz_radius(10) and chosen["landing_y"] - bs.seed_bottom_radius(nt) >= t10["y"] + bs.seed_top_radius(10) - 0.25
        self.assertFalse(covers and TAG in reason, "bonus must not be paid for covering an open T10 top")

    def test_margin_exactly_one_still_fires(self):
        fx = _load("anchor_lane_t11_beside_turn50.json")
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        an["reactor"]["deadline_margin"] = 1.0
        d = strategy.decide(gs, an)
        self.assertIn(TAG, str(d.get("reason", "")))

    def test_never_competes_with_available_merge(self):
        fx = _load("seed_contact_direct_available.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertTrue(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertNotIn(TAG, reason)

    def test_fail_closed_in_danger_regimes_and_t12(self):
        fx = _load("anchor_lane_t11_beside_turn50.json")
        gs = _gs(fx)
        gs["pieces"].append({"id": 9901, "type": 15, "x": 2.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        self.assertNotIn(TAG, _pipeline(gs)[1])
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        gs["deadline_crossed"] = True
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        an["reactor"]["deadline_margin"] = 0.9
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        gs = _gs(fx)
        gs["next"] = {"type": 12, "r": ab.TYPE_RADII[12]}
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))

    def test_malformed_anchor_geometry_does_not_raise(self):
        fx = _load("anchor_lane_t11_beside_turn50.json")
        gs = _gs(fx)
        for p in gs["pieces"]:
            if p["type"] == fx["next_type"] + 1:
                p["y"] = None
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        d = strategy.decide(gs, an)
        self.assertIn("x", d)

    def test_helper_radii(self):
        self.assertAlmostEqual(bs.seed_top_radius(12), ab.UNITY_PREFAB_DEADLINE_RADII[12]["top"])
        self.assertAlmostEqual(bs.seed_bottom_radius(12), ab.UNITY_PREFAB_DEADLINE_RADII[12]["bottom"])
        self.assertEqual(bs.seed_top_radius(999), 0.5)
        self.assertEqual(bs.seed_bottom_radius(999), 0.5)


if __name__ == "__main__":
    unittest.main()

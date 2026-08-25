"""v734: decide() の LOW_DROP_HIGH_LANE_COVER_AVOID (小駒 T1〜T8 で開いた T9〜T11 の上端を覆わない)。
実履歴の局面 (angle 付き) を fixture にし、フルパイプラインで v734 の着地が開いた T9〜T11 を覆わなくなること、
安全な代替が無い手ではタグだけ付いて着手が変わらないこと、危険域・ロシア在盤・next>=9・埋もれた上端・
next==T(N-1) の免除では発火しないことを確認する。"""
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
TAG = "LOW_DROP_HIGH_LANE_COVER_AVOID"


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


def _open_tops(pieces, lo=9, hi=11):
    out = []
    for p in pieces:
        t = p["type"]
        if t < lo or t > hi:
            continue
        h = bs.seed_horiz_radius(t)
        top = p["y"] + bs.seed_top_radius(t)
        if any(o is not p and abs(o["x"] - p["x"]) <= h and o["y"] - bs.seed_bottom_radius(o["type"]) >= top - 0.25 for o in pieces):
            continue
        out.append((p["x"], h, top))
    return out


def _covers(chosen, nt, tops):
    sx = chosen["x"] + (chosen.get("drift_x") or 0.0)
    bot = chosen["landing_y"] - bs.seed_bottom_radius(nt)
    return any(abs(sx - lx) <= lh and bot >= ltop - 0.25 for lx, lh, ltop in tops)


class LowDropHighLaneCoverTest(unittest.TestCase):
    def _assert_uncovered(self, name):
        fx = _load(name)
        gs = _gs(fx)
        tops = _open_tops(gs["pieces"])
        self.assertTrue(tops, "%s: fixture must have an open T9-T11 top" % name)
        x, reason, chosen, an = _pipeline(gs)
        logged = min(an["results"], key=lambda r: abs(r["x"] - float(fx["logged_decision_x"])))
        self.assertTrue(_covers(logged, fx["next_type"], tops), "%s: the logged v733 drop should have covered an open top" % name)
        self.assertFalse(_covers(chosen, fx["next_type"], tops), "%s: v734 drop must not cover an open T9-T11 top" % name)
        self.assertEqual(chosen["merge_grade"], "NO")
        self.assertFalse(chosen["crosses_deadline"])
        return x, chosen, gs

    def test_t2_does_not_bury_open_t9(self):
        self._assert_uncovered("low_drop_cover_t2_turn9.json")

    def test_t3_does_not_bury_open_t11_outside_old_band(self):
        # HIGH_TYPE_COVER_AVOID の帯域 (r*0.9=0.88) の外側でも Unity 水平半径 (1.39) で守る
        self.assertAlmostEqual(bs.seed_horiz_radius(11), 1.39, places=2)
        self._assert_uncovered("low_drop_cover_t3_turn41.json")

    def test_t4_does_not_bury_open_t10(self):
        self._assert_uncovered("low_drop_cover_t4_turn60.json")

    def test_failsafe_keeps_decision_when_no_clean_alternative(self):
        fx = _load("low_drop_cover_failsafe_turn74.json")
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(TAG, reason)
        self.assertAlmostEqual(x, float(fx["logged_decision_x"]), delta=0.06)

    def _base_tagged(self):
        """failsafe fixture: v734 でも決定は変わらないがタグが付く (= 罰則が評価されている) 局面。"""
        fx = _load("low_drop_cover_failsafe_turn74.json")
        gs = _gs(fx)
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        self.assertIn(TAG, str(strategy.decide(gs, an).get("reason", "")), "precondition: base decision must carry the tag")
        return fx, gs, an

    def test_covered_top_is_not_penalised(self):
        fx, gs, an = self._base_tagged()
        tops = _open_tops(gs["pieces"])
        self.assertTrue(tops)
        for lx, lh, ltop in tops:  # 開いた T9〜T11 を全て小駒で塞ぐ → 罰則対象が無くなる
            gs["pieces"].append({"id": 9900 + len(gs["pieces"]), "type": 1, "x": lx, "y": ltop + 0.20 + 0.01, "r": 0.2, "angle": 0.0})
        self.assertEqual(_open_tops(gs["pieces"]), [])
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))

    def test_domain_exclusions(self):
        # 各ガードを 1 つずつ外すと (precondition でタグが付く局面で) タグが消えることを確認する
        fx, gs, an = self._base_tagged()
        # margin ちょうど 1.0 は発火 (>= 1.0)
        an2 = sr.build_analysis(_gs(fx)); gs2 = _gs(fx); sr.enrich_game_state_deadline_fields(gs2, an2)
        an2["reactor"]["deadline_margin"] = 1.0
        self.assertIn(TAG, str(strategy.decide(gs2, an2).get("reason", "")))
        # margin < 1.0 は不発
        an2["reactor"]["deadline_margin"] = 0.99
        self.assertNotIn(TAG, str(strategy.decide(gs2, an2).get("reason", "")))
        # ロシア在盤は不発
        gs3 = _gs(fx)
        gs3["pieces"].append({"id": 9901, "type": 15, "x": 2.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        an3 = sr.build_analysis(gs3); sr.enrich_game_state_deadline_fields(gs3, an3)
        self.assertNotIn(TAG, str(strategy.decide(gs3, an3).get("reason", "")))
        # next_type >= 9 (v731/v732 の領域) は不発
        gs4 = _gs(fx); gs4["next"] = {"type": 9, "r": ab.TYPE_RADII[9]}
        an4 = sr.build_analysis(gs4); sr.enrich_game_state_deadline_fields(gs4, an4)
        self.assertNotIn(TAG, str(strategy.decide(gs4, an4).get("reason", "")))
        # deadline_crossed は早期 return (NO_MERGE_DEADLINE_GUARD) 側に入るのでタグは付かない
        gs5 = _gs(fx); an5 = sr.build_analysis(gs5); sr.enrich_game_state_deadline_fields(gs5, an5); gs5["deadline_crossed"] = True
        self.assertNotIn(TAG, str(strategy.decide(gs5, an5).get("reason", "")))

    def test_t8_onto_open_t9_is_exempt(self):
        fx = _load("low_drop_cover_t2_turn9.json")  # 開いた T9 がある局面で next=T8 は縦積み候補として免除
        gs = _gs(fx); gs["next"] = {"type": 8, "r": ab.TYPE_RADII[8]}
        an = sr.build_analysis(gs); sr.enrich_game_state_deadline_fields(gs, an)
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))

    def test_no_bonus_interaction_with_seed_axes(self):
        fx = _load("seed_contact_t11_turn48.json")  # next=T11: v734 は不発、v731 は従来どおり
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertNotIn(TAG, reason)
        self.assertIn("SAME_TYPE_SEED_CONTACT", reason)

    def test_malformed_high_piece_geometry_does_not_raise(self):
        fx = _load("low_drop_cover_t2_turn9.json")
        gs = _gs(fx)
        for p in gs["pieces"]:
            if 9 <= p["type"] <= 11:
                p["x"] = None
        an = sr.build_analysis(gs)
        sr.enrich_game_state_deadline_fields(gs, an)
        d = strategy.decide(gs, an)
        self.assertIn("x", d)


if __name__ == "__main__":
    unittest.main()

"""v770 LADDER_STACK: 小さい駒を上・大きい駒を下に積む。

なぜ (実測 2026-10-25, 本番 120 試合の非併合手):
  * 非併合で置いた駒の真下が T+1 だと、のちの併合で連鎖が平均 2.7〜3.2 回（他 ~2.3、床 ~1.5）。
  * 露出した T+1 がある非併合手は 43% なのに、実際に T+1 に載せたのは 8%。
  * 露出した小型 (T1-4) を 2 段以上大きい駒で覆うと 20 手以内の併合率が 29%（小さい駒が載ると 54%）。
  * landing_hit_id が T+1 でも、中心から半径 0.25 以内なら 61%、0.8 超では 13% しか上に止まらない。

ここでは (1) 重み関数、(2) 中心ずれと 2 段目の判定、(3) 実履歴の局面をフルパイプラインに通したときに
ラダーへ寄ること・DIRECT 併合と締切の安全を壊さないこと・_V770_LADDER_W=0 で旧挙動に戻ること、を固定する。
"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "1")
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


class _WeightMixin:
    def setUp(self):
        # 本番と同じ重み (V763=1.0, V770=1.0) を明示する。重みは env ではなくモジュール定数。
        self._saved = (strategy._V763_DIVERSITY_W, strategy._V770_LADDER_W)
        strategy._V763_DIVERSITY_W = 1.0
        strategy._V770_LADDER_W = 1.0

    def tearDown(self):
        strategy._V763_DIVERSITY_W, strategy._V770_LADDER_W = self._saved


class WeightTests(_WeightMixin, unittest.TestCase):
    def _w(self, value):
        strategy._V770_LADDER_W = value
        return strategy._v770_weight()

    def test_enabled_by_default_and_switchable_off(self):
        self.assertEqual(self._saved[1], 1.0, "既定で有効")
        self.assertEqual(self._w(1.0), 1.0)
        self.assertEqual(self._w(0.0), 0.0)

    def test_bad_values_and_saturation(self):
        for bad in ("abc", None, -1.0, float("nan")):
            self.assertEqual(self._w(bad), 0.0, repr(bad))
        self.assertEqual(self._w(50.0), 4.0)

    def test_production_v763_weight_is_kept_without_env(self):
        os.environ["V763_DIVERSITY_W"] = "0"
        try:
            self.assertEqual(strategy._v763_weight(), 1.0, "env では切り替わらない (本番値 1.0 を定数で持つ)")
        finally:
            os.environ.pop("V763_DIVERSITY_W", None)


class GeometryTests(unittest.TestCase):
    def test_center_offset_is_relative_to_target_radius(self):
        target = {"id": 1, "type": 9, "x": 1.0, "y": -3.0}
        self.assertAlmostEqual(strategy._v770_center_offset(target, 1.0), 0.0)
        r = strategy.board_stats.seed_horiz_radius(9)
        self.assertAlmostEqual(strategy._v770_center_offset(target, 1.0 + r * 0.5), 0.5, places=6)
        self.assertIsNone(strategy._v770_center_offset({"type": 9, "x": "bad"}, 0.0))

    def test_rests_on_type_detects_a_two_step_ladder(self):
        upper = {"id": 1, "type": 6, "x": 0.0, "y": -2.9}
        lower = {"id": 2, "type": 7, "x": 0.05, "y": -3.9}
        side = {"id": 3, "type": 7, "x": 2.5, "y": -3.9}
        self.assertTrue(strategy._v770_rests_on_type(upper, 7, [upper, lower]))
        self.assertFalse(strategy._v770_rests_on_type(upper, 7, [upper, side]), "横にあるだけでは支えていない")
        self.assertFalse(strategy._v770_rests_on_type(upper, 8, [upper, lower]), "型が違えば 2 段目ではない")
        self.assertFalse(strategy._v770_rests_on_type(lower, 6, [upper, lower]), "上にある駒は支えではない")


class PipelineTests(_WeightMixin, unittest.TestCase):
    def test_ladder_cases_move_onto_the_open_next_type(self):
        for name in ("v770_ladder_changed_1.json", "v770_ladder_changed_2.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertIn("LADDER_STACK", reason, name)
            hit = {p["id"]: p for p in fx["pieces"]}.get(chosen.get("landing_hit_id"))
            self.assertIsNotNone(hit, name)
            self.assertEqual(hit["type"], fx["next_type"] + 1, name)
            self.assertLessEqual(strategy._v770_center_offset(hit, chosen["x"]), 0.5, name)
            self.assertFalse(chosen.get("crosses_deadline"), name)
            self.assertGreater(abs(x - fx["base_x"]), 0.05, "%s: root と同じ手では検証にならない" % name)

    def test_weight_zero_restores_root_decision(self):
        strategy._V770_LADDER_W = 0.0
        for name in ("v770_ladder_changed_1.json", "v770_ladder_changed_2.json", "v770_burial_changed_1.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertNotIn("LADDER_STACK", reason, name)
            self.assertNotIn("INVERTED_BURIAL", reason, name)
            self.assertLessEqual(abs(x - fx["base_x"]), 0.011, name)

    def test_burial_case_avoids_covering_an_open_small_piece(self):
        fx = _load("v770_burial_changed_1.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        hit = {p["id"]: p for p in fx["pieces"]}.get(chosen.get("landing_hit_id"))
        small_open = {p["id"] for t in range(1, min(8, fx["next_type"] - 2) + 1)
                      for p in strategy._v742_open_twin(fx["pieces"], t)}
        self.assertFalse(hit is not None and hit["id"] in small_open, "露出した小型を下敷きにしない")
        self.assertFalse(chosen.get("crosses_deadline"))

    def test_direct_merge_unchanged(self):
        fx = _load("v748_direct_unchanged_1_turn3.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertIn("DIRECT_MERGE", reason)
        self.assertNotIn("LADDER_STACK", reason)
        self.assertLessEqual(abs(x - fx["base_x"]), 0.011)

    def test_no_fire_when_deadline_margin_tight(self):
        fx = _load("v748_tight_margin_nofire_1_turn76.json")
        x, reason, chosen, an = _pipeline(_gs(fx))
        self.assertNotIn("LADDER_STACK", reason)
        self.assertNotIn("INVERTED_BURIAL", reason)


if __name__ == "__main__":
    unittest.main()

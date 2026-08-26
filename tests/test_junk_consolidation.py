"""v741: decide() の JUNK_CONSOLIDATION (T1–3 の非併合手を「ジャンク隅」±2.8 か、隅が塞がっていれば開いたジャンクの塊へ
寄せる)。実履歴の局面で v736 の選択より隅/塊に近づき、DIRECT 存在・T4 以上・ロシア在盤・締切余裕不足・壁際回転リスク・
被覆タグでは発火しないことを確認する。"""
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

FX = os.path.join(ROOT, "tests", "fixtures")
TAGS = ("JUNK_CORNER_CONSOLIDATION", "JUNK_CLUSTER_CONSOLIDATION")
COVER_TAGS = ("HIGH_TYPE_COVER_AVOID", "LOW_DROP_HIGH_LANE_COVER_AVOID")
CORNER = ("junk_corner_t1_turn3.json", "junk_corner_t1_turn11.json", "junk_corner_t1_turn50.json")
CLUSTER = ("junk_cluster_t3_turn9.json", "junk_cluster_t1_turn16.json")
WALL_NEG = "junk_cluster_t2_turn29.json"  # 塊へ寄せる候補が壁際回転リスク付きしかない → 不発


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


def _gs(fx):
    nt = fx["next_type"]
    nnt = fx.get("next_next_type", 5)
    return {"state": "MOVE", "score": fx.get("score", 0), "pieces": [dict(p) for p in fx["pieces"]], "shapes": fx["shapes"],
            "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)}, "nextNext": {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)}}


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


def _types(gs):
    return {p["id"]: p["type"] for p in gs["pieces"]}


class JunkConsolidationTest(unittest.TestCase):
    def _assert_positive(self, name, tag):
        fx = _load(name)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(tag, reason, name)
        self.assertLessEqual(fx["next_type"], 3)
        self.assertFalse(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertEqual(chosen["merge_grade"], "NO")
        self.assertFalse(chosen["crosses_deadline"])
        self.assertFalse(chosen.get("wall_rotation_risk"))
        hit = chosen.get("landing_hit_id")
        self.assertTrue(hit is None or _types(gs).get(hit, 99) <= 4, "%s: lands on %s" % (name, _types(gs).get(hit)))
        for t in COVER_TAGS:
            self.assertNotIn(t, reason)
        self.assertNotAlmostEqual(x, float(fx["baseline_x"]), delta=0.05, msg="%s: pick must change" % name)
        return x, reason, chosen, gs, an

    def test_corner_fixtures(self):
        for name in CORNER:
            with self.subTest(fixture=name):
                x, reason, chosen, gs, an = self._assert_positive(name, "JUNK_CORNER_CONSOLIDATION")
                self.assertAlmostEqual(abs(x), 2.8, delta=0.05)

    def test_cluster_fixtures(self):
        for name in CLUSTER:
            with self.subTest(fixture=name):
                x, reason, chosen, gs, an = self._assert_positive(name, "JUNK_CLUSTER_CONSOLIDATION")
                junk = [p for p in gs["pieces"] if p["type"] <= 4]
                self.assertTrue(any(abs(p["x"] - x) <= 1.0 for p in junk))

    def test_wall_risk_candidates_are_not_rewarded(self):
        fx = _load(WALL_NEG)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertFalse(chosen.get("wall_rotation_risk"))
        self.assertFalse(any(t in reason for t in TAGS))
        self.assertAlmostEqual(x, float(fx["baseline_x"]), delta=0.05)

    def test_type4_and_direct_and_russia_disable(self):
        fx = _load("junk_corner_t1_turn3.json")
        gs = _gs(fx)
        gs["next"] = {"type": 4, "r": ab.TYPE_RADII.get(4, 0.5)}
        self.assertFalse(any(t in _pipeline(gs)[1] for t in TAGS))
        fxd = _load("seed_contact_direct_available.json")
        x, reason, chosen, an = _pipeline(_gs(fxd))
        self.assertTrue(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertFalse(any(t in reason for t in TAGS))
        gs = _gs(fx)
        gs["pieces"].append({"id": 9901, "type": 15, "x": 0.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        self.assertFalse(any(t in _pipeline(gs)[1] for t in TAGS))

    def test_margin_gate(self):
        fx = _load("junk_corner_t1_turn11.json")
        gs = _gs(fx)
        an = _analysis(gs)
        self.assertTrue(any(t in str(strategy.decide(gs, an).get("reason", "")) for t in TAGS))
        for r in an["results"]:
            r["deadline_margin"] = 1.99
        self.assertFalse(any(t in str(strategy.decide(gs, an).get("reason", "")) for t in TAGS))
        an = _analysis(gs)
        an["reactor"]["deadline_margin"] = 0.99
        self.assertFalse(any(t in str(strategy.decide(gs, an).get("reason", "")) for t in TAGS))

    def test_blocked_corners_fall_back_to_cluster_or_nothing(self):
        # 両壁を開いた T10 で塞ぐと隅は使えず、開いたジャンク塊も無ければ不発
        fx = _load("junk_corner_t1_turn3.json")
        gs = _gs(fx)
        for i, x in enumerate((-2.6, 2.6)):
            gs["pieces"].append({"id": 9910 + i, "type": 10, "x": x, "y": ab.FLOOR_Y + 1.0, "r": ab.TYPE_RADII[10], "angle": 0.0})
        gs["pieces"] = [p for p in gs["pieces"] if p["type"] > 4 or p["id"] >= 9910]
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn("JUNK_CORNER_CONSOLIDATION", reason)
        self.assertNotIn("JUNK_CLUSTER_CONSOLIDATION", reason)
        hit = chosen.get("landing_hit_id")
        self.assertTrue(hit is None or _types(gs).get(hit, 99) <= 4 or not any(t in reason for t in TAGS))

    def test_corner_never_covers_open_high_piece(self):
        fx = _load("junk_corner_t1_turn3.json")
        gs = _gs(fx)
        gs["pieces"].append({"id": 9920, "type": 10, "x": 2.6, "y": ab.FLOOR_Y + 1.0, "r": ab.TYPE_RADII[10], "angle": 0.0})
        x, reason, chosen, an = _pipeline(gs)
        hit = chosen.get("landing_hit_id")
        if "JUNK_CORNER_CONSOLIDATION" in reason:
            self.assertTrue(hit is None or _types(gs).get(hit, 99) <= 4)
            self.assertAlmostEqual(x, -2.8, delta=0.05)  # 右隅は T10 で塞がれた → 左隅
        for t in COVER_TAGS:
            self.assertNotIn(t, reason)

    def test_fail_closed(self):
        fx = _load("junk_corner_t1_turn11.json")
        gs = _gs(fx)
        for p in gs["pieces"]:
            if p["type"] <= 4:
                p["x"] = None
        an = _analysis(gs)
        d = strategy.decide(gs, an)
        self.assertIn("x", d)
        gs = _gs(fx)
        an = _analysis(gs)
        for r in an["results"]:
            r["landing_hit_id"] = "zzz"
        d = strategy.decide(gs, an)
        self.assertIn("x", d)
        self.assertFalse(any(t in str(d.get("reason", "")) for t in TAGS))


if __name__ == "__main__":
    unittest.main()

"""v736: decide() の PROBABLE_MERGE_CONTACT (NO 判定盤面で、上端の開いた同型相方への軸別 contact_gap が
小さい候補に確率比例の加点)。実履歴の局面 (angle 付き) で v734 の選択より contact_gap が縮み、埋もれた相方・
危険域・ロシア在盤・DIRECT 候補がある盤面では発火せず、被覆タグ付き候補 (開いた高型レーンを覆う手) には
gap 0 でも加点しないことを確認する。"""
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
TAG = "PROBABLE_MERGE_CONTACT"
COVER_TAGS = ("HIGH_TYPE_COVER_AVOID", "LOW_DROP_HIGH_LANE_COVER_AVOID")
FIXTURES = ("probable_merge_t4_turn20.json", "probable_merge_t8_turn41.json", "probable_merge_t9_turn7.json")
# gap 0 の接触候補が開いた高型上端を覆う盤面 (被覆タグ除外の検出力用)
COVER_FIXTURE = "probable_merge_cover_t7_turn57.json"
# v731 の旧 fixture: v734 は被覆タグ付き x=+0.20 (等方 index -0.37) を選んでいたが、v736 は被覆タグを避けて
# x=-1.00 (contact_gap 0.30, index 0.79 のまま) を選ぶ。A/B 11,023 手で v731 指標が最も緩む盤面 (既知の
# トレードオフ) であり、被覆タグ除外の効果をこの盤面で固定する。
T11_TURN48 = "seed_contact_t11_turn48.json"


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


def _pipeline(gs):
    an = _analysis(gs)
    d = strategy.decide(gs, an)
    d["x"] = max(-3.0, min(3.0, float(d.get("x", 0))))
    d = sr.enforce_deadline_safety(d, an, gs, strategy)
    d = sr.apply_strategy_final_decision(strategy, d, an, gs)
    fx = max(-3.0, min(3.0, float(d.get("x", 0))))
    chosen = min(an["results"], key=lambda r: abs(r["x"] - fx))
    return fx, str(d.get("reason", "")), chosen, an


def _cgap(chosen, nt, pieces):
    ids = {p["id"] for p in pieces if p["type"] == nt}
    return min([m["contact_gap"] for m in chosen["merges"] if m["id"] in ids] + [9.9])


class ProbableMergeContactTest(unittest.TestCase):
    def _assert_contact(self, name):
        fx = _load(name)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(TAG, reason, name)
        self.assertFalse(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]), "fixture must be a board-NO turn")
        self.assertEqual(chosen["merge_grade"], "NO")
        self.assertFalse(chosen["crosses_deadline"])
        # baseline_x = v734 (HEAD) のフルパイプライン再生で選ばれた x (本番ログの x は同ターンに接触を
        # 取っていた場合があるので比較には使わない)
        base = min(an["results"], key=lambda r: abs(r["x"] - float(fx.get("baseline_x", fx["logged_decision_x"]))))
        g0, g1 = _cgap(base, fx["next_type"], gs["pieces"]), _cgap(chosen, fx["next_type"], gs["pieces"])
        self.assertLess(g1, g0, "%s: contact gap %.3f -> %.3f" % (name, g0, g1))
        self.assertLessEqual(g1, 0.2)
        for t in COVER_TAGS:
            self.assertNotIn(t, reason)
        return x, reason, chosen, gs

    def test_t4_takes_the_contact_shot(self):
        self._assert_contact("probable_merge_t4_turn20.json")

    def test_t8_takes_the_contact_shot_across_the_board(self):
        self._assert_contact("probable_merge_t8_turn41.json")

    def test_t9_coexists_with_seed_axis(self):
        x, reason, chosen, gs = self._assert_contact("probable_merge_t9_turn7.json")
        self.assertIn("SAME_TYPE_SEED_CONTACT", reason)

    def test_buried_partner_is_not_a_target(self):
        fx = _load("probable_merge_t4_turn20.json")
        nt = fx["next_type"]
        gs = _gs(fx)
        for p in [q for q in gs["pieces"] if q["type"] == nt]:  # 全相方の上端を小駒で塞ぐ
            gs["pieces"].append({"id": 9900 + p["id"], "type": 1, "x": p["x"], "y": p["y"] + bs.seed_top_radius(nt) + 0.21, "r": 0.2, "angle": 0.0})
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)

    def test_cover_tagged_contact_is_not_rewarded(self):
        # gap 0 の接触候補 (x=-1.6) が開いた高型上端を覆う盤面: 被覆タグ除外が無いと v736 はそこへ +800 して
        # 被覆タグと同時付与で x=-1.6 を選ぶ。現行は接触候補を取らず、タグも付かない。
        fx = _load(COVER_FIXTURE)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertFalse(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        contact = [r for r in an["results"] if _cgap(r, fx["next_type"], gs["pieces"]) <= 0.2 and not r["crosses_deadline"]]
        self.assertTrue(contact, "fixture must offer a deadline-safe gap<=0.2 contact candidate")
        self.assertNotIn(TAG, reason)
        self.assertGreater(_cgap(chosen, fx["next_type"], gs["pieces"]), 0.2)

    def test_t11_turn48_escapes_cover_tag(self):
        fx = _load(T11_TURN48)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertIn(TAG, reason)
        for t in COVER_TAGS:
            self.assertNotIn(t, reason)
        self.assertLessEqual(_cgap(chosen, fx["next_type"], gs["pieces"]), 0.35)
        self.assertIn("SAME_TYPE_SEED_CONTACT", reason)

    def test_cover_tags_never_coexist_with_bonus(self):
        names = FIXTURES + (COVER_FIXTURE, T11_TURN48, "seed_contact_t11_turn15.json", "anchor_lane_t11_beside_turn50.json",
                            "low_drop_cover_t2_turn9.json", "low_drop_cover_t3_turn41.json")
        fired = 0
        for name in names:
            with self.subTest(fixture=name):
                x, reason, chosen, an = _pipeline(_gs(_load(name)))
                if TAG in reason:
                    fired += 1
                    for t in COVER_TAGS:
                        self.assertNotIn(t, reason)
        self.assertGreaterEqual(fired, 4)

    def test_domain_exclusions(self):
        fx = _load("probable_merge_t4_turn20.json")
        nt = fx["next_type"]
        gs = _gs(fx)
        an = _analysis(gs)
        an["reactor"]["deadline_margin"] = 1.0
        self.assertIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        an["reactor"]["deadline_margin"] = 0.99
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        # 締切系: v736 内の deadline_crossed / crosses_deadline 条件は多重防御で、実際には上流
        # (deadline_crossed かつ非併合の早期 return、NO+crossing 候補の事前除外) が先に効く。
        # ここではその上流の挙動 (タグ不発火) を固定する。
        gs = _gs(fx)
        an = _analysis(gs)
        gs["deadline_crossed"] = True
        d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))
        self.assertIn("DEADLINE_GUARD", str(d.get("reason", "")))
        gs = _gs(fx)
        an = _analysis(gs)
        for r in an["results"]:
            if _cgap(r, nt, gs["pieces"]) <= 1.0:
                r["crosses_deadline"] = True
        d = strategy.decide(gs, an)
        self.assertNotIn(TAG, str(d.get("reason", "")))
        self.assertGreater(_cgap(min(an["results"], key=lambda r: abs(r["x"] - float(d["x"]))), nt, gs["pieces"]), 1.0)
        gs = _gs(fx)
        gs["pieces"].append({"id": 9901, "type": 15, "x": 2.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        self.assertNotIn(TAG, _pipeline(gs)[1])
        # 盤上に DIRECT がある手では不発
        fxd = _load("seed_contact_direct_available.json")
        x, reason, chosen, an = _pipeline(_gs(fxd))
        self.assertTrue(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertNotIn(TAG, reason)

    def test_malformed_contact_gap_does_not_raise(self):
        fx = _load("probable_merge_t4_turn20.json")
        gs = _gs(fx)
        an = _analysis(gs)
        for r in an["results"]:
            for m in r.get("merges", []):
                m["contact_gap"] = "x"
        d = strategy.decide(gs, an)
        self.assertIn("x", d)
        self.assertNotIn(TAG, str(d.get("reason", "")))
        gs = _gs(fx)
        for p in gs["pieces"]:
            if p["type"] == fx["next_type"]:
                p["y"] = None
        an = _analysis(gs)
        self.assertIn("x", strategy.decide(gs, an))


if __name__ == "__main__":
    unittest.main()

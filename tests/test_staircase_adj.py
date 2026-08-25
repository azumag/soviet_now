"""v738: decide() の STAIRCASE_ADJ (同型相方が開いていない序盤の NO 盤面で、t+1 / t-1 型の開いた駒の「横」に
着地する候補へ +300×ramp×phase)。実履歴の局面で v736 の選択より横隣接 gap が縮み、開いた同型相方あり・埋没した
対象・重なり無し・gap 遠い・侵入・終盤・被覆タグ・壁際では発火しない (各否定テストは「発火しうる候補が存在する」
ことを幾何で確認して空虚にならないようにする)。"""
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
TAG = "STAIRCASE_ADJ"
COVER_TAGS = ("HIGH_TYPE_COVER_AVOID", "LOW_DROP_HIGH_LANE_COVER_AVOID", "T12_PAIR_COVER_AVOID")
POSITIVE = ("staircase_F1_up_t8.json", "staircase_F1_up_t5.json", "staircase_F2_dn_t5.json", "staircase_F2_dn_t2.json",
            "staircase_F3_gate_buried_same_1.json", "staircase_F3_gate_buried_same_3.json", "staircase_F7_phase_ramp.json")
# 変異 (ゲート除去 / 位相除去 / 侵入床除去 / 被覆タグ除外の除去) では決定が変わり STAIRCASE_ADJ が付く実局面。
# 現行では不発かつ v736 と同じ x を選ぶ (mutant_x は変異時の選択)。
MUTATION_FIXTURES = ("staircase_m_gate_removed_1.json", "staircase_m_gate_removed_2.json", "staircase_m_phase_removed_1.json",
                     "staircase_m_floor_removed_1.json", "staircase_m_floor_removed_2.json", "staircase_m_no_high_cover_1.json",
                     "staircase_m_no_high_cover_2.json", "staircase_m_no_low_cover_1.json", "staircase_m_no_low_cover_2.json",
                     "staircase_m_no_t12_cover_1.json", "staircase_m_no_t12_cover_3.json")
NEGATIVE = ("staircase_F3_gate_open_same.json", "staircase_F4_buried_target.json", "staircase_F5_overlap.json", "staircase_F6_gap_far.json",
            "staircase_F6_intrusion.json", "staircase_F7_phase_late.json", "staircase_F8_cover.json", "staircase_F9_wall.json")


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
    fx = max(-3.0, min(3.0, float(d.get("x", 0))))
    chosen = min(an["results"], key=lambda r: abs(r["x"] - fx))
    return fx, str(d.get("reason", "")), chosen, an


def _is_open(p, pieces):
    h = bs.seed_horiz_radius(p["type"])
    top = p["y"] + bs.seed_top_radius(p["type"])
    for o in pieces:
        if o["id"] == p["id"]:
            continue
        if abs(o["x"] - p["x"]) <= h and o["y"] - bs.seed_bottom_radius(o["type"]) >= top - 0.25:
            return False
    return True


def _geom(cand, pieces, nt, need_overlap=True):
    """候補の設定着地と、開いた t±1 駒との最小横 gap (v738 と同じ幾何)。need_overlap=False なら縦重なり条件を外す。"""
    sx = cand["x"] + (cand.get("drift_x") or 0.0)
    ly = cand["landing_y"]
    cb, ct = ly - bs.seed_bottom_radius(nt), ly + bs.seed_top_radius(nt)
    best = 9.9
    for p in pieces:
        if p["type"] not in (nt + 1, nt - 1) or not _is_open(p, pieces):
            continue
        ttop, tbot = p["y"] + bs.seed_top_radius(p["type"]), p["y"] - bs.seed_bottom_radius(p["type"])
        if need_overlap and min(ct, ttop) - max(cb, tbot) < 0.15:
            continue
        g = abs(sx - p["x"]) - (bs.seed_horiz_radius(nt) + bs.seed_horiz_radius(p["type"]))
        if g >= -0.20 and g < best:
            best = g
    return best


def _safe(an):
    return [r for r in an["results"] if not r.get("crosses_deadline")]


class StaircaseAdjTest(unittest.TestCase):
    def _assert_positive(self, name):
        fx = _load(name)
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        nt = fx["next_type"]
        self.assertIn(TAG, reason, name)
        self.assertFalse(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]), "must be a board-NO turn")
        self.assertFalse(any(p["type"] == nt and _is_open(p, gs["pieces"]) for p in gs["pieces"]), "no open same-type partner")
        base = min(an["results"], key=lambda r: abs(r["x"] - float(fx["baseline_x"])))
        g0, g1 = _geom(base, gs["pieces"], nt), _geom(chosen, gs["pieces"], nt)
        self.assertLess(g1, g0, "%s: hgap %.2f -> %.2f" % (name, g0, g1))
        self.assertLessEqual(g1, 0.20)
        self.assertFalse(chosen["crosses_deadline"])
        for t in COVER_TAGS:
            self.assertNotIn(t, reason)
        return x, reason, chosen, gs, an

    def test_positive_up_and_down_neighbors(self):
        for name in POSITIVE:
            with self.subTest(fixture=name):
                self._assert_positive(name)

    def test_t_minus_one_neighbor_is_a_target(self):
        # F2: 盤上に t+1 は無く t-1 だけがある局面で発火する (t+1 限定の変異を検出)
        for name in ("staircase_F2_dn_t5.json", "staircase_F2_dn_t2.json"):
            fx = _load(name)
            nt = fx["next_type"]
            self.assertFalse(any(p["type"] == nt + 1 for p in fx["pieces"]), name)
            self.assertTrue(any(p["type"] == nt - 1 for p in fx["pieces"]), name)
            self._assert_positive(name)

    def test_open_same_type_partner_silences(self):
        fx = _load("staircase_F3_gate_open_same.json")
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        nt = fx["next_type"]
        self.assertTrue(any(p["type"] == nt and _is_open(p, gs["pieces"]) for p in gs["pieces"]))
        self.assertTrue(any(_geom(r, gs["pieces"], nt) <= 0.45 for r in _safe(an)), "a beside candidate must exist")
        self.assertNotIn(TAG, reason)
        self.assertIn("PROBABLE_MERGE_CONTACT", reason)

    def test_buried_target_does_not_count_until_uncovered(self):
        fx = _load("staircase_F1_up_t8.json")
        nt = fx["next_type"]
        gs = _gs(fx)
        targets = [p for p in gs["pieces"] if p["type"] in (nt + 1, nt - 1)]
        self.assertTrue(targets)
        for p in targets:  # 全対象の上端を小駒で塞ぐ
            gs["pieces"].append({"id": 9900 + p["id"], "type": 1, "x": p["x"], "y": p["y"] + bs.seed_top_radius(p["type"]) + 0.21, "r": 0.2, "angle": 0.0})
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)
        # 実局面 F4 (t+1 が埋没、横候補は開いた高型を覆う) でも不発
        self.assertNotIn(TAG, _pipeline(_gs(_load("staircase_F4_buried_target.json")))[1])

    def test_vertical_overlap_required(self):
        fx = _load("staircase_F5_overlap.json")
        gs = _gs(fx)
        nt = fx["next_type"]
        x, reason, chosen, an = _pipeline(gs)
        # 横 gap は小さいが縦の重なりが無い候補が存在する (上に乗る配置) → 不発
        self.assertTrue(any(_geom(r, gs["pieces"], nt, need_overlap=False) <= 0.10 for r in _safe(an)))
        self.assertTrue(all(_geom(r, gs["pieces"], nt) > 0.45 for r in _safe(an)))
        self.assertNotIn(TAG, reason)

    def test_gap_window(self):
        # 開いた t±1 はあるが、全候補の横 gap が 0.45 を超える局面では不発 (G の拡大変異を検出)
        fx = _load("staircase_F6_gap_far.json")
        gs = _gs(fx)
        nt = fx["next_type"]
        x, reason, chosen, an = _pipeline(gs)
        self.assertTrue(any(p["type"] in (nt + 1, nt - 1) and _is_open(p, gs["pieces"]) for p in gs["pieces"]))
        gaps = [_geom(r, gs["pieces"], nt) for r in _safe(an)]
        self.assertTrue(all(g > 0.45 for g in gaps))
        self.assertLessEqual(min(gaps), 0.90)
        self.assertNotIn(TAG, reason)

    def test_phase_gate(self):
        fx = _load("staircase_F7_phase_late.json")
        gs = _gs(fx)
        nt = fx["next_type"]
        self.assertGreaterEqual(len(gs["pieces"]), 34)
        x, reason, chosen, an = _pipeline(gs)
        self.assertTrue(any(_geom(r, gs["pieces"], nt) <= 0.20 for r in _safe(an)), "a beside candidate must exist")
        self.assertNotIn(TAG, reason)
        # 30 駒 (ランプ域) では発火する
        fx = _load("staircase_F7_phase_ramp.json")
        self.assertTrue(22 < len(fx["pieces"]) < 34)
        self._assert_positive("staircase_F7_phase_ramp.json")
        # 序盤の正例から駒数を 34 以上に水増し (遠くの床に小駒を並べる) すると不発
        fx = _load("staircase_F1_up_t8.json")
        gs = _gs(fx)
        i = 0
        while len(gs["pieces"]) < 34:
            gs["pieces"].append({"id": 8000 + i, "type": 1, "x": 2.6 + 0.01 * i, "y": ab.FLOOR_Y + 0.21 + 0.42 * i, "r": 0.2, "angle": 0.0})
            i += 1
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)

    def test_cover_tags_exclude_and_never_coexist(self):
        fx = _load("staircase_F8_cover.json")
        gs = _gs(fx)
        nt = fx["next_type"]
        x, reason, chosen, an = _pipeline(gs)
        self.assertTrue(any(_geom(r, gs["pieces"], nt) <= 0.20 for r in _safe(an)), "a beside candidate must exist")
        self.assertNotIn(TAG, reason)
        fired = 0
        for name in POSITIVE + NEGATIVE + MUTATION_FIXTURES:
            with self.subTest(fixture=name):
                r = _pipeline(_gs(_load(name)))[1]
                if TAG in r:
                    fired += 1
                    for t in COVER_TAGS:
                        self.assertNotIn(t, r)
        self.assertGreaterEqual(fired, 7)

    def test_mutation_guard_fixtures(self):
        for name in MUTATION_FIXTURES:
            with self.subTest(fixture=name):
                fx = _load(name)
                gs = _gs(fx)
                nt = fx["next_type"]
                x, reason, chosen, an = _pipeline(gs)
                self.assertNotIn(TAG, reason)
                self.assertAlmostEqual(x, float(fx["baseline_x"]), delta=0.05)
                self.assertNotAlmostEqual(x, float(fx["mutant_x"]), delta=0.05)
                if "gate_removed" in name:  # 開いた同型相方がある (沈黙ゲートの対象)
                    self.assertTrue(any(p["type"] == nt and _is_open(p, gs["pieces"]) for p in gs["pieces"]))
                if "phase_removed" in name:
                    self.assertGreaterEqual(len(gs["pieces"]), 34)
                if "cover" in name:
                    for t in COVER_TAGS:
                        self.assertNotIn(t + "_" + TAG, reason)

    def test_wall_rotation_damping(self):
        fx = _load("staircase_F9_wall.json")
        gs = _gs(fx)
        x, reason, chosen, an = _pipeline(gs)
        self.assertNotIn(TAG, reason)
        self.assertFalse(chosen.get("wall_rotation_risk"))

    def test_domain_exclusions_and_fail_closed(self):
        fx = _load("staircase_F1_up_t8.json")
        nt = fx["next_type"]
        gs = _gs(fx)
        an = _analysis(gs)
        an["reactor"]["deadline_margin"] = 1.0
        self.assertIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        an["reactor"]["deadline_margin"] = 0.99
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        # 横候補だけを crossing にすると不発 (margin>=1.0 では上流の除外が走らないので到達する)
        gs = _gs(fx)
        an = _analysis(gs)
        flipped = 0
        for r in an["results"]:
            if _geom(r, gs["pieces"], nt) <= 0.45:
                r["crosses_deadline"] = True
                flipped += 1
        self.assertGreater(flipped, 0)
        self.assertNotIn(TAG, str(strategy.decide(gs, an).get("reason", "")))
        # ロシア在盤 / DIRECT あり
        gs = _gs(fx)
        gs["pieces"].append({"id": 9901, "type": 15, "x": 2.0, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0})
        self.assertNotIn(TAG, _pipeline(gs)[1])
        fxd = _load("seed_contact_direct_available.json")
        x, reason, chosen, an = _pipeline(_gs(fxd))
        self.assertTrue(any(r["merge_grade"] in ("DIRECT", "NEAR") for r in an["results"]))
        self.assertNotIn(TAG, reason)
        # fail-closed: 対象の y=None / landing_y 非数値
        gs = _gs(fx)
        for p in gs["pieces"]:
            if p["type"] in (nt + 1, nt - 1):
                p["y"] = None
        an = _analysis(gs)
        d = strategy.decide(gs, an)
        self.assertIn("x", d)
        self.assertNotIn(TAG, str(d.get("reason", "")))


if __name__ == "__main__":
    unittest.main()

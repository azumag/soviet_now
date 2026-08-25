"""v729: analyze_board の併合後ピース上端較正 (ANALYZE_BOARD_MERGE_TOP_MODEL)。

- 旧式 (mode 0) は helper を旧値に固定した出力と完全一致 (旧挙動)。
- 較正値は全候補・全モードで旧式を上回らない (単調キャップ) → 締切安全プールは縮まない。
- mode 2 は候補自身の risk_top/crosses_deadline/deadline_margin を変えない。mode 1 は変え得る。
- 実測 fixture: 旧式が誤警報した併合 (lifted) は許可され実測上端に余裕がある、旧式の唯一の見逃しは
  較正後も同じ (改善を装わない)、手動 99 手目は許可される。
- fail-closed: T16 (eff_radii に無い) / 非有限 / 例外 → 旧値。
"""
import json
import math
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import analyze_board as ab  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
TOGGLE = "ANALYZE_BOARD_MERGE_TOP_MODEL"
RANK = {"NO": 0, "NEAR": 1, "DIRECT": 2}


def _load(name):
    with open(os.path.join(FX, name), encoding="utf-8") as fh:
        return json.load(fh)


def _run(fx, mode):
    prev = os.environ.get(TOGGLE)
    os.environ[TOGGLE] = mode
    try:
        res, _ = ab.analyze_drops(fx["pieces"], fx["next_type"], fx["next_r"], fx["shapes"])
    finally:
        if prev is None:
            os.environ.pop(TOGGLE, None)
        else:
            os.environ[TOGGLE] = prev
    return res


def _at(res, x):
    return min(res, key=lambda r: abs(r["x"] - x))


BOARDS = (
    "merge_top_model_lifted.json",
    "merge_top_model_inherited_miss.json",
    "merge_top_model_manual99.json",
    "vertical_lane_direct_bigboard.json",
    "vertical_lane_direct_turn30.json",
)


class MergeTopModelTest(unittest.TestCase):
    def test_mode_variants(self):
        prev = os.environ.get(TOGGLE)
        try:
            for raw, mode in (("0", 0), ("false", 0), ("off", 0), ("1", 1), ("2", 2), ("", 2), ("x", 2)):
                os.environ[TOGGLE] = raw
                self.assertEqual(ab._merge_top_model_mode(), mode, raw)
            os.environ.pop(TOGGLE, None)
            self.assertEqual(ab._merge_top_model_mode(), 2)
        finally:
            if prev is None:
                os.environ.pop(TOGGLE, None)
            else:
                os.environ[TOGGLE] = prev

    def test_mode0_identical_to_legacy_helper(self):
        orig = ab._calibrated_merge_top
        for name in BOARDS:
            fx = _load(name)
            off = _run(fx, "0")
            ab._calibrated_merge_top = lambda legacy_top, *a, **k: legacy_top
            try:
                ref1 = _run(fx, "1")
                ref2 = _run(fx, "2")
            finally:
                ab._calibrated_merge_top = orig
            self.assertEqual(json.dumps(off, sort_keys=True), json.dumps(ref1, sort_keys=True), name)
            self.assertEqual(json.dumps(off, sort_keys=True), json.dumps(ref2, sort_keys=True), name)

    def test_monotone_cap_and_mode2_risk_invariance(self):
        mode1_differs = False
        for name in BOARDS:
            fx = _load(name)
            r0, r1, r2 = _run(fx, "0"), _run(fx, "1"), _run(fx, "2")
            self.assertEqual(len(r0), len(r1))
            self.assertEqual(len(r0), len(r2))
            for c0, c1, c2 in zip(r0, r1, r2):
                self.assertEqual(c0["x"], c2["x"])
                l0 = c0["merge_result_top_y"]
                for c in (c1, c2):
                    ln = c["merge_result_top_y"]
                    self.assertEqual(ln is None, l0 is None, name)
                    if l0 is not None:
                        self.assertLessEqual(ln, l0 + 1e-9, "%s x=%s" % (name, c0["x"]))
                        self.assertLessEqual(int(c["merge_result_crosses_deadline"]), int(c0["merge_result_crosses_deadline"]))
                    self.assertEqual(c["merge_grade"], c0["merge_grade"])
                # mode 2: 候補自身の締切フィールドは旧式と同一
                for k in ("risk_top_y_after_drop", "crosses_deadline", "deadline_margin", "top_y_after_drop"):
                    self.assertEqual(c2[k], c0[k], "%s x=%s %s" % (name, c0["x"], k))
                # mode 1: crosses_deadline は緩む方向のみ
                self.assertLessEqual(int(c1["crosses_deadline"]), int(c0["crosses_deadline"]))
                self.assertLessEqual(c1["risk_top_y_after_drop"], c0["risk_top_y_after_drop"] + 1e-9)
                if c1["risk_top_y_after_drop"] != c0["risk_top_y_after_drop"]:
                    mode1_differs = True
        self.assertTrue(mode1_differs, "mode 1 should relax risk_top on at least one candidate")

    def test_lifted_veto_has_real_headroom(self):
        fx = _load("merge_top_model_lifted.json")
        c0 = _at(_run(fx, "0"), fx["drop_x"])
        c2 = _at(_run(fx, "2"), fx["drop_x"])
        self.assertEqual(c0["merge_grade"], "DIRECT")
        self.assertGreaterEqual(c0["merge_result_top_y"], ab.DEADLINE_Y)
        self.assertTrue(c0["merge_result_crosses_deadline"])
        self.assertLess(c2["merge_result_top_y"], ab.DEADLINE_Y)
        self.assertFalse(c2["merge_result_crosses_deadline"])
        self.assertGreaterEqual(c2["merge_result_top_y"], fx["actual_merged_top"] + 0.4)

    def test_inherited_miss_is_not_pretended_fixed(self):
        fx = _load("merge_top_model_inherited_miss.json")
        c0 = _at(_run(fx, "0"), fx["drop_x"])
        c2 = _at(_run(fx, "2"), fx["drop_x"])
        self.assertAlmostEqual(c0["merge_result_top_y"], fx["legacy_merge_result_top_y"], places=2)
        self.assertLess(c0["merge_result_top_y"], fx["actual_merged_top"])  # 旧式の見逃し
        self.assertAlmostEqual(c2["merge_result_top_y"], c0["merge_result_top_y"], places=6)  # 較正は旧値を上限とするので同じ

    def test_manual_move99_unvetoed(self):
        fx = _load("merge_top_model_manual99.json")
        c0 = _at(_run(fx, "0"), fx["drop_x"])
        c2 = _at(_run(fx, "2"), fx["drop_x"])
        self.assertEqual(c0["merge_grade"], "DIRECT")
        self.assertTrue(c0["merge_result_crosses_deadline"])
        self.assertFalse(c2["merge_result_crosses_deadline"])
        self.assertGreater(c2["merge_result_top_y"], fx["actual_merged_top"])

    def test_fail_closed(self):
        fx = _load("merge_top_model_lifted.json")
        eff = ab.build_deadline_radii(fx["shapes"])
        target = next(p for p in fx["pieces"] if p["type"] == fx["next_type"])
        # T16 (ソ連) は eff_radii に無い → Lw None → 旧値
        self.assertIsNone(ab._merged_piece_landing_top(target, fx["pieces"], eff, 16, 0.9))
        self.assertEqual(ab._calibrated_merge_top(4.2, target, 3.0, 0.9, None), 4.2)
        lw = ab._merged_piece_landing_top(target, fx["pieces"], eff, fx["next_type"] + 1, 0.9)
        self.assertIsNotNone(lw)
        bad = dict(target)
        for v in (None, float("nan"), "y"):
            bad["y"] = v
            self.assertEqual(ab._calibrated_merge_top(4.2, bad, 3.0, 0.9, lw), 4.2)
        self.assertEqual(ab._calibrated_merge_top(4.2, target, float("nan"), 0.9, lw), 4.2)
        self.assertEqual(ab._calibrated_merge_top(4.2, target, 3.0, 0.9, float("inf")), 4.2)
        bad2 = dict(target)
        bad2["x"] = None
        self.assertIsNone(ab._merged_piece_landing_top(bad2, fx["pieces"], eff, fx["next_type"] + 1, 0.9))
        # 較正値は常に旧値以下
        self.assertLessEqual(ab._calibrated_merge_top(4.2, target, 3.0, 0.9, lw), 4.2)

    def test_t15_next_is_legacy_everywhere(self):
        """next=15 (併合結果 T16 は eff_radii に無い) では mode 2 の全出力が mode 0 と一致 (fail-closed)。"""
        base = _load("merge_top_model_lifted.json")
        floor_t15 = {"id": 1, "type": 15, "x": -0.8, "y": ab.FLOOR_Y + 1.062, "r": ab.TYPE_RADII[15], "angle": 0.0}
        fx = {"pieces": [floor_t15, {"id": 2, "type": 9, "x": 2.4, "y": ab.FLOOR_Y + 1.0, "r": 1.0, "angle": 0.0}],
              "next_type": 15, "next_r": ab.TYPE_RADII[15], "shapes": base["shapes"]}
        r0, r2 = _run(fx, "0"), _run(fx, "2")
        self.assertTrue(any(c["merge_grade"] == "DIRECT" for c in r0), "fixture must produce a T15 DIRECT candidate")
        self.assertTrue(any(c["merge_result_top_y"] is not None for c in r0))
        self.assertEqual(json.dumps(r0, sort_keys=True), json.dumps(r2, sort_keys=True))

    def test_landing_term_binds_on_real_board(self):
        """較正は Lw (列内着地) 項が支配的なケースを実際に持つ (dead code でない)。"""
        fx = _load("merge_top_model_lifted.json")
        eff = ab.build_deadline_radii(fx["shapes"])
        nt = fx["next_type"]
        R = ab.get_type_top_radius(nt + 1, fx["shapes"], eff)
        binds = 0
        for t in (p for p in fx["pieces"] if p["type"] == nt):
            lw = ab._merged_piece_landing_top(t, fx["pieces"], eff, nt + 1, R)
            self.assertIsNotNone(lw)
            ly_poly = t["y"] + 1.0
            blend = t["y"] + ab.MERGE_TOP_MODEL_BLEND_F * 1.0 + R + ab.MERGE_TOP_MODEL_MARGIN
            legacy = max(ly_poly, t["y"]) + R
            est = ab._calibrated_merge_top(legacy, t, ly_poly, R, lw)
            self.assertLessEqual(est, legacy)
            if lw > blend and est == min(legacy, lw):
                binds += 1
        self.assertGreater(binds, 0)

    def test_no_perf_regression(self):
        fx = _load("vertical_lane_direct_bigboard.json")
        t0 = time.perf_counter()
        _run(fx, "2")
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.5, "analyze_drops on %d pieces took %.3fs" % (len(fx["pieces"]), dt))


if __name__ == "__main__":
    unittest.main()

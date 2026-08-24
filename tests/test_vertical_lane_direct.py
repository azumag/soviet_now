"""v728: analyze_board の垂直開放レーン直撃 DIRECT 昇格 (ANALYZE_BOARD_VERTICAL_LANE_DIRECT)。

- 実履歴の局面 (angle 付き) で旧グレード NO → 新グレード DIRECT (mode 2 なら NEAR) になり、
  そのターゲットは次ターンで実際に消費されている (fixture 由来)。
- toggle=0 では helper を常に False にした出力と完全一致 (旧挙動)。
- 掠り (比率 1.9)・頭上被覆・不正入力では昇格しない (fail-closed)。
"""
import copy
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
TOGGLE = "ANALYZE_BOARD_VERTICAL_LANE_DIRECT"


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


class VerticalLaneDirectTest(unittest.TestCase):
    def test_turn30_promoted_from_no_to_direct(self):
        fx = _load("vertical_lane_direct_turn30.json")
        old = _at(_run(fx, "0"), fx["drop_x"])
        new = _at(_run(fx, "1"), fx["drop_x"])
        near = _at(_run(fx, "2"), fx["drop_x"])
        self.assertLess(abs(old["x"] - fx["drop_x"]), 0.06)
        tid = fx["target_id"]
        self.assertEqual(old["landing_hit_id"], tid)
        old_m = [m for m in old["merges"] if m["id"] == tid][0]
        new_m = [m for m in new["merges"] if m["id"] == tid][0]
        near_m = [m for m in near["merges"] if m["id"] == tid][0]
        self.assertEqual(old_m["grade"], "NO")
        self.assertEqual(old["merge_grade"], "NO")
        self.assertLessEqual(new_m["contact_gap"], ab.VERTICAL_LANE_MAX_GAP)
        self.assertEqual(new_m["grade"], "DIRECT")
        self.assertEqual(new["merge_grade"], "DIRECT")
        self.assertEqual(near_m["grade"], "NEAR")

    def test_toggle_off_is_identical_to_helper_disabled(self):
        for name in ("vertical_lane_direct_turn30.json", "vertical_lane_direct_bigboard.json"):
            fx = _load(name)
            off = _run(fx, "0")
            orig = ab._vertical_lane_direct
            ab._vertical_lane_direct = lambda *a, **k: False
            try:
                ref = _run(fx, "1")
            finally:
                ab._vertical_lane_direct = orig
            self.assertEqual(json.dumps(off, sort_keys=True), json.dumps(ref, sort_keys=True), name)

    def test_toggle_variants(self):
        prev = os.environ.get(TOGGLE)
        try:
            for raw, mode in (("0", 0), ("false", 0), ("off", 0), ("1", 1), ("", 1), ("2", 2), ("yes", 1)):
                os.environ[TOGGLE] = raw
                self.assertEqual(ab._vertical_lane_mode(), mode, raw)
            os.environ.pop(TOGGLE, None)
            self.assertEqual(ab._vertical_lane_mode(), 1)
        finally:
            if prev is None:
                os.environ.pop(TOGGLE, None)
            else:
                os.environ[TOGGLE] = prev

    def _pair_board(self, dx):
        """同 type 2 駒: ターゲット (id 1) の真上 dx ずれに落とす合成盤面。"""
        fx = _load("vertical_lane_direct_turn30.json")
        t = 6
        target = {"id": 1, "type": t, "x": -1.0, "y": -3.0, "r": 0.55, "angle": 0.0}
        base = {"id": 2, "type": 3, "x": 1.8, "y": -3.7, "r": 0.4, "angle": 0.0}
        pieces = [target, base]
        return pieces, t, 0.55, fx["shapes"], target

    def test_graze_ratio_not_promoted(self):
        pieces, t, r, shapes, target = self._pair_board(0.0)
        eff = ab.build_deadline_radii(shapes)
        horiz = ab.piece_deadline_horiz_radius(target, eff)
        drop_ext = ab._type_deadline_extents(t, r, eff)
        x = target["x"] + 1.9 * min(horiz, drop_ext["horiz"])
        ly, hit = ab.get_landing_info(x, r, pieces, eff, t)
        os.environ[TOGGLE] = "1"
        try:
            self.assertFalse(ab._vertical_lane_direct(x, ly, drop_ext, target, pieces, eff, 0.0))
            # 真上なら昇格条件を満たす
            ly0, hit0 = ab.get_landing_info(target["x"], r, pieces, eff, t)
            self.assertEqual(hit0, 1)
            self.assertTrue(ab._vertical_lane_direct(target["x"], ly0, drop_ext, target, pieces, eff, 0.0))
        finally:
            os.environ.pop(TOGGLE, None)

    def test_covered_target_not_promoted(self):
        pieces, t, r, shapes, target = self._pair_board(0.0)
        eff = ab.build_deadline_radii(shapes)
        drop_ext = ab._type_deadline_extents(t, r, eff)
        blocker = {"id": 3, "type": 4, "x": target["x"] + 0.2, "y": target["y"] + 1.2, "r": 0.45, "angle": 0.0}
        pieces2 = pieces + [blocker]
        ly, hit = ab.get_landing_info(target["x"], r, pieces2, eff, t)
        self.assertNotEqual(hit, 1)
        os.environ[TOGGLE] = "1"
        try:
            # 呼び出し規約違反 (hit != target): ly がターゲット上に無いので G3 で False
            self.assertFalse(ab._vertical_lane_direct(target["x"], ly, drop_ext, target, pieces2, eff, 0.0))
        finally:
            os.environ.pop(TOGGLE, None)

    def test_prominence_guard_rejects_flush_neighbour(self):
        """柱内の隣接駒の上端がターゲット上端とほぼ同じ高さ (< MIN_PROMINENCE) → パーチ保険で昇格しない。"""
        pieces, t, r, shapes, target = self._pair_board(0.0)
        eff = ab.build_deadline_radii(shapes)
        drop_ext = ab._type_deadline_extents(t, r, eff)
        ttop = ab.piece_deadline_top_y(target, eff)
        flush = {"id": 9, "type": 4, "x": target["x"] + 0.9, "y": 0.0, "r": 0.45, "angle": 0.0}
        flush["y"] = ttop - ab.piece_deadline_top_radius(flush, eff) - 0.01  # 上端がターゲット上端 - 0.01
        pieces2 = pieces + [flush]
        ly, hit = ab.get_landing_info(target["x"], r, pieces2, eff, t)
        self.assertEqual(hit, 1)
        os.environ[TOGGLE] = "1"
        try:
            self.assertFalse(ab._vertical_lane_direct(target["x"], ly, drop_ext, target, pieces2, eff, 0.0))
            flush["y"] -= 0.2  # 0.21 低ければ昇格
            ly2, hit2 = ab.get_landing_info(target["x"], r, pieces2, eff, t)
            self.assertEqual(hit2, 1)
            self.assertTrue(ab._vertical_lane_direct(target["x"], ly2, drop_ext, target, pieces2, eff, 0.0))
        finally:
            os.environ.pop(TOGGLE, None)

    def test_modes_never_demote_legacy_grade(self):
        """mode 1/2 は全候補・全ターゲットで toggle=0 のグレードを下回らない (H1 回帰)。"""
        rank = {"NO": 0, "NEAR": 1, "DIRECT": 2}
        for name in ("vertical_lane_direct_turn30.json", "vertical_lane_direct_bigboard.json"):
            fx = _load(name)
            off = {(c["x"], m["id"]): m["grade"] for c in _run(fx, "0") for m in c["merges"]}
            for mode in ("1", "2"):
                on = {(c["x"], m["id"]): m["grade"] for c in _run(fx, mode) for m in c["merges"]}
                self.assertEqual(set(on), set(off), name)
                worse = [k for k in off if rank[on[k]] < rank[off[k]]]
                self.assertEqual(worse, [], "%s mode %s demoted %d" % (name, mode, len(worse)))
                if mode == "2":
                    self.assertTrue(all(rank[on[k]] <= max(rank[off[k]], 1) for k in off), "mode 2 must not create DIRECT")

    def test_fail_closed_on_bad_input(self):
        pieces, t, r, shapes, target = self._pair_board(0.0)
        eff = ab.build_deadline_radii(shapes)
        drop_ext = ab._type_deadline_extents(t, r, eff)
        ly, _ = ab.get_landing_info(target["x"], r, pieces, eff, t)
        os.environ[TOGGLE] = "1"
        try:
            for bad in (None, float("nan"), "x"):
                tgt = dict(target)
                tgt["x"] = bad
                self.assertFalse(ab._vertical_lane_direct(target["x"], ly, drop_ext, tgt, pieces, eff, 0.0))
            self.assertFalse(ab._vertical_lane_direct(target["x"], ly, drop_ext, target, pieces, eff, None))
            self.assertFalse(ab._vertical_lane_direct(target["x"], ly, drop_ext, target, pieces, eff, 0.5))
            self.assertFalse(ab._vertical_lane_direct(target["x"], float("nan"), drop_ext, target, pieces, eff, 0.0))
        finally:
            os.environ.pop(TOGGLE, None)

    def test_no_perf_regression(self):
        fx = _load("vertical_lane_direct_bigboard.json")
        t0 = time.perf_counter()
        _run(fx, "1")
        dt = time.perf_counter() - t0
        self.assertLess(dt, 0.5, "analyze_drops on %d pieces took %.3fs" % (len(fx["pieces"]), dt))


if __name__ == "__main__":
    unittest.main()

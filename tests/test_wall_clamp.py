"""ANALYZE_BOARD_WALL_CLAMP: 解析器の壁反射半幅トグル。0 (既定) は旧式 (スプライト半径) と完全一致、1 は
当たり判定半幅 + WALL_CLAMP_PAD で壁から離れた位置に着地予測する。実測 (2026-08-26, 壁際 798 手): 旧式は
壁側へ +0.27/-0.25 の系統誤差。締切系フィールドは drift を使わないので不変。"""
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
import analyze_board as ab  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")
TOGGLE = "ANALYZE_BOARD_WALL_CLAMP"
SHAPES = json.load(open(os.path.join(FX, "pre_russia_chain_cover_turn57.json"), encoding="utf-8"))["shapes"]


def _gs(pieces, nt, nnt=5):
    return {"state": "MOVE", "score": 0, "pieces": [dict(p) for p in pieces], "shapes": SHAPES,
            "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)}, "nextNext": {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)}}


def _run(gs, mode, module=ab):
    with mock.patch.dict(os.environ, {TOGGLE: mode}):
        res, _ = module.analyze_drops(gs["pieces"], gs["next"]["type"], gs["next"]["r"], gs["shapes"])
    return res


def _at(res, x):
    return min(res, key=lambda r: abs(r["x"] - x))


def _board_fixtures():
    out = []
    for name in sorted(os.listdir(FX)):
        if not name.endswith(".json"):
            continue
        fx = json.load(open(os.path.join(FX, name), encoding="utf-8"))
        if isinstance(fx, dict) and "pieces" in fx and "next_type" in fx and fx.get("shapes"):
            gs = _gs(fx["pieces"], fx["next_type"], fx.get("next_next_type", 5))
            gs["shapes"] = fx["shapes"]
            out.append((name, gs))
    return out


class WallClampTest(unittest.TestCase):
    def test_mode_parsing(self):
        for raw, want in (("", 0), ("0", 0), ("false", 0), ("off", 0), ("no", 0), ("2", 0), ("x", 0), ("1", 1)):
            with mock.patch.dict(os.environ, {TOGGLE: raw}):
                self.assertEqual(ab._wall_clamp_mode(), want, raw)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(ab._wall_clamp_mode(), 0)

    def test_mode0_matches_head_analyzer_on_all_fixtures(self):
        # HEAD の analyze_board (トグル導入前) と mode 0 の出力が全 fixture で一致する
        try:
            src = subprocess.check_output(["git", "show", "HEAD:analyze_board.py"], cwd=ROOT, stderr=subprocess.DEVNULL).decode("utf-8")
        except Exception:
            self.skipTest("git HEAD analyze_board.py unavailable (VM has no git)")
        if "_wall_clamp_mode" in src:  # 既に HEAD に含まれる場合はこの比較は恒等
            self.skipTest("HEAD already contains the toggle")
        path = os.path.join(ROOT, "tmp", "_head_analyze_board_for_test.py")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(src)
        try:
            spec = importlib.util.spec_from_file_location("head_ab", path)
            head = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(head)
            n = 0
            for name, gs in _board_fixtures():
                a = json.dumps(_run(gs, "0"), sort_keys=True, default=str)
                b = json.dumps(_run(gs, "0", head), sort_keys=True, default=str)
                self.assertEqual(a, b, name)
                n += 1
            self.assertGreaterEqual(n, 10)
        finally:
            os.remove(path)

    def test_mode1_moves_wall_landing_inward(self):
        for nt in (1, 4, 8, 11):
            gs = _gs([], nt)
            a0, a1 = _run(gs, "0"), _run(gs, "1")
            r0, r1 = _at(a0, -3.0), _at(a1, -3.0)
            horiz = ab._type_deadline_extents(nt, ab.TYPE_RADII[nt], ab.UNITY_PREFAB_DEADLINE_RADII)["horiz"]
            want = ab.WALL_LEFT + horiz + ab.WALL_CLAMP_PAD
            self.assertAlmostEqual(r1["x"] + r1["drift_x"], want, places=2, msg="T%d" % nt)
            self.assertGreaterEqual(r0["x"] + r0["drift_x"], ab.WALL_LEFT + ab.TYPE_RADII[nt] - 1e-9, "T%d legacy" % nt)
            self.assertGreater(r1["x"] + r1["drift_x"], r0["x"] + r0["drift_x"])
            rr0, rr1 = _at(a0, 3.0), _at(a1, 3.0)
            self.assertAlmostEqual(rr1["x"] + rr1["drift_x"], ab.WALL_RIGHT - horiz - ab.WALL_CLAMP_PAD, places=2)
            self.assertLess(rr1["x"] + rr1["drift_x"], rr0["x"] + rr0["drift_x"])
            # 内側の候補は不変
            i0, i1 = _at(a0, 0.0), _at(a1, 0.0)
            self.assertEqual(i0["drift_x"], i1["drift_x"])

    def test_mode1_keeps_deadline_fields_and_interior_identical(self):
        keys = ("risk_top_y_after_drop", "crosses_deadline", "merge_result_crosses_deadline", "deadline_margin", "landing_y")
        for name, gs in _board_fixtures():
            a0, a1 = _run(gs, "0"), _run(gs, "1")
            self.assertEqual(len(a0), len(a1), name)
            for r0, r1 in zip(a0, a1):
                self.assertEqual(r0["x"], r1["x"], name)
                for k in keys:
                    self.assertEqual(r0.get(k), r1.get(k), "%s x=%s %s" % (name, r0["x"], k))
                horiz = ab._type_deadline_extents(gs["next"]["type"], gs["next"]["r"], ab.UNITY_PREFAB_DEADLINE_RADII)["horiz"]
                if abs(r0["x"] + r0["drift_x"]) <= ab.WALL_RIGHT - horiz - ab.WALL_CLAMP_PAD - 1e-9:
                    self.assertEqual(r0["drift_x"], r1["drift_x"], "%s x=%s" % (name, r0["x"]))
                else:
                    self.assertLessEqual(abs(r1["x"] + r1["drift_x"]), abs(r0["x"] + r0["drift_x"]) + 1e-9, "%s x=%s" % (name, r0["x"]))

    def test_fail_closed(self):
        # eff_radii 無し → フォールバック半幅 (有限) で動く / 判定不能は旧式
        self.assertAlmostEqual(ab._wall_half_width(0.5, 11, None) if ab._wall_clamp_mode() == 1 else 0.5, 0.5 if ab._wall_clamp_mode() != 1 else ab._wall_half_width(0.5, 11, None))
        with mock.patch.dict(os.environ, {TOGGLE: "1"}):
            self.assertGreater(ab._wall_half_width(0.5, 11, None), 0.5)
            self.assertEqual(ab._wall_half_width(0.5, 11, {11: {"horiz": float("nan")}}), 0.5)
            self.assertEqual(ab._wall_half_width(0.5, 11, {11: {"horiz": "x"}}), 0.5)
            self.assertEqual(ab._wall_half_width(0.5, None, "notadict"), 0.5)
        with mock.patch.dict(os.environ, {TOGGLE: "0"}):
            self.assertEqual(ab._wall_half_width(0.5, 11, ab.UNITY_PREFAB_DEADLINE_RADII), 0.5)


if __name__ == "__main__":
    unittest.main()

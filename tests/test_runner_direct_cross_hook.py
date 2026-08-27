"""strategy_runner.enforce_deadline_safety の v747 フック:
戦略モジュールが DEADLINE_ALLOW_DIRECT_CROSS=True を宣言し、理由に DEADLINE_GUARD_DIRECT_MERGE_CROSSING
（候補が DIRECT）または OPEN_TWIN_MERGE_DESPERATE を含む手だけ差し戻さない。宣言が無ければ従来どおり差し戻す。"""
import json
import os
import sys
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
os.environ.setdefault("ANALYZE_BOARD_WALL_CLAMP", "1")
import analyze_board as ab  # noqa: E402
import strategy_runner as sr  # noqa: E402

FX = os.path.join(ROOT, "tests", "fixtures")


def _gs(fx):
    nt = fx["next_type"]
    nnt = fx.get("next_next_type", 5)
    return {"state": "MOVE", "score": fx.get("score", 0), "pieces": [dict(p) for p in fx["pieces"]], "shapes": fx["shapes"],
            "next": {"type": nt, "r": ab.TYPE_RADII.get(nt, 0.5)}, "nextNext": {"type": nnt, "r": ab.TYPE_RADII.get(nnt, 0.5)}}


def _fixture_direct_crossing():
    with open(os.path.join(FX, "v747_guard_direct_crossing_turn141.json"), encoding="utf-8") as fh:
        fx = json.load(fh)
    gs = _gs(fx)
    an = sr.build_analysis(gs)
    sr.enrich_game_state_deadline_fields(gs, an)
    direct = [r for r in an["results"] if r.get("merge_grade") == "DIRECT" and r.get("crosses_deadline")]
    return gs, an, direct


class RunnerDirectCrossHookTests(unittest.TestCase):
    def test_declared_strategy_keeps_crossing_direct_merge(self):
        gs, an, direct = _fixture_direct_crossing()
        self.assertTrue(direct, "fixture must contain a crossing DIRECT candidate")
        mod = types.SimpleNamespace(DEADLINE_ALLOW_DIRECT_CROSS=True)
        d = {"x": float(direct[0]["x"]), "reason": "DEADLINE_GUARD_DIRECT_MERGE_CROSSING"}
        out = sr.enforce_deadline_safety(dict(d), an, gs, mod)
        self.assertAlmostEqual(float(out["x"]), float(direct[0]["x"]), places=3)
        self.assertIn("DEADLINE_GUARD_DIRECT_MERGE_CROSSING", str(out.get("reason", "")))

    def test_undeclared_strategy_is_still_overridden(self):
        gs, an, direct = _fixture_direct_crossing()
        mod = types.SimpleNamespace()
        d = {"x": float(direct[0]["x"]), "reason": "DEADLINE_GUARD_DIRECT_MERGE_CROSSING"}
        out = sr.enforce_deadline_safety(dict(d), an, gs, mod)
        self.assertGreater(abs(float(out["x"]) - float(direct[0]["x"])), 0.3, "undeclared module must be overridden as before")

    def test_declared_strategy_without_tag_is_still_overridden(self):
        gs, an, direct = _fixture_direct_crossing()
        mod = types.SimpleNamespace(DEADLINE_ALLOW_DIRECT_CROSS=True)
        d = {"x": float(direct[0]["x"]), "reason": "DIRECT_MERGE_SOMETHING_ELSE"}
        out = sr.enforce_deadline_safety(dict(d), an, gs, mod)
        self.assertGreater(abs(float(out["x"]) - float(direct[0]["x"])), 0.3)


if __name__ == "__main__":
    unittest.main()

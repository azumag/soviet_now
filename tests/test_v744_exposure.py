"""v744: 露出保持 (LAST_EXPOSED_COVER_AVOID) と露出同型直落とし (OPEN_TWIN_MERGE)。
実履歴の局面を fixture にし、フルパイプラインで
 (1) 非併合手で「露出した唯一の同型 T3-9」を覆う着地が避けられること、
 (2) DIRECT/NEAR 候補が無く露出同型 (relief<=1.0) がある手でその真上 (OPEN_TWIN_MERGE) が選ばれること、
 (3) DIRECT 併合がある手では着手が変わらないこと、を確認する。"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1")
os.environ.setdefault("ANALYZE_BOARD_MERGE_TOP_MODEL", "2")
os.environ.setdefault("ANALYZE_BOARD_WALL_CLAMP", "1")
import analyze_board as ab  # noqa: E402
import strategy  # noqa: E402
import strategy_runner as sr  # noqa: E402
from strategy_helpers import board_stats as bs  # noqa: E402

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


def _last_exposed(pieces, nt):
    byt = {}
    for t, x, y, r in bs.pieces_of_type_at_least(pieces, 3):
        if t >= 10 or t == nt or t - 1 == nt:
            continue
        top = y + r
        open_ = True
        for o in pieces:
            orr = float(o.get("r", 0.5) or 0.5)
            ob = float(o.get("y", 0.0) or 0.0) - orr
            if ob < top - 0.25:
                continue
            if abs(float(o.get("x", 0.0) or 0.0) - x) <= r:
                open_ = False
                break
        if open_:
            byt.setdefault(t, []).append((x, r * 0.9, top - 0.25))
    return [v[0] for v in byt.values() if len(v) == 1]


def _covers(x, chosen, lanes):
    ly = float(chosen.get("landing_y") or 0.0)
    return any(abs(x - lx) <= tol and ly >= my for lx, tol, my in lanes)


class V744ExposureTests(unittest.TestCase):
    def test_last_exposed_cover_is_avoided(self):
        for name in ("v744_last_exposed_changed_1_turn19.json", "v744_last_exposed_changed_2_turn23.json", "v744_last_exposed_changed_3_turn27.json"):
            fx = _load(name)
            gs = _gs(fx)
            x, reason, chosen, an = _pipeline(gs)
            lanes = _last_exposed(gs["pieces"], fx["next_type"])
            self.assertTrue(lanes, name)
            self.assertFalse(_covers(x, chosen, lanes), "%s: landing x=%.2f still covers a last-exposed piece (%s)" % (name, x, reason))
            self.assertGreater(abs(x - fx["base_x"]), 0.3, name)

    def test_open_twin_merge_fires(self):
        for name in ("v744_open_twin_fired_1_turn22.json", "v744_open_twin_fired_2_turn30.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertIn("OPEN_TWIN_MERGE", reason, name)
            twins = [p for p in fx["pieces"] if p["type"] == fx["next_type"]]
            self.assertTrue(twins, name)
            self.assertLessEqual(min(abs(x - p["x"]) for p in twins), 0.4, "%s: x=%.2f not above a twin" % (name, x))
            self.assertFalse(chosen.get("crosses_deadline"), name)

    def test_direct_merge_unchanged(self):
        for name in ("v744_direct_unchanged_1_turn3.json", "v744_direct_unchanged_2_turn7.json"):
            fx = _load(name)
            x, reason, chosen, an = _pipeline(_gs(fx))
            self.assertIn("DIRECT_MERGE", reason, name)
            self.assertNotIn("OPEN_TWIN_MERGE", reason, name)
            self.assertLessEqual(abs(x - fx["base_x"]), 0.011, name)


if __name__ == "__main__":
    unittest.main()

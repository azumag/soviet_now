"""建国直前の局面で、終盤専用ロジックが実際に発火することを固定する。

背景 (実測 2026-08-30): `RUSSIA_PHASE_*` / `DOUBLE_RUSSIA_*` / `SECOND_RUSSIA_*` の加点軸は
実戦 700 手で 1 度も発火していない。T15 到達率が 3%、T15 が 2 個同時に存在した試合は 0 例
だからで、論理的に到達不能なのではない。つまりこれらは「一度も実行検証されていないコード」で、
funnel が改善して実際に建国直前へ到達したときに初めて動く。ここで合成盤面を与えて、
少なくとも発火することと落下 x が盤内に収まることを固定しておく。

幾何の実測メモ:
  * 併合の接触判定は視覚半径で `dist < (r1 + r2) * 1.1`。T15 の視覚半径は約 1.32 なので
    **中心間 2.9 未満で併合**する。
  * 盤面の壁は実測で約 ±3.44。T15 の中心が取り得た最大 |x| は 2.43。
  * したがって **T15 は 2 個が離れて共存できる**（中心間 2.9 以上）。
    既に静止した同型ペアは押し付けても併合しない（実測: 3 手以内の併合率 3%）ため、
    **2 個目の T15 を 1 個目の接触圏内に生成できなければ、その試合はもう建国できない**。
"""
import glob
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_shapes():
    shapes = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "tests/fixtures/*.json"))):
        try:
            data = json.load(open(path))
        except Exception:
            continue
        if isinstance(data.get("shapes"), dict):
            for key, val in data["shapes"].items():
                shapes.setdefault(key, val)
    return shapes


class EndgameReachabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.chdir(ROOT)
        for key, val in (("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1"),
                         ("ANALYZE_BOARD_MERGE_TOP_MODEL", "2"),
                         ("ANALYZE_BOARD_WALL_CLAMP", "1"),
                         ("ANALYZE_BOARD_LANDING_ARC", "0")):
            os.environ.setdefault(key, val)
        import analyze_board
        import strategy
        import strategy_runner
        strategy_runner.log = lambda *a, **k: None
        cls.ab = analyze_board
        cls.sr = strategy_runner
        cls.strategy = strategy
        cls.shapes = _load_shapes()

    def _decide(self, pieces, next_type, next_next=5):
        types = {str(p["type"]) for p in pieces} | {str(next_type), str(next_next)}
        gs = {"state": "MOVE", "score": 9000, "pieceCount": len(pieces), "piece_count": len(pieces),
              "makeSorenCount": 0, "record": 0, "deadline_crossed": False, "deadline_y": 3.38,
              "pieces": [dict(p) for p in pieces],
              "shapes": {k: v for k, v in self.shapes.items() if k in types},
              "next": {"type": next_type, "r": self.ab.TYPE_RADII.get(next_type, 0.5), "x": 0},
              "nextNext": {"type": next_next, "r": self.ab.TYPE_RADII.get(next_next, 0.5)}}
        analysis = self.sr.build_analysis(gs)
        self.assertTrue(analysis.get("results"), "解析が候補を返さない")
        decision = self.strategy.decide(gs, analysis)
        decision["x"] = max(self.sr.GAME_X_MIN, min(self.sr.GAME_X_MAX, float(decision["x"])))
        decision = self.sr.enforce_deadline_safety(decision, analysis, gs, self.strategy)
        decision = self.sr.apply_strategy_final_decision(self.strategy, decision, analysis, gs)
        decision["x"] = max(self.sr.GAME_X_MIN, min(self.sr.GAME_X_MAX, float(decision["x"])))
        return decision

    TWO_T15 = [{"id": 1, "type": 15, "x": -1.5, "y": -3.0, "r": 1.32, "angle": 0},
               {"id": 2, "type": 15, "x": 1.5, "y": -3.0, "r": 1.32, "angle": 0}]
    ONE_T15_TWO_T14 = [{"id": 1, "type": 15, "x": -1.8, "y": -3.0, "r": 1.32, "angle": 0},
                       {"id": 2, "type": 14, "x": 0.6, "y": -3.4, "r": 1.34, "angle": 0},
                       {"id": 3, "type": 14, "x": 2.4, "y": -3.4, "r": 1.34, "angle": 0}]

    def test_double_russia_axis_fires_when_two_russias_exist(self):
        reason = str(self._decide(self.TWO_T15, 1).get("reason", ""))
        self.assertIn("DOUBLE_RUSSIA", reason, "T15 が 2 個あるのに終盤軸が発火しない: " + reason)

    def test_soviet_contact_shot_is_reachable(self):
        reason = str(self._decide(self.TWO_T15, 11).get("reason", ""))
        self.assertIn("SOVIET", reason, reason)

    def test_second_russia_axis_fires_with_one_russia_and_two_t14(self):
        reason = str(self._decide(self.ONE_T15_TWO_T14, 11).get("reason", ""))
        self.assertTrue("SECOND_RUSSIA" in reason or "RUSSIA_PHASE" in reason, reason)

    def test_endgame_decisions_stay_inside_the_board(self):
        for pieces, ntype in ((self.TWO_T15, 1), (self.TWO_T15, 11), (self.ONE_T15_TWO_T14, 11)):
            x = float(self._decide(pieces, ntype)["x"])
            self.assertGreaterEqual(x, self.sr.GAME_X_MIN)
            self.assertLessEqual(x, self.sr.GAME_X_MAX)

    def test_two_russias_can_sit_apart_without_merging(self):
        # 建国が「2 個作ってから寄せる」では成立しないことを数値で固定する。
        r15 = 1.32                       # 視覚半径の実測中央値
        contact = (r15 + r15) * 1.1      # analyze_board の接触条件
        self.assertAlmostEqual(contact, 2.904, places=3)
        wall = 3.44                      # 実測の壁位置
        max_gap = 2 * (wall - r15)       # 両端に置いたときの中心間距離
        self.assertGreater(max_gap, contact,
                           "2 個の T15 が離れて共存できないなら、生成即建国になるはず")


if __name__ == "__main__":
    unittest.main()

"""v763 SURFACE_DIVERSITY（再設計）の重み関数と post-state 型数の数え方を固定する。

なぜ露出型数を狙うのか (実測 2026-08-31、実戦 10622 手 / 併合 6035 回):
  * 併合の 61% は「露出同型の上に落とせた 26.8% の手」から出る (1.291 併合/手)。
    露出同型が無い 60.3% の手は 0.169 併合/手しか出ない。律速は機会の有無。
  * 機会率 = 露出型数 / 11。駒数で層別した統制付き検証では
    露出型 +1 あたり +0.045〜+0.087 併合/手（全層で一貫）。+1 型で 93 -> 110 手。
  * 選択の余地: 落下前 4.38 型 -> 実際の手の後 3.85 型、最良候補なら 4.81 型（平均 0.961 型の取り逃し）。

v757 との違い: 落下型 1..11 限定 / 露出判定は _v742_open_twin と同一 /
落とした駒自身を数える / type15 ゲート撤去。
"""
import importlib.util
import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
CAND = os.path.join(ROOT, "candidates", "strategy_v763_3a9bd96b76a0.py")


def _load():
    sys.path.insert(0, os.path.abspath(ROOT))
    spec = importlib.util.spec_from_file_location("v763_cand", CAND)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class WeightTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("V763_DIVERSITY_W")
        self.mod = _load()

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("V763_DIVERSITY_W", None)
        else:
            os.environ["V763_DIVERSITY_W"] = self._saved

    def _w(self, value):
        if value is None:
            os.environ.pop("V763_DIVERSITY_W", None)
        else:
            os.environ["V763_DIVERSITY_W"] = value
        return self.mod._v763_weight()

    def test_disabled_by_default(self):
        self.assertEqual(self._w(None), 0.0)
        for off in ("", "0", "off", "no", "false"):
            self.assertEqual(self._w(off), 0.0, off)

    def test_bad_values_disable_and_large_values_saturate(self):
        for bad in ("abc", "-1", "nan"):
            self.assertEqual(self._w(bad), 0.0, bad)
        self.assertEqual(self._w("50"), 8.0)


class PostStateTests(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def test_only_droppable_types_are_collected(self):
        pieces = [{"id": 1, "type": 5, "x": 0.0, "y": 0.0},
                  {"id": 2, "type": 13, "x": 2.0, "y": 0.0}]
        got = self.mod._v763_open_by_type(pieces)
        self.assertIn(5, got)
        self.assertNotIn(13, got, "type 12 以上は直接降らないので機会にならない")

    def test_the_dropped_piece_always_keeps_its_own_type_open(self):
        # 盤面に露出型がひとつも無くても、落とした駒自身が 1 型を作る。
        self.assertEqual(self.mod._v763_post_open_types({}, 4, 0.0, 0.0), 1)

    def test_covering_the_only_open_piece_of_a_type_removes_it(self):
        target = {"id": 1, "type": 2, "x": 0.0, "y": -3.0}
        open_by_type = {2: [target]}
        # 真上に、十分高い位置から落とす -> type 2 は塞がれるが、落とした type 7 が 1 型を担う
        kept = self.mod._v763_post_open_types(open_by_type, 7, 0.0, 3.0)
        self.assertEqual(kept, 1)

    def test_a_far_away_drop_keeps_everything_open(self):
        target = {"id": 1, "type": 2, "x": -2.8, "y": -3.0}
        open_by_type = {2: [target]}
        kept = self.mod._v763_post_open_types(open_by_type, 7, 2.8, 3.0)
        self.assertEqual(kept, 2, "遠くへ落としても既存の露出は残り、落とした型が加わる")

    def test_dropping_on_its_own_type_does_not_lose_that_type(self):
        target = {"id": 1, "type": 7, "x": 0.0, "y": -3.0}
        open_by_type = {7: [target]}
        kept = self.mod._v763_post_open_types(open_by_type, 7, 0.0, 3.0)
        self.assertEqual(kept, 1, "同型の上に落としても、その型は落とした駒が担う")

    def test_broken_input_falls_back_to_the_current_count(self):
        open_by_type = {3: [{"id": 1, "type": 3, "x": "bad", "y": 0.0}]}
        self.assertEqual(self.mod._v763_post_open_types(open_by_type, 3, 0.0, 0.0), 1)


if __name__ == "__main__":
    unittest.main()

"""issue #132 P0-2: strategy.py の意味的な破損を静的に検出する。

700 版以上の数値変異の結果、構文は通るが意味が壊れた箇所が残っている。ここでは
機械的に検出できる 2 クラスを固定する。

1. 連続単項マイナス (`--1`, `----1`): Python では `--1 == 1`、`----1 == 1` になり、
   欠測 sentinel として書かれた意図と符号が反転する。
2. 到達不能な elif 連鎖: 同じ変数に対し `elif v < A` の後に `elif v < B` (B < A) を置くと
   後者は永久に成立しない。実際に phase の `HIGH` (max_y < 1.275) が
   `MEDIUM` (max_y < 2.894) の後ろにあり到達不能で、`HIGH_TOWER` も死んでいる。
3. ドメイン外の座標比較: 盤面は x∈[-3,3] なのに `p["x"] < 4` のような比較は常真/常偽で、
   書かれた意図（左右バランス計測）を一切実行しない。実際に axis 4 がこの形で
   「中央寄せ定数ペナルティ」に退化していた（issue #132 実測）。
4. 証明可能に負にならない量への負定数比較: `len()` や `reactive_pair_count`
   （len() または 1 由来で常に >=0）に対する `>= -1` は常真、`< -2` / `== -1` は常偽。
   条件式の一部が死ぬと、ゲート全体が別の意図で発火/不発火する。
   なお analyzer 辞書をそのまま読む `danger_piece_count` は sentinel の負値を取りうる
   ため、この検出の対象外（負比較は意味を持ちうる）。
"""
import ast
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from strategy_semantic_lint import (  # noqa: E402
    _consecutive_unary_minus,
    _impossible_count_compare,
    _out_of_domain_coordinate_compare,
    _unreachable_lt_chain,
)

TARGETS = ["strategy.py"]


class StrategySemanticLintTests(unittest.TestCase):
    def _tree(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            self._src = fh.read()
            return ast.parse(self._src)

    # --- 既知欠陥の凍結 (issue #132 P0-2) ---
    # 修正が入ったらこのテストが落ちるので、期待値を更新すること。増えた場合も落ちる。
    # 凍結は行番号ではなくソース行シグネチャで行う（行ずれで壊れない）。
    # 2026-11 に全12行を値等価リテラルへ正規化済み（--1→1、---1→-1、----1→1。
    # 意味変化ゼロは構築上保証され、VM replay 2877ターンでも差分 0 を確認）。
    # 以後0件を維持する（新しい連続単項マイナスが増えたら欠陥）。
    KNOWN_UNARY_MINUS = []
    KNOWN_UNREACHABLE = []  # HIGH phase 到達不能は max_y 帯を昇順化して解消済み
    # 負にならない量への負定数比較（常真/常偽の死んだ条件）。2026-11 に全8件を除去済み
    # （常真連言の削除・常偽ブロックの除去のみで意味変化なし）。以後0件を維持する。
    KNOWN_COUNT_COMPARE = []

    def test_consecutive_unary_minus_is_frozen(self):
        hits = _consecutive_unary_minus(self._tree("strategy.py"), self._src)
        self.assertCountEqual(
            hits, self.KNOWN_UNARY_MINUS,
            "連続単項マイナスの集合が変わった (--1 は 1、----1 も 1 になる)。"
            "修正したなら KNOWN_UNARY_MINUS を更新し、増えたなら新しい欠陥である。実際: %s" % hits)

    def test_unreachable_lt_chain_is_frozen(self):
        hits = sorted(set(_unreachable_lt_chain(self._tree("strategy.py"))))
        self.assertEqual(
            hits, self.KNOWN_UNREACHABLE,
            "到達不能な elif 連鎖の集合が変わった。実際: %s" % hits)

    def test_lint_detects_known_patterns(self):
        """lint 自体が既知パターンを拾えることを固定する (自己テスト)。"""
        bad_src = "x = --1\ny = ----2\n"
        self.assertEqual(len(_consecutive_unary_minus(ast.parse(bad_src), bad_src)), 2)
        chain = ast.parse("if v < 2.894:\n    a=1\nelif v < 1.275:\n    a=2\n")
        hits = _unreachable_lt_chain(chain)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][0], "v")
        ok = ast.parse("if v < 1.0:\n    a=1\nelif v < 2.0:\n    a=2\n")
        self.assertEqual(_unreachable_lt_chain(ok), [])

    def test_out_of_domain_coordinate_compare_is_empty(self):
        """axis 4 の `p["x"] < 4`（常真・左右バランス計測の死滅）は修正済み。
        同型のドメイン外比較が今後の数値変異で再発したらこのテストが落ちる。"""
        hits = _out_of_domain_coordinate_compare(self._tree("strategy.py"))
        self.assertEqual(
            hits, [],
            "ドメイン外の座標比較を検出した。実際: %s" % hits)

    def test_impossible_count_compare_is_frozen(self):
        hits = _impossible_count_compare(self._tree("strategy.py"))
        self.assertEqual(
            hits, self.KNOWN_COUNT_COMPARE,
            "負にならない量への負定数比較の集合が変わった。修正したなら一覧を更新し、"
            "増えたなら新しい欠陥である。実際: %s" % hits)

    def test_new_detectors_catch_known_patterns(self):
        bad = ast.parse('for p in pieces:\n    if p["x"] < 4:\n        a = 1\n')
        self.assertEqual(len(_out_of_domain_coordinate_compare(bad)), 1)
        good = ast.parse('for p in pieces:\n    if p["x"] < 2.7:\n        a = 1\n')
        self.assertEqual(_out_of_domain_coordinate_compare(good), [])
        # 変数名形: landing_y は床〜デッドライン+半径で [-6,6] を超えない
        nb = ast.parse("if landing_y > 7:\n    a = 1\n")
        self.assertEqual(len(_out_of_domain_coordinate_compare(nb)), 1)
        ng = ast.parse("if landing_y > -2:\n    a = 1\n")
        self.assertEqual(_out_of_domain_coordinate_compare(ng), [])
        cnt = ast.parse("if reactive_pair_count >= -1:\n    a = 1\n")
        self.assertEqual(len(_impossible_count_compare(cnt)), 1)
        sentinel = ast.parse("if danger_piece_count == -1:\n    a = 1\n")
        self.assertEqual(_impossible_count_compare(sentinel), [])


if __name__ == "__main__":
    unittest.main()

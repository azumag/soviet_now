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
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["strategy.py"]


def _consecutive_unary_minus(tree):
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.UnaryOp) and isinstance(node.operand.op, ast.USub):
            hits.append(getattr(node, "lineno", -1))
    return sorted(set(hits))


def _unreachable_lt_chain(tree):
    """同一変数に対する `if v < A: ... elif v < B: ...` で B <= A のものを返す。"""
    out = []

    def const(n):
        return n.value if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) else None

    def var(n):
        return n.id if isinstance(n, ast.Name) else None

    def cond(n):
        if not (isinstance(n, ast.Compare) and len(n.ops) == 1 and isinstance(n.ops[0], ast.Lt)):
            return None
        v, c = var(n.left), const(n.comparators[0])
        return (v, c) if v is not None and c is not None else None

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        seen = {}
        cur = node
        while isinstance(cur, ast.If):
            c = cond(cur.test)
            if c:
                v, bound = c
                if v in seen and bound <= seen[v]:
                    out.append((getattr(cur, "lineno", -1), v, bound, seen[v]))
                else:
                    seen[v] = bound if v not in seen else max(seen[v], bound)
            cur = cur.orelse[0] if len(cur.orelse) == 1 and isinstance(cur.orelse[0], ast.If) else None
    return out


def _fold_const(node):
    """数値定数と単項マイナスを畳み込む。`--1` は +1、`-1` は -1。それ以外は None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        v = _fold_const(node.operand)
        return -v if v is not None else None
    return None


# ピース中心 x ∈ [-3, 3]、y は床 -4.48 〜 デッドライン+半径程度。余裕を持たせた
# 外側境界で、これを超える比較だけを「証明可能に定数」とみなす。
_X_ABS_BOUND = 3.5
_Y_LO_BOUND = -6.0
_Y_HI_BOUND = 6.0


def _out_of_domain_coordinate_compare(tree):
    """`obj["x"]` / `obj["y"]` と定数の比較で、盤面ドメイン上 常真/常偽 になるものを返す。"""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        if not (isinstance(left, ast.Subscript)
                and isinstance(left.slice, ast.Constant)
                and left.slice.value in ("x", "y")):
            continue
        c = _fold_const(node.comparators[0])
        if c is None:
            continue
        lo, hi = (-_X_ABS_BOUND, _X_ABS_BOUND) if left.slice.value == "x" \
            else (_Y_LO_BOUND, _Y_HI_BOUND)
        op = node.ops[0]
        if isinstance(op, (ast.Lt, ast.LtE)):
            dead = c > hi or c <= lo
        elif isinstance(op, (ast.Gt, ast.GtE)):
            dead = c < lo or c >= hi
        elif isinstance(op, (ast.Eq, ast.NotEq)):
            dead = c < lo or c > hi
        else:
            dead = False
        if dead:
            hits.append((node.lineno, left.slice.value, c))
    return sorted(hits)


# 負値を取りえないことがソースから証明できる識別子。
# reactive_pair_count は `len(...) if isinstance(..., list) else 1`、
# piece_count は board_stats の実駒カウント由来。analyzer 生値の
# danger_piece_count は sentinel 負値を取りうるため対象外。
_NONNEG_COUNT_NAMES = frozenset({"reactive_pair_count", "piece_count"})


def _impossible_count_compare(tree):
    """負にならない量（len()・証明済みカウント変数）と負定数の比較を返す。
    `>= -k`/`> -k`/`!= -k` は常真、`< -k`/`<= -k`/`== -k` (k>0) は常偽。"""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        if isinstance(left, ast.Name) and left.id in _NONNEG_COUNT_NAMES:
            name = left.id
        elif isinstance(left, ast.Call) and isinstance(left.func, ast.Name) \
                and left.func.id == "len":
            name = "len()"
        else:
            continue
        c = _fold_const(node.comparators[0])
        if c is None or c >= 0:
            continue
        hits.append((node.lineno, name, type(node.ops[0]).__name__, c))
    return sorted(hits)


class StrategySemanticLintTests(unittest.TestCase):
    def _tree(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            return ast.parse(fh.read())

    # --- 既知欠陥の凍結 (issue #132 P0-2) ---
    # 進行中の A/B (v757) 中は strategy.py の挙動を変えられないため、いま検出できる欠陥を
    # そのまま固定する。修正が入ったらこのテストが落ちるので、期待値を空へ更新すること。
    # 逆に「新しい欠陥が増えた」場合も落ちる。
    KNOWN_UNARY_MINUS = [1706, 2093, 2094, 2236, 2349, 2369, 2440, 2839, 3075, 3130, 3250, 3266]
    KNOWN_UNREACHABLE = [(1686, "max_y", 1.275, 2.894)]  # HIGH phase が MEDIUM の後ろで到達不能
    # 負にならない量への負定数比較（常真/常偽の死んだ条件）。修正したら対応エントリを消す。
    KNOWN_COUNT_COMPARE = [
        (2307, "reactive_pair_count", "GtE", -3),
        (2495, "reactive_pair_count", "GtE", -4),
        (2822, "reactive_pair_count", "GtE", -1),
        (2831, "reactive_pair_count", "GtE", -1),
        (2831, "reactive_pair_count", "Lt", -2),
        (3125, "reactive_pair_count", "GtE", -3),
        (3184, "reactive_pair_count", "GtE", -1),
    ]

    def test_consecutive_unary_minus_is_frozen(self):
        hits = _consecutive_unary_minus(self._tree("strategy.py"))
        self.assertEqual(
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
        bad = ast.parse("x = --1\ny = ----2\n")
        self.assertEqual(len(_consecutive_unary_minus(bad)), 2)
        chain = ast.parse("if v < 2.894:\n    a=1\nelif v < 1.275:\n    a=2\n")
        hits = _unreachable_lt_chain(chain)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], "v")
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
        cnt = ast.parse("if reactive_pair_count >= -1:\n    a = 1\n")
        self.assertEqual(len(_impossible_count_compare(cnt)), 1)
        sentinel = ast.parse("if danger_piece_count == -1:\n    a = 1\n")
        self.assertEqual(_impossible_count_compare(sentinel), [])


if __name__ == "__main__":
    unittest.main()

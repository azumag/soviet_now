"""issue #132 P0-2: strategy.py の意味的な破損を静的に検出する。

700 版以上の数値変異の結果、構文は通るが意味が壊れた箇所が残っている。ここでは
機械的に検出できる 2 クラスを固定する。

1. 連続単項マイナス (`--1`, `----1`): Python では `--1 == 1`、`----1 == 1` になり、
   欠測 sentinel として書かれた意図と符号が反転する。
2. 到達不能な elif 連鎖: 同じ変数に対し `elif v < A` の後に `elif v < B` (B < A) を置くと
   後者は永久に成立しない。実際に phase の `HIGH` (max_y < 1.275) が
   `MEDIUM` (max_y < 2.894) の後ろにあり到達不能で、`HIGH_TOWER` も死んでいる。
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


class StrategySemanticLintTests(unittest.TestCase):
    def _tree(self, name):
        with open(os.path.join(ROOT, name), encoding="utf-8") as fh:
            return ast.parse(fh.read())

    # --- 既知欠陥の凍結 (issue #132 P0-2) ---
    # 進行中の A/B (v757) 中は strategy.py の挙動を変えられないため、いま検出できる欠陥を
    # そのまま固定する。修正が入ったらこのテストが落ちるので、期待値を空へ更新すること。
    # 逆に「新しい欠陥が増えた」場合も落ちる。
    KNOWN_UNARY_MINUS = [1577, 1962, 1963, 2105, 2218, 2238, 2309, 2708, 2907, 2962, 3081, 3097]
    KNOWN_UNREACHABLE = [(1560, "max_y", 1.275, 2.894)]  # HIGH phase が MEDIUM の後ろで到達不能

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


if __name__ == "__main__":
    unittest.main()

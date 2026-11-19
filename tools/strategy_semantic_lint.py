#!/usr/bin/env python3
"""strategy 候補の意味的破損を検出する共有 lint。

tests/test_strategy_semantic_lint.py（CI 凍結）と strategy/ab_gate.sh（採用前
ゲート）の双方から使う単一の検出器実装。A/B 採用経路は本番へ直接書き込むため、
候補が数値変異で死んだ条件や sentinel 反転を持ち込んでも本番適用前に止める。

使い方:
    python3 tools/strategy_semantic_lint.py <path/to/strategy.py>
    python3 tools/strategy_semantic_lint.py --baseline <base.py> <path/to/strategy.py>
検出0件で rc=0、1件以上で rc=1（ヒット内容を stdout へ）。

--baseline を渡すと、baseline 側（通常は現 live strategy.py）の検出結果を
セクション名＋検出内容の安定キーで差し引き、**新規に増えた欠陥だけ**を
表示・rc=1 にする。live のコピーを base に生成される改善候補は base の既存
欠陥をそのまま継承するため、継承欠陥で候補を全拒否しないための差分判定。
baseline が読めない場合は rc=2（環境要因。呼出側は警告のみで継続する）。
"""
import ast
import os
import sys


def _line_sig(source, node):
    """その欠陥を含むソース行テキストを返す（行ずれ耐性の凍結シグネチャ用）。"""
    lines = source.splitlines()
    if 1 <= node.lineno <= len(lines):
        return lines[node.lineno - 1].strip()
    return "<line %d>" % node.lineno


def _consecutive_unary_minus(tree, source):
    # 同一行の入れ子（----2 は USub×3 で 2 ノード一致）は1欠陥として扱い、
    # 別行の同一テキストは複数欠陥として多重集計する。
    linenos = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.UnaryOp) and isinstance(node.operand.op, ast.USub):
            linenos.add(node.lineno)

    class _N:  # _line_sig 互換の最小ノード
        def __init__(self, lineno):
            self.lineno = lineno

    return sorted(_line_sig(source, _N(n)) for n in linenos)


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
                    out.append((v, bound, seen[v]))
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

# axis 4 型のドメイン外比較を添字形 `p["x"]` だけでなく素直な変数名にも拡げる。
# 含めるのは「座標としてドメインが定義できる」名前のみ — `x`/`dist` のような
# 汎用名は誤検出リスクがあるため対象外（距離系は別クラスで扱う）。
_NAME_BOUNDS = {
    "x": (-_X_ABS_BOUND, _X_ABS_BOUND),          # 候補 drop x（board x∈[-3,3]）
    "drop_x": (-_X_ABS_BOUND, _X_ABS_BOUND),
    "piece_x": (-_X_ABS_BOUND, _X_ABS_BOUND),
    "landing_x": (-_X_ABS_BOUND, _X_ABS_BOUND),
    "target_x": (-_X_ABS_BOUND, _X_ABS_BOUND),
    "drift_x": (-6.5, 6.5),                      # x 方向ドリフト（盤面幅差分+余裕）
    "landing_y": (_Y_LO_BOUND, _Y_HI_BOUND),
    "drop_y": (_Y_LO_BOUND, _Y_HI_BOUND),
    "max_y": (_Y_LO_BOUND, _Y_HI_BOUND),
    "min_y": (_Y_LO_BOUND, _Y_HI_BOUND),
    "target_y": (_Y_LO_BOUND, _Y_HI_BOUND),
    "piece_y": (_Y_LO_BOUND, _Y_HI_BOUND),
}


def _out_of_domain_coordinate_compare(tree):
    """`obj["x"]` / `obj["y"]` / ドメイン既知の変数名 と定数の比較で、
    盤面ドメイン上 常真/常偽 になるものを返す。"""
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or len(node.ops) != 1:
            continue
        left = node.left
        bounds = None
        if isinstance(left, ast.Subscript) \
                and isinstance(left.slice, ast.Constant) \
                and left.slice.value in ("x", "y"):
            bounds = (-_X_ABS_BOUND, _X_ABS_BOUND) if left.slice.value == "x" \
                else (_Y_LO_BOUND, _Y_HI_BOUND)
        elif isinstance(left, ast.Name):
            bounds = _NAME_BOUNDS.get(left.id)
        if bounds is None:
            continue
        lo, hi = bounds
        c = _fold_const(node.comparators[0])
        if c is None:
            continue
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
            coord = left.slice.value if isinstance(left, ast.Subscript) else left.id
            hits.append((coord, type(op).__name__, c))
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
        hits.append((name, type(node.ops[0]).__name__, c))
    return sorted(hits)


def collect_problems(path):
    """4 検出器を走らせ、セクション名→ヒット一覧 を返す（出力しない）。"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    problems = []
    unary = _consecutive_unary_minus(tree, src)
    if unary:
        problems.append(("consecutive_unary_minus", unary))
    unreachable = sorted(set(_unreachable_lt_chain(tree)))
    if unreachable:
        problems.append(("unreachable_lt_chain", unreachable))
    domain = _out_of_domain_coordinate_compare(tree)
    if domain:
        problems.append(("out_of_domain_coordinate_compare", domain))
    count = _impossible_count_compare(tree)
    if count:
        problems.append(("impossible_count_compare", count))
    return problems


def _hit_key(hit):
    """行番号に依存しない安定キー（検出内容そのもの）を返す。"""
    return repr(hit)


def diff_problems(current, baseline):
    """baseline 分を差し引き、新規に増えたヒットだけを残す。

    同一キーが複数行にある場合（別行の同じ `--1` 等）は出現回数で差し引く。
    1 件でも新規が残ればそのセクションを返す。
    """
    remaining = {}
    for name, hits in baseline:
        bucket = remaining.setdefault(name, {})
        for hit in hits:
            key = _hit_key(hit)
            bucket[key] = bucket.get(key, 0) + 1
    new_problems = []
    for name, hits in current:
        bucket = remaining.get(name, {})
        added = []
        for hit in hits:
            key = _hit_key(hit)
            if bucket.get(key, 0) > 0:
                bucket[key] -= 1
                continue
            added.append(hit)
        if added:
            new_problems.append((name, added))
    return new_problems


def _report(problems):
    for name, hits in problems:
        print("semantic-lint %s: %s" % (name, hits))
    return 1 if problems else 0


def check_file(path):
    """4 検出器を走らせ、欠陥があれば 1 を返す（既定経路。CI の凍結テスト用）。"""
    return _report(collect_problems(path))


def _parse_args(argv):
    """(--baseline 無し) の従来呼び出しも含めて解釈する。(path, baseline, err)"""
    path = None
    baseline = None
    idx = 1
    while idx < len(argv):
        arg = argv[idx]
        if arg == "--baseline":
            if idx + 1 >= len(argv):
                return None, None, "--baseline には値が必要"
            baseline = argv[idx + 1]
            idx += 2
        elif arg.startswith("--baseline="):
            baseline = arg.split("=", 1)[1]
            idx += 1
        elif not arg.startswith("-") and path is None:
            path = arg
            idx += 1
        else:
            return None, None, "unexpected argument: %s" % arg
    if path is None:
        return None, None, "missing path"
    return path, baseline, None


def main(argv):
    path, baseline_path, err = _parse_args(argv)
    if err is not None:
        print(
            "usage: strategy_semantic_lint.py [--baseline <base.py>] <path>",
            file=sys.stderr,
        )
        return 2
    if not os.path.isfile(path):
        print("semantic-lint: file not found: %s" % path, file=sys.stderr)
        return 2
    if baseline_path is None:
        return check_file(path)
    # baseline 読み取り不能 = 環境要因。0/1 と混同しないよう rc=2 で返し、
    # 呼出側（ab_gate / regression）は警告のみで継続する。
    if not os.path.isfile(baseline_path):
        print("semantic-lint: baseline not found: %s" % baseline_path, file=sys.stderr)
        return 2
    try:
        baseline_problems = collect_problems(baseline_path)
    except (OSError, SyntaxError, ValueError) as exc:
        print(
            "semantic-lint: baseline unreadable: %s: %s" % (baseline_path, exc),
            file=sys.stderr,
        )
        return 2
    try:
        current_problems = collect_problems(path)
    except SyntaxError as exc:
        # 候補自身が壊れている場合は欠陥（rc=1）。baseline の環境要因とは区別する。
        print("semantic-lint: candidate parse failed: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("semantic-lint: file unreadable: %s: %s" % (path, exc), file=sys.stderr)
        return 2
    return _report(diff_problems(current_problems, baseline_problems))


if __name__ == "__main__":
    sys.exit(main(sys.argv))

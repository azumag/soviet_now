#!/usr/bin/env python3
"""policy bundle hash: 実際に着手を決める一式の同一性を 1 つの hash で表す (issue #132 Phase 2)。

`extract_decide_hash.py` は `decide()` の AST だけを見るので、helper・解析器・runner・
解析器モードが変わっても同じ hash のままになる。A/B の腕が「戦略以外は同一」であることを
保証できないため、実験の再現性が担保できなかった。

このツールは次を 1 つの hash にまとめる:
  * 戦略ファイル (AST 正規化。コメント・docstring・空白の違いでは変わらない)
  * 到達する helper モジュール (strategy_helpers/*.py のうち import されているもの)
  * analyze_board.py / strategy_runner.py (AST 正規化)
  * 解析器モードの実効値 (ANALYZE_BOARD_* と SOREN_SETTLE_REQUIRED)

使い方:
  python3 tools/policy_bundle.py --strategy strategy.py [--json]
"""
import argparse
import ast
import hashlib
import json
import os
import re
import sys

MODE_ENV = (
    "ANALYZE_BOARD_WALL_CLAMP",
    "ANALYZE_BOARD_VERTICAL_LANE_DIRECT",
    "ANALYZE_BOARD_MERGE_TOP_MODEL",
    "ANALYZE_BOARD_LANDING_ARC",
    "SOREN_SETTLE_REQUIRED",
)
CORE = ("analyze_board.py", "strategy_runner.py")


def normalized_source_hash(path):
    """AST を正規化して hash 化する。コメント・docstring・整形の違いでは変わらない。"""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError):
        return None
    for node in ast.walk(tree):
        # docstring を落とす (意味に影響しない)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                node.body = body[1:] or [ast.Pass()]
    return hashlib.sha256(ast.dump(tree, include_attributes=False).encode("utf-8")).hexdigest()


def reachable_helpers(strategy_path, root):
    """戦略が import している strategy_helpers のモジュール名 (ソート済み)。"""
    try:
        src = open(strategy_path, encoding="utf-8").read()
    except OSError:
        return []
    # import 行は 1 行に限定する (複数行にまたがる貪欲マッチで後続コードを拾わないため)
    names = set(re.findall(r"from\s+strategy_helpers\s+import\s+([^\n(]+)", src))
    out = set()
    for group in names:
        for part in group.split(","):
            part = part.split(" as ")[0].strip()
            if part:
                out.add(part)
    out |= set(re.findall(r"strategy_helpers\.([A-Za-z0-9_]+)", src))
    present = []
    for name in sorted(out):
        p = os.path.join(root, "strategy_helpers", name + ".py")
        if os.path.exists(p):
            present.append(name)
    return present


def bundle(strategy_path, root=".", env=None):
    env = os.environ if env is None else env
    parts = {"strategy": normalized_source_hash(strategy_path)}
    for name in CORE:
        parts[name] = normalized_source_hash(os.path.join(root, name))
    helpers = {}
    for name in reachable_helpers(strategy_path, root):
        helpers[name] = normalized_source_hash(os.path.join(root, "strategy_helpers", name + ".py"))
    parts["helpers"] = helpers
    parts["modes"] = {k: str(env.get(k, "")) for k in MODE_ENV}
    blob = json.dumps(parts, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16], parts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="strategy.py")
    ap.add_argument("--root", default=".")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    h, parts = bundle(a.strategy, a.root)
    if a.json:
        print(json.dumps({"policy_bundle_hash": h, "parts": parts}, ensure_ascii=False, indent=1))
    else:
        print(h)
    return 0


if __name__ == "__main__":
    sys.exit(main())

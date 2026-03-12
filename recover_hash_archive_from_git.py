#!/usr/bin/env python3
"""Recover missing strategy_versions/by_hash entries from git history.

Usage:
  python3 recover_hash_archive_from_git.py
  python3 recover_hash_archive_from_git.py <hash1> <hash2> ...
"""

from __future__ import annotations

import ast
import json
import math
import subprocess
import sys
from pathlib import Path


ROLLING_SCORES_FILE = Path("tmp/state/rolling_scores.json")
BY_HASH_DIR = Path("strategy_versions/by_hash")
MIN_GAMES = 12
LBC_Z = 1.28
W_P50 = 0.55
W_P25 = 0.30
W_LCB = 0.15


def run_git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL)


def quantile(vals: list[int], p: float) -> float:
    xs = sorted(vals)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def calc_comp(scores: list[int]) -> float:
    mean = sum(scores) / len(scores)
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    if len(scores) > 1:
        var = sum((x - mean) ** 2 for x in scores) / len(scores)
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - LBC_Z * (std / math.sqrt(len(scores)))
    return (W_P50 * p50) + (W_P25 * p25) + (W_LCB * lcb)


def extract_decide_hash_from_source(source: str) -> str:
    def stable_ast_dump(node):
        if isinstance(node, ast.AST):
            fields = []
            for field in getattr(node, "_fields", ()):
                value = getattr(node, field)
                if value == [] or value is None:
                    continue
                fields.append(f"{field}={stable_ast_dump(value)}")
            if fields:
                return f"{node.__class__.__name__}({', '.join(fields)})"
            return f"{node.__class__.__name__}()"
        if isinstance(node, list):
            return "[" + ", ".join(stable_ast_dump(item) for item in node) + "]"
        return repr(node)

    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            normalized = stable_ast_dump(ast.Module(body=body, type_ignores=[]))
            import hashlib

            return hashlib.md5(normalized.encode("utf-8")).hexdigest()[:12]
    return ""


def load_target_hashes(argv: list[str]) -> list[str]:
    if argv:
        return argv
    rolling = json.loads(ROLLING_SCORES_FILE.read_text())
    rows: list[tuple[float, str]] = []
    for hash_, data in rolling.items():
        scores = [int(x) for x in data.get("scores", [])]
        if len(scores) < MIN_GAMES:
            continue
        if (BY_HASH_DIR / f"{hash_}.py").exists():
            continue
        rows.append((calc_comp(scores), hash_))
    rows.sort(reverse=True)
    return [hash_ for _, hash_ in rows]


def main() -> int:
    BY_HASH_DIR.mkdir(parents=True, exist_ok=True)
    targets = set(load_target_hashes(sys.argv[1:]))
    if not targets:
        print("no missing target hashes")
        return 0

    recovered: dict[str, str] = {}
    commits = [c for c in run_git(["log", "--format=%H", "--all", "--", "strategy.py"]).splitlines() if c]

    for commit in commits:
        if not targets:
            break
        try:
            source = run_git(["show", f"{commit}:strategy.py"])
        except subprocess.CalledProcessError:
            continue
        try:
            hash_ = extract_decide_hash_from_source(source)
        except Exception:
            continue
        if hash_ not in targets:
            continue
        out = BY_HASH_DIR / f"{hash_}.py"
        if not out.exists():
            out.write_text(source, encoding="utf-8")
        recovered[hash_] = commit
        targets.remove(hash_)

    for hash_, commit in sorted(recovered.items()):
        print(f"recovered {hash_} from {commit}")
    if targets:
        for hash_ in sorted(targets):
            print(f"missing {hash_}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

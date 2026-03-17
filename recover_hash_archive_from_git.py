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
STRATEGY_VERSIONS_DIR = Path("strategy_versions")
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


def iter_local_candidate_paths() -> list[Path]:
    paths: list[Path] = []
    for path in [Path("strategy.py"), Path("tmp/revert_strategy.py")]:
        if path.exists():
            paths.append(path)
    if STRATEGY_VERSIONS_DIR.exists():
        paths.extend(sorted(STRATEGY_VERSIONS_DIR.glob("*.py")))
        by_hash_dir = STRATEGY_VERSIONS_DIR / "by_hash"
        if by_hash_dir.exists():
            paths.extend(sorted(by_hash_dir.glob("*.py")))
    return paths


def iter_git_candidate_objects() -> list[tuple[str, str]]:
    out = run_git(
        [
            "log",
            "--format=COMMIT:%H",
            "--name-only",
            "--diff-filter=AMR",
            "--all",
            "--",
            "strategy.py",
            "tmp/revert_strategy.py",
            "strategy_versions",
        ]
    )
    rows: list[tuple[str, str]] = []
    current_commit = ""
    seen: set[tuple[str, str]] = set()
    for raw in out.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("COMMIT:"):
            current_commit = line.split(":", 1)[1]
            continue
        if not current_commit or not line.endswith(".py"):
            continue
        item = (current_commit, line)
        if item in seen:
            continue
        seen.add(item)
        rows.append(item)
    return rows


def main() -> int:
    BY_HASH_DIR.mkdir(parents=True, exist_ok=True)
    targets = set(load_target_hashes(sys.argv[1:]))
    if not targets:
        print("no missing target hashes")
        return 0

    recovered: dict[str, str] = {}
    for path in iter_local_candidate_paths():
        if not targets:
            break
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            continue
        path_hash = path.stem if path.parent == BY_HASH_DIR else ""
        if path_hash and path_hash in targets:
            out = BY_HASH_DIR / f"{path_hash}.py"
            if not out.exists():
                out.write_text(source, encoding="utf-8")
            recovered[path_hash] = str(path)
            targets.remove(path_hash)
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
        recovered[hash_] = str(path)
        targets.remove(hash_)

    for commit, path in iter_git_candidate_objects():
        if not targets:
            break
        basename_hash = Path(path).stem if path.startswith("strategy_versions/by_hash/") else ""
        if basename_hash and basename_hash in targets:
            try:
                source = run_git(["show", f"{commit}:{path}"])
            except subprocess.CalledProcessError:
                continue
            out = BY_HASH_DIR / f"{basename_hash}.py"
            if not out.exists():
                out.write_text(source, encoding="utf-8")
            recovered[basename_hash] = f"{commit}:{path}"
            targets.remove(basename_hash)
            continue
        try:
            source = run_git(["show", f"{commit}:{path}"])
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
        recovered[hash_] = f"{commit}:{path}"
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

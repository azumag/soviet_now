#!/usr/bin/env python3
"""Inject a deadline-safety guard block into the top-N strategies in the hash archive.

- Picks top-N hashes by composite score from rolling_scores.
- Prepends a self-contained "DEADLINE GUARD" prelude to each decide() body
  (after the docstring if present).
- Renames archive files to match the new hash, and rewrites rolling_scores
  so the existing score history follows the modified strategy.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROLLING = ROOT / "tmp/state/rolling_scores.json"
HOT = ROOT / "strategy_versions/by_hash"
PERM = ROOT / "strategy_versions_archive/by_hash"
EXTRACT = ROOT / "extract_decide_hash.py"

TOP_N = 10
MIN_GAMES = 12
LBC_Z = 1.0
W_P50, W_P25, W_LCB = 0.55, 0.30, 0.15

GUARD_BLOCK = '''    # --- BEGIN DEADLINE GUARD (injected from current strategy deadline logic) ---
    # Emergency deadline safety: when the reactor is past/near the deadline,
    # force an immediate merge or the safest landing to avoid runaway stacking.
    __dlg_game_state = game_state if isinstance(game_state, dict) else {}
    __dlg_analysis = analysis if isinstance(analysis, dict) else {}
    __dlg_reactor = __dlg_analysis.get("reactor", {}) if isinstance(__dlg_analysis.get("reactor", {}), dict) else {}
    __dlg_margin = __dlg_reactor.get("deadline_margin", 99.0)
    try:
        __dlg_margin = float(__dlg_margin)
    except (TypeError, ValueError):
        __dlg_margin = 99.0
    __dlg_dcross = bool(__dlg_game_state.get("deadline_crossed", False))
    __dlg_rps = __dlg_reactor.get("reactive_pairs", [])
    if isinstance(__dlg_rps, list):
        __dlg_rp_count = len(__dlg_rps)
    else:
        try:
            __dlg_rp_count = int(__dlg_rps)
        except (TypeError, ValueError):
            __dlg_rp_count = 0
    __dlg_cands = __dlg_analysis.get("results", []) or __dlg_analysis.get("candidates", []) or []
    if not isinstance(__dlg_cands, list):
        __dlg_cands = []
    __dlg_critical = __dlg_dcross or __dlg_margin < 0.3 or __dlg_rp_count >= 3
    if __dlg_critical and __dlg_cands:
        __dlg_direct = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "DIRECT" and not c.get("crosses_deadline")
        ]
        if __dlg_direct:
            def __dlg_score_direct(c):
                return (
                    0 if c.get("danger_direct_merge_available") else 1,
                    float(c.get("landing_y", 99.0) or 99.0),
                )
            __dlg_best = min(__dlg_direct, key=__dlg_score_direct)
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_DIRECT_MERGE"}
        __dlg_near_safe = [
            c for c in __dlg_cands
            if isinstance(c, dict) and c.get("merge_grade") == "NEAR" and not c.get("crosses_deadline")
        ]
        if __dlg_near_safe:
            __dlg_best = min(__dlg_near_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_NEAR_MERGE"}
        __dlg_safe = [c for c in __dlg_cands if isinstance(c, dict) and not c.get("crosses_deadline")]
        if __dlg_safe:
            __dlg_best = min(__dlg_safe, key=lambda c: float(c.get("landing_y", 99.0) or 99.0))
            return {"x": float(__dlg_best.get("x", 0.0) or 0.0), "reason": "DEADLINE_GUARD_SAFE_LANDING"}
    # --- END DEADLINE GUARD ---
'''


def quantile(vals, p):
    xs = sorted(vals)
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def composite(scores):
    n = len(scores)
    mean = sum(scores) / n
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    std = math.sqrt(sum((x - mean) ** 2 for x in scores) / n) if n > 1 else 0.0
    lcb = mean - LBC_Z * (std / math.sqrt(n))
    return W_P50 * p50 + W_P25 * p25 + W_LCB * lcb


def top_hashes(rs: dict, n: int) -> list[str]:
    rows = []
    for h, data in rs.items():
        scores = [int(x) for x in data.get("scores", [])]
        if len(scores) < MIN_GAMES:
            continue
        rows.append((composite(scores), h))
    rows.sort(reverse=True)
    return [h for _, h in rows[:n]]


def compute_hash(path: Path) -> str:
    out = subprocess.check_output(["python3", str(EXTRACT), str(path)], text=True)
    return out.strip()


def inject_guard(src: str) -> str | None:
    """Return modified source with guard injected, or None if already present."""
    if "BEGIN DEADLINE GUARD" in src:
        return None
    tree = ast.parse(src)
    decide_node = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            decide_node = node
            break
    if decide_node is None:
        return None
    # Find insertion line number: after docstring if present, else at body start
    first_stmt = decide_node.body[0]
    is_doc = (
        isinstance(first_stmt, ast.Expr)
        and isinstance(first_stmt.value, ast.Constant)
        and isinstance(first_stmt.value.value, str)
    )
    if is_doc:
        insert_line = first_stmt.end_lineno  # 1-based; insert AFTER this line
    else:
        insert_line = first_stmt.lineno - 1  # insert BEFORE first statement
    lines = src.split("\n")
    guard_lines = GUARD_BLOCK.split("\n")
    # GUARD_BLOCK already ends with \n so split yields a trailing ""
    if guard_lines and guard_lines[-1] == "":
        guard_lines = guard_lines[:-1]
    new_lines = lines[:insert_line] + guard_lines + lines[insert_line:]
    return "\n".join(new_lines)


def main():
    rs = json.loads(ROLLING.read_text())
    hashes = top_hashes(rs, TOP_N)
    print(f"TOP-{TOP_N} hashes: {hashes}")

    renames = []  # list of (old_hash, new_hash)
    for old_hash in hashes:
        old_path = HOT / f"{old_hash}.py"
        if not old_path.exists():
            print(f"  SKIP {old_hash}: file missing in hot archive")
            continue
        src = old_path.read_text()
        new_src = inject_guard(src)
        if new_src is None:
            print(f"  SKIP {old_hash}: guard already present or no decide()")
            continue
        # Write to temp, compute new hash, then rename
        tmp_path = old_path.with_suffix(".py.tmp_inject")
        tmp_path.write_text(new_src)
        try:
            new_hash = compute_hash(tmp_path)
        except subprocess.CalledProcessError as exc:
            print(f"  FAIL {old_hash}: hash compute error {exc}")
            tmp_path.unlink(missing_ok=True)
            continue
        if new_hash == old_hash:
            print(f"  WEIRD {old_hash}: hash unchanged after injection; leaving alone")
            tmp_path.unlink(missing_ok=True)
            continue
        # Validate syntax by parsing
        try:
            ast.parse(new_src)
        except SyntaxError as exc:
            print(f"  FAIL {old_hash}: syntax error {exc}")
            tmp_path.unlink(missing_ok=True)
            continue
        new_hot = HOT / f"{new_hash}.py"
        new_perm = PERM / f"{new_hash}.py"
        # Write to final targets
        new_hot.write_text(new_src)
        new_perm.write_text(new_src)
        tmp_path.unlink(missing_ok=True)
        # Remove old files from hot and perm
        old_path.unlink(missing_ok=True)
        old_perm = PERM / f"{old_hash}.py"
        old_perm.unlink(missing_ok=True)
        renames.append((old_hash, new_hash))
        print(f"  OK  {old_hash} -> {new_hash}")

    # Rewrite rolling_scores: move entries from old_hash to new_hash
    if renames:
        for old_hash, new_hash in renames:
            if old_hash in rs:
                entry = rs.pop(old_hash)
                # Preserve provenance
                entry.setdefault("_meta", {})
                entry["_meta"]["derived_from"] = old_hash
                entry["_meta"]["injected"] = "deadline_guard"
                rs[new_hash] = entry
        # Backup and write
        bak = ROLLING.with_suffix(".json.bak_pre_inject")
        bak.write_text(ROLLING.read_text())
        ROLLING.write_text(json.dumps(rs))
        print(f"rolling_scores updated; backup at {bak}")

    print(f"done: {len(renames)} strategies modified")


if __name__ == "__main__":
    main()

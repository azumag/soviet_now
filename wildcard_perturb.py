#!/usr/bin/env python3
"""Wildcard パラメータ摂動: AI を介さず strategy.py の数値定数を ±σ 摂動させる。

帯域脱出機構 F の本体。改善が連続で空振り (stagnation_counter >= WILDCARD_TRIGGER_STAGNATION)
した時、AI 改善の代わりに本スクリプトが strategy.py の decide() 関数内の魔法定数を
ランダム選択して摂動する。

- スコープは `strategy.py` のみ。strategy_helpers/ は触らない。
- ast.unparse で全体再生成しない。AST で Constant ノードを特定し、テキストを
  lineno/col_offset で **スライス置換**する。コメント・空行・docstring を破壊しない。
- 摂動候補は decide() 関数 AST の Assign/Compare/BinOp/Return 右辺にある float/int
  リテラル、絶対値が 0.05〜500 のもの。

Usage:
    python3 wildcard_perturb.py [--dry-run] [--input strategy.py] [--output strategy.py.staging]
                                [--count 1..3] [--ratio 0.20..0.40] [--seed N]
                                [--exclude-lines 12,34,56] [--prefer-lines 78,90]
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path


MAGIC_MIN = 0.05
MAGIC_MAX = 500.0


@dataclass
class Candidate:
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int
    value: float
    is_int: bool
    context: str  # parent node kind for logging


def _find_decide_func(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            return node
    return None


def _collect_candidates(decide_node: ast.FunctionDef) -> list[Candidate]:
    candidates: list[Candidate] = []

    def _record(node: ast.Constant, parent_kind: str) -> None:
        v = node.value
        if isinstance(v, bool):
            return  # True/False は除外
        if not isinstance(v, (int, float)):
            return
        if v == 0:
            return
        absv = abs(float(v))
        if absv < MAGIC_MIN or absv > MAGIC_MAX:
            return
        if node.end_lineno is None or node.end_col_offset is None:
            return
        candidates.append(
            Candidate(
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                value=float(v),
                is_int=isinstance(v, int),
                context=parent_kind,
            )
        )

    # decide() 関数全体を歩く。ただし AST 解析対象は Assign / Compare / BinOp / Return / If.test の中の Constant に限定
    for parent in ast.walk(decide_node):
        if isinstance(parent, ast.Assign):
            for child in ast.walk(parent.value):
                if isinstance(child, ast.Constant):
                    _record(child, "Assign")
        elif isinstance(parent, ast.AugAssign):
            for child in ast.walk(parent.value):
                if isinstance(child, ast.Constant):
                    _record(child, "AugAssign")
        elif isinstance(parent, ast.Compare):
            for child in parent.comparators:
                for sub in ast.walk(child):
                    if isinstance(sub, ast.Constant):
                        _record(sub, "Compare")
        elif isinstance(parent, ast.BinOp):
            for side in (parent.left, parent.right):
                if isinstance(side, ast.Constant):
                    _record(side, "BinOp")
        elif isinstance(parent, ast.Return):
            if parent.value is not None:
                for child in ast.walk(parent.value):
                    if isinstance(child, ast.Constant):
                        _record(child, "Return")
        elif isinstance(parent, ast.If):
            for child in ast.walk(parent.test):
                if isinstance(child, ast.Constant):
                    _record(child, "If")

    # 重複除去 (同じ位置の Constant が複数の親経路から拾われる)
    seen = set()
    unique = []
    for c in candidates:
        key = (c.lineno, c.col_offset, c.end_lineno, c.end_col_offset)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)
    return unique


def _perturb_value(value: float, is_int: bool, ratio: float, rng: random.Random) -> tuple[float | int, str]:
    direction = rng.choice([-1, 1])
    delta = abs(value) * ratio * direction
    new_val = value + delta
    # 符号は変えない (絶対値が大きい場合は同符号を維持)
    if value > 0 and new_val <= 0:
        new_val = abs(new_val) or value * 0.1
    elif value < 0 and new_val >= 0:
        new_val = -(abs(new_val) or abs(value) * 0.1)
    if is_int:
        new_int = int(round(new_val))
        if new_int == int(value):
            new_int = int(value) + direction  # 最低 1 動かす
        if new_int == 0:
            # 0 への着地は退化なので、元の符号を保ったまま 1 段ずらす
            new_int = int(value) + (direction * 2)
            if new_int == 0:
                new_int = int(value) + (1 if value > 0 else -1)
        return new_int, str(new_int)
    # float: 桁を value に合わせる (元が 0.5 なら 1 桁、12.0 なら 1 桁)
    if abs(value) >= 100:
        repr_str = f"{new_val:.1f}"
    elif abs(value) >= 10:
        repr_str = f"{new_val:.2f}"
    elif abs(value) >= 1:
        repr_str = f"{new_val:.3f}"
    else:
        repr_str = f"{new_val:.4f}"
    return float(repr_str), repr_str


def _apply_patches(text: str, patches: list[tuple[Candidate, str]]) -> str:
    """patches を後ろから前へ適用してオフセットずれを回避。"""
    lines = text.split("\n")
    # 単一行リテラルのみ対応 (multi-line literal は除外済み)
    # 後ろから処理
    patches_sorted = sorted(
        patches,
        key=lambda p: (p[0].lineno, p[0].col_offset),
        reverse=True,
    )
    for cand, new_repr in patches_sorted:
        if cand.lineno != cand.end_lineno:
            continue  # multi-line は触らない
        idx = cand.lineno - 1
        if idx < 0 or idx >= len(lines):
            continue
        line = lines[idx]
        start = cand.col_offset
        end = cand.end_col_offset
        if start < 0 or end > len(line) or start >= end:
            continue
        lines[idx] = line[:start] + new_repr + line[end:]
    return "\n".join(lines)


def run(
    input_path: str,
    output_path: str | None,
    count: int,
    ratio_min: float,
    ratio_max: float,
    seed: int | None,
    dry_run: bool,
    exclude_lines: set[int] | None = None,
    prefer_lines: set[int] | None = None,
    explore_rate: float = 0.35,
) -> dict:
    text = Path(input_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise RuntimeError(f"parse error: {e}") from e
    decide_node = _find_decide_func(tree)
    if decide_node is None:
        raise RuntimeError("decide() not found")

    candidates = _collect_candidates(decide_node)
    if not candidates:
        raise RuntimeError("no perturbable constants found in decide()")

    rng = random.Random(seed)
    excluded = exclude_lines or set()
    preferred = prefer_lines or set()
    filtered_candidates = [c for c in candidates if c.lineno not in excluded]
    candidates_for_sample = filtered_candidates if len(filtered_candidates) >= count else candidates
    exclude_applied = candidates_for_sample is filtered_candidates
    preferred_candidates = [c for c in candidates_for_sample if c.lineno in preferred]
    use_preferred = (
        bool(preferred_candidates)
        and len(preferred_candidates) >= min(count, len(candidates_for_sample))
        and rng.random() >= max(0.0, min(1.0, explore_rate))
    )
    if use_preferred:
        candidates_for_sample = preferred_candidates
    actual_count = min(count, len(candidates_for_sample))
    chosen = rng.sample(candidates_for_sample, actual_count)

    patches: list[tuple[Candidate, str]] = []
    summary = []
    for c in chosen:
        ratio = rng.uniform(ratio_min, ratio_max)
        new_val, new_repr = _perturb_value(c.value, c.is_int, ratio, rng)
        patches.append((c, new_repr))
        summary.append(
            {
                "lineno": c.lineno,
                "col_offset": c.col_offset,
                "context": c.context,
                "old": c.value,
                "new": new_val,
                "new_repr": new_repr,
                "ratio": round(ratio, 3),
            }
        )

    new_text = _apply_patches(text, patches)
    # Verify the new text still parses
    try:
        ast.parse(new_text)
    except SyntaxError as e:
        raise RuntimeError(f"perturbed code does not parse: {e}") from e

    if not dry_run:
        out = output_path or (input_path + ".staging")
        Path(out).write_text(new_text, encoding="utf-8")

    return {
        "input": input_path,
        "output": output_path if output_path else (input_path + ".staging"),
        "applied": summary,
        "excluded_lines": sorted(excluded),
        "preferred_lines": sorted(preferred),
        "prefer_applied": use_preferred,
        "explore_rate": max(0.0, min(1.0, explore_rate)),
        "exclude_applied": exclude_applied,
        "dry_run": dry_run,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="strategy.py")
    p.add_argument("--output", default=None)
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--ratio-min", type=float, default=0.20)
    p.add_argument("--ratio-max", type=float, default=0.40)
    p.add_argument("--seed", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--exclude-lines", default="")
    p.add_argument("--prefer-lines", default="")
    p.add_argument("--explore-rate", type=float, default=0.35)
    args = p.parse_args()

    count = max(1, min(args.count, 5))
    exclude_lines = set()
    for raw in str(args.exclude_lines or "").replace(" ", "").split(","):
        if not raw:
            continue
        try:
            line = int(raw)
        except ValueError:
            continue
        if line > 0:
            exclude_lines.add(line)
    prefer_lines = set()
    for raw in str(args.prefer_lines or "").replace(" ", "").split(","):
        if not raw:
            continue
        try:
            line = int(raw)
        except ValueError:
            continue
        if line > 0:
            prefer_lines.add(line)
    try:
        result = run(
            args.input,
            args.output,
            count,
            args.ratio_min,
            args.ratio_max,
            args.seed,
            args.dry_run,
            exclude_lines,
            prefer_lines,
            args.explore_rate,
        )
    except RuntimeError as e:
        print(f"[wildcard_perturb] FAIL: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

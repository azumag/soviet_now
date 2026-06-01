#!/usr/bin/env python3
"""Wildcard パラメータ摂動: AI を介さず strategy.py の数値定数を ±σ 摂動させる。

帯域脱出機構 F の本体。改善が連続で空振り (stagnation_counter >= WILDCARD_TRIGGER_STAGNATION)
した時、AI 改善の代わりに本スクリプトが strategy.py の戦略ロジック内の魔法定数を
ランダム選択して摂動する。

- スコープは `strategy.py` のみ。strategy_helpers/ は触らない。
- ast.unparse で全体再生成しない。AST で Constant ノードを特定し、テキストを
  lineno/col_offset で **スライス置換**する。コメント・空行・docstring を破壊しない。
- 摂動候補は decide() から到達できる helper と参照グローバル定数の
  Assign/Compare/BinOp/Return 右辺にある float/int リテラル。

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


@dataclass
class Candidate:
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int
    value: float | bool
    is_int: bool
    is_bool: bool
    context: str  # parent node kind for logging


def _find_decide_func(tree: ast.AST) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "decide":
            return node
    return None


def _top_level_name_targets(node: ast.AST) -> set[str]:
    targets: list[ast.AST] = []
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    names: set[str] = set()
    for target in targets:
        for child in ast.walk(target):
            if isinstance(child, ast.Name):
                names.add(child.id)
    return names


def _load_names(node: ast.AST) -> set[str]:
    return {child.id for child in ast.walk(node) if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}


def _called_function_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            names.add(child.func.id)
    return names


def _walk_strategy_node(root: ast.AST):
    stack = [root]
    while stack:
        node = stack.pop()
        yield node
        if node is not root and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        stack.extend(reversed(list(ast.iter_child_nodes(node))))


def _reachable_strategy_nodes(tree: ast.Module, decide_node: ast.FunctionDef) -> list[ast.AST]:
    top_functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    global_assignments: dict[str, list[ast.AST]] = {}
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        for name in _top_level_name_targets(node):
            global_assignments.setdefault(name, []).append(node)

    reachable_functions: dict[str, ast.FunctionDef] = {decide_node.name: decide_node}
    referenced_names: set[str] = set()
    queue = [decide_node.name]
    while queue:
        name = queue.pop(0)
        func = reachable_functions[name]
        referenced_names.update(_load_names(func))
        for called_name in _called_function_names(func):
            if called_name in top_functions and called_name not in reachable_functions:
                reachable_functions[called_name] = top_functions[called_name]
                queue.append(called_name)

    reachable_global_names = {name for name in referenced_names if name in global_assignments}
    global_queue = list(reachable_global_names)
    while global_queue:
        name = global_queue.pop(0)
        for assignment in global_assignments.get(name, []):
            for ref in _load_names(assignment):
                if ref in global_assignments and ref not in reachable_global_names:
                    reachable_global_names.add(ref)
                    global_queue.append(ref)

    nodes: list[ast.AST] = list(reachable_functions.values())
    seen_assignments: set[int] = set()
    for name in sorted(reachable_global_names):
        for assignment in global_assignments.get(name, []):
            ident = id(assignment)
            if ident in seen_assignments:
                continue
            seen_assignments.add(ident)
            nodes.append(assignment)
    return nodes


def _collect_candidates(tree: ast.Module, decide_node: ast.FunctionDef) -> list[Candidate]:
    candidates: list[Candidate] = []
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _expr_has_type_like_name(expr: ast.AST) -> bool:
        for child in ast.walk(expr):
            if isinstance(child, ast.Name) and "type" in child.id:
                return True
        return False

    def _is_structural_type_step_literal(node: ast.Constant) -> bool:
        if isinstance(node.value, bool) or node.value != 1:
            return False
        parent = parents.get(node)
        effective_child: ast.AST = node
        if isinstance(parent, ast.UnaryOp) and isinstance(parent.op, ast.USub):
            effective_child = parent
            parent = parents.get(parent)
        if not isinstance(parent, ast.BinOp) or not isinstance(parent.op, (ast.Add, ast.Sub)):
            return False
        other = parent.left if parent.right is effective_child else parent.right
        return _expr_has_type_like_name(other)

    def _record(node: ast.Constant, parent_kind: str) -> None:
        v = node.value
        if not isinstance(v, (bool, int, float)):
            return
        if node.end_lineno is None or node.end_col_offset is None:
            return
        if _is_structural_type_step_literal(node):
            return
        candidates.append(
            Candidate(
                lineno=node.lineno,
                col_offset=node.col_offset,
                end_lineno=node.end_lineno,
                end_col_offset=node.end_col_offset,
                value=v if isinstance(v, bool) else float(v),
                is_int=isinstance(v, int) and not isinstance(v, bool),
                is_bool=isinstance(v, bool),
                context=parent_kind,
            )
        )

    # decide() から到達できる戦略ロジックを歩く。ただし AST 解析対象は
    # Assign / Compare / BinOp / Return / If.test の中の Constant に限定する。
    for root in _reachable_strategy_nodes(tree, decide_node):
        for parent in _walk_strategy_node(root):
            if isinstance(parent, ast.Assign):
                for child in ast.walk(parent.value):
                    if isinstance(child, ast.Constant):
                        _record(child, "Assign")
            elif isinstance(parent, ast.AnnAssign):
                if parent.value is None:
                    continue
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


def _magnitude_ratio_scale(value: float) -> float:
    absv = abs(value)
    if absv < 0.1:
        return 2.6
    if absv < 1:
        return 2.0
    if absv < 5:
        return 1.6
    if absv < 20:
        return 1.25
    if absv < 100:
        return 1.0
    if absv < 500:
        return 0.8
    return 0.55


def _pick_perturb_ratio(
    value: float,
    ratio_min: float,
    ratio_max: float,
    rng: random.Random,
) -> tuple[float, float, bool]:
    low = max(0.0, min(ratio_min, ratio_max))
    high = max(low, max(ratio_min, ratio_max))
    scale = _magnitude_ratio_scale(value)
    mean = ((low + high) / 2.0) * scale
    sigma = max((high - low) / 2.0, high * 0.35, 0.01) * scale
    ratio = abs(rng.gauss(mean, sigma))
    floor = max(0.005, low * scale * 0.25)
    ceiling = max(high * scale * 3.0, floor)
    outlier = ratio > high * scale
    ratio = min(max(ratio, floor), ceiling)
    return ratio, scale, outlier


def _perturb_value(
    value: float | bool,
    is_int: bool,
    is_bool: bool,
    ratio: float,
    rng: random.Random,
) -> tuple[float | int | bool, str]:
    if is_bool:
        new_bool = not bool(value)
        return new_bool, "True" if new_bool else "False"
    direction = rng.choice([-1, 1])
    numeric_value = float(value)
    delta_base = abs(numeric_value) if numeric_value != 0 else 1.0
    delta = delta_base * ratio * direction
    new_val = numeric_value + delta
    # 符号は変えない (絶対値が大きい場合は同符号を維持)
    if numeric_value > 0 and new_val <= 0:
        new_val = abs(new_val) or numeric_value * 0.1
    elif numeric_value < 0 and new_val >= 0:
        new_val = -(abs(new_val) or abs(numeric_value) * 0.1)
    if is_int:
        new_int = int(round(new_val))
        if new_int == int(numeric_value):
            new_int = int(numeric_value) + direction  # 最低 1 動かす
        if new_int == 0:
            # 0 への着地は退化なので、元の符号を保ったまま 1 段ずらす
            new_int = int(numeric_value) + (direction * 2)
            if new_int == 0:
                new_int = int(numeric_value) + (1 if numeric_value > 0 else -1)
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
    random_count: bool = False,
) -> dict:
    text = Path(input_path).read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError as e:
        raise RuntimeError(f"parse error: {e}") from e
    decide_node = _find_decide_func(tree)
    if decide_node is None:
        raise RuntimeError("decide() not found")

    candidates = _collect_candidates(tree, decide_node)
    if not candidates:
        raise RuntimeError("no perturbable constants found in reachable strategy logic")

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
    max_count = len(candidates_for_sample)
    requested_count = count
    if random_count:
        actual_count = rng.randint(1, max_count)
    else:
        actual_count = min(count, max_count)
    chosen = rng.sample(candidates_for_sample, actual_count)

    patches: list[tuple[Candidate, str]] = []
    summary = []
    for c in chosen:
        if c.is_bool:
            ratio, magnitude_scale, normal_outlier = 1.0, 1.0, False
        else:
            ratio, magnitude_scale, normal_outlier = _pick_perturb_ratio(
                float(c.value),
                ratio_min,
                ratio_max,
                rng,
            )
        new_val, new_repr = _perturb_value(c.value, c.is_int, c.is_bool, ratio, rng)
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
                "magnitude_scale": round(magnitude_scale, 3),
                "normal_outlier": normal_outlier,
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
        "available_candidates": len(candidates),
        "sample_pool_candidates": len(candidates_for_sample),
        "requested_count": requested_count,
        "selected_count": actual_count,
        "random_count": random_count,
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
    p.add_argument("--random-count", action="store_true")
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
            args.random_count,
        )
    except RuntimeError as e:
        print(f"[wildcard_perturb] FAIL: {e}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

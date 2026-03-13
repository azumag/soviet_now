#!/usr/bin/env python3
"""Generate a Mermaid strategy phyrogenetic graph.

The graph uses rolling_scores.json for recent parent links and git history for
older backfill. Because rollbacks can point to an existing older strategy, the
result is a DAG rather than a strict tree.
"""

from __future__ import annotations

import argparse
import ast
import json
import math
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
ROLLING_SCORES_FILE = ROOT / "tmp/state/rolling_scores.json"
CURRENT_STRATEGY_RUN_FILE = ROOT / "tmp/state/current_strategy_run.json"
BEST_STRATEGY_ANCHOR_FILE = ROOT / "tmp/state/best_strategy_anchor.json"
LAST_ROLLBACK_PAIR_FILE = ROOT / "tmp/state/last_rollback_pair.json"
STRATEGY_FILE = ROOT / "strategy.py"
STRATEGY_HASH_ARCHIVE_DIR = ROOT / "strategy_versions/by_hash"

RANK_LCB_Z = 1.28
RANK_WEIGHT_P50 = 0.55
RANK_WEIGHT_P25 = 0.30
RANK_WEIGHT_LCB = 0.15

EDGE_PRIORITY = {
    "rolling": 10,
    "state": 20,
    "git": 30,
    "pending": 40,
}


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def run_git(args: list[str]) -> str:
    try:
        return subprocess.check_output(
            ["git", *args],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return ""


def stable_ast_dump(node: ast.AST | list | object) -> str:
    if isinstance(node, ast.AST):
        fields: list[str] = []
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


def compute_hash_from_source(source: str) -> str:
    try:
        tree = ast.parse(source)
    except Exception:
        return ""

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


def compute_hash_from_file(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return compute_hash_from_source(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return ""


def quantile(vals: list[int], p: float) -> float:
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def calc_metrics(scores: list[int]) -> dict[str, float] | None:
    if not scores:
        return None
    n = len(scores)
    mean = sum(scores) / n
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - RANK_LCB_Z * (std / math.sqrt(n))
    comp = (RANK_WEIGHT_P50 * p50) + (RANK_WEIGHT_P25 * p25) + (RANK_WEIGHT_LCB * lcb)
    return {
        "n": float(n),
        "p25": p25,
        "p50": p50,
        "lcb": lcb,
        "comp": comp,
    }


@dataclass
class Node:
    hash_value: str
    first_order: int = 10**9
    sources: set[str] = field(default_factory=set)
    games_total: int | None = None
    sample_n: int | None = None
    comp: float | None = None
    p50: float | None = None
    p25: float | None = None
    first_commit: str = ""
    first_subject: str = ""
    tags: set[str] = field(default_factory=set)

    def absorb_metrics(self, games_total: int | None, metrics: dict[str, float] | None) -> None:
        if games_total is not None and self.games_total is None:
            self.games_total = games_total
        if not metrics:
            return
        if self.sample_n is None:
            self.sample_n = int(metrics["n"])
        if self.comp is None:
            self.comp = metrics["comp"]
        if self.p50 is None:
            self.p50 = metrics["p50"]
        if self.p25 is None:
            self.p25 = metrics["p25"]


@dataclass
class Edge:
    src: str
    dst: str
    kind: str
    source: str
    order: int
    note: str = ""


class GraphBuilder:
    def __init__(self) -> None:
        self.nodes: dict[str, Node] = {}
        self.edges: dict[tuple[str, str], Edge] = {}

    def ensure_node(self, hash_value: str, *, source: str, order: int) -> Node | None:
        if not hash_value:
            return None
        node = self.nodes.get(hash_value)
        if node is None:
            node = Node(hash_value=hash_value, first_order=order)
            self.nodes[hash_value] = node
        node.first_order = min(node.first_order, order)
        node.sources.add(source)
        return node

    def add_edge(self, src: str, dst: str, *, kind: str, source: str, order: int, note: str = "") -> None:
        if not src or not dst or src == dst:
            return
        self.ensure_node(src, source=source, order=order)
        self.ensure_node(dst, source=source, order=order + 1)
        key = (src, dst)
        incoming = Edge(src=src, dst=dst, kind=kind, source=source, order=order, note=note)
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = incoming
            return
        current_priority = EDGE_PRIORITY.get(existing.source, 0)
        incoming_priority = EDGE_PRIORITY.get(source, 0)
        if incoming_priority > current_priority:
            self.edges[key] = incoming
            return
        if incoming_priority == current_priority and order < existing.order:
            self.edges[key] = incoming


def infer_edge_kind(subject: str, dst_hash: str, seen_hashes: set[str]) -> str:
    lowered = subject.lower()
    if "auto-revert" in lowered or "rollback" in lowered or "revert" in lowered:
        return "rollback"
    if dst_hash in seen_hashes:
        return "rollback"
    return "improve"


def collect_graph(pending_edges: list[tuple[str, str, str]]) -> tuple[GraphBuilder, str, str]:
    builder = GraphBuilder()
    rolling = load_json(ROLLING_SCORES_FILE)

    for order, (hash_value, data) in enumerate(rolling.items(), start=1_000_000):
        node = builder.ensure_node(hash_value, source="rolling", order=order)
        scores = [int(x) for x in data.get("scores", []) if isinstance(x, int) or str(x).isdigit()]
        metrics = calc_metrics(scores)
        games_total = data.get("games_total")
        try:
            games_total_int = int(games_total) if games_total is not None else len(scores)
        except Exception:
            games_total_int = len(scores)
        if node is not None:
            node.absorb_metrics(games_total_int, metrics)
        prev_hash = str(data.get("prev_hash", "") or "")
        if prev_hash:
            builder.add_edge(prev_hash, hash_value, kind="improve", source="rolling", order=order)

    for order, file_path in enumerate(sorted(STRATEGY_HASH_ARCHIVE_DIR.glob("*.py")), start=2_000_000):
        builder.ensure_node(file_path.stem, source="state", order=order)

    git_log = run_git(["log", "--reverse", "--format=%H%x1f%ct%x1f%s", "--", "strategy.py"])
    prev_hash = ""
    seen_hashes: set[str] = set()
    for order, line in enumerate(git_log.splitlines(), start=1):
        parts = line.split("\x1f", 2)
        if len(parts) != 3:
            continue
        commit, _epoch, subject = parts
        source = run_git(["show", f"{commit}:strategy.py"])
        if not source:
            continue
        hash_value = compute_hash_from_source(source)
        if not hash_value:
            continue
        node = builder.ensure_node(hash_value, source="git", order=order)
        if node is not None and not node.first_commit:
            node.first_commit = commit
            node.first_subject = subject
        if prev_hash and prev_hash != hash_value:
            edge_kind = infer_edge_kind(subject, hash_value, seen_hashes)
            builder.add_edge(prev_hash, hash_value, kind=edge_kind, source="git", order=order, note=subject)
        seen_hashes.add(hash_value)
        prev_hash = hash_value

    working_tree_hash = compute_hash_from_file(STRATEGY_FILE)
    current_run = load_json(CURRENT_STRATEGY_RUN_FILE)
    current_run_hash = str(current_run.get("hash", "") or "")
    current_hash = working_tree_hash if pending_edges else (current_run_hash or working_tree_hash)
    if current_run_hash:
        node = builder.ensure_node(current_run_hash, source="state", order=3_000_000)
        scores = [int(x) for x in current_run.get("scores", []) if isinstance(x, int) or str(x).isdigit()]
        if node is not None:
            node.absorb_metrics(int(current_run.get("games_total", len(scores)) or len(scores)), calc_metrics(scores))
    if current_hash:
        node = builder.ensure_node(current_hash, source="state", order=3_000_001)
        if node is not None:
            node.tags.add("current")

    anchor = load_json(BEST_STRATEGY_ANCHOR_FILE)
    anchor_hash = str(anchor.get("hash", "") or "")
    if anchor_hash:
        node = builder.ensure_node(anchor_hash, source="state", order=3_000_002)
        if node is not None:
            node.tags.add("anchor")
            if node.comp is None:
                try:
                    node.comp = float(anchor.get("comp"))
                    node.p50 = float(anchor.get("p50"))
                    node.p25 = float(anchor.get("p25"))
                    node.sample_n = int(anchor.get("n"))
                except Exception:
                    pass

    rollback_pair = load_json(LAST_ROLLBACK_PAIR_FILE)
    from_hash = str(rollback_pair.get("from_hash", "") or "")
    to_hash = str(rollback_pair.get("to_hash", "") or "")
    note = str(rollback_pair.get("note", "") or "")
    if from_hash and to_hash:
        builder.add_edge(from_hash, to_hash, kind="rollback", source="state", order=3_000_010, note=note)

    for index, (kind, src, dst) in enumerate(pending_edges, start=3_100_000):
        builder.add_edge(src, dst, kind=kind, source="pending", order=index)
        if dst == current_hash:
            node = builder.ensure_node(dst, source="pending", order=index)
            if node is not None:
                node.tags.add("current")

    return builder, current_hash, anchor_hash


def node_label(node: Node) -> str:
    lines = [node.hash_value]
    tags = []
    if "current" in node.tags:
        tags.append("CURRENT")
    if "anchor" in node.tags:
        tags.append("ANCHOR")
    if tags:
        lines.append(" ".join(tags))
    parts: list[str] = []
    if node.games_total is not None:
        parts.append(f"g={node.games_total}")
    if node.sample_n is not None:
        parts.append(f"n={node.sample_n}")
    if parts:
        lines.append(" ".join(parts))
    if node.comp is not None:
        lines.append(f"comp={node.comp:.1f}")
    return "<br/>".join(lines)


def node_class(node: Node) -> str:
    if "current" in node.tags and "anchor" in node.tags:
        return "current_anchor"
    if "current" in node.tags:
        return "current"
    if "anchor" in node.tags:
        return "anchor"
    return "plain"


def render_markdown(builder: GraphBuilder, current_hash: str, anchor_hash: str) -> str:
    ordered_nodes = sorted(builder.nodes.values(), key=lambda n: (n.first_order, n.hash_value))
    ordered_edges = sorted(builder.edges.values(), key=lambda e: (e.order, e.src, e.dst))
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    lines: list[str] = []
    lines.append("# Strategy Phyrogenetic Tree")
    lines.append("")
    lines.append(f"- Updated: `{now}`")
    lines.append(f"- Nodes: `{len(ordered_nodes)}`")
    lines.append(f"- Edges: `{len(ordered_edges)}`")
    lines.append(f"- Current: `{current_hash or 'unknown'}`")
    lines.append(f"- Anchor: `{anchor_hash or 'unknown'}`")
    lines.append("- Solid edge: mutation/improvement")
    lines.append("- Dashed edge: rollback")
    lines.append("- Older history is backfilled from `git log -- strategy.py` when local rolling data is incomplete.")
    lines.append("")
    lines.append("```mermaid")
    lines.append("flowchart TD")
    for node in ordered_nodes:
        lines.append(f'    h_{node.hash_value}["{node_label(node)}"]')
    lines.append("")
    for edge in ordered_edges:
        if edge.kind == "rollback":
            lines.append(f"    h_{edge.src} -. rollback .-> h_{edge.dst}")
        else:
            lines.append(f"    h_{edge.src} -->|improve| h_{edge.dst}")
    lines.append("")
    lines.append("    classDef plain fill:#f8f8f8,stroke:#666,stroke-width:1px,color:#222;")
    lines.append("    classDef current fill:#ffe8a3,stroke:#9a6700,stroke-width:3px,color:#222;")
    lines.append("    classDef anchor fill:#d7f5dd,stroke:#1f6f43,stroke-width:3px,color:#222;")
    lines.append("    classDef current_anchor fill:#f3e4a8,stroke:#1f6f43,stroke-width:4px,color:#222;")
    lines.append("")
    for node in ordered_nodes:
        lines.append(f"    class h_{node.hash_value} {node_class(node)};")
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="phyrogenetic-tree.md")
    parser.add_argument(
        "--pending-edge",
        nargs=3,
        action="append",
        metavar=("KIND", "FROM_HASH", "TO_HASH"),
        default=[],
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pending_edges: list[tuple[str, str, str]] = []
    for kind, src, dst in args.pending_edge:
        if kind not in {"improve", "rollback"}:
            continue
        pending_edges.append((kind, src, dst))

    builder, current_hash, anchor_hash = collect_graph(pending_edges)
    output_path = (ROOT / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output_path.write_text(render_markdown(builder, current_hash, anchor_hash), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

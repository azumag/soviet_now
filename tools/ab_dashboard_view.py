#!/usr/bin/env python3
"""Post-process status_dashboard output for active A/B experiments.

The normal strategy ranking is backed by rolling_scores.json, whose production
window is intentionally small because it is also used by regression/rollback
logic.  An interleaved A/B experiment has a better source for viewer-facing
comparison: ab_games.jsonl.  This adapter replaces only the rendered Strategy
Comparison panel while an A/B is active, using up to the last 100 valid samples
per arm from the experiment ledger.  It does not mutate rolling state or any
strategy/regression decision input.
"""

from __future__ import annotations

import copy
import json
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import status_dashboard as sd

AB_VIEW_KEEP = 100


def _metric_value(row: dict, primary: str):
    module = sd._ab_report_module()
    if module is not None and hasattr(module, "primary_value"):
        return module.primary_value(row, primary)
    key = "eval" if primary == "eval" else "score"
    try:
        value = float(row.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _ledger_scores(ab_status: dict, games_path: str | None = None) -> dict[str, list[float]]:
    """Return de-duplicated valid A/B samples, capped only for rendering."""
    path = Path(games_path or sd.AB_GAMES_FILE)
    primary = str(ab_status.get("metric") or "score")
    seen: set[int] = set()
    values = {"A": [], "B": []}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for raw in lines:
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict) or row.get("tainted"):
            continue
        try:
            idx = int(row.get("idx"))
        except (TypeError, ValueError):
            continue
        if idx in seen:
            continue
        seen.add(idx)
        arm = str(row.get("arm") or "")
        if arm not in values:
            continue
        value = _metric_value(row, primary)
        if value is not None:
            values[arm].append(value)

    return {arm: samples[-AB_VIEW_KEEP:] for arm, samples in values.items()}


def _entry_from_scores(hash_: str, scores: list[float], total: int):
    metrics = sd.calc_strategy_metrics(scores)
    if not hash_ or not metrics:
        return None
    return {
        "hash": hash_,
        "h8": hash_[:8],
        "n_roll": metrics["n"],
        "n_total": total,
        "comp": metrics["comp"],
        "p50": metrics["p50"],
        "p25": metrics["p25"],
        "lcb": metrics["lcb"],
        "russia_count": 0,
    }


def render_ab_strategy_comparison(ab_status: dict, *, games_path: str | None = None):
    """Render Strategy Comparison with active experiment samples for A and B."""
    rolling = copy.deepcopy(sd.load_rolling())
    scores = _ledger_scores(ab_status, games_path)
    a_hash = str(ab_status.get("a_hash") or "")
    b_hash = str(ab_status.get("b_hash") or "")

    # n_total is the experiment population for the arm, while n_roll is the
    # viewer-facing last-100 window.  Never feed this replacement back into the
    # real rolling file; regression/rollback semantics stay untouched.
    totals = {}
    for arm in ("A", "B"):
        try:
            totals[arm] = int(ab_status.get(f"n_{arm.lower()}") or len(scores[arm]))
        except (TypeError, ValueError):
            totals[arm] = len(scores[arm])
        totals[arm] = max(totals[arm], len(scores[arm]))

    for arm, hash_ in (("A", a_hash), ("B", b_hash)):
        if hash_ and scores[arm]:
            previous = rolling.get(hash_) if isinstance(rolling.get(hash_), dict) else {}
            rolling[hash_] = {
                **previous,
                "scores": list(scores[arm]),
                "games_total": totals[arm],
            }

    current_hash = sd.get_strategy_hash()
    original_get_current = sd.get_current_strategy_run_entry

    def get_current_from_experiment(hash_):
        if hash_ == a_hash and scores["A"]:
            entry = _entry_from_scores(a_hash, scores["A"], totals["A"])
            if entry:
                # russia_count is not an A/B ranking metric; preserve it only
                # when the ordinary current-run entry already has evidence.
                ordinary = original_get_current(hash_)
                if ordinary:
                    entry["russia_count"] = ordinary.get("russia_count", 0)
                return entry
        return original_get_current(hash_)

    sd.get_current_strategy_run_entry = get_current_from_experiment
    try:
        return sd.render_strategy_comparison(rolling, current_hash, ab_status=ab_status)
    finally:
        sd.get_current_strategy_run_entry = original_get_current


def postprocess(text: str, *, games_path: str | None = None) -> str:
    ab_status = sd.load_ab_progress()
    if not isinstance(ab_status, dict) or not ab_status.get("active"):
        return text
    if not (_ledger_scores(ab_status, games_path)["A"] or _ledger_scores(ab_status, games_path)["B"]):
        return text

    replacement = render_ab_strategy_comparison(ab_status, games_path=games_path)
    lines = text.splitlines()
    start = next(
        (i for i, line in enumerate(lines)
         if sd.ANSI_RE.sub("", line).strip().startswith("Strategy Comparison")),
        None,
    )
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and lines[end].strip():
        end += 1
    lines[start:end] = replacement
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(lines) + suffix


def main() -> int:
    sys.stdout.write(postprocess(sys.stdin.read()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

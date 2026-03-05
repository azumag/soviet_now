#!/usr/bin/env python3
"""regression_calc.py - Shared regression/ranking calculations for eloop.

Subcommands:
  rank           Rank strategy hashes by composite score, excluding current hash.
  prune          Return top N hashes to keep (for archive pruning).
  check          Check if current strategy is a regression vs best candidate.
  update_scores  Append a score to the rolling scores JSON file.
"""
import json
import math
import os
import sys


def quantile(vals, p):
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


def metrics(scores, lcb_z, w_p50, w_p25, w_lcb):
    """Return (composite, p50, p25, lcb, n) for a list of scores."""
    n = len(scores)
    mean = sum(scores) / n
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    composite = w_p50 * p50 + w_p25 * p25 + w_lcb * lcb
    return composite, p50, p25, lcb, n


# ---- Subcommand: rank ----
def cmd_rank(args):
    rs_file = args[0]
    current_hash = args[1]
    min_games = int(args[2])
    lcb_z = float(args[3])
    w_p50 = float(args[4])
    w_p25 = float(args[5])
    w_lcb = float(args[6])
    rs = json.load(open(rs_file))

    rows = []
    for h, data in rs.items():
        if h == current_hash:
            continue
        scores = [int(x) for x in data.get("scores", [])]
        if len(scores) < min_games:
            continue
        comp, p50, p25, lcb, n = metrics(scores, lcb_z, w_p50, w_p25, w_lcb)
        rows.append((comp, p50, p25, lcb, n, h))

    rows.sort(key=lambda x: (x[0], x[1], x[2], x[4]), reverse=True)
    for comp, p50, p25, lcb, n, h in rows:
        print(f"{h}|{comp:.2f}|{p50:.1f}|{p25:.1f}|{lcb:.1f}|{n}")


# ---- Subcommand: prune ----
def cmd_prune(args):
    rs_file = args[0]
    min_games = int(args[1])
    keep_top = int(args[2])
    lcb_z = float(args[3])
    w_p50 = float(args[4])
    w_p25 = float(args[5])
    w_lcb = float(args[6])
    rs = json.load(open(rs_file))

    rows = []
    for h, data in rs.items():
        scores = [int(x) for x in data.get("scores", [])]
        if len(scores) < min_games:
            continue
        comp, p50, p25, lcb, n = metrics(scores, lcb_z, w_p50, w_p25, w_lcb)
        # composite_score in original returned (comp, p50, p25, n) — sort uses same fields
        rows.append((comp, p50, p25, n, h))
    rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    for _, _, _, _, h in rows[:keep_top]:
        print(h)


# ---- Subcommand: check ----
def cmd_check(args):
    rs_file = args[0]
    current_hash = args[1]
    min_games_current = int(args[2])
    min_games_candidates = int(args[3])
    lcb_z = float(args[4])
    w_p50 = float(args[5])
    w_p25 = float(args[6])
    w_lcb = float(args[7])
    composite_ratio = float(args[8])
    p25_ratio = float(args[9])

    if not os.path.exists(rs_file):
        print("OK")
        return

    with open(rs_file) as f:
        rs = json.load(f)

    if current_hash not in rs:
        print("OK")
        return

    current_scores = [int(x) for x in rs[current_hash].get("scores", [])]
    if len(current_scores) < min_games_current:
        print("OK")
        return

    curr_comp, curr_p50, curr_p25, curr_lcb, curr_n = metrics(
        current_scores, lcb_z, w_p50, w_p25, w_lcb
    )
    current = {
        "composite": curr_comp,
        "p50": curr_p50,
        "p25": curr_p25,
        "lcb": curr_lcb,
        "n": curr_n,
    }

    candidates = []
    for h, data in rs.items():
        if h == current_hash:
            continue
        scores = [int(x) for x in data.get("scores", [])]
        if len(scores) < min_games_candidates:
            continue
        comp, p50, p25, lcb, n = metrics(scores, lcb_z, w_p50, w_p25, w_lcb)
        m = {"composite": comp, "p50": p50, "p25": p25, "lcb": lcb, "n": n}
        candidates.append((comp, p50, p25, n, h, m))

    if not candidates:
        print("OK")
        return

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
    best_comp, _, _, best_n, best_hash, best = candidates[0]
    curr_comp = current["composite"]

    is_comp_regression = best_comp > 0 and curr_comp < best_comp * composite_ratio
    is_p25_regression = best["p25"] > 0 and current["p25"] < best["p25"] * p25_ratio

    if is_comp_regression and is_p25_regression:
        print(
            "REGRESSION:"
            f"best_hash={best_hash},best_comp={best_comp:.1f},curr_comp={curr_comp:.1f},"
            f"best_p25={best['p25']:.1f},curr_p25={current['p25']:.1f},"
            f"best_n={best_n},curr_n={current['n']}"
        )
    else:
        print("OK")


# ---- Subcommand: update_scores ----
def cmd_update_scores(args):
    rs_file = args[0]
    h = args[1]
    score = int(args[2])
    max_window = int(args[3])

    if os.path.exists(rs_file):
        with open(rs_file) as f:
            rs = json.load(f)
    else:
        rs = {}

    if h not in rs:
        rs[h] = {"scores": [], "prev_hash": "", "games_total": 0}
    if "games_total" not in rs[h]:
        rs[h]["games_total"] = len(rs[h].get("scores", []))
    rs[h]["scores"].append(score)
    rs[h]["games_total"] += 1
    rs[h]["scores"] = rs[h]["scores"][-max_window:]

    with open(rs_file, "w") as f:
        json.dump(rs, f)


# ---- Main dispatcher ----
def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <rank|prune|check|update_scores> [args...]", file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    rest = sys.argv[2:]

    if subcmd == "rank":
        cmd_rank(rest)
    elif subcmd == "prune":
        cmd_prune(rest)
    elif subcmd == "check":
        cmd_check(rest)
    elif subcmd == "update_scores":
        cmd_update_scores(rest)
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

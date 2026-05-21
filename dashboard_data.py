#!/usr/bin/env python3
"""Build compact JSON data for score_dashboard.html."""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


SCORE_HISTORY = Path("score_history.txt")
EVAL_SCORE_HISTORY = Path("eval_score_history.txt")
RUSSIA_HISTORY = Path("tmp/history/russia_creation_history.tsv")
GAME_HISTORY = Path("game_history")
GAME_COUNT = Path("game_count.txt")
DEFAULT_CHART_GAMES = 1200
STAGE_TYPES = [
    (11, "トルクメニスタン"),
    (13, "ウクライナ"),
    (14, "カザフスタン"),
    (15, "ロシア"),
]


def parse_chart_limit(raw: str | None) -> int:
    if not raw:
        return DEFAULT_CHART_GAMES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_CHART_GAMES
    return value if value >= 0 else DEFAULT_CHART_GAMES


def parse_score_history(path: Path) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return scores

    for line in lines:
        if not line:
            continue
        parts = line.split("\t")
        game = len(scores) + 1
        try:
            if len(parts) >= 2 and parts[1].strip():
                scores.append({"ts": parts[0], "game": game, "score": int(parts[1])})
            elif parts[0].isdigit():
                scores.append({"ts": None, "game": game, "score": int(parts[0])})
        except ValueError:
            continue
    return scores


def parse_russia_history(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return rows

    for line in lines:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        try:
            rows.append(
                {
                    "ts": parts[0],
                    "label": parts[1],
                    "game": int(parts[2]),
                    "score": int(parts[3]),
                    "turns": int(parts[4]),
                }
            )
        except ValueError:
            continue
    return rows


def game_summary(path: Path) -> dict[str, Any] | None:
    max_type = 0
    hashes: set[str] = set()
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                strategy_hash = row.get("strategy_hash")
                if strategy_hash:
                    hashes.add(str(strategy_hash))
                pieces = row.get("state_snapshot", {}).get("pieces", [])
                max_type = max(max_type, max((int(p.get("type", 0) or 0) for p in pieces), default=0))
    except OSError:
        return None
    if max_type <= 0:
        return None
    return {"file": path.name, "maxType": max_type, "hashes": sorted(hashes)}


def parse_stage_history(path: Path, limit: int = 100) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    files = sorted(path.glob("*score*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    rows: list[dict[str, Any]] = []
    for p in files[:limit]:
        row = game_summary(p)
        if row is not None:
            rows.append(row)
    return rows


def current_strategy_hash(path: Path = Path("tmp/state/current_strategy_run.json")) -> str | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    value = data.get("hash")
    return str(value) if value else None


def read_current_game(path: Path, fallback: int) -> int:
    try:
        current_game = int(path.read_text(encoding="utf-8").strip() or "0")
    except (FileNotFoundError, ValueError):
        current_game = fallback
    return current_game if current_game > 0 else fallback


def pct(part: int, total: int) -> float:
    return round(part / total * 100, 2) if total else 0


def avg(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0


def percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * p) - 1))
    return ordered[idx]


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def score_stats(scores: list[dict[str, Any]], current_game: int) -> dict[str, Any]:
    score_values = [int(d["score"]) for d in scores]
    unique_scores = sorted(set(score_values), reverse=True)
    best = unique_scores[0] if len(unique_scores) > 0 else 0
    best_entry = next((d for d in reversed(scores) if d["score"] == best), None)
    recent10 = scores[-10:]
    recent50 = scores[-50:]
    recent100 = scores[-100:]
    recent50_values = [int(d["score"]) for d in recent50]
    recent100_values = [int(d["score"]) for d in recent100]
    score3000 = len([v for v in score_values if v >= 3000])
    score2000 = len([v for v in score_values if v >= 2000])
    recent100_3000 = len([v for v in recent100_values if v >= 3000])
    recent100_2000 = len([v for v in recent100_values if v >= 2000])

    return {
        "count": len(scores),
        "currentGame": current_game,
        "best": best,
        "second": unique_scores[1] if len(unique_scores) > 1 else None,
        "third": unique_scores[2] if len(unique_scores) > 2 else None,
        "average": round(avg(score_values)) if score_values else 0,
        "recent10Average": round(avg([int(d["score"]) for d in recent10])) if recent10 else 0,
        "recent50Average": round(avg(recent50_values)) if recent50_values else 0,
        "recent50Best": max(recent50_values, default=0),
        "recent100Average": round(avg(recent100_values)) if recent100_values else 0,
        "recent100Best": max(recent100_values, default=0),
        "recent100Median": round(percentile(recent100_values, 0.5)) if recent100_values else 0,
        "recent100P90": round(percentile(recent100_values, 0.9)) if recent100_values else 0,
        "recent100Score3000": recent100_3000,
        "recent100Score3000Rate": pct(recent100_3000, len(recent100_values)),
        "recent100Score2000": recent100_2000,
        "recent100Score2000Rate": pct(recent100_2000, len(recent100_values)),
        "median": round(percentile(score_values, 0.5)) if score_values else 0,
        "p90": round(percentile(score_values, 0.9)) if score_values else 0,
        "score3000": score3000,
        "score3000Rate": pct(score3000, len(scores)),
        "score2000": score2000,
        "score2000Rate": pct(score2000, len(scores)),
        "bestGame": best_entry["game"] if best_entry else None,
        "bestTs": best_entry["ts"] if best_entry else None,
    }


def empty_score_stats(current_game: int) -> dict[str, Any]:
    return score_stats([], current_game)


def russia_stats(rows: list[dict[str, Any]], current_game: int) -> dict[str, Any]:
    now = datetime.now().astimezone()
    today = now.date()
    recent100_floor = max(0, current_game - 99)

    today_rows = [d for d in rows if (parse_ts(d["ts"]) or now).date() == today]
    recent_24h = [
        d
        for d in rows
        if (ts := parse_ts(d["ts"])) is not None and now - ts <= timedelta(hours=24)
    ]
    recent100 = [d for d in rows if d["game"] >= recent100_floor]
    scores = [int(d["score"]) for d in rows]
    turns = [int(d["turns"]) for d in rows]

    return {
        "count": len(rows),
        "rate": pct(len(rows), current_game),
        "recent100": len(recent100),
        "recent100Rate": pct(len(recent100), min(100, current_game)),
        "today": len(today_rows),
        "last24h": len(recent_24h),
        "last": rows[-1] if rows else None,
        "averageScore": round(avg(scores)) if scores else None,
        "bestScore": max(scores) if scores else None,
        "averageTurns": round(avg(turns)) if turns else None,
        "fastestTurns": min(turns) if turns else None,
    }


def stage_gate_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    stages = []
    focus = None
    for piece_type, name in STAGE_TYPES:
        reached = len([d for d in rows if int(d.get("maxType", 0)) >= piece_type])
        rate = pct(reached, total)
        item = {
            "type": piece_type,
            "name": name,
            "reached": reached,
            "total": total,
            "rate": rate,
        }
        stages.append(item)
        if total > 0 and focus is None and rate < 100:
            focus = item
    return {
        "window": total,
        "stages": stages,
        "focus": focus if total > 0 else None,
    }


def stage_gate_stats_for_hash(rows: list[dict[str, Any]], strategy_hash: str | None) -> dict[str, Any]:
    if not strategy_hash:
        stats = stage_gate_stats([])
        stats["hash"] = None
        return stats
    current_rows = [
        d
        for d in rows
        if d.get("hashes") == [strategy_hash]
    ]
    stats = stage_gate_stats(current_rows)
    stats["hash"] = strategy_hash
    return stats


def build_dashboard_data(chart_games: int) -> dict[str, Any]:
    scores = parse_score_history(SCORE_HISTORY)
    eval_scores = parse_score_history(EVAL_SCORE_HISTORY)
    russia = parse_russia_history(RUSSIA_HISTORY)
    stage_history = parse_stage_history(GAME_HISTORY)
    strategy_hash = current_strategy_hash()
    current_game = read_current_game(GAME_COUNT, len(scores))
    chart_scores = scores[-chart_games:] if chart_games > 0 else scores
    chart_eval_scores = eval_scores[-chart_games:] if chart_games > 0 else eval_scores

    return {
        "chartLimit": chart_games,
        "chartScores": chart_scores,
        "chartEvalScores": chart_eval_scores,
        "scoreStats": score_stats(scores, current_game),
        "evalScoreStats": score_stats(eval_scores, current_game) if eval_scores else empty_score_stats(current_game),
        "russiaStats": russia_stats(russia, current_game),
        "stageGateStats": stage_gate_stats(stage_history),
        "currentStageGateStats": stage_gate_stats_for_hash(stage_history, strategy_hash),
    }


def main() -> None:
    chart_games = parse_chart_limit(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(build_dashboard_data(chart_games), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()

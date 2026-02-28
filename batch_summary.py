#!/usr/bin/env python3
"""batch_summary.py - 複数ゲーム履歴JSONLからコンパクトなサマリーを生成

Usage: python3 batch_summary.py file1.jsonl file2.jsonl ...

出力:
- 全試合スコア一覧・統計 (min/max/avg/median/stddev)
- 各試合の decision_reason 分布と score_delta 相関
- 各試合の merge_available 率、max_y 推移
- 全試合横断のパターン分析（高スコア vs 低スコアの違い）
- ベスト/ワースト試合のファイル名
"""

import json
import math
import os
import re
import sys
from collections import Counter, defaultdict


def load_game(filepath):
    """JSONLファイルを読み込み、ターンのリストを返す"""
    turns = []
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if line:
                    turns.append(json.loads(line))
    except Exception as e:
        print(f"WARNING: {filepath} の読み込み失敗: {e}", file=sys.stderr)
    return turns


def calc_stats(values):
    """基本統計量を計算"""
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "median": 0, "stddev": 0, "count": 0}
    n = len(values)
    sorted_v = sorted(values)
    avg = sum(values) / n
    median = sorted_v[n // 2] if n % 2 == 1 else (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    variance = sum((x - avg) ** 2 for x in values) / n if n > 0 else 0
    return {
        "min": min(values),
        "max": max(values),
        "avg": round(avg, 1),
        "median": median,
        "stddev": round(math.sqrt(variance), 1),
        "count": n,
    }


def analyze_game(turns, filepath):
    """1試合の要約を生成"""
    if not turns:
        return None

    final = turns[-1]
    score = final.get("score", 0)
    num_turns = len(turns)

    # decision_reason 分布
    reason_counter = Counter()
    reason_score_delta = defaultdict(list)
    merge_count = 0
    max_y_values = []

    for t in turns:
        reason = t.get("decision_reason", "unknown")
        reason_counter[reason] += 1
        # score_delta の計算（JSONL内ではscore_delta=0の場合が多いので、前後差分で計算）
        reason_score_delta[reason].append(t.get("score_delta", 0))
        if t.get("merge_available", False):
            merge_count += 1
        max_y_values.append(t.get("max_y", -5.0))

    # スコア推移の計算（score_deltaが0の場合が多いので、直接スコアから計算）
    score_deltas_by_reason = defaultdict(list)
    for i in range(1, len(turns)):
        delta = turns[i].get("score", 0) - turns[i - 1].get("score", 0)
        reason = turns[i].get("decision_reason", "unknown")
        score_deltas_by_reason[reason].append(delta)

    merge_rate = round(merge_count / num_turns * 100, 1) if num_turns > 0 else 0

    # max_y の推移サマリー
    early_max_y = max_y_values[:num_turns // 3] if num_turns >= 3 else max_y_values
    late_max_y = max_y_values[-(num_turns // 3):] if num_turns >= 3 else max_y_values

    return {
        "file": os.path.basename(filepath),
        "score": score,
        "turns": num_turns,
        "reason_dist": dict(reason_counter.most_common()),
        "reason_avg_delta": {
            r: round(sum(ds) / len(ds), 1) if ds else 0
            for r, ds in score_deltas_by_reason.items()
        },
        "merge_rate": merge_rate,
        "early_avg_max_y": round(sum(early_max_y) / len(early_max_y), 2) if early_max_y else 0,
        "late_avg_max_y": round(sum(late_max_y) / len(late_max_y), 2) if late_max_y else 0,
        "soviet_created": any(t.get("soviet_created", False) for t in turns),
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 batch_summary.py file1.jsonl file2.jsonl ...", file=sys.stderr)
        sys.exit(1)

    files = sys.argv[1:]
    games = []
    all_turns_by_game = []

    for fp in files:
        turns = load_game(fp)
        if turns:
            summary = analyze_game(turns, fp)
            if summary:
                games.append(summary)
                all_turns_by_game.append((fp, turns))

    if not games:
        print("ERROR: 有効なゲームデータがありません", file=sys.stderr)
        sys.exit(1)

    scores = [g["score"] for g in games]
    stats = calc_stats(scores)
    turns_list = [g["turns"] for g in games]
    turns_stats = calc_stats(turns_list)

    # ベスト/ワースト特定
    sorted_games = sorted(games, key=lambda g: g["score"])
    worst_games = sorted_games[:2]
    best_games = sorted_games[-2:]

    # 全試合横断の decision_reason 集計
    all_reason_counter = Counter()
    all_reason_deltas = defaultdict(list)
    for g in games:
        for reason, count in g["reason_dist"].items():
            all_reason_counter[reason] += count
        for reason, avg_d in g["reason_avg_delta"].items():
            all_reason_deltas[reason].append(avg_d)

    # 高スコア vs 低スコアの比較
    mid = len(sorted_games) // 2
    low_half = sorted_games[:mid] if mid > 0 else sorted_games[:1]
    high_half = sorted_games[mid:] if mid > 0 else sorted_games[:1]

    low_merge_rate = sum(g["merge_rate"] for g in low_half) / len(low_half) if low_half else 0
    high_merge_rate = sum(g["merge_rate"] for g in high_half) / len(high_half) if high_half else 0

    low_reasons = Counter()
    high_reasons = Counter()
    for g in low_half:
        for r, c in g["reason_dist"].items():
            low_reasons[r] += c
    for g in high_half:
        for r, c in g["reason_dist"].items():
            high_reasons[r] += c

    # 出力
    print("=" * 60)
    print(f"  BATCH SUMMARY ({len(games)} games)")
    print("=" * 60)

    print(f"\n## スコア統計")
    print(f"  min={stats['min']}  max={stats['max']}  avg={stats['avg']}  median={stats['median']}  stddev={stats['stddev']}")

    print(f"\n## ターン数統計")
    print(f"  min={turns_stats['min']}  max={turns_stats['max']}  avg={turns_stats['avg']}  median={turns_stats['median']}")

    print(f"\n## 全試合スコア一覧")
    for g in sorted_games:
        soviet_mark = " [SOVIET!]" if g["soviet_created"] else ""
        print(f"  {g['file']}: score={g['score']}, turns={g['turns']}, merge_rate={g['merge_rate']}%{soviet_mark}")

    print(f"\n## decision_reason 全体分布 (上位10)")
    total_decisions = sum(all_reason_counter.values())
    for reason, count in all_reason_counter.most_common(10):
        pct = round(count / total_decisions * 100, 1)
        avg_delta = round(sum(all_reason_deltas[reason]) / len(all_reason_deltas[reason]), 1) if all_reason_deltas[reason] else 0
        print(f"  {reason}: {count}回 ({pct}%), avg_score_delta={avg_delta}")

    print(f"\n## 高スコア群 vs 低スコア群の比較")
    print(f"  高スコア群 (上位{len(high_half)}試合): avg_score={round(sum(g['score'] for g in high_half) / len(high_half))}, merge_rate={round(high_merge_rate, 1)}%")
    print(f"  低スコア群 (下位{len(low_half)}試合): avg_score={round(sum(g['score'] for g in low_half) / len(low_half))}, merge_rate={round(low_merge_rate, 1)}%")

    print(f"\n  高スコア群の reason 上位5:")
    high_total = sum(high_reasons.values())
    for reason, count in high_reasons.most_common(5):
        print(f"    {reason}: {round(count / high_total * 100, 1)}%")

    print(f"  低スコア群の reason 上位5:")
    low_total = sum(low_reasons.values())
    for reason, count in low_reasons.most_common(5):
        print(f"    {reason}: {round(count / low_total * 100, 1)}%")

    print(f"\n## max_y 推移 (盤面の高さ)")
    print(f"  高スコア群: 序盤avg={round(sum(g['early_avg_max_y'] for g in high_half) / len(high_half), 2)}, 終盤avg={round(sum(g['late_avg_max_y'] for g in high_half) / len(high_half), 2)}")
    print(f"  低スコア群: 序盤avg={round(sum(g['early_avg_max_y'] for g in low_half) / len(low_half), 2)}, 終盤avg={round(sum(g['late_avg_max_y'] for g in low_half) / len(low_half), 2)}")

    print(f"\n## ベスト試合")
    for g in reversed(best_games):
        print(f"  {g['file']}: score={g['score']}")

    print(f"\n## ワースト試合")
    for g in worst_games:
        print(f"  {g['file']}: score={g['score']}")

    # ベスト/ワーストファイル名をマーカー付きで出力（eloop.shが抽出用）
    print(f"\n===BEST_FILE==={best_games[-1]['file']}")
    print(f"===WORST_FILE==={worst_games[0]['file']}")


if __name__ == "__main__":
    main()

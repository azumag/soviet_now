#!/usr/bin/env python3
"""分析スクリプト: 振り子パターン検出と履歴データ分析"""

import json
import re
from collections import Counter


def analyze_game_history(filepath):
    """game_historyファイルを解析"""
    data = []
    with open(filepath) as f:
        for line in f:
            data.append(json.loads(line))

    # score_deltaを計算（履歴ファイルのscore_deltaが正しくない場合があるため再計算）
    for i in range(1, len(data)):
        data[i]["score_delta_calc"] = data[i]["score"] - data[i - 1]["score"]

    # 1. decision_reason 分布
    all_reasons = Counter()
    for turn in data:
        reason = turn.get("decision_reason", "")
        all_reasons[reason] += 1

    # 2. HIGHフェーズ（max_y >= 1.8）でのdecision_reason
    high_reasons = Counter()
    high_turns = []
    for turn in data:
        if turn.get("max_y", -999) >= 1.8:
            reason = turn.get("decision_reason", "")
            high_reasons[reason] += 1
            high_turns.append(turn)

    # 3. merge_available=true のターン分析
    merge_available_turns = [t for t in data if t.get("merge_available") == True]
    merge_score_deltas = [t.get("score_delta_calc", 0) for t in merge_available_turns]

    # 4. merge_available=false だがスコアが上がったターン
    merge_false_score_up = []
    for t in data:
        if t.get("merge_available") == False and t.get("score_delta_calc", 0) > 0:
            merge_false_score_up.append(t)

    # 5. スコアが大きく上がったターン（score_delta > 50）
    big_score_jumps = [t for t in data if t.get("score_delta_calc", 0) > 50]

    return {
        "total_turns": len(data),
        "final_score": data[-1].get("score", 0) if data else 0,
        "all_reasons": all_reasons,
        "high_reasons": high_reasons,
        "high_turns": high_turns,
        "merge_available_count": len(merge_available_turns),
        "merge_available_deltas": merge_score_deltas,
        "merge_available_turns": merge_available_turns,
        "merge_false_score_up": merge_false_score_up,
        "big_score_jumps": big_score_jumps,
    }


def main():
    # 1. strategy.pyの変更履歴から振り子パターンを抽出
    with open("/Users/azumag/work/sandbox/soren/strategy.py") as f:
        strategy_content = f.read()

    # 変更履歴セクションを行単位で処理
    history_lines = [
        line.strip()
        for line in strategy_content.split("\n")
        if line.strip().startswith("#")
        and any(
            v in line
            for v in [
                "v19",
                "v42",
                "v50",
                "v84",
                "v93",
                "v123",
                "v126",
                "v128",
                "v129",
                "v158",
                "v159",
                "v160",
            ]
        )
    ]

    print("\n" + "=" * 80)
    print("1. 振り子パターン検出 (strategy.py 変更履歴)")
    print("=" * 80)

    print("\n[パターン1] NO_MERGEペナルティの追加↔削除:")
    no_merge_lines = []
    for line in history_lines:
        if "NO_MERGE" in line:
            no_merge_lines.append(line)
    for p in no_merge_lines:
        print(f"  - {p[:150]}...")

    print("\n[パターン2] HIGH_TOWERペナルティの削除↔復帰:")
    high_tower_lines = []
    for line in history_lines:
        if "HIGH_TOWER" in line and (
            "削除" in line or "復帰" in line or "振り子" in line
        ):
            high_tower_lines.append(line)
    for p in high_tower_lines:
        print(f"  - {p[:150]}...")

    print("\n[パターン3] height_multの閾値シャッフル:")
    for line in history_lines:
        matches = re.findall(r"height_mult[=: ]*([0-9.]+)", line)
        if matches:
            for m in matches:
                print(f"  - height_mult={m} | {line[:120]}...")

    print("\n[パターン4] MEDIUMフェーズheight_multの振り子:")
    for line in history_lines:
        if "MEDIUM" in line and "height_mult" in line:
            print(f"  - {line[:120]}...")

    # 2. 最高スコア履歴の分析
    print("\n" + "=" * 80)
    print("2. 最高スコア履歴分析 (v128: 3689点)")
    print("=" * 80)

    analysis = analyze_game_history(
        "/Users/azumag/work/sandbox/soren/game_history/20260227_172800_score3689.jsonl"
    )

    print(f"\n総ターン数: {analysis['total_turns']}")
    print(f"最終スコア: {analysis['final_score']}")

    print(f"\ndecision_reason 分布 (全ターン, Top 20):")
    for reason, count in analysis["all_reasons"].most_common(20):
        print(f"  {reason}: {count}")

    print(f"\nHIGHフェーズ (max_y >= 1.8) decision_reason 分布:")
    print(f"  HIGHフェーズターン数: {len(analysis['high_turns'])}")
    for reason, count in analysis["high_reasons"].most_common(20):
        print(f"  {reason}: {count}")

    print(f"\nmerge_available=true のターン: {analysis['merge_available_count']} 回")
    if analysis["merge_available_deltas"]:
        print(
            f"  score_delta: 平均={sum(analysis['merge_available_deltas']) / len(analysis['merge_available_deltas']):.1f}, 最小={min(analysis['merge_available_deltas'])}, 最大={max(analysis['merge_available_deltas'])}"
        )

        print(f"\n  merge_available=true の各ターン詳細:")
        for t in analysis["merge_available_turns"]:
            print(
                f"    turn {t['turn']}: +{t['score_delta_calc']}pts, best_merge={t['best_merge_grade']}, max_y={t['max_y']:.2f}, reason={t['decision_reason']}"
            )

    print(
        f"\nmerge_available=false だがスコアが上がったターン: {len(analysis['merge_false_score_up'])} 回"
    )
    for t in analysis["merge_false_score_up"][:10]:  # 最初の10件
        print(
            f"  turn {t['turn']}: +{t['score_delta_calc']}pts, reason={t['decision_reason']}"
        )

    print(
        f"\nスコアが大きく上がったターン (score_delta > 50): {len(analysis['big_score_jumps'])} 回"
    )
    for t in analysis["big_score_jumps"]:
        print(
            f"  turn {t['turn']}: +{t['score_delta_calc']}pts, merge={t['merge_available']}, max_y={t['max_y']:.2f}, reason={t['decision_reason']}"
        )

    # 3. 最新のv160の失敗履歴分析
    print("\n" + "=" * 80)
    print("3. 最新v160失敗履歴分析 (593点)")
    print("=" * 80)

    analysis_latest = analyze_game_history(
        "/Users/azumag/work/sandbox/soren/game_history/20260227_234800_score0593.jsonl"
    )

    print(f"\n総ターン数: {analysis_latest['total_turns']}")
    print(f"最終スコア: {analysis_latest['final_score']}")

    print(f"\ndecision_reason 分布 (全ターン, Top 20):")
    for reason, count in analysis_latest["all_reasons"].most_common(20):
        print(f"  {reason}: {count}")

    print(f"\nHIGHフェーズ (max_y >= 1.8) decision_reason 分布:")
    print(f"  HIGHフェーズターン数: {len(analysis_latest['high_turns'])}")
    for reason, count in analysis_latest["high_reasons"].most_common(20):
        print(f"  {reason}: {count}")

    print(
        f"\nmerge_available=true のターン: {analysis_latest['merge_available_count']} 回"
    )
    if analysis_latest["merge_available_deltas"]:
        print(
            f"  score_delta: 平均={sum(analysis_latest['merge_available_deltas']) / len(analysis_latest['merge_available_deltas']):.1f}"
        )
        for t in analysis_latest["merge_available_turns"]:
            print(
                f"    turn {t['turn']}: +{t['score_delta_calc']}pts, best_merge={t['best_merge_grade']}, max_y={t['max_y']:.2f}, reason={t['decision_reason']}"
            )

    # 4. 結論
    print("\n" + "=" * 80)
    print("4. 結論")
    print("=" * 80)

    print("\n[振り子パターン]")
    print("  YES: 以下のパターンが確認されました")
    print("    - NO_MERGEペナルティ: v93-96で追加↔削除↔復活")
    print("    - HIGH_TOWERペナルティ: v157-159で削除↔復帰↔削除")
    print("    - MEDIUMフェーズheight_mult: v123-125で2.2→2.4→2.2→1.8")
    print("    - HIGHフェーズheight_mult: v158-159で1.8→1.6→1.4")

    print("\n[支配的なdecision_reason]")
    print("  v128成功時: HEIGHT_CONTROL(25), NEAR_MERGE_HIGH_LAYER(11), HIGH_TOWER(10)")
    print("  v160失敗時: HIGH_LAYER支配（大量のNEAR_PAIR付与）")

    print("\n[併合予測精度]")
    print(
        f"  v128: merge_available {analysis['merge_available_count']}回、スコア獲得は限定的"
    )
    print(
        f"  v160: merge_available {analysis_latest['merge_available_count']}回（57ターン中）"
    )

    print("\n[改善の優先順位]")
    print(
        "  1. HIGHフェーズで併合機会を確保（height_mult=1.8を維持し、HIGH_TOWERを1.3倍に固定）"
    )
    print("  2. 振り子パターン回避（v128の成功設定に固定）")
    print("  3. decision_reasonの簡素化（v159の複雑なNEAR_PAIR連鎖を回避）")


if __name__ == "__main__":
    main()

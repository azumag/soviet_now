#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# [BEST:604] v0: ランダム配置（ベースライン）
# [BEST:1486] v1: マージ重視戦略（DIRECT/NEAR優先、高度管理、ドリフト最小化）
# v2: 高度管理強化版 - SMALL_PIECE_GAP削除、段階的強化、左右バランス導入、reactorチェイン削除

import math


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ優先、高度管理強化、左右バランスで配置する."""

    # 全サンプルX座標の物理情報から最適位置を選択
    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    # 各X座標をスコアリング
    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面の最大高度
    max_y = (
        max([p["y"] for p in game_state.get("pieces", [])])
        if game_state.get("pieces")
        else -4.0
    )

    # 左右バランス計算
    pieces = game_state.get("pieces", [])
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（最重要）
        if merge_grade == "DIRECT":
            score += 1000.0
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 500.0
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 100.0
            reasons.append("FAR_MERGE")
        else:
            # マージなしはペナルティ（高い盤面では強化）
            no_merge_penalty = 200.0
            if max_y > 1.0:
                no_merge_penalty *= 2.0
            if max_y > 2.0:
                no_merge_penalty *= 2.0
            score -= no_merge_penalty

        # 2. 高度によるスコア（低いほど良い）- 段階的強化
        height_penalty = landing_y * 50.0

        # 高い盤面では高度ペナルティを段階的に強化
        if max_y > 1.0:
            height_penalty *= 2.0
            reasons.append("DANGER_TOWER")
        if max_y > 2.0:
            height_penalty *= 1.5  # max_y > 2.0ではさらに1.5倍（合計3倍）

        score -= height_penalty

        # 3. ドリフトによるペナルティ（小さいほど良い）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（ピースが多い側への配置をペナルティ）
        balance_penalty = x * balance_bias * 20.0
        score -= abs(balance_penalty)

        # スコア更新
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # 安全な範囲内にクリップ
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

    return {"x": best_x, "reason": best_reason}


# --- AI改変禁止ゾーン ---
if __name__ == "__main__":
    import json
    import sys

    # スタンドアロンテスト用
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # analyze_board から解析データ取得
    try:
        from analyze_board import analyze_drops, calc_reactor_state

        pieces = game_state.get("pieces", [])
        shapes = game_state.get("shapes", {})
        nxt = game_state.get("next", {})
        nt = nxt.get("type", 0)
        nr = nxt.get("r", 0.5)

        results, same_type = analyze_drops(pieces, nt, nr, shapes)
        reactor = calc_reactor_state(pieces)
        analysis = {
            "results": results,
            "same_type": [
                {"id": p["id"], "type": p["type"], "x": p["x"], "y": p["y"]}
                for p in same_type
            ],
            "reactor": reactor,
        }
    except Exception as e:
        analysis = {"results": [], "same_type": [], "reactor": {}, "error": str(e)}

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))

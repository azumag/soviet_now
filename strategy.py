#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v0: ランダム配置（ベースライン）
# v1: マージ重視戦略（DIRECT/NEAR優先、高度管理、ドリフト最小化）

import math


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ可能ならマージ優先、なければ高さ管理・ドリフト最小化で配置する."""

    # 全サンプルX座標の物理情報から最適位置を選択
    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    # 各X座標をスコアリング
    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    reactor_pairs_raw = analysis.get("reactor", {}).get("reactive_pairs", 0)
    reactor_pairs = (
        len(reactor_pairs_raw)
        if isinstance(reactor_pairs_raw, list)
        else reactor_pairs_raw
    )

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
            # マージなしはペナルティ（ただし他要素で補正可）
            score -= 200.0

        # 2. 高度によるスコア（低いほど良い）
        # 盤面の最大高度に応じて重みを変動
        max_y = (
            max([p["y"] for p in game_state.get("pieces", [])])
            if game_state.get("pieces")
            else -4.0
        )
        height_penalty = landing_y * 50.0

        # 高い盤面では高度ペナルティを強化
        if max_y > 2.0:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（小さいほど良い）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. reactor reactive_pairs が多い時は、近接配置をボーナス
        if reactor_pairs >= 4 and merge_grade in ("DIRECT", "NEAR"):
            score += 200.0
            reasons.append("REACTOR_CHAIN")

        # 5. 次のピースタイプを考慮（小さいピースは高い位置の隙間に配置可能）
        next_piece = game_state.get("next", {})
        next_type = next_piece.get("type", 0)
        next_radius = next_piece.get("r", 0.5)

        # 小さいピース（r < 0.6）は、高い位置の隙間に入りやすいので高度ペナルティを軽減
        if next_radius < 0.6 and landing_y > -1.0:
            score += 50.0
            reasons.append("SMALL_PIECE_GAP")

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

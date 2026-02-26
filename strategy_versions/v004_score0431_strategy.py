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
# v3: 重量バランス導入版 - ピースタイプに応じた重み付け、フェーズ制導入、高度管理調整
# v4: フェーズ制廃止・統合版 - 動的危険度係数、SMALL_GAP削除、カウントベースバランス復活

import math


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ一貫重視、動的危険度評価で配置する."""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # 左右バランス計算（カウントベースに戻す）
    left_count = sum(1 for p in pieces if p["x"] < 0)
    right_count = len(pieces) - left_count
    balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

    # 動的危険度係数（max_yに応じて滑らかに変化）
    # max_y < 0.5: 安全（係数1.0）
    # max_y = 1.0: 予備警告（係数1.5）
    # max_y = 2.0: 危険（係数2.0）
    # max_y = 3.0: 危機（係数3.0）
    danger_factor = 1.0 + max(0, (max_y + 0.5) ** 1.5) * 0.4

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_radius = next_piece.get("r", 0.5)
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（一貫して重視）
        # 危険な盤面でもマージ優先（フェーズ制廃止）
        if merge_grade == "DIRECT":
            score += 1200.0
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 150.0
            reasons.append("FAR_MERGE")
        else:
            # マージなしはペナルティ（危険度に応じて強化）
            no_merge_penalty = 150.0 * danger_factor
            score -= no_merge_penalty

        # 2. 高度によるスコア（危険度係数で動的に調整）
        # 着地Yがmax_yに近いほど危険（盤面を高くする）
        height_from_top = max_y - landing_y + 0.5  # max_yよりどれだけ低い位置か
        height_penalty = landing_y * 50.0 * danger_factor

        # max_yに近い位置への配置は追加ペナルティ
        if height_from_top < 1.0:
            height_penalty *= 1.5  # 盤面頂点付近なら1.5倍
            reasons.append("TOP_LAYER")
        elif height_from_top < 2.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 35.0
        score -= drift_penalty

        # 4. 左右バランス補正（危険度に応じて強化）
        balance_penalty = x * balance_bias * 25.0 * (1.0 + (danger_factor - 1.0) * 0.5)
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら、配置位置を中央寄せにしてチャンスを残す
        if next_next_type == next_type:
            # 中央寄せ（|x|が小さいほど良い）
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 35.0
            score += center_bonus
            reasons.append("NEXT_SAME")

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

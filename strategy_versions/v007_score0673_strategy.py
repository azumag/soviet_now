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
# [BEST:1615] v3: 重量バランス導入版 - ピースタイプに応じた重み付け、フェーズ制導入、高度管理調整
# v5: フェーズ制復活・簡素化版 - 動的危険度係数廃止、シンプル3フェーズ制、マージ高度バランス調整
# v6: 重量バランス復活・散在抑制版 - v3の重量計算復活、HIGHフェーズ閾値調整、タイプ別マージ優先、散在ピースペナルティ導入
# v7: 散在抑制削除・マージ強化版 - count_scattered_pieces()削除（ロジックエラー修正）、HIGHフェーズでマージ重視強化、シンプル化

# ピースタイプごとの重み（おおよその面積や重さを反映）
PIECE_WEIGHTS = {
    1: 1.0,  # 小さい六角形
    2: 1.2,  # 小さい五角形
    3: 1.3,  # 中くらいの星形
    4: 1.5,  # 中くらいの五角形
    5: 1.4,  # 中くらいの六角形
    6: 1.8,  # 大きい六角形
    7: 2.0,  # 大きい多角形
    8: 2.5,  # 大きい星形
    9: 2.8,  # かなり大きい星形
    10: 2.2,  # 大きい五角形
    11: 3.5,  # 大きな多角形（最重要！）
    12: 2.7,  # 大きな星形
    13: 4.0,  # 最大の多角形
}

# タイプごとのマージ重要度（大きいほど重要）
MERGE_IMPORTANCE = {
    1: 1.0,
    2: 1.0,
    3: 1.0,
    4: 1.0,
    5: 1.0,
    6: 1.2,
    7: 1.2,
    8: 1.2,
    9: 1.2,
    10: 1.2,
    11: 2.5,  # 重要！散在を防ぐべき
    12: 1.5,
    13: 1.5,
    14: 2.0,
}


def calc_weight_balance(pieces: list) -> float:
    """左右の重量バランスを計算する（-1.0〜1.0）"""
    left_weight = 0.0
    right_weight = 0.0

    for p in pieces:
        weight = PIECE_WEIGHTS.get(p.get("type", 1), 1.0)
        if p["x"] < 0:
            left_weight += weight
        else:
            right_weight += weight

    total_weight = left_weight + right_weight
    if total_weight == 0:
        return 0.0

    return (right_weight - left_weight) / total_weight


def get_phase(max_y: float) -> str:
    """盤面フェーズを判定する"""
    if max_y < 0.8:
        return "LOW"  # 低盤面: マージ重視
    elif max_y < 1.8:
        return "MEDIUM"  # 中盤: マージ+高度管理
    else:
        return "HIGH"  # 高盤面: 高度管理重視


def decide(game_state: dict, analysis: dict) -> dict:
    """重量バランス、フェーズ制、マージ優先で配置する."""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # 重量バランス計算（v3から維持）
    balance_bias = calc_weight_balance(pieces)

    # フェーズ判定（HIGH閾値0.8を維持）
    phase = get_phase(max_y)

    # 次と次の次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # 次のピースのマージ重要度
    merge_importance = MERGE_IMPORTANCE.get(next_type, 1.0)

    # フェーズに応じた重み設定（v6から調整）
    if phase == "LOW":
        merge_weight = 1.2
        height_weight = 0.8
        no_merge_base = 150.0
        balance_strength = 18.0
    elif phase == "MEDIUM":
        merge_weight = 1.0
        height_weight = 1.2
        no_merge_base = 250.0
        balance_strength = 22.0
    else:  # HIGH
        merge_weight = 1.1  # v6: 0.9→1.1に強化（マージ優先）
        height_weight = 1.3  # v6: 1.5→1.3に緩和（高度抑制緩和）
        no_merge_base = 300.0  # v6: 350.0→300.0に緩和
        balance_strength = 26.0  # v6: 28.0→26.0に緩和

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)
        merge_target_type = result.get("merge_target_type", None)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（タイプ重要度で補正）
        if merge_grade == "DIRECT":
            score += 1100.0 * merge_weight * merge_importance
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_weight * merge_importance
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 150.0 * merge_weight * merge_importance
            reasons.append("FAR_MERGE")
        else:
            # マージなしはペナルティ
            score -= no_merge_base

        # 2. 高度によるスコア（フェーズに応じて重み付け）
        height_penalty = landing_y * 50.0 * height_weight

        if phase == "HIGH":
            height_penalty *= 1.5
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.2
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 重量バランス補正
        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら、中央寄せでチャンスを残す
        if next_next_type == next_type:
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

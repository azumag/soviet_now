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
    11: 3.5,  # 大きな多角形
    12: 2.7,  # 大きな星形
    13: 4.0,  # 最大の多角形
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


def get_phase(max_y: float, piece_count: int) -> str:
    """盤面フェーズを判定する"""
    if max_y < 1.0:
        return "LOW"  # 低盤面: マージ重視
    elif max_y < 2.0:
        return "MEDIUM"  # 中盤: マージ+高度管理
    else:
        return "HIGH"  # 高盤面: 高度管理重視


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ優先、重量バランス考慮、フェーズ制で配置する."""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # 重量バランス計算
    balance_bias = calc_weight_balance(pieces)

    # フェーズ判定
    phase = get_phase(max_y, len(pieces))

    # 次と次の次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_radius = next_piece.get("r", 0.5)
    next_next_type = next_next_piece.get("type", 0)

    # フェーズに応じた重み設定
    if phase == "LOW":
        merge_weight = 1.0
        height_weight = 0.8
        no_merge_base = 100.0
    elif phase == "MEDIUM":
        merge_weight = 0.7
        height_weight = 1.0
        no_merge_base = 200.0
    else:  # HIGH
        merge_weight = 0.3
        height_weight = 1.5
        no_merge_base = 300.0

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")
        has_merge = result.get("has_merge", False)

        score = 0.0
        reasons = []

        # 1. マージグレードによるスコア（フェーズに応じて重み付け）
        if merge_grade == "DIRECT":
            score += 1000.0 * merge_weight
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 500.0 * merge_weight
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 100.0 * merge_weight
            reasons.append("FAR_MERGE")
        else:
            # マージなしはペナルティ
            no_merge_penalty = no_merge_base
            if phase == "HIGH":
                no_merge_penalty *= 1.5
            score -= no_merge_penalty

        # 2. 高度によるスコア（フェーズに応じて重み付け）
        height_penalty = landing_y * 50.0 * height_weight

        # 高盤面での追加ペナルティ
        if phase == "HIGH":
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.2
            reasons.append("MEDIUM_TOWER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 重量バランス補正（フェーズに応じて強度を調整）
        balance_strength = 15.0
        if phase == "HIGH":
            balance_strength = 30.0  # 高盤面ではバランス重視
        elif phase == "MEDIUM":
            balance_strength = 20.0

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. 次のピースとnextNextの相性を考慮
        # 小さいピースは高い位置の隙間に入りやすい
        if next_radius < 0.6 and landing_y > -1.0:
            score += 40.0
            reasons.append("SMALL_GAP")

        # nextNextが同じタイプなら、配置位置を中央寄せにしてチャンスを残す
        if next_next_type == next_type:
            # 中央寄せ（|x|が小さいほど良い）
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 30.0
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

#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v542: v540/v128-based simplified version)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v542: v540/v128単純化版 - v422の複雑さを除去し、v540(2176点)とv128の成功構造に戻る。batch_summary.txtでv540が最高点2176を達成し、v128-like構造は平均1170.8点を出したことが確認された。v422の複雑なロジック（先読みマージボーナス支配62.7%、チェーン予測、reactor情報）は実際のマージ率を低下させていた。
#   根本原因の特定:
#   - v422のFUTURE_MERGEが62.7%を占めるが、実際のマージ(DIRECT/NEAR/FAR)は4.5%しかない
#   - 先読みボーナスが支配的すぎて、実際のマージ機会を見逃している
#   - v422のチェーン予測、reactor情報(near_pairs、reactive_pairs)はオーバーヘッド
#   - v422のmerge_bonus: DIRECT 2500/NEAR 1500/FAR 800 は大きすぎて判断を歪める
#   改善策(v540/v128単純化):
#   - マージボーナスをv128レベルに戻す: DIRECT 1200/NEAR 600/FAR 200（実用的なバランス）
#   - 先読みマージボーナスとチェーン予測を削除（実際のマージに集中）
#   - reactor情報(near_pairs、reactive_pairs)を削除（単純化）
#   - フェーズ判定と高度ペナルティを維持（v128の成功要素）
#   - ドリフトペナルティ、バランス補正、中央寄せボーナスを維持（基本制御）
#   核心的発見: v540とv128の単純構造が最高点2176と平均1170.8点を出した。複雑さを除去し、実際のマージに集中することでスコア向上。
#   成功基準: scoreがv540の2176に近づく、または平均がv422を上回る
#   失敗基準: scoreがv422以下、または実際のマージ率が5%以下


def decide(game_state: dict, analysis: dict) -> dict:
    """v542: v540/v128-based simplified strategy"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v542: v128の設定を採用）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v128の設定
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v128の設定
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし
        merge_mult = 0.6

    # nextNextピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v542: v540/v128単純化版 ===

        # 1. マージグレードによるスコア（v542: v128の実用的値を採用）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult  # v128: DIRECT 1200
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult  # v128: NEAR 600
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult  # v128: FAR 200
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v542: v128の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # MEDIUM/HIGHフェーズのタワー判定（v542: v128の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v128: HIGH_TOWER 1.3倍
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v128: MEDIUM_TOWER 1.5倍
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v542: v128の設定を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 20.0  # v128: 20.0
        score -= drift_penalty

        # 4. 左右バランス補正（v542: v128の設定を維持）
        balance_strength = 10.0
        if phase == "HIGH":
            balance_strength = 40.0  # v128: HIGHフェーズで強化
        elif phase == "MEDIUM":
            balance_strength = 20.0  # v128: MEDIUMフェーズで中程度

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v542: v128の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
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
            "merge_history": [],
        }
    except Exception as e:
        analysis = {
            "results": [],
            "same_type": [],
            "reactor": {},
            "merge_history": [],
            "error": str(e),
        }

    result = decide(game_state, analysis)
    print(json.dumps(result, ensure_ascii=False, indent=2))

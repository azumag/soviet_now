#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v338: マージ品質応答・高度管理調整強化版 - v337のバッチ分析から高スコア群（avg=1164）と低スコア群（avg=502）の比較で、NEAR_MERGEやDIRECT_MERGEのボーナスが低いことが原因を特定。v337のNEAR=600/DIRECT=1200が不十分で、HIGHフェーズでNEAR_MERGEを優先するためボーナスを強化（NEAR=800/DIRECT=1500）。
#   v337バッチ分析から特定した問題:
#   - avg=899.0, stddev=360.8: スコアのばらつきが大きい
#   - ベストゲーム（score=1398, turns=86）: NEAR_MERGEやDIRECT_MERGEが多く、マージに成功
#   - ワーストゲーム（score=492, turns=60）: HEIGHT_CONTROLが支配的で、マージ機会を損失
#   - 高スコア群（上位3試合）: NEAR_MERGE割合が高い、ターン数が長い
#   - 低スコア群（下位2試合）: HEIGHT_CONTROLが支配的、マージ機会を損失
#   - decision_reason分布: HEIGHT_CONTROLが17.1%、NEAR_PAIRが13.3%、NEAR_MERGEが4.6%
#   - ベストゲームのTURN 54でNEAR_MERGE発生し、スコア936に急増
#   根本原因:
#   - v337のNEAR_MERGEボーナス600.0/DIRECT_MERGEボーナス1200.0が不十分
#   - HIGHフェーズでNEAR_MERGEやDIRECT_MERGEの優先度が低い
#   - HIGH_TOWERペナルティが強すぎて、NEAR_MERGEを優先できない
#   解決策（マージ品質応答・高度管理調整強化）:
#   - NEAR_MERGEボーナス強化: 600.0 → 800.0（v338: マージ品質向上）
#   - DIRECT_MERGEボーナス強化: 1200.0 → 1500.0（v338: マージ品質向上）
#   - FAR_MERGEボーナス強化: 200.0 → 300.0（v338: マージ品質向上）
#   - HIGHフェーズのHIGH_TOWERペナルティ緩和: 1.3倍 → 1.1倍（v338: 高度管理緩和）
#   - MEDIUMフェーズのHIGH_TOWERペナルティ緩和: 1.5倍 → 1.3倍（v338: 高度管理緩和）
#   - CRITICALフェーズのHIGH_TOWERペナルティ緩和: 1.5倍 → 1.3倍（v338: 高度管理緩和）
#   - reactive_pairsボーナス強化: 50.0 → 60.0（v338: 盤面活発化ボーナス強化）
#   - near_pairsボーナス強化: 30.0 → 40.0（v338: マージ機会ボーナス強化）
#   核心的発見: ベストゲームのNEAR_MERGE発生時のスコア急増（TURN 54で936）から、NEAR_MERGEやDIRECT_MERGEのボーナスを強化し、HIGHフェーズで高度管理を緩和することが重要。v337のマージ品質応答・高度管理調整をベースに、ボーナスを強化してマージの質を向上させる。
#   成功基準: avg_scoreがv337の899.0以上、またはmerge_rateが15%以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv335の507.3未満、またはmerge_rate为10%未満、またはavg_scoreがv337の899.0未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ品質応答・高度管理調整強化版。NEAR_MERGEやDIRECT_MERGEのボーナスを強化し、HIGHフェーズで高度管理を緩和。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v338: 静的なボーナスとして活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs_val = reactor.get("reactive_pairs", 0)
    reactive_pairs = (
        len(reactive_pairs_val)
        if isinstance(reactive_pairs_val, list)
        else reactive_pairs_val
    )
    near_pairs_val = reactor.get("near_pairs", 0)
    near_pairs = (
        len(near_pairs_val) if isinstance(near_pairs_val, list) else near_pairs_val
    )

    # フェーズ判定（v338: v128の閾値0.8/1.8/3.0を採用、動的切り替えなし）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v338: v128: v42の2.4を維持
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v338: v128の1.8を維持、動的切り替えなし
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0
        merge_mult = 0.6

    # 次のピース情報
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

        # === v338: マージ品質応答・高度管理調整強化 ===

        # 1. マージグレードによるスコア（v338: ボーナス強化）
        merge_bonus = 0.0
        if merge_grade == "DIRECT":
            merge_bonus = 1500.0 * merge_mult  # v338: 1200.0 → 1500.0に強化
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            merge_bonus = 800.0 * merge_mult  # v338: 600.0 → 800.0に強化
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            merge_bonus = 300.0 * merge_mult  # v338: 200.0 → 300.0に強化
            reasons.append("FAR_MERGE")

        # nextNextが同じタイプならボーナス係数（v338: v128の値を維持）
        if next_next_type == next_type:
            merge_bonus *= 1.2
            reasons.append("NEXT_SAME")

        score += merge_bonus

        # 2. 高度によるペナルティ（v338: 高度管理緩和）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v338: 高度管理緩和）
        if phase == "HIGH" and landing_y > 0.5:
            # v338: 高度管理緩和（v337の1.3倍 → 1.1倍）
            height_penalty *= 1.1
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            # v338: 高度管理緩和（v337の1.5倍 → 1.3倍）
            height_penalty *= 1.3
            reasons.append("MEDIUM_TOWER")
        elif phase == "CRITICAL" and landing_y > 0.5:
            # v338: 高度管理緩和
            height_penalty *= 1.3
            reasons.append("CRITICAL_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v338: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v338: v128の値を維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 40.0
        elif phase == "MEDIUM":
            balance_strength = 30.0

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v338: v128の値を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 80.0  # v337: 50.0 → 80.0に強化
            score += center_bonus
            if "CENTER" not in reasons:
                reasons.append("CENTER")

        # 6. reactor情報活用ボーナス（v338: ボーナス強化）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 60.0  # v338: 50.0 → 60.0に強化
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 30.0  # v338: 30.0を維持

        # near_pairsが多いほど、マージ機会が多い
        if near_pairs >= 2:
            score += 40.0  # v338: 30.0 → 40.0に強化
            reasons.append("NEAR_PAIR")

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

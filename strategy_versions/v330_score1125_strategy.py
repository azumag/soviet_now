#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v323: マージ優先・高度緩和版 - v322の失敗（avg=1103.0、HEIGHT_CONTROL支配24.1%）を受けて、v321の成功要素を再評価・改善。
#   v322バッチ分析から特定した問題:
#   - マージボーナス過剰削減: DIRECT_MERGEがわずか6回（2.1%）しか発生、avg_score_delta=0.8と効果なし
#   - HEIGHT_CONTROL支配・非効率: 69回（24.1%）でavg_score_delta=12.6と低い。v321の「高度ペナルティ完全廃止」を捨てて失敗
#   - バランス補正一律化の喪失: v42の段階的設定を復活したが、v321の簡素化の成功要因を失った
#   - スコア大幅悪化: avg=1103.0（v321の1618.2より大幅低下）、stddev=426.6と不安定
#   - マージ率低い: 高スコア群でもmerge_rate=14.7%と低い
#   v321成功要素の再評価:
#   - 高度ペナルティ完全廃止が「振動戦略」に親和性が高く成功
#   - マージボーナス強化（DIRECT=3000）でマージを圧倒的に優先
#   - バランス補正一律化で簡素化・頑健性確保
#   解決策（マージ優先・高度緩和）:
#   - マージボーナス微調整: v321の過剰値から少し引き下げ（DIRECT:3000→2500、NEAR:1000→800、FAR:300→250）
#   - 高度ペナルティ緩和: 完全廃止は維持、CRITICALフェーズで軽度ペナルティ導入（height_mult=0.5）
#   - バランス補正動的化: MEDIUMフェーズで強化（30.0）、HIGHで維持（20.0）
#   - HIGH_TOWERペナルティ緩和: マージ優先のため大幅緩和（HIGH:1.2倍、MEDIUM:1.3倍）
#   - merge_mult導入: HIGHフェーズでマージさらに優先（merge_mult=1.2）
#   成功基準: avg_scoreがv321の1618.2以上、またはマージ決定率が20%以上
#   失敗基準: avg_scoreがv321の1618.2未満、または盤面崩壊で即敗北率が30%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """マージ優先・高度緩和版。v321の成功要素を維持しつつ、バランス補正とCRITICALフェーズ管理で改善。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v323: v321の完全廃止を基本に、CRITICALで軽度ペナルティ導入）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.0  # v323: v321通り完全廃止
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 0.0  # v323: v321通り完全廃止
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 0.0  # v323: v321通り完全廃止
        merge_mult = 1.2  # v323: HIGHフェーズでマージ優先
    else:
        phase = "CRITICAL"
        height_mult = 0.5  # v323: 新規導入、盤面崩壊防止
        merge_mult = 1.0

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

        # === v323: マージ優先・高度緩和 ===

        # 1. マージグレードによるスコア（v323: v321の微調整版）
        if merge_grade == "DIRECT":
            score += 2500.0 * merge_mult  # v323: v321の3000から2500に微減
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 800.0 * merge_mult  # v323: v321の1000から800に微減
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 250.0 * merge_mult  # v323: v321の300から250に微減
            reasons.append("FAR_MERGE")
        # v323: マージボーナスをv321の85%程度に維持、圧倒的優先を維持

        # 2. 高度によるペナルティ（v323: v321の完全廃止を基本に、CRITICALで軽度導入）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v323: 大幅緩和、マージ優先）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.2  # v323: v321から導入、v128の1.3倍より緩和
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.3  # v323: v321から導入、v128の1.5倍より緩和
            reasons.append("MEDIUM_TOWER")
        elif phase == "CRITICAL" and landing_y > 0.5:
            height_penalty *= 1.1  # v323: 新規導入、軽度ペナルティ
            reasons.append("CRITICAL_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v323: v321の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v323: MEDIUMフェーズ強化・HIGH維持）
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 20.0  # v323: v321通り維持、簡素化優先
        elif phase == "MEDIUM":
            balance_strength = 30.0  # v323: MEDIUMフェーズ強化
        elif phase == "CRITICAL":
            balance_strength = 25.0  # v323: 新規導入

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v323: v321の50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新（v323: v321のreason生成を維持）
        if score > best_score:
            best_score = score
            best_x = x
            best_reason = "_".join(reasons) if reasons else "PRECISION_CONTROL"

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

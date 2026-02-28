#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v335: 動的フェーズ切り替え・マージ機会最大化版 - v334の失敗（ターン数16、merge_rate=12.5%が低すぎ）を受けて、動的フェーズ切り替えを導入し、マージ機会を最大化。
#   v334バッチ分析から特定した問題:
#   - ターン数16ターン: 平均16ターンで終了し、早期にゲームオーバー
#   - merge_rate 12.5%: 目標15%に届かず、マージ機会を損失
#   - decision_reasonの偏り: HIGH_TOWER(31.2%)が支配的で、高度管理が強すぎる
#   - NEAR_MERGE予測の精度: NEAR_MERGEが25%の決定理由だが、実際のマージは2/16=12.5%のみ
#   - 振り子パターンの再発: v334はv42のheight_mult=2.6を採用したが、v128のheight_mult=1.8が成功
#   根本原因:
#   - v334はv42のパラメータをコピーしたが、v42の安定性とv128のマージ機会最大化の両立に失敗
#   - phase判定が静的: max_yが1.8以上なら常にHIGHフェーズ、merge_available=falseでもHIGHを維持
#   - マージ機会が少ない時の対応不十分: near_pairs=0でheight_penaltyを強化するが、HIGHフェーズではheight_mult=2.6が強すぎ
#   - マージ予測精度問題: NEAR_MERGEの予測が多いが、実際のマージ発生が少ない
#   - reactor情報活用が表面的: near_pairsをマージ機会判定に使っているが、精度が低い
#   解決策（動的フェーズ切り替え・マージ機会最大化）:
#   - 動的フェーズ切り替え: merge_availableに応じてHIGHフェーズのheight_multを動的に変更
#     * merge_available=true: height_mult=1.8（v128の成功設定、高度管理緩和・マージ優先）
#     * merge_available=false: height_mult=2.6（v42の安定設定、高度管理強化・崩壊防止）
#   - HIGH_TOWERペナルティの動的化: merge_availableに応じて乗数を調整
#     * merge_available=true: 1.3倍（v84の緩和設定、マージ機会最大化）
#     * merge_available=false: 2.0倍（v42の厳しい設定、高度管理強化）
#   - merge_multの動的化: merge_availableに応じてマージボーナスを調整
#     * merge_available=true: merge_mult=1.5（マージ優先を強化）
#     * merge_available=false: merge_mult=0.8（高度管理優先）
#   - reactor情報活用の強化: near_pairsボーナス100.0→150.0、reactive_pairsボーナス50.0→80.0
#   核心的発見: 単にheight_multを1.8に戻すだけでは振り子パターン。マージ機会がある時だけHIGHフェーズのheight_multを緩和する動的戦略が必要。v42の安定性とv128のマージ機会最大化を動的に切り替えることで、両方の良い面を取り入れる。
#   成功基準: avg_turnsが30以上、またはmerge_rateが15%以上、またはavg_scoreがv334の1681以上
#   失敗基準: avg_turnsが10未満、またはmerge_rateが10%未満、またはavg_scoreがv334の1681未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """動的フェーズ切り替え・マージ機会最大化版。マージ機会に応じてHIGHフェーズのheight_multを動的に調整し、マージ機会を最大化する。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # reactor情報（v335: 中核化・活用強化）
    reactor = analysis.get("reactor", {})
    # reactive_pairsとnear_pairsはリストの場合、その長さを取得
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

    # フェーズ判定（v335: v42/v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42: 安定値
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        # v335: 動的height_mult（マージ機会に応じて切り替え）
        # マージ機会がある時はv128の1.8（緩和）、ない時はv42の2.6（安定）
        height_mult = 2.6  # デフォルトはv42の安定値
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

    # マージ機会判定（v335: 中核化・活用強化）
    # near_pairsが2以上あればマージ機会あり、merge_multを強化
    merge_available = near_pairs >= 2

    # v335: 動的フェーズ切り替え（マージ機会に応じてHIGHフェーズのパラメータを調整）
    if phase == "HIGH":
        if merge_available:
            # マージ機会がある時: v128の設定（高度管理緩和・マージ優先）
            height_mult = 1.8  # v128: 緩和設定
            merge_mult = 1.5  # v335: マージ優先を強化
        else:
            # マージ機会がない時: v42の設定（高度管理強化・崩壊防止）
            height_mult = 2.6  # v42: 安定設定
            merge_mult = 0.8  # v335: 高度管理優先
    else:
        # HIGHフェーズ以外ではmerge_availableによる調整をしない
        if merge_available:
            merge_mult *= 1.2
        else:
            merge_mult *= 0.9

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v335: 動的フェーズ切り替え・マージ機会最大化 ===

        # 1. マージグレードによるスコア（v335: 動的merge_mult適用）
        merge_bonus = 0.0
        if merge_grade == "DIRECT":
            merge_bonus = 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            merge_bonus = 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            merge_bonus = 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # nextNextが同じタイプならボーナス係数（v335: 動的調整）
        if next_next_type == next_type:
            merge_bonus *= 1.2
            reasons.append("NEXT_SAME")

        score += merge_bonus

        # 2. 高度によるペナルティ（v335: 動的height_mult適用）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v335: 動的乗数適用）
        if phase == "HIGH" and landing_y > 0.5:
            if merge_available:
                height_penalty *= 1.3  # v84: 緩和設定（マージ機会最大化）
            else:
                height_penalty *= 2.0  # v42: 厳しい設定（高度管理強化）
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v42: 1.5倍
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v335: v42の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v335: v42の安定設定を採用）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v335: v42の一律50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("CENTER")

        # 6. reactor情報活用ボーナス（v335: 中核化、活用強化）
        # reactive_pairsが多いほど、盤面が活発でマージが起きやすい
        if reactive_pairs >= 3:
            score += 80.0  # v335: ボーナス強化（50.0→80.0）
            reasons.append("REACTIVE")
        elif reactive_pairs >= 1:
            score += 30.0  # v335: ボーナス強化（20.0→30.0）

        # near_pairsが多いほど、マージ機会が多い（v335: 中核化、活用強化）
        if near_pairs >= 2:
            score += 150.0  # v335: ボーナス強化（100.0→150.0）
            reasons.append("NEAR_PAIR")
        elif near_pairs == 1:
            score += 40.0  # v335: ボーナス強化（30.0→40.0）
            reasons.append("NEAR_PAIR")

        # マージ機会が少ないなら高度管理優先（v335: reactor中核化）
        if near_pairs == 0 and landing_y < 0.0:
            # マージ機会がないなら、盤面を下げる（高度管理優先）
            score -= abs(landing_y) * 20.0
            reasons.append("HEIGHT_CONTROL")

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

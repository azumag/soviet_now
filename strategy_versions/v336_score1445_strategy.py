#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v324: フェーズ感応的マージ強化版 - v323の改善方向（マージ優先・高度緩和）は正しいが、強度が不足（avg=1430.0、merge_rate=14.6%）。
#   v323バッチ分析から特定した問題:
#   - マージボーナス不足: v321の85%（DIRECT=2500）にすぎず、HIGHフェーズでのマージ優先が不十分
#   - PRECISION_CONTROL支配: 17.3%で支配的だがavg_score_delta=12.6と低い。精度に固執しすぎ
#   - HIGH_LAYER支配: 18.5%で支配的。高度管理がまだ強すぎる
#   - マージ率低い: 高スコア群でも18.7%で、v321の「振動戦略」には遠く及ばない
#   根本原因:
#   - v323はv321とv322の「中間」を目指したが、両方の弱点を引き継いだ
#   - v321の3000は過剰だったが、HIGHフェーズでの圧倒的優先が必要
#   - MEDIUMフェーズでの高度管理は必要だが、HIGHフェーズでは廃止すべき
#   解決策（フェーズ感応的マージ強化）:
#   - マージボーナスフェーズ感応化: MEDIUMまではv322（DIRECT=1200）、HIGHでv321（DIRECT=3000）。HIGHフェーズでのみ圧倒的優先
#   - HIGHフェーズ高度管理完全廃止: max_y<3.0ではheight_mult=0.0。HIGH_TOWERペナルティも廃止
#   - バランス補正単純化: 全フェーズ一律15.0にし、PRECISION_CONTROLを抑制
#   - MEDIUMフェーズ高度管理維持: v322の設定（height_mult=2.4）を採用し、HIGH到達遅延
#   - CRITICALフェーズ高度管理緩和: v323のheight_mult=0.5を維持し、即時崩壊防止
#   - nextNext中央寄せ維持: v321の50.0ボーナスは有効なので維持
#   成功基準: avg_scoreが1500以上、またはマージ決定率が20%以上
#   失敗基準: avg_scoreがv323の1430.0未満、または盤面崩壊で即敗北率が25%以上
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版


def decide(game_state: dict, analysis: dict) -> dict:
    """フェーズ感応的マージ強化版。MEDIUMまでは高度管理優先、HIGHではマージ圧倒的優先。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v324: HIGHフェーズでのみ高度管理廃止）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.0
        merge_mult = 1.0
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v324: v322の2.4を維持、HIGH到達遅延
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 0.0  # v324: 完全廃止、HIGHフェーズでのみ高度管理なし
        merge_mult = 2.5  # v324: v321並みに強化、圧倒的優先
    else:
        phase = "CRITICAL"
        height_mult = 0.5  # v324: v323の0.5を維持、即時崩壊防止
        merge_mult = 2.5

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

        # === v324: フェーズ感応的マージ強化 ===

        # 1. マージグレードによるスコア（v324: フェーズ感応化）
        if merge_grade == "DIRECT":
            # v324: MEDIUMまではv322、HIGHでv321
            if phase == "HIGH" or phase == "CRITICAL":
                score += 3000.0 * merge_mult  # v321並み
            else:
                score += 1200.0 * merge_mult  # v322並み
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            if phase == "HIGH" or phase == "CRITICAL":
                score += 1000.0 * merge_mult  # v321並み
            else:
                score += 600.0 * merge_mult  # v322並み
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            if phase == "HIGH" or phase == "CRITICAL":
                score += 300.0 * merge_mult  # v321並み
            else:
                score += 200.0 * merge_mult  # v322並み
            reasons.append("FAR_MERGE")
        # v324: HIGHフェーズでのみマージボーナス大幅強化

        # 2. 高度によるペナルティ（v324: HIGHフェーズ完全廃止）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v324: HIGHフェーズでは廃止）
        if phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5  # v324: v322の1.5倍を維持
            reasons.append("MEDIUM_TOWER")
        elif phase == "CRITICAL" and landing_y > 0.5:
            height_penalty *= 1.1  # v324: v323の1.1倍を維持
            reasons.append("CRITICAL_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v324: 一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v324: 全フェーズ一律15.0に単純化）
        balance_strength = 15.0  # v324: 単純化、PRECISION_CONTROL抑制

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # 5. nextNextが同じタイプなら中央寄せボーナス（v324: v321の50.0を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # スコア更新
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

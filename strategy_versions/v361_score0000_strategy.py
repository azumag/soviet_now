#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v332: v128完全復帰版 - v331の失敗（batch_summary.txtが空だが、v331の変更を逆転）を受けて、v128のシンプルで頑健な構造に戻す。HIGHフェーズでmerge_mult=1.5を削除しv128の1.0に戻す。バランス補正をv128の値（HIGH=40.0, MEDIUM=30.0）に戻す。連鎖マージボーナスを削除。ドリフトペナルティを30.0に戻す。v128の成功要素を維持し、シンプルで頑健な構造に戻す。
# v331: HIGHフェーズ強化・バランス補正強化版 - v330の失敗(avg=295.5、ターン数10.5)を受けて、v328の成功構造に回帰しつつHIGHフェーズ戦略を強化。
#   v330バッチ分析から特定した問題:
#   - 高度ペナルティ過剰(47.5): ワースト試合は9ターンで終了、終盤max_y=-1.33でHIGHフェーズに到達しない
#   - バランス補正不足: balance_strengthが弱すぎ、盤面が左右どちらかに偏って崩壊
#   - マージボーナス不足: v328より5%削減しすぎ、マージ機会を逃している
#   - ターン数過少: 平均10.5ターンは目標の90ターンを大幅に下回る
#   - 高スコア群vs低スコア群: max_y推移(終盤1.33 vs -1.33)、merge_rate(50% vs 33.3%)
#   根本原因:
#   - v330はv328の微調整(5%削減)に失敗し、戦略の本質を損なった
#   - 盤面をHIGHフェーズに到達させる戦略が不足し、ターン数が伸びない
#   - バランス補正が弱すぎ、盤面が崩壊して早期にゲームオーバーになる
#   - READMEの「旗側集約」原則を活用できていない: 大型ピースを左右に分散させている
#   解決策（HIGHフェーズ強化・バランス補正強化）:
#   - 高度ペナルティ回帰: v328の50.0に戻し、盤面がHIGHフェーズに到達できるようにする
#   - バランス補正大幅強化: v328のHIGH=40.0を60.0に、MEDIUM=30.0を40.0に増幅し、旗側集約を強制
#   - マージボーナス回帰: v328の値(DIRECT=1200/NEAR=600/FAR=200)に戻す、微調整は削除
#   - HIGHフェーズ高度管理緩和: v328の1.8を維持しつつ、HIGH_TOWERペナルティを1.3倍に緩和(v128の値)
#   - マージ履歴ボーナス導入: 直近5ターン内にマージがあったらボーナス、連鎖マージを促進
#   - HIGHフェーズドリフト緩和: HIGHフェーズでマージ可能ならドリフトペナルティを緩和、衝撃波による空間的擾乱を許容
#   核心的発見: ターン数を伸ばすには、(1) 盤面をHIGHフェーズに到達させる、(2) バランス補正を強化して崩壊防止、(3) HIGHフェーズでマージ最大化。v328の成功構造を維持しつつ、HIGHフェーズ戦略を強化。
#   成功基準: avg_scoreが1500以上、またはavg_turnsが50以上、またはavg_scoreがv328の1509.0以上
#   失敗基準: avg_scoreがv330の295.5未満、またはavg_turnsが10未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """HIGHフェーズマージ優先版。v128のシンプルで頑健な構造を採用。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v332: v128の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8
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

        # === v332: v128完全復帰 ===

        # 1. マージグレードによるスコア（v332: v128の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v332: v128のフェーズ感応化を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v332: v128の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v332: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v332: v128のフェーズ感応化を維持）
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

        # 5. nextNext中央寄せボーナス（v332: v128の一律50.0を維持）
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

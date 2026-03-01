#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v339: v128の成功構造復帰 - batch_summary.txtの分析から、reactive_pairs/near_pairsボーナスの効果が低い（avg_score_deltaが低い）ことを特定。v128（BEST:3689）の構造に戻す：マージボーナスを弱化（DIRECT=1200/NEAR=600/FAR=200）、NEXT_SAME中央ボーナスを弱化（80.0→50.0）、reactive_pairs/near_pairsボーナスを削除。これによりHEIGHT_CONTROLの支配を緩和し、よりバランスの取れた戦略に。batch_summary.txtのdecision_reason分布（HEIGHT_CONTROL: 17.3%の支配的）とベストゲームの比較から、高度管理とマージのバランス改善が重要であることが判明。
#   batch_summary.txtの分析結果:
#   - reactive_pairsのavg_score_delta: 7.5（非常に低い）
#   - near_pairsのavg_score_delta: 31.8（高いが頻度が低い）
#   - HEIGHT_CONTROLが17.3%で支配（バランス不均衡）
#   - ベストゲーム（score=1981）の特徴: merge_rate=21.3%、NEAR_MERGE頻繁、max_y推移が良好
#   - v128（BEST:3689）の構造: height_mult HIGH=1.8、merge_bonus DIRECT=1200/NEAR=600/FAR=200、NEXT_SAME=50.0、reactive_pairs/near_pairsボーナスなし
#   根本原因:
#   - v338のreactive_pairs/near_pairsボーナスは期待通りの効果がなかった（avg_score_deltaが低い）
#   - NEXT_SAME中央ボーナスが強すぎて中央寄せが過剰（80.0）
#   - マージボーナスが強すぎて高度管理が軽視された（1500/800/300）
#   - HEIGHT_CONTROLが17.3%で支配的（バランス不均衡）
#   解決策（v339: v128構造復帰）:
#   - reactive_pairsボーナス削除（効果が低い）
#   - near_pairsボーナス削除（効果が低い）
#   - NEXT_SAME中央ボーナス弱化（80.0→50.0）
#   - マージボーナス弱化（1500/800/300→1200/600/200）
#   - v128の成功構造を維持: height_mult HIGH=1.8、merge_mult HIGH=1.0、CRITICAL merge_mult=0.6
#   核心的発現: batch_summary.txtのデータに基づき、効果のないボーナスを削除し、v128の成功構造に復帰することでバランスを改善する
#   成功基準: avg_scoreがv338の1425.2以上、またはmerge_rateが16%以上、またはavg_scoreがv128の3689以上
#   失敗基準: avg_scoreがv338の1425.2未満、またはmerge_rateが15%未満
# [BEST:3689] v128: HIGHフェーズマージ優先版
# [BEST:2335] v42: v19復活・v31/v29複雑化要素削除版
# [BEST:1509] v328: HIGHフェーズマージ強化・v42ベース版


def decide(game_state: dict, analysis: dict) -> dict:
    """v128の成功構造を復帰し、バランスの取れた戦略を実現"""
    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v339: v128の閾値0.8/1.8/3.0を維持）
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
        height_mult = 1.8  # v339: v128の1.8を維持
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

        # === v339: v128構造復帰 ===

        # 1. マージグレードによるスコア（v339: v128の弱い値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v339: v128の設定を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # HIGH_TOWERペナルティ（v339: v128の緩和設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 1.3  # v339: v128の1.3倍を維持
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v339: v128の一律30.0を維持）
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正（v339: v128の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v339: v128の弱い値を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0  # v339: 80.0→50.0に弱化
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

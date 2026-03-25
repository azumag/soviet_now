#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部,ヘルパー関数,定数,import
# AI改変禁止: decide() シグネチャ,if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v20: reactive_pairs活用の盤面圧縮ボーナス追加版 - v19のシンプル構造をベースにaxis 9.6を追加
# batch_summary: 低スコア群はHEIGHT_CONTROLが17.0%選択され、序盤で高さを抑えすぎている（序盤avg=-2.47 vs 高スコア群-1.31）
# ワーストゲーム(score0284): reactive_pairsが常に0、HEIGHT_CONTROLが続き、max_y=-1.93で併合不足
# ベストゲーム(score1634): deadline_crossed状態でも即時併合機会を確実に捉えている
# ロールバック分析: p25（下位25%）が-1142.8と特に悪く、下振れ耐性が不足
# advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「同じタイプが続いて来たらそのタイプの上に置く」に基づく改善
# reactive_pairsを活用した盤面圧縮ボーナス（axis 9.6）を追加: reactive_pairs>=1 && merge_grade=="NO"の場合、(-landing_y) * 150.0
# 即時併合(DIRECT=1200, NEAR=600)より小さいボーナスで、即時併合優先を維持しつつ、同タイプピースの近接配置を促進
# Fixes rollback failure mode: p25悪化（-1142.8）と即時併合機会取りこぼし（axis 9.6 reactive_pairs compression bonus追加）
# refs: tmp/improve_brief.md, tmp/batch_summary.txt, advice.md, tmp/state/last_rollback_analysis.md,
#       game_history/20260326_040005_score0284.jsonl, game_history/20260326_035421_score1634.jsonl,
#       strategy_versions/best_score2335_strategy.py, analyze_board.py


def decide(game_state: dict, analysis: dict) -> dict:
    """v20: reactive_pairs活用の盤面圧縮ボーナス追加版 - v19のシンプル構造をベースにaxis 9.6を追加

    v19のシンプルかつ頑健な構造（DIRECT=1200/NEAR=600/FAR=200、height_penalty=50*height_mult、drift_penalty=30）を維持しつつ、
    reactive_pairsを活用した盤面圧縮ボーナス（axis 9.6）を追加。
    即時併合がない場合に同タイプピースの近接配置を促進し、将来の併合機会を確保する。

    batch_summary: 低スコア群はHEIGHT_CONTROLが17.0%選択され、序盤で高さを抑えすぎている（序盤avg=-2.47 vs 高スコア群-1.31）
    ワーストゲーム(score0284): reactive_pairsが常に0、HEIGHT_CONTROLが続き、max_y=-1.93で併合不足
    ベストゲーム(score1634): deadline_crossed状態でも即時併合機会を確実に捉えている
    ロールバック分析: p25（下位25%）が-1142.8と特に悪く、下振れ耐性が不足

    advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「同じタイプが続いて来たらそのタイプの上に置く」に基づく改善
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定
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
        height_mult = 2.6
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

    # reactor情報（reactive_pairs活用）
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # 1. 併合グレードによるスコア
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ
        height_penalty = landing_y * 50.0 * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # 4. 左右バランス補正
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

        # 5. nextNextが同じタイプなら中央寄せボーナス
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. 盤面圧縮ボーナス（reactive_pairs活用） - axis 9.6
        # advice.md「盤面がどうだろうが即時併合狙った方が絶対勝率高い」「同じタイプが続いて来たらそのタイプの上に置く」に基づく改善
        # reactive_pairs>=1 && merge_grade=="NO"の場合、同タイプピースの近接配置を促進し、将来の併合機会を確保
        # ボーナスは即時併合(DIRECT=1200, NEAR=600)より小さく、即時併合優先を維持
        if reactive_pair_count >= 1 and merge_grade == "NO":
            compression_bonus = (-landing_y) * 150.0
            score += compression_bonus
            reasons.append("REACTIVE_PAIRS_COMPRESSION")

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

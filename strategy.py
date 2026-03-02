#!/usr/bin/env python3
"""strategy.py - AI改善対象の決定スクリプト (v607: MERGE_EXPECT強化・ドリフト品質調整版)"""

# 固定インターフェース:
# decide(game_state: dict, analysis: dict) -> dict
#    戻り値: {"x": float, "reason": str}
#
# AI改変可能: decide() 内部、ヘルパー関数、定数、import
# AI改変禁止: decide() シグネチャ、if __name__ == "__main__" ブロック

# --- 変更履歴 ---
# v607: MERGE_EXPECT強化・ドリフト品質調整版 - batch_summary分析でDRIFT_NO_MERGEのavg_score_delta=4.3と非常に低いことが判明。v607ではMERGE_EXPECTのボーナスを強化し（0.5/0.3→0.8/0.5）、next/nextNextがマージ可能なtypeのペアがあるかチェックし、その近くにボーナスを追加して連鎖マージを促進。ドリフトペナルティをマージ品質に応じて動的に調整し、DIRECT/NEAR/FARでペナルティを緩和することでマージ機会を確保。既存のv42構造を維持しつつ、MERGE_EXPECTの有効性を最大化する。
# v603: v602失敗・v42成功構造完全復帰版 - v602の過度な保守性（EARLY_HIGH_TOWER_PENALTY、DRIFT_NO_MERGE回避、複雑なドリフト品質調整）がゲームを68ターンmax_y=2.9で早期終了させた。batch_summaryでDRIFT_NO_MERGEのavg_score_delta=4.3と極端に低いことを特定。v603ではv42のシンプルで成功した構造に完全復帰しつつ、v19のCRITICALフェーズ（merge_mult=0.6、height_multなし）のみを保持。EARLY_HIGH_TOWER_PENALTYと複雑なドリフト調整を削除し、頑健なシンプル構造に戻す。
# v602: v551ベース・マージ品質重視・早期ペナルティ緩和版 - v602はスコア798に失敗。EARLY_HIGH_TOWER_PENALTYとDRIFT_NO_MERGE回避が過激で、ゲームが68ターンでmax_y=2.9で早期終了。batch_summary分析でDRIFT_NO_MERGEが10.3%を占めるがavg_score_delta=4.3と非常に低く、マージ機会を見逃していることが判明。v602では早期HIGH_TOWERペナルティを緩和し（-150→-50）、EARLY_HIGH_LAYER_MERGE_WAITを削除して「待つ」を抑制。HIGH_LAYERマージボーナスを強化し（DIRECT:150→200, NEAR:100→150）、マージ品質に応じたドリフト調整を強化。v128の「即時重視・複雑さ排除」の思想を復活し、少ないが高品質なマージを優先。


def decide(game_state: dict, analysis: dict) -> dict:
    """v607: MERGE_EXPECT強化・ドリフト品質調整版 - batch_summary分析でDRIFT_NO_MERGEのavg_score_delta=4.3と非常に低いことが判明。v607ではMERGE_EXPECTのボーナスを強化し、next/nextNextがマージ可能なtypeのペアがあるかチェックし、その近くにボーナスを追加して連鎖マージを促進。ドリフトペナルティをマージ品質に応じて動的に調整し、DIRECT/NEAR/FARでペナルティを緩和することでマージ機会を確保。既存のv42構造を維持しつつ、MERGE_EXPECTの有効性を最大化する。"""

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # 盤面情報
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # フェーズ判定（v42の閾値0.8/1.8/3.0を維持）
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0
        merge_mult = 1.2
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 2.4  # v42の設定
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 2.6  # v42の設定
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL: height_multなし（v42の設定）
        merge_mult = 0.6  # v42の設定（v19のCRITICALフェーズ）

    # 次のピース情報
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # MERGE_EXPECTのスコアテーブル（v607: 強化版）
    merge_score_table = {
        1: 1,
        2: 3,
        3: 6,
        4: 10,
        5: 15,
        6: 21,
        7: 28,
        8: 36,
        9: 45,
        10: 55,
        11: 66,
        12: 78,
        13: 91,
        14: 105,
        15: 120,
    }

    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")

        score = 0.0
        reasons = []

        # === v607: MERGE_EXPECT強化・ドリフト品質調整版 ===

        # 1. マージグレードによるスコア（v42の値を維持）
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # 2. 高度によるペナルティ（v42の一律計算を維持）
        height_penalty = landing_y * 50.0 * height_mult

        # 高盤面での追加ペナルティ（v42の設定を維持）
        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # 3. ドリフトによるペナルティ（v607: マージ品質に応じて動的調整）
        drift_penalty_base = 30.0  # v42の一律30.0をベース

        # v607: マージ品質に応じた動的調整
        if merge_grade == "DIRECT":
            drift_penalty_base *= 0.4  # v607: DIRECTをさらに緩和（0.5→0.4）
            reasons.append("DRIFT_DIRECT")
        elif merge_grade == "NEAR":
            drift_penalty_base *= 0.6  # v607: NEARを緩和（0.7→0.6）
            reasons.append("DRIFT_NEAR")
        else:
            # v607: NO_MERGEならベース値そのまま（ペナルティ重視）
            reasons.append("DRIFT_NO_MERGE")

        drift_penalty = (abs(drift_x) + drift_unc) * drift_penalty_base
        score -= drift_penalty

        # 4. 左右バランス補正（v42の設定を維持）
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

        # 5. nextNextが同じタイプなら中央寄せボーナス（v42の設定を維持）
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # 6. MERGE_EXPECTボーナス（v607: 強化版）
        # マージ期待値を計算: next/nextNextが盤面のどのタイプとマージするか
        expectation = 0.0

        # next_typeがマージする候補を検索
        for p in pieces:
            p_type = p.get("type", 0)
            if p_type == next_type - 1:  # next_typeとマージ可能
                dist = abs(p["x"] - x)
                if dist < 1.0:  # 近距離なら高い期待値
                    expectation += (
                        merge_score_table.get(next_type, 0) * 0.8
                    )  # v607: 強化（0.5→0.8）
                    reasons.append("MERGE_EXPECT_NEXT")

        # next_next_typeも考慮
        for p in pieces:
            p_type = p.get("type", 0)
            if p_type == next_next_type - 1:  # next_next_typeとマージ可能
                dist = abs(p["x"] - x)
                if dist < 1.5:  # 少し遠くてもOK
                    expectation += (
                        merge_score_table.get(next_next_type, 0) * 0.5
                    )  # v607: 強化（0.3→0.5）
                    reasons.append("MERGE_EXPECT_NEXT_NEXT")

        # v607: 連鎖マージ促進ボーナス
        # next/nextNextがマージ可能なtypeのペアがあるかチェックし、その近くにボーナスを追加
        for p in pieces:
            p_type = p.get("type", 0)
            # next_typeとマージ可能かつ、next_next_typeともマージ可能かチェック
            if p_type == next_type - 1 and p_type == next_next_type - 1:
                # 同じtypeペアが近くにあるなら、連鎖マージが期待できる
                dist = abs(p["x"] - x)
                if dist < 2.0:  # 連鎖マージの波及範囲
                    expectation += (
                        merge_score_table.get(next_type, 0) * 0.3
                    )  # 連鎖ボーナス
                    reasons.append("CHAIN_MERGE_BONUS")

        if expectation > 0:
            score += expectation
            reasons.append("MERGE_EXPECT")

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

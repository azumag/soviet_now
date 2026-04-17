#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
   - Player controls only drop X coordinate

Decision Logic (9 evaluation axes):
   1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
   2. Height penalty - Penalty for high landing position (varies by phase, early_game: max_y < -3.0)
   3. Drift penalty - Penalty for post-landing drift due to polygon shape
   4. Left-right balance correction - Bonus for correcting piece count bias
   5. nextNext centering - Center for next merge opportunity if nextNext same type
   6. Chain merge bonus - Evaluate possibility of further merges after merge (v171: CHAIN_MERGE基本ボーナス強化)
   7. Early game merge priority - Strong bonus for merge opportunities in early game (v172)
   8. Reactive merge priority - Bonus for merge opportunities when reactive_pairs >= 1 (v176強化版)
   9. Board density bonus - Prefer placement on less-dense side of board (v171/v173成功パターン復帰版)

Phases (determined by board max Y):
   LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
   MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
   HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
   CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""

# refs: tmp/change_log.txt, tmp/batch_summary.txt, tmp/advice.md, game_history/20260307_113909_score0262.jsonl,
# game_history/20260307_112134_score2959.jsonl, game_history/20260307_112715_score0856.jsonl, game_history/20260307_114610_score2417.jsonl,
# strategy_versions/v2402_score2417_strategy.py, strategy_versions/best_score2335_strategy.py,
# strategy_versions/best_score4324_strategy.py, strategy_versions/best_score4999_strategy.py,
# analyze_board.py, sorengame/_extracted/soren-game-fixed/Assets/SORENGAMEFIXED/Script/RepublicController.cs

# --- Change History ---
# [BEST:3689] v126: v42-based HIGH phase merge enhancement
# [BEST:4026] v155: chain_distance 4.5→5.0, chain_bonus 400.0→450.0 achieved best score 4026
# v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
# v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
# v159: 序盤HEIGHT_CONTROL抑制強化版 - max_y < -1.0, height_multiplier=0.2
# v167: 評価精度最適化版 - chain_distance 5.0→4.5縮小
# v168: v155成功パラメータ復帰・動的調整復帰版
# v169: early_game判定超拡大・CHAIN_MERGE評価範囲拡大版 - batch_summaryでHEIGHT_CONTROLが25.2%選択(avg_score_delta=1.4)と過剰であること、
# ワーストゲーム(score0554)で初期11ターンのうち8ターンがHEIGHT_CONTROL/NEXT_SAMEとなり併合機会を逃していることを確認。
# early_game判定をmax_y < -1.0→-3.0に超拡大し、chain_distance_maxを5.0→5.2に拡大して、CHAIN_MERGE選択率を10-15%に引き上げる。
# v170: MEDIUM phase height penalty relaxation版 - batch_summaryでMEDIUM_TOWERがavg_score_delta=3.4（正の値）だが選択率が10.8%（低スコア群）と低いことを確認。
# 高スコア群と低スコア群の比較でMEDIUM_TOWER選択率に13.6% vs 10.8%の差があることを特定。
# MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進し、HEIGHT_CONTROL選択を削減することでスコア安定性を向上させる。
# v171: CHAIN_MERGE基本ボーナス強化版 - batch_summaryでCHAIN_MERGE関連がavg_score_delta=26.9-43.2（高価値）だが選択率は3.8-9.2%と低いことを確認。
# ワーストゲーム(score0633)で初期5ターンが全てHEIGHT_CONTROLとなり、CHAIN_MERGE選択が0回であることを特定。
# chain_distance_max基本値を5.2→5.0に戻し（v155成功値）、chain_bonus_multiplier初期値を450.0→480.0に強化して初期段階でのCHAIN_MERGE選択を促進。
# 着地高による動的調整（landing_y*150.0）は維持し、初期段階と中盤以降の両方でCHAIN_MERGE選択を向上させる。
# v172: 序盤マージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが25.9%選択(avg_score_delta=1.6)と過剰であり、低スコア群で30.3%選択されていることを確認。
# ワーストゲーム(score0545)で初期5ターンが全てHEIGHT_CONTROLとなり併合機会を逃している失敗モードを特定。
# early_game条件下でmerge_gradeがNEARの場合、追加ボーナス800.0を付与する評価軸を追加し、初期段階でのマージ機会を最優先してHEIGHT_CONTROL選択を超強力に抑制する。
# v176: reactor情報活用によるマージ優先評価軸追加版 - batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と依然として過剰であることを確認。
# reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
# これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
# v173: 序盤HEIGHT_CONTROL抑制超強化版 - batch_summaryでDEFAULT_PLACEMENTが20.7%選択(avg_score_delta=2.2)と依然として高いことを確認。
# ワーストゲーム(score0705)で序盤(max_y=-5.0〜-2.02)にDEFAULT_PLACEMENTが10回選択され、併合機会を逃している失敗パターンを特定。
# v172のearly_game判定(max_y < -2.0)をmax_y < -3.0に拡大し、height_multiplierを0.2→0.1に削減して、序盤のHEIGHT_CONTROL選択を超強力に抑制。
# これによりDEFAULT_PLACEMENTの選択率を15%未満に減らし、併合機会を最優先することでスコア安定性を向上させる。
# v181 (今回): reactive_pairs活用強化版 + 盤面左右密度評価追加版
# - reactive_pairs条件を >=2 → >=1 に緩和し、ペア数に応じて段階的ボーナス付与
# - 盤面左右の密度を計算し、密度が低い側への配置を優遇する評価軸を追加
#   - v171/v173成功パターンのBOARD_DENSITY評価軸をシンプル化して実装
# - 低スコア群の中央配置傾向（x≈0.0連呼）を回避し、左右片側配置を促進
#   - reactive_pairsが多い状況でマージを優先し、盤面整理を促進
# - 目的：HEIGHT_CONTROL選択率の削減、マージ機会の最大化、中央配置回避

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v181: reactive_pairs活用強化版 + 盤面左右密度評価追加版

    batch_summary分析でHEIGHT_CONTROLが26.3%選択(avg_score_delta=1.0)と依然として高いことを確認。
    ワーストゲーム(score0262)で序盤20ターンほとんどが中央配置(x≈0.0)を続け、併合機会を逃している失敗モードを特定。
    ベストゲーム(score2959)では序盤から左右に振って配置し、マージを積極的に狙っている。

    v181の改善点:
    1. reactive_pairs活用の強化
       - 条件を>=2→>=1に緩和し、より多くの状況でマージを優先
       - reactive_pairsが多いほど、ペア数に応じてボーナスを段階的に調整
       - reactive_pairs >= 1: +200.0ボーナス（基本値）
       - reactive_pairs >= 2: さらに+200.0ボーナス（合計+400.0）
    2. 盤面左右密度評価軸の追加（v171/v173成功パターンのシンプル化）
       - 盤面左右の密度を計算し、密度が低い側への配置を優遇
       - 中央配置(x≈0.0)を回避し、左右片側配置を促進
       - 既存のボーナスよりも弱いが、頻繁選択される中央配置を抑制する効果を期待

    Args:
        game_state: game state (pieces, next, nextNext, score, etc.)
        analysis: analyze_board.py analysis results
            - results: landing information for each drop X candidate
                - x: drop X coordinate
                - landing_y: estimated landing Y coordinate (high=dangerous)
                - drift_x/drift_unc: post-landing drift due to polygon shape
                - merge_grade: best merge judgment (DIRECT/NEAR/FAR/NO)
                - merges: individual distance/merge judgment for each same-type piece
            - reactor: reactor state (reactive_pairs, near_pairs, etc.)

    Returns:
        {"x": drop X coordinate, "reason": selection reason}
    """

    results = analysis.get("results", [])

    if not results:
        return {"x": 0.0, "reason": "no analysis data"}

    best_x = 0.0
    best_score = -float("inf")
    best_reason = ""

    # --- board information collection ---
    pieces = game_state.get("pieces", [])
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0

    # --- v169: early_game判定超拡大（max_y < -3.0） ---
    # batch_summaryでHEIGHT_CONTROLが25.2%選択(avg_score_delta=1.4)と過剰であること、
    # ワーストゲーム(score0554)で初期11ターンのうち8ターンがHEIGHT_CONTROL/NEXT_SAMEとなり併合機会を逃していることを確認。
    # early_game判定をmax_y < -1.0→-3.0に超拡大し、初期盤面でのHEIGHT_CONTROL選択を強力に抑制
    early_game = max_y < -3.0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v170: MEDIUM phase height penalty relaxation (1.8->1.4) to increase MEDIUM_TOWER selections
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGH relaxation to ensure merge opportunity
        merge_mult = 1.0
    else:
        phase = "CRITICAL"
        height_mult = 1.0  # CRITICAL height penalty basic value only
        merge_mult = 0.6  # v42: CRITICAL phase merge suppression

    # --- next piece information ---
    next_piece = game_state.get("next", {})
    next_next_piece = game_state.get("nextNext", {})
    next_type = next_piece.get("type", 0)
    next_next_type = next_next_piece.get("type", 0)

    # --- Type-specific merge bonus calculation ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # --- v181: Calculate board density (for new evaluation axis 9) ---
    # Count pieces and calculate weighted height on each side
    # Density is weighted by height to avoid stacking on already-high side
    left_density = 0.0
    right_density = 0.0
    for p in pieces:
        x = p["x"]
        y = p["y"]
        # Weight density by height (higher pieces contribute more to density)
        weight = max(0, y + 4.0)  # y=-4.48 at bottom, so shift to positive
        if x < 0:
            left_density += weight
        else:
            right_density += weight

    # Normalize densities
    total_density = left_density + right_density
    if total_density > 0:
        left_density /= total_density
        right_density /= total_density

    # =======================================================================
    #  score each drop candidate (x coordinate) with 9 evaluation axes
    # =======================================================================
    for result in results:
        x = result["x"]
        landing_y = result.get("landing_y", 0)
        drift_x = result.get("drift_x", 0)
        drift_unc = result.get("drift_unc", 0)
        merge_grade = result.get("merge_grade", "NO")  # DIRECT/NEAR/FAR/NO

        score = 0.0
        reasons = []

        # ----- evaluation axis 1: merge bonus -----
        # analyze_board judged merge_grade gives bonus
        # DIRECT: direct hit target (success rate 95.7%)
        # NEAR:   contact zone after landing (success rate 68.5%)
        # FAR:    contact possibility by drift (low probability)
        if merge_grade == "DIRECT":
            score += 1200.0 * merge_mult
            reasons.append("DIRECT_MERGE")
        elif merge_grade == "NEAR":
            score += 600.0 * merge_mult
            reasons.append("NEAR_MERGE")
        elif merge_grade == "FAR":
            score += 200.0 * merge_mult
            reasons.append("FAR_MERGE")

        # ----- evaluation axis 2: height penalty -----
        # landing Y coordinate higher means larger penalty. phase height_mult adjusts weight.
        # v169: early_game（max_y < -3.0）の場合、height_multiplierを0.2に削減してHEIGHT_CONTROL過剰選択を超強力に抑制
        # v170: MEDIUM phase height_multを1.8→1.4に削減してMEDIUM_TOWER選択を促進
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.2  # v169: 序盤はHEIGHT_CONTROLを超強力に抑制し、併合機会を最優先

        height_penalty = landing_y * height_multiplier * height_mult

        if phase == "HIGH" and landing_y > 0.5:
            height_penalty *= 2.0
            reasons.append("HIGH_TOWER")
        elif phase == "MEDIUM" and landing_y > 0.5:
            height_penalty *= 1.5
            reasons.append("MEDIUM_TOWER")
        elif landing_y > 0.0:
            reasons.append("HIGH_LAYER")

        score -= height_penalty

        # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v162: enhanced) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        # v162: MEDIUM phase balance correction enhanced (35.0->40.0)
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter (40.0->50.0)
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v162: MEDIUM phase balance correction enhanced (35.0->40.0)

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v171: CHAIN_MERGE基本ボーナス強化) -----
        # v171: CHAIN_MERGE関連がavg_score_delta=26.9-43.2（高価値）だが選択率は3.8-9.2%と低いことを確認。
        # ワーストゲーム(score0633)で初期5ターンが全てHEIGHT_CONTROLとなり、CHAIN_MERGE選択が0回であることを特定。
        # chain_distance_max基本値を5.2→5.0に戻し（v155成功値）、chain_bonus_multiplier初期値を450.0→480.0に強化して初期段階でのCHAIN_MERGE選択を促進。
        # 着地高による動的調整は維持し、初期段階と中盤以降の両方でCHAIN_MERGE選択を向上させる。
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v171: CHAIN_MERGE基本ボーナス強化
                # chain_distance_max = 5.0 + landing_y * 0.6 (v155成功値に戻す、着地高に応じて拡大)
                # chain_bonus_multiplier = 480.0 + landing_y * 150.0 (初期値を450.0→480.0に強化、着地高に応じて増強）
                # 例: landing_y=-3.0 → distance_max=3.2, multiplier=30.0（初期段階）
                # 例: landing_y=0.0 → distance_max=5.0, multiplier=480.0（初期値強化）
                # 例: landing_y=1.0 → distance_max=5.6, multiplier=630.0
                # 例: landing_y=2.0 → distance_max=6.2, multiplier=780.0
                # 例: landing_y=3.0 → distance_max=6.8, multiplier=930.0
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 480.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda piece: piece[0])

                # v171: CHAIN_MERGE基本ボーナス強化 - 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                # chain_distance_max=5.0（v155成功値）とchain_bonus_multiplier初期値480.0（強化）で初期段階でのCHAIN_MERGE選択を促進
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[0]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[1]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 7: early game merge priority (v172: 新規追加) -----
        # v172: batch_summaryでHEIGHT_CONTROLが25.9%選択(avg_score_delta=1.6)と過剰であること、
        # 低スコア群で30.3%選択されていることを確認（高スコア群22.5%より7.8ポイント高い）。
        # ワーストゲーム(score0545)で初期5ターンが全てHEIGHT_CONTROLとなり併合機会を逃している失敗モードを特定。
        # early_game条件下でmerge_gradeがNEARの場合、追加ボーナス800.0を付与し、初期段階でのマージ機会を最優先する。
        # これによりHEIGHT_CONTROL選択を超強力に抑制し、スコア安定性を向上させる。
        if early_game and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期段階でのマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 800.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: reactive merge priority (v181: 強化版) -----
        # v176: reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する。
        # v181: 条件を>=2→>=1に緩和し、ペア数に応じて段階的ボーナス付与で効果を最大化
        # 盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
        reactor = analysis.get("reactor", {})
        reactive_pairs = reactor.get("reactive_pairs", [])
        reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

        if reactive_pair_count >= 1 and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairsが1つ以上ある場合、段階的ボーナスを付与
            # ペア数が多いほどボーナスを強化し、マージを優先
            reactive_bonus = 200.0 * reactive_pair_count
            score += reactive_bonus
            reasons.append("REACTIVE_MERGE")

        # ----- evaluation axis 9: board density bonus (v181: v171/v173成功パターン復帰版) -----
        # Prefer placement on less-dense side of board to improve height gain capability
        # This addresses problem where DEFAULT_PLACEMENT (x=0.0) is too frequent but provides low value
        # Low-score games often place pieces in center early, which reduces height gain capability in mid/late game
        if not reasons or merge_grade == "NO":
            # Only apply when no strong merge reason exists (avoid overriding merge opportunities)
            # Calculate density bonus: prefer placing on less-dense side
            if x < 0:
                # Placing on left side: bonus if right side is more dense
                density_bonus = (right_density - left_density) * 50.0
            else:
                # Placing on right side: bonus if left side is more dense
                density_bonus = (left_density - right_density) * 50.0
            # Apply bonus (positive means placing on less-dense side)
            if abs(density_bonus) > 10.0:  # Only add reason if density difference is significant
                score += density_bonus
                reasons.append("BOARD_DENSITY")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            # v169: HEIGHT_CONTROLフォールバック削除を維持
            best_reason = "_".join(reasons) if reasons else "HEIGHT_CONTROL"

    # clip to drop range [-3.0, +3.0]
    best_x = max(-3.0, min(3.0, best_x))
    best_x = round(best_x, 2)

    return {"x": best_x, "reason": best_reason}


# --- AI modification prohibited zone ---
if __name__ == "__main__":
    import json
    import sys

    # standalone test
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        game_state = json.load(open(gs_path))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # get analysis data from analyze_board
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

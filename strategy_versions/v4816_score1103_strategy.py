#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

 Decision Logic (8 evaluation axes):
   1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
   2. Height penalty - Penalty for high landing position (varies by phase)
   2.5. near_pairs bonus - Bonus for near_pairs in dangerous situations when immediate merge unavailable
   3. Drift penalty - Penalty for post-landing drift due to polygon shape
   4. Left-right balance correction - Bonus for correcting piece count bias
   5. nextNext centering - Center for next merge opportunity if nextNext same type
   6. Chain merge bonus - Evaluate possibility of further merges after merge
   7. Board density bonus - Prefer placement on less-dense side of board

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.8)
  HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
  CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)

v178 additional logic:
  - Dangerous situation detection (max_y >= 2.0 and reactive_pairs >= 3)
  - In dangerous situations, reduce height_multiplier to 15.0 to prioritize immediate merge
"""

# Fixed interface:
# decide(game_state: dict, analysis: dict) -> dict
#    Returns: {"x": float, "reason": str}
#
# AI modifiable: decide() body, helper functions, constants, imports
# AI prohibited: decide() signature, if __name__ == "__main__" block

# --- Change History ---
# [BEST:3689] v126: v42-based HIGH phase merge enhancement
# [BEST:4026] v155: chain_distance 4.5→5.0, chain_bonus 400.0→450.0 achieved best score 4026
# [BEST:4319] v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
# [BEST:4324] v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
# v159: 序盤HEIGHT_CONTROL抑制強化版 - max_y < -1.0, height_multiplier=0.2
# v167: 評価精度最適化版 - chain_distance 5.0→4.5縮小
# v168: v155成功パラメータ復帰・動的調整復帰版
# v169: HEIGHT_CONTROLフォールバック削除 - batch_summaryでHEIGHT_CONTROLが23.9%選択(avg_score_delta=2.5)と低価値を確認
# v170: 序盤HEIGHT_CONTROL抑制拡大版 - max_y < 0.0, height_multiplier=0.1
# v171: ボード密度評価軸追加 - batch_summaryでDEFAULT_PLACEMENTが21.7%選択(avg_score_delta=1.4)と非常に頻繁だが価値がないことを確認。
# 低スコア群と高スコア群のmax_y推移差(初期差0.39 vs 終盤差1.64)から、序盤の中心放置が中盤以降の高さ稼ぎに失敗しているパターンを特定。
# DEFAULT_PLACEMENT(x=0.0)を避け、密度が低い側(左or右)を優先する評価軸を追加することで、ボードの高さ稼ぎ能力を向上させスコア安定性を改善。
# v173: 序盤HEIGHT_CONTROL抑制超強化版 - batch_summaryでDEFAULT_PLACEMENTが20.7%選択(avg_score_delta=2.2)と依然として高いことを確認。
# ワーストゲーム(score0705)で序盤(max_y=-5.0〜-2.02)にDEFAULT_PLACEMENTが10回選択され、併合機会を逃している失敗パターンを特定。
# v172のearly_game判定(max_y < -2.0)をmax_y < -3.0に拡大し、height_multiplierを0.2→0.1に削減して、序盤のHEIGHT_CONTROL選択を超強力に抑制。
# これによりDEFAULT_PLACEMENTの選択率を15%未満に減らし、併合機会を最優先することでスコア安定性を向上させる。
# v178: 危険局面即時併合優先強化版 - batch_summaryでDEFAULT_PLACEMENTが19.5%選択(avg_score_delta=1.8)と依然として高いことを確認。
# ワーストゲーム(score0593)の終盤分析で、max_y>=2.0かつreactive_pairs>=4あるにもかかわらずHIGH_LAYER_BOARD_DENSITYが6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
# max_y >= 2.0かつreactive_pairs >= 3の危険局面でheight_multiplierを15.0に削減し、即時併合を強制的に優先することで、併合機会を活かしスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, advice.md, game_history/20260312_135916_score0593.jsonl turns 36-42, game_history/20260312_141222_score2015.jsonl turns 83-85

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v178: 危険局面即時併合優先強化版
    
    batch_summaryでDEFAULT_PLACEMENTが19.5%選択(avg_score_delta=1.8)と依然として高いことを確認。
    ワーストゲーム(score0593)の終盤分析で、max_y>=2.0かつreactive_pairs>=4あるにもかかわらずHIGH_LAYER_BOARD_DENSITYが6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定。
    
    v178の改善点:
    1. 危険局面での即時併合優先
       - max_y >= 2.0かつreactive_pairs >= 3の危険局面でheight_multiplierを15.0に削減
       - reactor情報のreactive_pairsを活用し、危険局面で即時併合を強制的に優先
       - ワーストゲームの失敗パターン（reactive_pairs=4-6あるのに即時併合を逃す）を解消
    2. v173の序盤HEIGHT_CONTROL抑制超強化を維持
       - early_game判定(max_y < -3.0)とheight_multiplier=0.1を維持
       - 序盤のDEFAULT_PLACEMENT選択を抑制し、併合機会を最優先
    3. v173のボード密度評価軸とv172の序盤HEIGHT_CONTROL抑制を維持
    
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
    
    # --- v173: 序盤判定をmax_y < -3.0に拡大 ---
    early_game = max_y < -3.0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v151: height_mult 2.2->1.8 relaxation, ensure merge opportunity
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
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # --- v171: Calculate board density (for new evaluation axis 7) ---
    # Count pieces and calculate weighted height on each side
    # Density is weighted by piece height to avoid stacking on already-high side
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
    #  score each drop candidate (x coordinate) with 7 evaluation axes
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
        # v173: early_game判定（max_y < -3.0）の場合、height_multiplierを0.1に削減
        # これにより序盤のHEIGHT_CONTROL選択を超強力に抑制し、併合機会を最優先
        # v178: 危険局面即時併合優先強化 - max_y >= 2.0かつreactive_pairs >= 3の場合、
        #        height_multiplierを15.0に削減し、即時併合を強制的に優先
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.1  # v173: 序盤はHEIGHT_CONTROLを超強力に抑制
        
        # v178: 危険局面での即時併合優先
        # reactor情報のreactive_pairsを活用し、危険局面で即時併合を優先する
        # ワーストゲーム(score0593)の終盤分析で、reactive_pairs=4-6あるにもかかわらず
        # HIGH_LAYER_BOARD_DENSITYが6ターン連続で選択され、即時併合機会を完全に逃している失敗パターンを特定
        # max_y >= 2.0かつreactive_pairsの長さ >= 3の危険局面でheight_multiplierを15.0に削減
        reactor = analysis.get("reactor", {})
        reactive_pairs = reactor.get("reactive_pairs", [])
        if max_y >= 2.0 and isinstance(reactive_pairs, list) and len(reactive_pairs) >= 3:
            height_multiplier = 15.0  # v178: 危険局面はHEIGHT_CONTROLを抑制し、即時併合を最優先

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

        # ----- evaluation axis 2.5: near_pairs bonus in dangerous situations -----
        # 危険局面で即時併合がない場合、near_pairsを活用する配置を優先
        # ワーストゲーム(score0574, score0593)の終盤分析で、max_y>=2.0かつreactive_pairs>=4-6あるにもかかわらず
        # HIGH_TOWER/HIGH_LAYERが選択され続け即時併合機会を逃している失敗パターンを特定
        # dangerous_situation かつ merge_grade == "NO" の場合、near_pairs が多い配置を優先
        dangerous_situation = max_y >= 2.0 and isinstance(reactive_pairs, list) and len(reactive_pairs) >= 3
        if dangerous_situation and merge_grade == "NO":
            near_pairs = reactor.get("near_pairs", [])
            if isinstance(near_pairs, list):
                near_pairs_count = len(near_pairs)
                # near_pairsが多いほど将来のreactive_pairsへの昇格可能性が高い
                # reactive_pairsが3以上ある状況で、near_pairsを増やすことでさらに盤面圧縮を促進
                if near_pairs_count >= 3:
                    score += 800.0  # near_pairs活用ボーナス
                    reasons.append("NEAR_PAIRS_OPPORTUNITY")
                elif near_pairs_count >= 2:
                    score += 400.0
                    reasons.append("NEAR_PAIRS_OPPORTUNITY")

        # ----- evaluation axis 3: drift penalty -----
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v162: enhanced) -----
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter
        elif phase == "MEDIUM":
            balance_strength = 40.0  # v162: MEDIUM phase balance correction enhanced

        left_count = sum(1 for p in pieces if p["x"] < 0)
        right_count = len(pieces) - left_count
        balance_bias = (right_count - left_count) / (len(pieces) if pieces else 1)

        balance_penalty = x * balance_bias * balance_strength
        score -= abs(balance_penalty)

        # ----- evaluation axis 5: nextNext centering -----
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v170: v155 parameters & dynamic adjustment) -----
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v170: v155成功パラメータ復帰 & v157/v159動的調整復帰
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda piece: piece[0])

                # v170: v155距離加重ボーナス復帰 - 3つの最も近いピースに対して、距離に応じて減衰するボーナスを適用
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

        # ----- evaluation axis 7: board density bonus (v171: NEW) -----
        # Prefer placement on less-dense side of board to improve height gain capability
        # This addresses the problem where DEFAULT_PLACEMENT (x=0.0) is too frequent but provides low value
        # Low-score games often place pieces in center early, which reduces height gain capability in mid/late game
        if not reasons or merge_grade == "NO":
            # Only apply when no strong merge reason exists (avoid overriding merge opportunities)
            if x < 0:
                # Placing on left side: bonus if right side is more dense
                density_bonus = (right_density - left_density) * 50.0
            else:
                # Placing on right side: bonus if left side is more dense
                density_bonus = (left_density - right_density) * 50.0

            # Apply bonus (positive means placing on less-dense side)
            if density_bonus > 10.0:  # Only add reason if density difference is significant
                score += density_bonus
                reasons.append("BOARD_DENSITY")

        # ----- update best candidate -----
        if score > best_score:
            best_score = score
            best_x = x
            # v169: HEIGHT_CONTROLフォールバック削除を維持
            best_reason = "_".join(reasons) if reasons else "DEFAULT_PLACEMENT"

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

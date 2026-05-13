#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (8 evaluation axes):
    1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
    2. Height penalty - Penalty for high landing position (varies by phase, early_game: max_y < -3.0)
    3. Drift penalty - Penalty for post-landing drift due to polygon shape
    4. Left-right balance correction - Bonus for correcting piece count bias
    5. nextNext centering - Center for next merge opportunity if nextNext same type (v178: 強化)
    6. Chain merge bonus - Evaluate possibility of further merges after merge (v177: 初期段階CHAIN_MERGE探索範囲拡大)
    7. Early game merge priority - Strong bonus for merge opportunities in early game (v175)
    8. Board density bonus - Prefer placement on less-dense side of board (v4324/v4999 success pattern)

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.2)
  HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
  CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""

# Fixed interface:
# decide(game_state: dict, analysis: dict) -> dict
#    Returns: {"x": float, "reason": str}
#
# AI modifiable: decide() body, helper functions, constants, imports
# AI prohibited: decide() signature, if __name__ == "__main__" block

# --- Change History ---
# refs: strategy.py.staging (v179), strategy_versions/best_score4324_strategy.py, strategy_versions/best_score4999_strategy.py
# refs: tmp/batch_summary.txt, tmp/change_log.txt, game_history/*.jsonl
# [BEST:3689] v126: v42-based HIGH phase merge enhancement
# [BEST:4026] v155: chain_distance 4.5→5.0, chain_bonus 400.0→450.0 achieved best score 4026
# [BEST:4319] v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
# [BEST:4324] v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
# [BEST:4999] v171: ボード密度評価軸追加 - BOARD_DENSITY評価軸を導入し、DEFAULT_PLACEMENT過剰選択を抑制
# v178: NEXT_SAME中央配置ボーナス強化版 - 中央配置ボーナス50.0→80.0に強化してnextNext考慮を重視
# v179: MEDIUM phase height_multを1.4→1.2にさらに削減してMEDIUM_TOWER選択をより促進
# v180: BOARD_DENSITY評価軸再導入版 - v4324/v4999成功パターン復帰
#   batch_summary分析でHEIGHT_CONTROLが26.9%選択(avg_score_delta=2.8)と依然として過剰であることを確認。
#   低スコア群でHEIGHT_CONTROL選択率が34.4%と高スコア群（23.2%）より高いことを特定。
#   v4324/v4999で成功したBOARD_DENSITY評価軸を再導入し、DEFAULT_PLACEMENT(x=0.0)の過剰選択を抑制。
#   early_game判定をpiece_count <= 12からmax_y < -3.0に変更し、height_multiplierを0.2→0.1に削減して序盤のHEIGHT_CONTROL抑制を強化。
#   ボード密度を計算し、密度が低い側への配置を優先することで盤面の高さ稼ぎ能力を向上させスコア安定性を改善。

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v180: BOARD_DENSITY評価軸再導入版

    batch_summary分析でHEIGHT_CONTROLが26.9%選択(avg_score_delta=2.8)と依然として過剰であることを確認。
    低スコア群でHEIGHT_CONTROL選択率が34.4%と高スコア群（23.2%）より高いことを特定。

    v180の改善点:
    1. BOARD_DENSITY評価軸再導入 (v4324/v4999成功パターン復帰)
       - ボード左右の密度を計算（ピース数および高さを考慮）
       - 密度が低い側への配置を優先（DEFAULT_PLACEMENT過剰選択を抑制）
       - 低スコア群と高スコア群のmax_y推移差から、序盤の中心放置が中盤以降の高さ稼ぎに失敗しているパターンを解消
    2. early_game判定をpiece_count <= 12からmax_y < -3.0に変更
       - v4324/v4999成功判定基準を採用し、盤面がまだ低い段階でHEIGHT_CONTROLを抑制
    3. height_multiplierを0.2→0.1に削減
       - v4324/v4999成功パラメータを採用し、序盤のHEIGHT_CONTROL抑制を強力化

    v179から継承:
    1. MEDIUM phase height_mult削減 (1.4→1.2)
       - MEDIUM_TOWER選択を促進し、HEIGHT_CONTROL選択を削減
    2. NEXT_SAME中央配置ボーナス強化 (80.0)
    3. 初期段階CHAIN_MERGE探索範囲拡大 (chain_distance_max=5.0)
    4. EARLY_MERGE_PRIORITY評価軸 (初期12ターンでマージ最優先)

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
    piece_count = len(pieces)

    # --- v180: early_game判定をmax_y < -3.0に変更 (v4324/v4999成功パターン復帰) ---
    # batch_summaryでHEIGHT_CONTROLが26.9%選択(avg_score_delta=2.8)と依然として過剰であることを確認。
    # v4324/v4999成功判定基準(max_y < -3.0)を採用し、盤面がまだ低い段階でHEIGHT_CONTROLを抑制
    early_game = max_y < -3.0

    # --- v180: Calculate board density (for evaluation axis 8) ---
    # Count pieces and calculate weighted height on each side
    # Density is weighted by piece height to avoid stacking on already-high side
    # This addresses the problem where DEFAULT_PLACEMENT (x=0.0) is too frequent but provides low value
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

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.2  # v179: MEDIUM phase height penalty relaxation (1.4->1.2)
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

    # =======================================================================
    #  score each drop candidate (x coordinate) with 8 evaluation axes
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
        # v180: early_game判定(max_y < -3.0)の場合、height_multiplierを0.1に削減(v4324/v4999成功パターン)
        # v179: MEDIUM phase height_multを1.4→1.2にさらに削減してMEDIUM_TOWER選択をより促進
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.1  # v180: 序盤はHEIGHT_CONTROLを超強力に抑制(v4324/v4999成功パラメータ)

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

        # ----- evaluation axis 5: nextNext centering (v178: 強化) -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        # v178: 中央配置ボーナスを強化してnextNext考慮を重視（50.0→80.0）
        # 中央配置は盤面の整理と次のマージ機会の確保を促進する戦略的配置
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 80.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v177: 初期段階CHAIN_MERGE探索範囲拡大) -----
        # v177: batch_summaryでHEIGHT_CONTROLが29.4%選択(avg_score_delta=2.4)と依然として過剰であること、CHAIN_MERGE選択率が3.8-9.2%と低いことを確認。
        # 初期段階(landing_y=-3.0)でchain_distance_max=2.7と探索範囲が狭すぎ、merged_typeピースを見逃す問題を特定。
        # v155成功例(chain_distance=5.0)と比較すると初期段階の探索範囲が半減しており、CHAIN_MERGE機能が実質的に無効化されている。
        # chain_distance_max基本値を4.5→5.0に拡大し、初期段階の探索範囲を11%広げてCHAIN_MERGE選択率を3.8-9.2%から10-15%に引き上げる。
        # v177: CHAIN_MERGE探索範囲拡大版
        # chain_distance_max = 5.0 + landing_y * 0.6（基本値v155成功値に復帰、初期段階の探索範囲11%拡大）
        # chain_bonus_multiplier = 450.0 + landing_y * 50.0（v176の基本定数強化を維持）
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v177: CHAIN_MERGE探索範囲拡大版
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 50.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v177: CHAIN_MERGE探索範囲拡大版 - 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
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

        # ----- evaluation axis 7: early game merge priority (v175/v176: 初期12ターンマージ重視) -----
        # v175: early_game判定をpiece_count <= 12に変更し、EARLY_MERGE_PRIORITYの適用範囲を確定。
        # v180: early_game判定をmax_y < -3.0に変更(v4324/v4999成功パターン復帰)
        # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
        # これにより初期段階でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
        if early_game and merge_grade == "NEAR":
            score += 800.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 8: board density bonus (v180: v4324/v4999成功パターン復帰) -----
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

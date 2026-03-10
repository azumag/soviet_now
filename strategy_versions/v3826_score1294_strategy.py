#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

 Decision Logic (7 evaluation axes):
    1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
    2. Height penalty - Penalty for high landing position (varies by phase)
    3. Drift penalty - Penalty for post-landing drift due to polygon shape
    4. Left-right balance correction - Bonus for correcting piece count bias
    5. nextNext centering - Center for next merge opportunity if nextNext same type
    6. Chain merge bonus - Evaluate possibility of further merges after merge
    7. Reactive merge priority - Bonus for merge opportunities in HIGH phase when reactive_pairs >= 2 (v175)

    v3805: BOARD_DENSITY評価軸削除版 - batch_summary分析でBOARD_DENSITYが10.7%選択(avg_score_delta=0.3)と低価値を確認
    低スコア群のDEFAULT_PLACEMENT(17.1%)とBOARD_DENSITY(12.8%)選択率が高スコア群より高いことを特定
    BOARD_DENSITYを削除し、併合機会を逃す配置を排除することでスコア安定性を向上させる

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.8)
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
# [BEST:3689] v126: v42-based HIGH phase merge enhancement
# [BEST:4026] v155: chain_distance 4.5→5.0, chain_bonus 400.0→450.0 achieved best score 4026
# [BEST:4319] v156: v42/v126成功構造復帰・CHAIN_MERGE削除版
# [BEST:4324] v162: MEDIUMフェーズバランス補正強化版 - balance_strength 35.0→40.0
# [BEST:5310] v177: reactor情報活用によるマージ優先評価軸追加版 - reactive_pairs>=2でDIRECT/NEARに+500ボーナス
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
# v174: HIGHフェーズ高さペナルティ抑制版 - batch_summaryでNEAR_MERGE(avg_score_delta=22.6)が高価値だが選択率6.5%と低いことを確認。
# HIGHフェーズでheight_mult=1.8が高すぎてNEAR_MERGE機会を潰している問題に対し、height_multを1.8→1.0に抑制し併合機会を優先。
# v175: REACTIVE_MERGE_PRIORITY評価軸追加版 - 終盤高危険域(max_y>=1.8)でreactive_pairs>=2がある場合、DIRECT/NEARに+500ボーナスを付与し即時併合機会を優先。
# ワーストゲームの終盤8ターン(max_y=2.37-2.38, reactive_pairs=3)でDEFAULT_PLACEMENTが続き即時併合機会を逃している失敗パターンを特定。
# v177の成功構造を復帰し、反応器（reactor）情報を活用して反応濃度の高い状況での即時反応を優先することでスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260310_125627_score0528.jsonl, strategy_versions/best_score5310_strategy.py, prompts/game_theory.md
# v176: DANGER_RECOVERY_PENALTY評価軸追加版 - ワーストゲーム(score0794)の終盤8ターン(turns 58-60)でHIGH_TOWERが3回選択され、reactive_pairs=2があるにもかかわらず即時併合を逃している失敗パターンを特定。
# 現行のDANGER_RECOVERY評価軸（+800ボーナス）はmerge_gradeが"DIRECT"または"NEAR"の場合のみ発動するが、NEAR_MERGE機会自体が選ばれていないという悪循環がある。
# max_y>=2.0の危険局面でreactive_pairs>=2がある場合、DIRECT/NEARマージ機会がない配置に-1000ペナルティを課す評価軸を追加。
# これにより、HIGH_TOWERなどの非併合配置を間接的に抑制し、NEAR_MERGE選択率を向上させることでスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260310_132049_score0794.jsonl, strategy_versions/v3769_score0794_strategy.py, strategy_versions/best_score5310_strategy.py, prompts/game_theory.md
# v177: 危険局面フィルタリング追加版 - ワーストゲーム(score1143)の終盤8ターン(turns 72-74)でHIGH_LAYER_DANGER_RECOVERY_PENALTYが3回連続選択され、max_y=3.31まで上昇してdead lineに到達寸前。
# DANGER_RECOVERY_PENALTYの-1000ペナルティが発動しているが、全ての候補にペナルティが課されているため、非併合配置が依然として選ばれる悪循環を特定。
# max_y>=2.0でreactive_pairs>=2の場合、merge_gradeがDIRECTまたはNEARの候補のみを評価対象にするフィルタリングを追加し、非併合配置を物理的に除外。
# これにより、HIGH_TOWER/HIGH_LAYERなどの延命配置を防ぎ、即時併合による盤面圧縮を強制することでスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260310_133049_score1143.jsonl turns 72-74
# v3805: BOARD_DENSITY評価軸削除版 - batch_summary分析でBOARD_DENSITYが10.7%選択(avg_score_delta=0.3)と低価値を確認。
# 低スコア群のDEFAULT_PLACEMENT(17.1%)とBOARD_DENSITY(12.8%)選択率が高スコア群(DEFAULT_PLACEMENT 13.7%, BOARD_DENSITY 9.0%)より高いことを特定。
# BOARD_DENSITYを削除し、併合機会を逃す配置を排除することでスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/improve_brief.md

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v3805: BOARD_DENSITY評価軸削除版

    batch_summary分析でBOARD_DENSITYが10.7%選択(avg_score_delta=0.3)と低価値を確認。
    低スコア群のDEFAULT_PLACEMENT(17.1%)とBOARD_DENSITY(12.8%)選択率が高スコア群(DEFAULT_PLACEMENT 13.7%, BOARD_DENSITY 9.0%)より高いことを特定。
    v171で追加したBOARD_DENSITY評価軸を削除し、併合機会を逃す盤面分散配置を排除することでスコア安定性を向上させる。

    v3805の改善点:
    1. BOARD_DENSITY評価軸削除
       - board density bonus評価軸を完全に削除
       - 盤面密度を基準とした配置を排除
       - 併合機会を最優先する決定ルールに集約
    2. v177の危険局面フィルタリング構造を維持（max_y>=2.0, reactive_pairs>=2 → DIRECT/NEARマージ候補のみ）
    3. v175のREACTIVE_MERGE_PRIORITY評価軸を維持
    4. v174のHIGHフェーズ高さペナルティ抑制を維持
    5. v173の序盤HEIGHT_CONTROL抑制を維持
    
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

    # --- v175: reactor情報の取得（REACTIVE_MERGE_PRIORITY評価軸用） ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

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
        height_mult = 1.0  # v174: batch_summary分析に基づきHIGHフェーズの高さペナルティ抑制(1.8→1.0)しNEAR_MERGE機会を優先
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
    #  score each drop candidate (x coordinate) with 8 evaluation axes
    #  v3805: 危険局面フィルタリング発動閾値引下版 - max_y>=1.8でreactive_pairs>=2の場合、
    #       merge_gradeがDIRECTまたはNEARの候補のみを評価対象にし、非併合配置を除外する
    #       これにより、HIGH_TOWER/HIGH_LAYERなどの延命配置を物理的に防ぎ、即時併合による盤面圧縮を強制
    #       refs: tmp/batch_summary.txt, tmp/improve_brief.md, game_history/20260310_133049_score1143.jsonl turns 72-74
    # =======================================================================

    # v3805: 危険局面フィルタリング - 併合機会がある場合、非併合配置を候補から除外
    # 危険度閾値引下: max_y>=2.0 -> 1.8, 危険局面をより早く定義して盤面圧縮を早める
    if max_y >= 1.8 and reactive_pair_count >= 2:
        # merge_gradeがDIRECTまたはNEARの候補のみを評価対象にする
        merge_candidates = [r for r in results if r.get("merge_grade") in ["DIRECT", "NEAR"]]
        if merge_candidates:
            results = merge_candidates  # 併合機会がある場合、それのみを評価
        # 併合機会がない場合は全候補を評価（DANGER_RECOVERY_PENALTYでペナルティ課す）
    
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
        height_multiplier = 30.0
        if early_game:
            height_multiplier = 0.1  # v173: 序盤はHEIGHT_CONTROLを超強力に抑制

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

        # v3805: BOARD_DENSITY evaluation axis removed - batch_summary analysis showed BOARD_DENSITY has 10.7% frequency but avg_score_delta=0.3 (low value)
        # Low-score games have higher DEFAULT_PLACEMENT (17.1%) and BOARD_DENSITY (12.8%) selection rates compared to high-score games
        # Removing BOARD_DENSITY prevents non-merge placements that were prioritizing board density over immediate merge opportunities
        # This structural change removes an evaluation axis, allowing merge-focused decisions to dominate
        # refs: tmp/batch_summary.txt, tmp/improve_brief.md

         # ----- evaluation axis 8: REACTIVE_MERGE_PRIORITY (v175: NEW) -----
        # reactor情報のreactive_pairsを活用し、反応濃度の高い状況での即時反応を優先
        # 終盤高危険域(max_y>=1.8)でreactive_pairs>=2がある場合、DIRECT/NEARに+500ボーナス
        # これにより、反応器（reactor）情報を活用して反応濃度の高い状況での即時反応を優先
        # 理論的背景: game_theory.md - 反応効率は反応物の濃度に比例する（reactive_pairsは接触圏内の同typeペア数）
        # refs: game_history/20260310_125627_score0528.jsonl turn 64-66 (max_y=2.37-2.38, reactive_pairs=3でDEFAULT_PLACEMENTが続き即時併合機会を逃している)
        if max_y >= 1.8 and reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            score += 500.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # DANGER_RECOVERY_PENALTY評価軸はv177のフィルタリングだけで十分のため削除（v178）

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

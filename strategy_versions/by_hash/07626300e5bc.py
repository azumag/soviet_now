#!/usr/bin/env python3
"""strategy.py - Soviet Puzzle Game AI Drop Position Script

Game Overview:
  - Drop pieces, merge same type pieces (N+N -> N+1)
  - Score table: type1=1, type2=3, type3=6, ..., typeN = N*(N+1)/2
  - Board: x in [-3.0, +3.0], floor y=-4.48, deadline y=3.32
  - Player controls only drop X coordinate

Decision Logic (6 evaluation axes):
  1. Merge bonus - High score for immediate merge (DIRECT > NEAR > FAR)
  2. Height penalty - Penalty for high landing position (varies by phase)
  3. Drift penalty - Penalty for post-landing drift due to polygon shape
  4. Left-right balance correction - Bonus for correcting piece count bias
  5. nextNext centering - Center for next merge opportunity if nextNext same type
  6. Chain merge bonus - Evaluate possibility of further merges after merge (v154: density evaluation version, v158: re-introduced for CHAIN_MERGE selection rate improvement)

Phases (determined by board max Y):
  LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
  MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=2.4)
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
# v151-v153: CHAIN_MERGE強化版（係数200.0→300.0→400.0、chain_distance 3.0→3.5→4.0）
# v154: 併合ターゲット周辺の密度評価版 - 既存のCHAIN_MERGE評価ロジックの問題を修正。
# 既存ロジックでは「併合ターゲットの最も近いmerged_typeピース1つ」だけを評価していたが、これは不十分。
# batch_summaryでCHAIN_MERGE関連がavg_score_delta=40〜52.8と高いが選択率が低い（約7%）問題を解決。
# 新評価法：併合ターゲット周辺のmerged_typeピースの「密度」を評価。
# - chain_distance内の全てのmerged_typeピースを収集
# - 最も近い3つのピースからボーナス計算: 1番目=(chain_distance-dist)*400.0, 2番目=(chain_distance-dist)*200.0, 3番目=(chain_distance-dist)*100.0
# - chain_distanceを4.0→4.5に拡大して評価範囲を広げる
# 効果：併合ターゲット周辺に同じtypeが集中している配置=連鎖確率が高い、を正確に評価し、CHAIN_MERGE選択率を向上。
# v155-v157: 動的パラメータ調整版 - v154の密度評価版は評価範囲をchain_distance=4.5まで拡大したが、CHAIN_MERGE選択率はまだ低い。
# v157: 着地高動的調整・CHAIN_MERGE促進版 - v156のheight_multiplier抑制（40.0）でもHEIGHT_CONTROL選択率が高い問題を解決。
# 単純なパラメータ調整ではなく、構造的改善として着地高に応じた動的調整を導入。
# landing_yが高いほどchain_distance_maxを拡大（5.0 + landing_y*0.6）し、chain_bonus_multiplierも強化（450.0 + landing_y*150.0）することで、
# HIGH_LAYER状況でのCHAIN_MERGE選択を強制的に誘導し、HEIGHT_CONTROLの選択を減らしてスコア安定性を向上させる。
# v158: 密度評価版再導入・HEIGHT_CONTROL抑制版 - batch_summary分析でHEIGHT_CONTROLが26.0%選択されながらavg_score_delta=2.9と低いこと、
# NEAR_MERGE_HIGH_LAYER_CHAIN_MERGEがavg_score_delta=67.9と高価値だが選択率は10.2%と低いことを確認。
# v157の動的調整ロジックは導入したが、CHAIN_MERGE選択率は依然として低い（約20%）。
# v155で密度評価版（3つの連鎖ピース評価）が簡素化版（最も近い1つのみ）に変更されたことが原因を特定。
# 構造的改善としてv154の密度評価版を再導入し、最も近い3つの連鎖ピースを考慮した評価に戻す。
# 同時にheight_multiplierを30.0→35.0に微増し、連鎖評価の信頼性を確保。
# これによりCHAIN_MERGE選択率を15%以上に引き上げ、HEIGHT_CONTROL選択率を削減してスコア安定性を向上させる。
# v159: 着地高動的拡大版 - v158の密度評価版（chain_distance=4.5固定）をベースに、
# 着地高に応じてchain_distanceを動的に拡大（chain_distance = 4.5 + landing_y * 0.4）することで、
# HIGH_LAYER/MEDIUM_TOWER状況でのCHAIN_MERGE選択を促進し、HEIGHT_CONTROLの選択率を減らしてスコア安定性を向上させる。
# v157の動的調整はchain_distance_maxとchain_bonus_multiplierの双方を強化しすぎて失敗したが、
# v159はchain_distanceの動的拡大のみを行い、chain_bonus_multiplierは400.0固定にすることで、
# より安定したCHAIN_MERGE選択率向上を目指す。

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v159: 着地高動的拡大版

    v158の密度評価版（chain_distance=4.5固定）をベースに、
    着地高に応じてchain_distanceを動的に拡大（chain_distance = 4.5 + landing_y * 0.4）することで、
    HIGH_LAYER/MEDIUM_TOWER状況でのCHAIN_MERGE選択を促進し、HEIGHT_CONTROLの選択率を減らしてスコア安定性を向上させる。
    v157の動的調整はchain_distance_maxとchain_bonus_multiplierの双方を強化しすぎて失敗したが、
    v159はchain_distanceの動的拡大のみを行い、chain_bonus_multiplierは400.0固定にすることで、
    より安定したCHAIN_MERGE選択率向上を目指す。

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
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    #  score each drop candidate (x coordinate) with 6 evaluation axes
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
        # v158: height_multiplier 30.0->35.0微増、連鎖評価の信頼性を確保
        # additional multiplier if HIGH/MEDIUM landing high (>0.5)
        height_penalty = landing_y * 35.0 * height_mult

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

        # ----- evaluation axis 4: left-right balance correction (v148: enhanced) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
        # v148: higher board increases balance_strength, strictens balance control
        balance_strength = 20.0
        if phase == "HIGH":
            balance_strength = 50.0  # v148: HIGH balance control even stricter (40.0->50.0)
        elif phase == "MEDIUM":
            balance_strength = 35.0  # v148: MEDIUM also strengthen balance control (30.0->35.0)

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

        # ----- evaluation axis 6: chain merge bonus (v159: 着地高動的拡大版) -----
        # v159: v158の密度評価版（chain_distance=4.5固定）をベースに、
        # 着地高に応じてchain_distanceを動的に拡大（chain_distance = 4.5 + landing_y * 0.4）することで、
        # HIGH_LAYER/MEDIUM_TOWER状況でのCHAIN_MERGE選択を促進し、HEIGHT_CONTROLの選択率を減らす。
        # v157の動的調整はchain_distance_maxとchain_bonus_multiplierの双方を強化しすぎて失敗したが、
        # v159はchain_distanceの動的拡大のみを行い、chain_bonus_multiplierは400.0固定にすることで、
        # より安定したCHAIN_MERGE選択率向上を目指す。
        # 評価方法：
        # - chain_distanceを着地高に応じて動的に拡大 (4.5 + landing_y * 0.4)
        # - 併合ターゲットからchain_distance以内のmerged_typeピースを全て収集
        # - 最も近い3つのピースからボーナス計算 (chain_distance - dist) * chain_bonus_multiplier
        # - chain_bonus_multiplierは400.0固定（v157失敗の教訓）
        # 効果：着地高が高いほど遠くの連鎖も評価し、CHAIN_MERGE選択率を向上してHEIGHT_CONTROL選択率を減らす。
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v159: 着地高に応じてchain_distanceを動的に拡大
                # 着地高が高いほど遠くの連鎖も評価し、CHAIN_MERGE選択率を向上
                chain_distance = 4.5 + landing_y * 0.4

                # chain_bonus_multiplierは400.0固定（v157失敗の教訓）
                chain_bonus_multiplier = 400.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance:
                            nearby_pieces.append((dist, p))

                # sort by distance
                nearby_pieces.sort(key=lambda x: x[0])

                # bonus calculation from closest 3 pieces using density evaluation
                # 1st: (chain_distance - dist) * chain_bonus_multiplier
                # 2nd: (chain_distance - dist) * chain_bonus_multiplier * 0.5
                # 3rd: (chain_distance - dist) * chain_bonus_multiplier * 0.25
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[0]
                    chain_bonus = (chain_distance - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[1]
                    chain_bonus = (chain_distance - dist) * chain_bonus_multiplier * 0.5
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (chain_distance - dist) * chain_bonus_multiplier * 0.25
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

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

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
   6. Chain merge bonus - Evaluate possibility of further merges after merge (v158: dynamic adjustment with reduced height penalty)
   7. NextNext chain bonus - Evaluate possibility of nextNext merges after current merge (NEW)

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
# v151-v155: CHAIN_MERGE enhanced versions (coefficients 200.0->300.0->400.0, chain_distance 3.0->3.5->4.0->4.5->5.0)
# v154: Merge target density evaluation - Fixed issue with existing CHAIN_MERGE logic
# v155: Dynamic parameter adjustment version - chain_distance=5.0, chain_bonus=450.0 fixed, but uses dynamic adjustment logic
# v157: Revert to v153 settings - height_multiplier 40.0->50.0, lost v155's dynamic adjustment
# v158: Restore v155 dynamic adjustment + reduce height penalty - Reintroduce v155's dynamic adjustment logic (chain_distance_max=5.0+landing_y*0.6, chain_bonus_multiplier=450.0+landing_y*150.0), and reduce height_multiplier from 50.0 to 45.0 to promote merge opportunities. This balances HEIGHT_CONTROL (27.3% selection rate with low avg_score_delta=1.6) and CHAIN_MERGE (8.2% selection rate with high avg_score_delta=29.7) by lowering height penalty while keeping dynamic chain merge bonuses.
# v159: nextNext 2手先評価軸追加版 - batch_summary/adviceで「A上にBを置くとnextNextの併合を逃す」問題に対処。
# nextNextが現在nextと同じtypeの場合、「現在併合→nextNextで更に併合」の2連鎖を評価する評価軸を追加。
# これにより、盤面A・nextB・nextNextAの状況でA上にBを置くとnextNextの併合を逃す問題に構造的に対処し、CHAIN_MERGE選択を促進する。
# v160: MEDIUMフェーズHEIGHT_CONTROL抑制版 - batch_summaryで低スコア群がHEIGHT_CONTROLを34.3%選択していることを確認（高スコア群は24.3%）。
# MEDIUMフェーズでheight_multiplierを45.0→30.0に削減し、HEIGHT_CONTROL過剰選択を抑制して併合選択を促進することでスコア安定性を向上させる。
# refs: tmp/batch_summary.txt, tmp/advice.md, game_history/20260307_194847_score0553.jsonl, game_history/20260307_202436_score3912.jsonl, strategy_versions/best_score4999_strategy.py, strategy_versions/best_score5310_strategy.py, analyze_board.py

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v161: reactor情報活用併用版

    batch_summaryで低スコア群がHEIGHT_CONTROLを37.1%選択していることを確認（高スコア群は22.2%）。
    reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
    これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を構造的に抑制しスコア安定性を向上させる。
    MEDIUMフェーズのheight_multiplierを30.0→20.0に削減し、マージ選択を促進。

    v159から継承:
    - nextNext 2手先評価軸（盤面A・nextB・nextNextAの状況でA上にBを置くとnextNextの併合を逃す問題に対処）

    v160から継承:
    - MEDIUMフェーズheight_multiplier削減（30.0→20.0）

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

    # --- phase judgment (v42 thresholds, v155 values) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 1.0  # low board weak height penalty
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.8  # v155: height_mult relaxation for merge opportunities
        merge_mult = 1.0
        # v161: MEDIUMフェーズheight_multiplier削減版 - batch_summaryで低スコア群がHEIGHT_CONTROLを37.1%選択していることを確認
        # height_multiplierを30.0→20.0に削減し、reactive_pairs活用と合わせてHEIGHT_CONTROL選択を抑制し、CHAIN_MERGE選択を促進
        # これにより、高スコア群のHEIGHT_CONTROL選択率(22.2%)に近づけ、スコア安定性を向上させる
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # v155: HIGH relaxation to ensure merge opportunity
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

    # --- reactor information (v176/v177: reactive_pairs-based merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pair_count = len(reactor.get("reactive_pairs", []))
    has_many_merge_opps = reactive_pair_count >= 2

    # --- Type-specific merge bonus calculation ---
    # merge result type (next_type+1) higher means higher score value
    # example: type1 merge -> bonus=330, type5 merge -> bonus=510, type14 merge -> bonus=1660
    merge_result_type = min(next_type + 1, 16)
    type_merge_bonus = SCORE_TABLE.get(merge_result_type, 10) * 10 + 300

    # --- v149: pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

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
        # v161: reactor情報活用併用版 - reactive_pairs >= 2の場合、マージを優先しheight_multiplierを抑制
        # batch_summaryで低スコア群がHEIGHT_CONTROLを37.1%選択していることを確認（高スコア群は22.2%）
        # additional multiplier if HIGH/MEDIUM landing high (>0.5)
        height_multiplier = 45.0
        if phase == "MEDIUM":
            height_multiplier = 20.0  # v161: MEDIUMフェーズheight_multiplier削減（30.0→20.0）
        # v161: reactor_pairs >= 2の場合、height_multiplierをさらに抑制しマージ選択を促進
        if has_many_merge_opps:
            height_multiplier = height_multiplier * 0.7  # 30%削減（MEDIUM: 14.0, HIGH: 12.6, LOW/CRITICAL: 基本値）
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

        # ----- evaluation axis 6: chain merge bonus (v158: dynamic adjustment restored from v155) -----
        # v158: Reintroduce v155's dynamic adjustment logic with reduced height penalty
        # chain_distance_max = 5.0 + landing_y * 0.6 (expands as landing_y increases)
        # chain_bonus_multiplier = 450.0 + landing_y * 150.0 (bonus increases as landing_y increases)
        # This promotes CHAIN_MERGE in HIGH_LAYER situations and reduces HEIGHT_CONTROL selection
        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v158: Dynamic adjustment - chain_distance_max and chain_bonus_multiplier expand as landing_y increases
                # Example: landing_y=0.0 -> distance_max=5.0, multiplier=450.0
                # Example: landing_y=1.0 -> distance_max=5.6, multiplier=600.0
                # Example: landing_y=2.0 -> distance_max=6.2, multiplier=750.0
                # Example: landing_y=3.0 -> distance_max=6.8, multiplier=900.0
                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance
                nearby_pieces.sort(key=lambda x: x[0])

                # bonus calculation from closest 3 pieces
                # 1st: (chain_distance_max - dist) * chain_bonus_multiplier
                # 2nd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                # 3rd: (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
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

        # ----- evaluation axis 7: reactive merge priority (v161: NEW) -----
        # v161: reactor情報活用による併合優先評価軸追加版
        # batch_summaryでHEIGHT_CONTROLが27.5%選択(avg_score_delta=2.4)と依然として過剰であることを確認
        # 高スコア群(22.2%)と低スコア群(37.1%)の比較で、低スコア群が14.9%も多くHEIGHT_CONTROLを選択していることを特定
        # reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加
        # これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を構造的に抑制しスコア安定性を向上させる
        if has_many_merge_opps and merge_grade in ["DIRECT", "NEAR"]:
            # reactive_pairs >= 2の場合、マージを強力に優先するボーナス
            reactive_merge_bonus = 500.0
            score += reactive_merge_bonus
            reasons.append("REACTIVE_MERGE")

        # ----- evaluation axis 8: nextNext chain bonus (v159: v161: 軴用) -----
        # v159: nextNextが現在nextと同じtypeの場合、2連鎖評価を追加し「現在併合→nextNext併合」を促進。
        # 盤面A・nextB・nextNextAの状況でA上にBを置くとnextNextの併合を逃す問題に構造的に対処。
        if next_next_type == next_type and merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # type after merge (merged_type + 1) will be merged with nextNext
                # nextNext will try to merge with pieces of type (merged_type + 1)
                next_next_merge_type = min(merged_type + 1, 16)

                # collect all next_next_merge_type pieces near the merge target
                # use a moderate search distance for 2-step chain evaluation
                next_next_chain_distance = 4.0
                next_next_nearby_pieces = []
                for p in pieces:
                    if p.get("type") == next_next_merge_type:
                        dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                        if dist < next_next_chain_distance:
                            next_next_nearby_pieces.append((dist, p))

                # sort by distance
                next_next_nearby_pieces.sort(key=lambda x: x[0])

                # bonus for 2-step chain merge - if nextNext can merge after current merge
                # give bonus to promote current merge placement that enables nextNext merge
                if len(next_next_nearby_pieces) >= 1:
                    dist, _ = next_next_nearby_pieces[0]
                    # bonus based on distance - closer nextNext merge target = higher bonus
                    next_next_bonus = (next_next_chain_distance - dist) * 400.0
                    score += next_next_bonus
                    reasons.append("NEXTNEXT_CHAIN")

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

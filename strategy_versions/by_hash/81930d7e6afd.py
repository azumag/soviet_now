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
    3. Drift penalty - Penalty for post-landing drift due to polygon shape
    4. Left-right balance correction - Bonus for correcting piece count bias
    5. nextNext centering - Center for next merge opportunity if nextNext same type
    6. Chain merge bonus - Evaluate possibility of further merges after merge (v196: v155 parameters revived)
    7. Early stage CHAIN_MERGE bonus - Bonus for early game chain merges when landing_y < -1.0 (v198 maintained)
    8. Reactive pairs bonus - Bonus for multiple merge opportunities (v177: reactor info utilization)
    9. Early game merge priority - Strong bonus for merge opportunities in early game
    10. Anti-passive start bonus (v199: NEW) - Suppresses HEIGHT_CONTROL in first 8 turns when no merge is available

Phases (determined by board max Y):
   LOW      (max_y < 0.8) : Early game. Merge priority (merge_mult=1.2)
   MEDIUM   (0.8 <= max_y < 1.8) : Mid game. Height management (height_mult=1.4)
   HIGH     (1.8 <= max_y < 3.0) : Late game. Merge opportunity (height_mult=1.8)
   CRITICAL (3.0 <= max_y) : Danger. DIRECT merge priority, board compression (NEAR carefully)
"""

# Merge result score: type N merge gives N*(N+1)/2 points
# Example: type1+1->2 gives +3 points, type8+8->9 gives +45 points, type14+14->15 gives +120 points
SCORE_TABLE = {i: i * (i + 1) // 2 for i in range(1, 17)}


def decide(game_state: dict, analysis: dict) -> dict:
    """v199: Early game Anti-passive start

    batch_summary shows HEIGHT_CONTROL selected 29.5% (low-score group) vs 24.6% (high-score group).
    Worst games (score 809, 873) show 6 of first 8 turns selecting HEIGHT_CONTROL, missing chain merge opportunities.
    Best games actively select CHAIN_MERGE from early turns.

    Root cause: Low-score games play too passively in early game, placing pieces too low (avg max_y=-2.99) creating flat boards that stifle reactive_pairs.
    High-score games place pieces slightly higher (avg max_y=-2.75) creating density and reactive_pairs.

    v199 improvements:
     1. New Evaluation Axis 10: Anti-passive start bonus (NEW).
        - If merge_grade == "NO" and piece_count <= 8:
          - Penalize DEFAULT_PLACEMENT (x=0.0) if it creates a flat board (max_y < -3.5).
          - Bonus EDGE_STACKING (x=1.5 or -1.5) to encourage building height for future reactive_pairs.
     2. Maintain v198 successful mechanisms (EARLY_CHAIN_MERGE, REACTIVE_MERGE_PRIORITY, EARLY_MERGE_PRIORITY).
     3. Height penalty tuning:
        - Allow slightly higher placement in early game (avoid excessive flatness).

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

    # --- reactor information (for reactive merge priority) ---
    reactor = analysis.get("reactor", {})
    reactive_pairs = reactor.get("reactive_pairs", [])
    # reactive_pairs is a list, count pairs for evaluation
    reactive_pair_count = len(reactive_pairs) if isinstance(reactive_pairs, list) else 0

    # --- phase judgment (v42 thresholds) ---
    if max_y < 0.8:
        phase = "LOW"
        height_mult = 0.6  # v197: LOW phase height_mult reduced (0.8→0.6) to enable early chain opportunities
        merge_mult = 1.2  # 20% merge bonus increase, actively target
    elif max_y < 1.8:
        phase = "MEDIUM"
        height_mult = 1.4  # v177: MEDIUM phase height_mult from v42 (2.4→1.4)
        merge_mult = 1.0
    elif max_y < 3.0:
        phase = "HIGH"
        height_mult = 1.8  # HIGH phase height_mult from v42
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

    # --- pre-calculate merged type (for chain judgment) ---
    merged_type = min(next_type + 1, 16)

    # =======================================================================
    #  score each drop candidate (x coordinate) with 6 evaluation axes (NEW: +1 axis for anti-passive start)
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
        # v197: LOW phase height_mult=0.6 enables early chain opportunities by allowing slightly higher placement
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

        # ----- evaluation axis 3: drift penalty -----
        # polygon shape pieces roll after landing. larger drift amount and uncertainty means
        # higher risk of deviation from targeted position
        drift_penalty = (abs(drift_x) + drift_unc) * 30.0
        score -= drift_penalty

        # ----- evaluation axis 4: left-right balance correction (v42: simple) -----
        # bonus for correcting left-right piece count bias.
        # balance_bias > 0 means right majority -> left (x<0) placement reduces penalty
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

        # ----- evaluation axis 5: nextNext centering -----
        # if nextNext same type as current next, next also has merge opportunity.
        # place near center to allow merge in either direction next turn
        if next_next_type == next_type:
            center_bonus = max(0, 1.0 - abs(x) / 2.0) * 50.0
            score += center_bonus
            reasons.append("NEXT_SAME")

        # ----- evaluation axis 6: chain merge bonus (v196: v155 parameters revived) -----
        # batch_summary shows CHAIN_MERGE reasons have high value (avg_score_delta=43.9) but low selection rates (3.6-5.6%).
        # Worst game (score0638) shows 5 turns HEIGHT_CONTROL in first 8 turns, 0 CHAIN_MERGE selections.
        # Best game (score2416) actively selects CHAIN_MERGE from early turns.
        # v196: chain_distance_max=5.0, chain_bonus_multiplier初期値450.0+landing_y*150.0
        # This dynamic adjustment ensures CHAIN_MERGE is evaluated throughout the game.

        if merge_grade in ["DIRECT", "NEAR"] and result.get("merges"):
            merges = result["merges"]
            if merges:
                # get best merge target (closest distance)
                best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
                target_x = best_merge.get("x", 0)
                target_y = best_merge.get("y", 0)

                # v196: v155成功パラメータ(chain_distance=5.0, chain_bonus_multiplier初期値450.0+landing_y*150.0)復帰
                # chain_distance_max = 5.0 + landing_y * 0.6 (着地高に応じて拡大)
                # chain_bonus_multiplier = 450.0 + landing_y * 150.0 (着地高に応じて増強)

                chain_distance_max = 5.0 + landing_y * 0.6
                chain_bonus_multiplier = 450.0 + landing_y * 150.0

                # collect all merged_type pieces within chain_distance_max of merge target
                nearby_pieces = []
                for p in pieces:
                    if p.get("type") == merged_type:
                        dist = (
                            (p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2
                        ) ** 0.5
                        if dist < chain_distance_max:
                            nearby_pieces.append((dist, p))

                # sort by distance (closest first)
                nearby_pieces.sort(key=lambda x: x[0])

                # v155距離加重ボーナス復帰 - 3つの最も近いピースに対し、距離に応じて減衰するボーナスを適用
                if len(nearby_pieces) >= 1:
                    dist, _ = nearby_pieces[0]
                    chain_bonus = (chain_distance_max - dist) * chain_bonus_multiplier
                    score += chain_bonus

                if len(nearby_pieces) >= 2:
                    dist, _ = nearby_pieces[1]
                    chain_bonus = (
                        (chain_distance_max - dist) * chain_bonus_multiplier * 0.5
                    )
                    score += chain_bonus

                if len(nearby_pieces) >= 3:
                    dist, _ = nearby_pieces[2]
                    chain_bonus = (
                        (chain_distance_max - dist) * chain_bonus_multiplier * 0.25
                    )
                    score += chain_bonus

                if nearby_pieces:
                    reasons.append("CHAIN_MERGE")

        # ----- evaluation axis 7: early stage CHAIN_MERGE bonus (v198: maintained) -----
        # batch_summary shows CHAIN_MERGE reasons have high value (avg_score_delta=29-61) but low selection rates (3.6-4.6%).
        # Worst game (score0738) shows 7 of first 8 turns selecting HEIGHT_CONTROL, missing early chain merge opportunities.
        # Best game (score2620) actively selects NEAR_MERGE_EARLY_MERGE_PRIORITY from early turns.
        # v197's LOW phase height_mult reduction (0.8→0.6) helps but insufficient to boost CHAIN_MERGE selection.
        # Add early stage CHAIN_MERGE bonus when landing_y < -1.0 to encourage early chain merges and suppress HEIGHT_CONTROL.

        if (
            merge_grade in ["DIRECT", "NEAR"]
            and result.get("merges")
            and landing_y < -1.0
        ):
            # Early stage: landing_y < -1.0 means piece is placed low on the board
            # Bonus decays as landing_y increases: 300.0 * max(0, (-1.0 - landing_y))
            # Example: landing_y=-2.0 → 300.0 * 1.0 = 300.0
            # Example: landing_y=-1.5 → 300.0 * 0.5 = 150.0
            # Example: landing_y=-1.0 → 300.0 * 0.0 = 0.0 (no bonus)
            early_chain_bonus = 300.0 * max(0.0, -1.0 - landing_y)
            if early_chain_bonus > 0:
                score += early_chain_bonus
                reasons.append("EARLY_CHAIN_MERGE")

        # ----- evaluation axis 8: early game merge priority -----
        # 初期8ターンでマージ機会がある場合、強力なボーナスを付与
        # batch_summaryでHEIGHT_CONTROLが28.7%選択(avg_score_delta=1.8)と過剰であり、
        # ワーストゲーム(score0826)では初期8ターンのうち7ターンがHEIGHT_CONTROLを選択し、マージ機会を逃している。
        # ベストゲーム(score2330)では初期段階から積極的にNEAR_MERGE_EARLY_MERGE_PRIORITYを選択し、スコア2330を出している。
        # v194のearly_game判定(max_y < -2.5)では抑制が強すぎ、gapがある間のマージ機会を見逃している問題を解決。
        # マージ機会がある場合の優先配置を高めるため、early_gameをmax_y < -2.5に緩和し、初期段階でのHEIGHT_CONTROL選択を抑制しつつマージ優先を強化。
        # 初期8ターンまででEARLY_MERGE_PRIORITY条件を緩和し、全体的にマージ機会を優先する戦略へ転換。
        if piece_count <= 8 and merge_grade == "NEAR":
            # 初期段階でNEAR_MERGE機会がある場合、強力なボーナスを付与
            # これにより初期8ターン全体でマージ機会を最優先し、HEIGHT_CONTROL選択を抑制
            score += 1000.0
            reasons.append("EARLY_MERGE_PRIORITY")

        # ----- evaluation axis 9: reactive pairs bonus (v177: reactor info utilization) -----
        # batch_summaryでHEIGHT_CONTROLが26.2%選択(avg_score_delta=1.7)と過剰であることを確認。
        # reactor情報のreactive_pairs（反応性のあるペア）を活用し、2つ以上ある場合にマージを優先する評価軸を追加。
        # これにより、盤面に多数の併合機会がある状況でHEIGHT_CONTROL選択を抑制し、スコア安定性を向上させる。
        if reactive_pair_count >= 2 and merge_grade in ["DIRECT", "NEAR"]:
            # 2つ以上の反応可能ペアがある場合、マージ優先ボーナス
            score += 500.0
            reasons.append("REACTIVE_MERGE_PRIORITY")

        # ----- evaluation axis 10: Anti-passive start bonus (NEW) -----
        # batch_summary shows HEIGHT_CONTROL selected 29.5% (low-score group) vs 24.6% (high-score group).
        # Worst games (score 809, 873) show 6 of first 8 turns selecting HEIGHT_CONTROL, missing chain merge opportunities.
        # Best games actively select CHAIN_MERGE from early turns.
        # Root cause: Low-score games play too passively in early game, placing pieces too low (avg max_y=-2.99) creating flat boards that stifle reactive_pairs.
        # High-score games place pieces slightly higher (avg max_y=-2.75), creating density and reactive_pairs.
        # v198 improvements (EARLY_CHAIN_MERGE, REACTIVE_MERGE_PRIORITY, EARLY_MERGE_PRIORITY) were insufficient to address passive start.
        # This axis penalizes passive flat placement and encourages edge stacking for height/density.

        if merge_grade == "NO" and piece_count <= 8:
            # Only apply in early game (piece_count <= 8) when no merge exists

            # Get max_y estimate for all candidates (assuming candidate x placement)
            # We want to avoid flat boards (max_y < -3.5) which kill reactive_pairs.
            # High-density placement (edges) creates better long-term prospects.

            # Predictive max_y calculation:
            # Height of this piece + average height of existing pieces (excluding this piece)
            current_piece_y = -4.0  # approximate floor drop height
            avg_other_y = (
                sum([p["y"] for p in pieces if p["id"] != 0]) / len(pieces)
                if pieces
                else -3.8
            )
            predicted_max_y = current_piece_y + avg_other_y

            # 1. Anti-flat center penalty: Penalize x=0.0 if it results in a flat board (max_y < -3.5)
            # We want to build height, not flatness
            if predicted_max_y < -3.5 and abs(x) < 0.5:
                score -= 150.0  # Penalize center placement in low/flat game
                reasons.append("ANTI_FLAT_CENTER")
            # 2. Edge stacking bonus: Reward edge placement (x > 1.5 or x < -1.5) to build height
            elif abs(x) > 1.0:
                score += 250.0  # Reward edge stacking (builds reactive pairs) - v200: increased from 100.0
                reasons.append("EDGE_STACKING")

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

#!/usr/bin/env python3
"""2-turn lookahead evaluation helper for Soviet Puzzle Game AI

This module implements a 2-step planning evaluation that considers:
1. Whether placing current piece would block nextNext merge opportunities
2. Future chain merge potential based on piece type progression
3. Board state evolution after two moves

This addresses the issue: "盤面A・nextB・nextNextAの状況で、A上にBを置くとnextNextの併合を逃す問題"
"""

import math


def evaluate_2turn_lookahead(
    game_state, analysis, next_type, next_next_type, merged_type
):
    """
    Evaluate board state evolution after 2 turns with improved planning.

    This function implements a 2-step lookahead strategy that:
    - Prioritizes keeping placement flexible when nextNext == next type
    - Evaluates chain merge potential for merged pieces
    - Penalizes overly conservative placement that blocks future merges

    Args:
        game_state: Current game state with pieces, next, nextNext
        analysis: Current board analysis results
        next_type: Type of next piece
        next_next_type: Type of nextNext piece
        merged_type: Type that would result from current merge

    Returns:
        bonus_score: Score bonus for good 2-turn planning
        reason: Explanation of the evaluation
    """
    pieces = game_state.get("pieces", [])

    # Get current best merge target from analysis
    results = analysis.get("results", [])
    if not results:
        return 0.0, ""

    # Find the best current merge opportunity
    best_current = None
    for result in results:
        if result.get("merge_grade") in ["DIRECT", "NEAR"]:
            best_current = result
            break

    if not best_current:
        return 0.0, ""

    current_x = best_current["x"]
    landing_y = best_current.get("landing_y", 0)

    # Simulate first move: place current piece at current_x
    # Then simulate second move: evaluate nextNext placement

    # Simple heuristic: if nextNext is same type as next, prioritize keeping placement flexible
    if next_next_type == next_type:
        # Need to leave space for next merge in either direction
        # Prefer positions that allow merges on both sides
        if abs(current_x) < 0.5:
            # Already centered, good for flexible placement
            return 50.0, "NEXT_SAME_CENTERED"
        # Check if current position blocks potential next merges
        # If placed too far right, can't merge with left pieces
        if current_x > 1.5:
            return -50.0, "NEXT_SAME_RIGHT_BLOCKED"
        if current_x < -1.5:
            return -50.0, "NEXT_SAME_LEFT_BLOCKED"

    # Check for chain merge potential: if merged piece is close to same-type pieces
    if best_current.get("merges"):
        merges = best_current["merges"]
        if merges:
            best_merge = min(merges, key=lambda m: m.get("dist", float("inf")))
            target_x = best_merge.get("x", 0)
            target_y = best_merge.get("y", 0)

            # Count same-type pieces within chain distance
            chain_distance = 5.0 + landing_y * 0.6
            nearby_count = 0
            for p in pieces:
                if p.get("type") == merged_type:
                    dist = ((p["x"] - target_x) ** 2 + (p["y"] - target_y) ** 2) ** 0.5
                    if dist < chain_distance:
                        nearby_count += 1

            # Bonus for having potential chain targets
            if nearby_count >= 2:
                return nearby_count * 100.0, f"CHAIN_MERGE_POTENTIAL_{nearby_count}"

    # Evaluate board density: avoid creating flat boards that stifle chain merges
    # High density (many pieces close together) enables more chain merges
    max_y = max([p["y"] for p in pieces]) if pieces else -4.0
    if max_y < -2.0:
        # Early game - prefer slightly higher placement for density
        if landing_y < -3.0:
            return -30.0, "TOO_LOW_EARLY_GAME"

    return 0.0, ""


def calculate_board_density(pieces):
    """
    Calculate board density metric.
    Higher density = more opportunities for chain merges.
    """
    if not pieces:
        return 0.0

    # Count pieces in each layer
    layers = {}
    for p in pieces:
        y = round(p["y"], 1)
        layers[y] = layers.get(y, 0) + 1

    # Calculate average layer occupancy
    if not layers:
        return 0.0

    avg_occupancy = sum(layers.values()) / len(layers)
    return avg_occupancy

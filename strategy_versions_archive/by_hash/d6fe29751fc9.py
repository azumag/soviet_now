#!/usr/bin/env python3
"""
Soren Game Strategy - AI decision-making for drop position.

This file contains the core AI logic for determining the best column to drop
the current piece in the Soviet puzzle game.
"""

def find_best_drop_position(game_state, analysis):
    """
    Find the best column to drop the current piece based on game state and analysis.

    Args:
        game_state: Dict containing board state and current piece info
        analysis: Dict containing board analysis (drop predictions, reactor state, etc.)

    Returns:
        Tuple[int, str]: (best_column, reason) or (None, reason) if no valid move
    """
    board = game_state.get('board', [])
    current_piece = game_state.get('current_piece', {})
    cols = len(board[0]) if board else 0
    rows = len(board)

    if cols == 0:
        return None, "No valid columns - empty board"

    # Get piece width (for large pieces, need to ensure landing is valid)
    piece_width = len(current_piece.get('pattern', [[1]])) if current_piece else 1

    best_col = None
    best_score = float('-inf')
    best_reason = ""

    for col in range(cols - piece_width + 1):
        # Skip if this column can't accommodate the piece width
        if analysis and 'drop_predictions' in analysis:
            pred = analysis['drop_predictions'].get(col, {})
            if pred.get('can_drop', False):
                row = pred.get('landing_row', rows - 1)
            else:
                continue
        else:
            # Fallback: find landing position by counting empty cells from bottom
            row = rows - 1
            while row >= 0 and board[row][col] == 0:
                row -= 1
            row += 1  # Land on top of first non-empty cell (or bottom if empty)

        # Calculate score for this position
        score = 0
        reasons = []

        # Factor 1: Chain reaction potential (higher is better)
        if analysis and 'mergable_cells' in analysis:
            mergable = analysis['mergable_cells']
            mergable_in_col = sum(1 for r, c in mergable if c == col)
            score += mergable_in_col * 10
            if mergable_in_col > 0:
                reasons.append(f"mergable={mergable_in_col}")

        # Factor 2: Fill lower cells first (prioritize bottom)
        score -= row  # Lower rows (smaller row index) get higher score
        reasons.append(f"row={row}")

        # Factor 3: Balance board heights
        col_heights = []
        for c in range(cols):
            h = 0
            for r in range(rows):
                if board[r][c] != 0:
                    h = rows - r
                    break
            col_heights.append(h)
        avg_height = sum(col_heights) / len(col_heights) if col_heights else 0
        new_height = max(row + 1, col_heights[col] if col < len(col_heights) else 0)
        height_penalty = abs(new_height - avg_height)
        score -= height_penalty
        reasons.append(f"height={new_height}")

        # Factor 4: Reactor readiness (if near completion)
        if analysis and 'reactor' in analysis:
            reactor = analysis['reactor']
            if reactor.get('is_ready', False):
                # Slight bonus to drop in columns that help activate reactor
                # This is a heuristic; actual optimal would depend on reactor position
                score += 5
                reasons.append("reactor_ready")

        # Factor 5: Avoid creating holes (check cells above landing position)
        hole_penalty = 0
        for r in range(row - 1, -1, -1):
            if board[r][col] == 0:
                # Check if there are blocks above this empty cell
                has_blocks_above = any(board[r2][col] != 0 for r2 in range(r))
                if has_blocks_above:
                    hole_penalty += 1
        score -= hole_penalty * 3
        if hole_penalty > 0:
            reasons.append(f"holes={hole_penalty}")

        reason = ", ".join(reasons)
        if score > best_score:
            best_score = score
            best_col = col
            best_reason = reason

    if best_col is None:
        return None, "No valid drop position found"

    return best_col, f"score={best_score:.1f}, {best_reason}"


def decide(game_state, analysis):
    """
    Decide which column to drop the current piece.

    Args:
        game_state: Dict containing board state and current piece info
            - board: 2D array of cell values (0 = empty)
            - current_piece: Dict with piece info (pattern, type, etc.)
        analysis: Dict containing board analysis
            - drop_predictions: Dict mapping columns to landing predictions
            - mergable_cells: List of (row, col) tuples that can merge
            - reactor: Dict with reactor state (is_ready, type, etc.)

    Returns:
        Dict with format: {"x": column_number, "reason": "explanation string"}
        column_number: 0-indexed column to drop piece into
        reason: Human-readable explanation of the decision
    """
    # Find best drop position using helper function
    col, reason = find_best_drop_position(game_state, analysis)

    if col is None:
        # Fallback: drop in center column if no valid position found
        cols = len(game_state.get('board', [[]])) if game_state.get('board') else 0
        fallback_col = cols // 2 if cols > 0 else 0
        return {
            "x": float(fallback_col),
            "reason": f"Fallback to center column (no valid position): {reason}"
        }

    return {
        "x": float(col),
        "reason": reason
    }


# --- AI modification prohibited zone ---
if __name__ == "__main__":
    import json
    import sys

    # standalone test
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"

    try:
        with open(gs_path) as f:
            game_state = json.load(f)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

    # Create minimal analysis if not provided
    analysis = game_state.get("analysis", {})

    try:
        result = decide(game_state, analysis)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)

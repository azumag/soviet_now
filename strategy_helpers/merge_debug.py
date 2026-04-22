"""merge_debug.py - Debug logging for merge availability mismatch detection

Used to diagnose: "merge_available=true but non-merge candidate selected"
worst game T41-T44: merge_available=true, best_merge_grade="NEAR" but
reason shows HIGH_LAYER/NO_MERGE — contradiction that needs investigation.
"""

import json
import os
import time
from pathlib import Path


def log_merge_mismatch(state_name, turn_num, candidate_count, merge_candidates,
                       selected_merge_grade, global_merge_available, best_x,
                       best_reason, max_y, reactive_pair_count, deadline_crossed,
                       score):
    """Log merge availability mismatch for debugging.

    Args:
        state_name: identifier for this game state snapshot
        turn_num: turn number if available, else 0
        candidate_count: total number of drop candidates evaluated
        merge_candidates: list of (x, merge_grade, score) for candidates with merge opportunity
        selected_merge_grade: merge_grade of the selected (best) candidate
        global_merge_available: True if any candidate has merge opportunity
        best_x: x coordinate of selected candidate
        best_reason: reason string of selected candidate
        max_y: current max_y of the board
        reactive_pair_count: count of reactive pairs
        deadline_crossed: whether deadline is crossed
        score: score at this point (0 if mid-game)
    """
    # Only log when there's a mismatch: merge available but non-merge selected
    if global_merge_available and selected_merge_grade == "NO":
        log_path = os.environ.get("MERGE_DEBUG_LOG", "tmp/merge_debug.log")
        log_dir = str(Path(log_path).parent)
        if log_dir and log_dir != ".":
            os.makedirs(log_dir, exist_ok=True)

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        lines = []
        lines.append(f"=== MERGE MISMATCH DETECTED at {timestamp} ===")
        lines.append(f"  state={state_name} turn={turn_num} score={score}")
        lines.append(f"  board: max_y={max_y}, deadline_crossed={deadline_crossed}, rp={reactive_pair_count}")
        lines.append(f"  candidates: {candidate_count} total")
        lines.append(f"  merge_candidates: {len(merge_candidates)}")
        for x, mg, sc in merge_candidates:
            lines.append(f"    x={x:.2f} merge_grade={mg} score={sc:.1f}")
        lines.append(f"  selected: x={best_x:.2f} merge_grade={selected_merge_grade} reason={best_reason}")
        lines.append(f"  global_merge_available={global_merge_available} but NO_MERGE selected")
        lines.append("")

        with open(log_path, "a") as f:
            f.write("\n".join(lines))


def log_merge_selection(state_name, turn_num, candidate_count, merge_available_count,
                        selected_x, selected_merge_grade, selected_reason,
                        best_score, global_merge_available, max_y, piece_count):
    """Log merge selection decision for debugging.

    Args:
        state_name: identifier for this game state snapshot
        turn_num: turn number if available, else 0
        candidate_count: total number of drop candidates evaluated
        merge_available_count: number of candidates with merge opportunity
        selected_x: x coordinate of selected candidate
        selected_merge_grade: merge_grade of the selected (best) candidate
        selected_reason: reason string of selected candidate
        best_score: score of the selected candidate
        global_merge_available: True if any candidate has merge opportunity
        max_y: current max_y of the board
        piece_count: number of pieces on board
    """
    log_path = os.environ.get("MERGE_DEBUG_LOG", "tmp/merge_debug.log")
    log_dir = str(Path(log_path).parent)
    if log_dir and log_dir != ".":
        os.makedirs(log_dir, exist_ok=True)

    # Log when merge candidates exist but a non-merge was selected with high score
    if merge_available_count > 0 and selected_merge_grade == "NO":
        # Check if any merge candidate had a positive score
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(log_path, "a") as f:
            f.write(f"=== MERGE BYPASS at {timestamp} ===\n")
            f.write(f"  state={state_name} turn={turn_num} max_y={max_y} pc={piece_count}\n")
            f.write(f"  merge_available={merge_available_count}/{candidate_count} candidates\n")
            f.write(f"  selected: x={selected_x:.2f} grade={selected_merge_grade} reason={selected_reason}\n")
            f.write(f"  global_merge_available={global_merge_available} best_score={best_score:.1f}\n")
            f.write("  [MERGE CANDIDATES BYPASSED]\n\n")


def dump_candidates_for_merge_debug(state_name, turn_num, results, best_x,
                                    best_reason, best_score, global_merge_available):
    """Dump all candidates when merge mismatch is suspected.

    Args:
        state_name: identifier for this game state snapshot
        turn_num: turn number if available, else 0
        results: list of candidate result dicts
        best_x: x coordinate of selected candidate
        best_reason: reason string of selected candidate
        best_score: score of the selected candidate
        global_merge_available: True if any candidate has merge opportunity
    """
    log_path = os.environ.get("MERGE_DEBUG_LOG", "tmp/merge_debug.log")

    # Only dump when merge is available but best candidate is NO_MERGE with decent score
    best_is_no_merge = best_score > 0 and global_merge_available

    if not best_is_no_merge:
        return

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    lines.append(f"=== CANDIDATE DUMP at {timestamp} ===")
    lines.append(f"  state={state_name} turn={turn_num}")
    lines.append(f"  global_merge_available={global_merge_available}, best_is_NO_MERGE")
    lines.append(f"  selected: x={best_x:.2f} reason={best_reason} score={best_score:.1f}")
    lines.append("  all_candidates:")
    for r in results:
        mg = r.get("merge_grade", "NO")
        x = r.get("x", 0)
        ly = r.get("landing_y", 0)
        score_val = 0.0
        # Calculate approximate score contribution for merge grade
        if mg == "DIRECT":
            score_val = 1200.0
        elif mg == "NEAR":
            score_val = 600.0
        elif mg == "FAR":
            score_val = 200.0
        lines.append(f"    x={x:.2f} merge={mg} landing_y={ly:.2f} approx_merge_score={score_val:.0f}")
    lines.append("")

    with open(log_path, "a") as f:
        f.write("\n".join(lines))
"""Helpers for preserving reactive-pair merge lanes."""


def blocking_penalty(
    reactive_pairs,
    piece_pos_by_id,
    next_type,
    drop_x,
    landing_y,
    *,
    per_pair=88.8,
    cap=810.9,
):
    """Return the penalty for covering a reactive pair with another type.

    analyze_board.py emits each pair as ``(id1, id2, type)``.  Malformed
    entries or pairs whose pieces are no longer present are ignored.
    """
    penalty = 0.0
    if not isinstance(reactive_pairs, list):
        return penalty

    for pair in reactive_pairs:
        if not isinstance(pair, (list, tuple)) or len(pair) < 3:
            continue
        id1, id2, pair_type = pair[:3]
        if pair_type == next_type:
            continue
        pos1 = piece_pos_by_id.get(id1)
        pos2 = piece_pos_by_id.get(id2)
        if not pos1 or not pos2:
            continue
        x1, y1 = pos1
        x2, y2 = pos2
        span_min = min(x1, x2) - 0.3211
        span_max = max(x1, x2) + 0.2526
        if span_min <= drop_x <= span_max and landing_y >= min(y1, y2):
            penalty += per_pair

    return min(penalty, cap)

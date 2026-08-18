"""盤面の機械的集計ユーティリティ（戦略判断ロジックは strategy.py 側に残す）。

改善ループの decide hash は decide() 本体のみを追跡するため、ここには
「係数・閾値・優先度を一切持たない純粋計算」だけを置く。
"""


def board_max_y_and_count(pieces, fallback=-1.676):
    """盤面の最高 y とピース数を返す。空盤面は fallback を使う。"""
    pieces = pieces if isinstance(pieces, list) else []
    max_y = max([p["y"] for p in pieces]) if pieces else fallback
    return max_y, len(pieces)


def same_type_stack(pieces, piece_type):
    """指定 type のピース一覧と、その中で最も高い位置のピースを返す。"""
    pieces = pieces if isinstance(pieces, list) else []
    same_type = [p for p in pieces if p.get("type") == piece_type]
    top = max(same_type, key=lambda p: p.get("y", -10)) if same_type else None
    return same_type, top


def piece_positions(pieces):
    """piece id → (x, y) の辞書。候補ループ前に一度だけ計算する用途。"""
    pieces = pieces if isinstance(pieces, list) else []
    return {p["id"]: (p["x"], p["y"]) for p in pieces}


def has_reactive_for_type(reactive_pairs, piece_type):
    """指定 type が reactive pair の片側に含まれるか（機械的判定）。"""
    return any(
        rp[1] == piece_type
        for rp in reactive_pairs
        if isinstance(rp, (list, tuple)) and len(rp) >= 6
    )


def has_near_for_type(near_pairs, piece_type):
    """指定 type が near pair の末尾に含まれるか（機械的判定）。"""
    return any(
        np[-1] == piece_type
        for np in near_pairs
        if isinstance(np, (list, tuple)) and len(np) >= 2
    )


def pieces_of_type_at_least(pieces, min_type):
    """type>=min_type のピースを (type, x, y, r) の一覧で返す（機械的集計）。

    閾値 min_type は呼び出し側が渡す（数値リテラルを decide() 側に残し、
    hash 追跡・wildcard 摂動の対象に保つため）。
    """
    pieces = pieces if isinstance(pieces, list) else []
    out = []
    for p in pieces:
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        if not isinstance(t, (int, float)) or isinstance(t, bool):
            continue
        if t < min_type:
            continue
        out.append(
            (
                t,
                float(p.get("x", 0.0) or 0.0),
                float(p.get("y", 0.0) or 0.0),
                float(p.get("r", 0.5) or 0.5),
            )
        )
    return out

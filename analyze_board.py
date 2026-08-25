#!/usr/bin/env python3
"""analyze_board.py - 盤面空間解析プリプロセッサ (人工化学モデル対応版)

ゲームを空間的反応器 (S=ピース種, R=A+A→B, A=物理エンジン) として解析。
game_state.json を空間解析し、併合可否・着地予測を計算。
物理挙動（回転・転がり・爆発衝撃波）を考慮した予測を含む。
スコアリング・戦略評価は strategy.py 側の責任。

Usage: python3 analyze_board.py [game_state.json] [output.md]
"""

import json
import math
import os
import sys

# --- 定数 ---
WALL_LEFT = -3.5
WALL_RIGHT = 3.5
DROP_X_MIN = -3.0
DROP_X_MAX = 3.0
FLOOR_Y = -5.0
DROP_Y = 4.25
# Unity source: Red Line visual is centered at y=3.32, but the game-over
# warning trigger is a BoxCollider2D at y=3.32 with offset +0.08 and height
# 0.04, so overlap begins at y=3.38.
RED_LINE_VISUAL_Y = 3.32
DEADLINE_Y = 3.38
TYPE_RADII = {
    1: 0.207,
    2: 0.259,
    3: 0.316,
    4: 0.380,
    5: 0.414,
    6: 0.470,
    7: 0.559,
    8: 0.660,
    9: 0.746,
    10: 0.846,
    11: 0.982,
    12: 1.068,
    13: 1.207,
    14: 1.385,
    15: 1.600,
}
UNITY_PREFAB_DEADLINE_RADII = {
    1: {"horiz": 0.208, "top": 0.368, "bottom": 0.368},
    2: {"horiz": 0.280, "top": 0.362, "bottom": 0.338},
    3: {"horiz": 0.358, "top": 0.315, "bottom": 0.310},
    4: {"horiz": 0.554, "top": 0.371, "bottom": 0.370},
    5: {"horiz": 0.477, "top": 0.364, "bottom": 0.371},
    6: {"horiz": 0.556, "top": 0.442, "bottom": 0.438},
    7: {"horiz": 0.530, "top": 0.560, "bottom": 0.564},
    8: {"horiz": 0.767, "top": 0.578, "bottom": 0.604},
    9: {"horiz": 1.045, "top": 0.532, "bottom": 0.533},
    10: {"horiz": 0.888, "top": 0.545, "bottom": 0.572},
    11: {"horiz": 1.390, "top": 0.981, "bottom": 0.866},
    12: {"horiz": 1.075, "top": 0.873, "bottom": 0.831},
    13: {"horiz": 1.342, "top": 0.946, "bottom": 0.932},
    14: {"horiz": 1.637, "top": 0.807, "bottom": 0.765},
    15: {"horiz": 2.243, "top": 1.068, "bottom": 1.062},
}

# ポリゴン形状補正係数
# 円モデルの衝突半径 = r * COLLISION_POLY_FACTOR
# ピースの r はスプライトBBox外接円相当だが、実際の PolygonCollider2D はより小さい。
# 旧値 0.75 では大型ピース（type10: r≈1.0, type11: r≈1.7）が隣接する場合に
# 経路ブロック誤判定が多発し、DIRECT検出率が著しく低下（実測 0-33%成功率）。
# 0.55 は shapes データの最小 horiz/r 比（type1: 0.62）に近く、
# 落下経路のポリゴン間ギャップをより正確に反映する。
COLLISION_POLY_FACTOR = 0.55

# v728 (2026-08-25): 垂直開放レーンへの自由落下直撃を DIRECT に昇格する際のゲート。
# 手動チャレンジ (docs/manual_challenge_20260825_insights.md §1) で 3/3 併合した「頭上が完全に
# 開いた相方への垂直落下」が、has_obstruction (レーン外ピースの ±(drop+p)*1.05 帯) と
# has_horizontal_obstruction (ターゲット自身の土台) で NO に誤降格されていた。実履歴 8 試合の
# 実着手 x での再評価では昇格 22/22 が実際に併合 (旧 DIRECT の精度 94.8%)。
VERTICAL_LANE_MAX_GAP = 0.02          # G1: 接触ギャップ (着地予測がターゲット上と一致していること)
VERTICAL_LANE_OVERLAP_FRAC = 1.0      # G2: |x - target.x| <= frac * min(drop_horiz, target_horiz)
VERTICAL_LANE_LANDING_TOL = 0.02      # G3: ly が「ターゲット上端 + 落下駒の下半径」と一致
VERTICAL_LANE_CLEARANCE_MARGIN = 1.0  # G4: 落下柱と重なる全ピースの上端 <= ターゲット上端
# v729 (2026-08-25): 併合後ピース上端 (merge_result_top_y) の較正。旧式 max(ly_poly, target.y)+R は
# 実測 466 併合で平均 +1.05 (中央値 +0.96) 過大 (dy=ly_poly-target.y は併合位置と無相関)。
# 較正式 est = min(legacy, max(Lw, blend)): Lw=ターゲット列で相方を除いて併合後ピースを落とした着地上端
# (±X_WINDOW で広げる)、blend=target.y + F*max(0, ly_poly-target.y) + R + MARGIN。legacy を上限に
# するため値は決して旧式を上回らない (299,798 候補で 0 件) → 締切安全プールは縮まない。
# 実測: bias +1.05→+0.79、過小 (< -0.2) 0/466、閾値 3.38 での誤警報 49→36 (真の越境 10/10 維持)。
MERGE_TOP_MODEL_BLEND_F = 0.25
MERGE_TOP_MODEL_MARGIN = 0.20
MERGE_TOP_MODEL_X_WINDOW = 0.35
VERTICAL_LANE_MIN_PROMINENCE = 0.05   # 柱内の他ピース上端はターゲット上端より 0.05 以上低い (パーチ保険。実測 67/1708 除外・真陽性損失 0)
# 実効条件の注記: hit_id == target が成立している時点で G1/G3/G4(上端条件) は着地モデルから自動的に
# 満たされる (実 707 局面で棄却 0)。実質の追加条件は G2 (横重なり >= 50%) と MIN_PROMINENCE。

# 物理定数（Unity 2D Physics準拠）
GRAVITY = 9.81
EXPLOSION_FORCE = 450.0
EXPLOSION_RADIUS = 2.0
MASS_MULTIPLIER = 10.0  # RepublicController: mass *= 10

# 解析するX位置 (0.2刻み) + 併合ターゲット付近の精密サンプル
BASE_XS = [round(-3.0 + i * 0.2, 1) for i in range(31)]  # -3.0 to 3.0


# --- 物理予測関数 ---


def estimate_polygon_drift(
    drop_x, landing_y, hit_id, next_r, pieces, shapes, next_type
):
    """ポリゴン形状を考慮した着地後のドリフト推定。
    凸ポリゴンは着地後に回転・転がりを起こし、最終位置がdrop_xからずれる。

    戻り値: (drift_x, drift_uncertainty)
      drift_x: 推定ドリフト量（正=右方向）
      drift_uncertainty: 不確実性（大きいほど予測不能）
    """
    shape_key = str(next_type)
    if shape_key not in shapes:
        return 0.0, 0.0  # 形状データなし → ドリフトなし

    verts = shapes[shape_key]
    if len(verts) < 3:
        return 0.0, 0.0

    # ポリゴンの非円形度を計算（円からの偏差）
    dists = [math.sqrt(v[0] ** 2 + v[1] ** 2) for v in verts]
    mean_r = sum(dists) / len(dists)
    variance = sum((d - mean_r) ** 2 for d in dists) / len(dists)
    eccentricity = math.sqrt(variance) / mean_r if mean_r > 0 else 0

    # 非円形度が高いほどドリフトが大きい
    # 着地面の傾斜を推定（ヒットしたピースの位置からの相対角度）
    slope = 0.0
    if hit_id is not None:
        hit_piece = next((p for p in pieces if p["id"] == hit_id), None)
        if hit_piece:
            dx = drop_x - hit_piece["x"]
            # ピース表面の傾斜: 中心からずれるほど急斜面
            norm_dx = (
                dx / (next_r + hit_piece["r"]) if (next_r + hit_piece["r"]) > 0 else 0
            )
            slope = norm_dx  # -1〜+1, 正=右下がり斜面

    # ドリフト推定: 斜面方向 × 非円形度 × 半径
    drift_x = slope * eccentricity * next_r * 2.0

    # 壁際での反射
    estimated_x = drop_x + drift_x
    if estimated_x < WALL_LEFT + next_r:
        drift_x = (WALL_LEFT + next_r) - drop_x
    elif estimated_x > WALL_RIGHT - next_r:
        drift_x = (WALL_RIGHT - next_r) - drop_x

    # 不確実性: 非円形度と着地高さに比例
    uncertainty = eccentricity * next_r * 1.5

    return round(drift_x, 3), round(uncertainty, 3)


def estimate_explosion_displacement(merge_x, merge_y, pieces, exclude_ids=None):
    """併合爆発衝撃波による周囲ピースの移動予測。
    Unity: AddExplosionForce2D(origin, 450, 2.0, ForceMode2D.Impulse)

    戻り値: [(piece_id, dx, dy, new_contact_pairs), ...]
    """
    if exclude_ids is None:
        exclude_ids = set()

    displacements = []
    for p in pieces:
        if p["id"] in exclude_ids:
            continue
        dx = p["x"] - merge_x
        dy = p["y"] - merge_y
        dist = math.sqrt(dx**2 + dy**2)
        if dist >= EXPLOSION_RADIUS or dist < 0.01:
            continue

        # forceFalloff = 1 - (distance / explosionRadius)
        falloff = 1.0 - dist / EXPLOSION_RADIUS
        # 推定質量: r^2に比例 × MASS_MULTIPLIER
        mass = (p["r"] ** 2) * MASS_MULTIPLIER * 3.14
        # impulse → velocity = force * falloff / mass
        if mass < 0.01:
            continue
        force_magnitude = EXPLOSION_FORCE * falloff / mass
        # 方向
        ndx = dx / dist
        ndy = dy / dist
        # 推定変位量（impulse後に重力で減衰、簡易推定0.3秒分）
        disp_x = ndx * force_magnitude * 0.3
        disp_y = ndy * force_magnitude * 0.3 - 0.5 * GRAVITY * 0.09  # 重力減衰
        # 壁・床でクランプ
        new_x = max(WALL_LEFT + p["r"], min(WALL_RIGHT - p["r"], p["x"] + disp_x))
        new_y = max(FLOOR_Y + p["r"], p["y"] + disp_y)
        actual_dx = new_x - p["x"]
        actual_dy = new_y - p["y"]

        if abs(actual_dx) > 0.05 or abs(actual_dy) > 0.05:
            displacements.append(
                {
                    "id": p["id"],
                    "type": p["type"],
                    "dx": round(actual_dx, 2),
                    "dy": round(actual_dy, 2),
                    "new_x": round(new_x, 2),
                    "new_y": round(new_y, 2),
                }
            )

    # 爆発後に新たな併合ペアが生まれるか検査
    new_merges = []
    moved = {d["id"]: d for d in displacements}
    for i, p1 in enumerate(pieces):
        if p1["id"] in exclude_ids:
            continue
        for p2 in pieces[i + 1 :]:
            if p2["id"] in exclude_ids:
                continue
            if p1["type"] != p2["type"]:
                continue
            # 移動後座標
            x1 = moved[p1["id"]]["new_x"] if p1["id"] in moved else p1["x"]
            y1 = moved[p1["id"]]["new_y"] if p1["id"] in moved else p1["y"]
            x2 = moved[p2["id"]]["new_x"] if p2["id"] in moved else p2["x"]
            y2 = moved[p2["id"]]["new_y"] if p2["id"] in moved else p2["y"]
            # 移動前距離
            old_dist = math.sqrt((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2)
            # 移動後距離
            new_dist = math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
            contact_r = p1["r"] + p2["r"]
            if new_dist < contact_r * 1.2 and old_dist >= contact_r * 1.2:
                new_merges.append((p1["id"], p2["id"], p1["type"]))

    return displacements, new_merges


def calc_soviet_progress(pieces):
    """ソ連(type16)までの高tier在庫と、2本目のRussia連鎖を要約する。

    type15を1個作った後は、通常の「盤面上の最大type」だけでは残った
    type10-14の連鎖がどこにあり、あとどれだけの材料があるか分からない。
    戦略側が生のpiecesを何度も独自集計しなくて済むよう、幾何と在庫を
    reactorの安定した信号として返す。
    """
    valid = [p for p in pieces if isinstance(p, dict)]
    type_count = {}
    for piece in valid:
        try:
            piece_type = int(piece.get("type", 0) or 0)
        except (TypeError, ValueError):
            continue
        type_count[piece_type] = type_count.get(piece_type, 0) + 1

    russia_pieces = [p for p in valid if int(p.get("type", 0) or 0) == 15]
    soviet_count = type_count.get(16, 0)
    russia_count = len(russia_pieces)
    precursor_pieces = [
        p for p in valid if 10 <= int(p.get("type", 0) or 0) <= 14
    ]
    precursor_equivalent = sum(
        2.0 ** (int(piece.get("type", 0) or 0) - 15)
        for piece in precursor_pieces
    )
    total_equivalent = (
        precursor_equivalent + float(russia_count) + float(soviet_count * 2)
    )

    if soviet_count:
        stage = "soviet"
    elif russia_count >= 2:
        stage = "double_russia"
    elif russia_count == 1:
        stage = "second_russia"
    elif type_count.get(14, 0):
        stage = "russia_approach"
    elif any(type_count.get(piece_type, 0) for piece_type in (12, 13)):
        stage = "high_tier_build"
    else:
        stage = "foundation"

    # type12-14を2本目の主幹とみなす。まだ無ければtype11を種として使う。
    lane_pieces = [
        p for p in precursor_pieces if int(p.get("type", 0) or 0) >= 12
    ]
    if not lane_pieces:
        lane_pieces = [
            p for p in precursor_pieces if int(p.get("type", 0) or 0) == 11
        ]
    lane_x = None
    lane_type = None
    if lane_pieces:
        lane_weight = sum(
            2.0 ** (int(piece.get("type", 0) or 0) - 10)
            for piece in lane_pieces
        )
        lane_x = sum(
            float(piece.get("x", 0.0) or 0.0)
            * 2.0 ** (int(piece.get("type", 0) or 0) - 10)
            for piece in lane_pieces
        ) / lane_weight
        lane_type = max(int(piece.get("type", 0) or 0) for piece in lane_pieces)

    t15_gap = None
    t15_mid_x = None
    t15_merge_ready = False
    if len(russia_pieces) >= 2:
        closest = None
        for index, left in enumerate(russia_pieces):
            for right in russia_pieces[index + 1 :]:
                distance = math.hypot(
                    float(left.get("x", 0.0) or 0.0)
                    - float(right.get("x", 0.0) or 0.0),
                    float(left.get("y", 0.0) or 0.0)
                    - float(right.get("y", 0.0) or 0.0),
                )
                gap = distance - (
                    float(left.get("r", 0.0) or 0.0)
                    + float(right.get("r", 0.0) or 0.0)
                )
                if closest is None or gap < closest[0]:
                    closest = (gap, left, right)
        if closest is not None:
            t15_gap = round(closest[0], 3)
            t15_mid_x = round(
                (
                    float(closest[1].get("x", 0.0) or 0.0)
                    + float(closest[2].get("x", 0.0) or 0.0)
                )
                / 2.0,
                3,
            )
            t15_merge_ready = closest[0] <= 0.04

    return {
        "stage": stage,
        "russia_count": russia_count,
        "soviet_count": soviet_count,
        "high_type_count": {
            piece_type: type_count.get(piece_type, 0)
            for piece_type in range(10, 16)
        },
        "remaining_russia_equivalent": round(precursor_equivalent, 4),
        "soviet_progress": round(min(1.0, total_equivalent / 2.0), 4),
        "second_russia_lane_x": round(lane_x, 3) if lane_x is not None else None,
        "second_russia_lane_type": lane_type,
        "t15_gap": t15_gap,
        "t15_mid_x": t15_mid_x,
        "t15_merge_ready": t15_merge_ready,
    }


def calc_reactor_state(pieces, shapes=None):
    """人工化学としての反応器状態を解析。
    - 分子種ごとの濃度（個数）
    - 反応可能ペア数（接触圏内の同type）
    - パイプライン健全性（type連鎖の接続性）
    - 空間的分離度
    """
    if not pieces:
        return {}
    deadline_radii = build_deadline_radii(shapes)

    # 分子種カウント
    type_count = {}
    for p in pieces:
        type_count[p["type"]] = type_count.get(p["type"], 0) + 1

    # 反応可能ペア数（同typeで接触圏内 ×1.5）
    reactive_pairs = []
    near_pairs = []
    for i, p1 in enumerate(pieces):
        for p2 in pieces[i + 1 :]:
            if p1["type"] != p2["type"]:
                continue
            dist = math.sqrt((p1["x"] - p2["x"]) ** 2 + (p1["y"] - p2["y"]) ** 2)
            contact_r = p1["r"] + p2["r"]
            if dist < contact_r * 1.1:
                reactive_pairs.append((p1["id"], p2["id"], p1["type"]))
            elif dist < contact_r * 2.0:
                # 間に別タイプのピースが挟まっている場合は除外
                if not has_horizontal_obstruction(
                    p1["x"], p1["y"], p1["r"], p2, pieces
                ):
                    near_pairs.append(
                        (p1["id"], p2["id"], p1["type"], round(dist - contact_r, 2))
                    )

    # パイプライン健全性: 連続するtype間の距離
    types_present = sorted(type_count.keys())
    pipeline = []
    for t in types_present:
        t_next = t + 1
        if t_next not in type_count:
            continue
        # type t と type t+1 の最近接距離
        ps_t = [p for p in pieces if p["type"] == t]
        ps_tn = [p for p in pieces if p["type"] == t_next]
        min_dist = float("inf")
        for a in ps_t:
            for b in ps_tn:
                d = math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)
                if d < min_dist:
                    min_dist = d
        pipeline.append((t, t_next, round(min_dist, 2)))

    # 空間的分離度: LEFT vs RIGHT の分子分布
    left_types = {}
    right_types = {}
    for p in pieces:
        bucket = left_types if p["x"] < 0 else right_types
        bucket[p["type"]] = bucket.get(p["type"], 0) + 1

    top_center_y = max((p["y"] for p in pieces), default=FLOOR_Y)
    top_edge_y = max(
        (piece_deadline_top_y(p, deadline_radii) for p in pieces),
        default=FLOOR_Y,
    )
    danger_pieces = [
        p
        for p in pieces
        if float(p.get("redLineTime", 0) or 0) > 0
        or piece_deadline_top_y(p, deadline_radii) >= DEADLINE_Y
    ]
    positive_redline = [
        float(p.get("redLineTime", 0) or 0)
        for p in pieces
        if float(p.get("redLineTime", 0) or 0) > 0
    ]

    return {
        "type_count": type_count,
        "soviet": calc_soviet_progress(pieces),
        "reactive_pairs": reactive_pairs,
        "near_pairs": near_pairs,
        "pipeline": pipeline,
        "left_types": left_types,
        "right_types": right_types,
        "deadline_y": DEADLINE_Y,
        "top_center_y": round(top_center_y, 3),
        "top_edge_y": round(top_edge_y, 3),
        "deadline_margin": round(DEADLINE_Y - top_edge_y, 3),
        "deadline_crossed": top_edge_y >= DEADLINE_Y,
        "danger_piece_count": len(danger_pieces),
        "min_redline_time": round(min(positive_redline), 3)
        if positive_redline
        else 0.0,
    }


def build_sample_xs(pieces, next_type, deadline_radii=None):
    """基本サンプル + 併合ターゲット座標の精密サンプルを生成"""
    xs = set(BASE_XS)
    top_edge_y = max(
        (piece_deadline_top_y(p, deadline_radii) for p in pieces),
        default=FLOOR_Y,
    )
    if top_edge_y >= DEADLINE_Y - 1.5:
        for i in range(121):
            xs.add(round(DROP_X_MIN + i * 0.05, 2))
    same_type = [p for p in pieces if p["type"] == next_type]
    for p in same_type:
        tx = p["x"]
        # ターゲット座標を中心に±0.3を0.05刻みで追加
        for offset in range(-6, 7):
            candidate = round(tx + offset * 0.05, 2)
            if DROP_X_MIN <= candidate <= DROP_X_MAX:
                xs.add(candidate)
    return sorted(xs)


def load_game_state(path):
    with open(path) as f:
        return json.load(f)


def _vertex_xy(vertex):
    if isinstance(vertex, dict):
        return float(vertex.get("x", 0.0) or 0.0), float(vertex.get("y", 0.0) or 0.0)
    return float(vertex[0]), float(vertex[1])


def calc_effective_radii(shapes):
    """shapes (type→頂点リスト) から各typeのポリゴン実効半径を計算。
    r (bbox半幅) はスプライトバウンディングボックスの外接円相当だが、
    実際の PolygonCollider2D はこれより小さい。
    着地判定用に水平半幅 (horiz)、通常上端高さ (top)、壁際で
    縦向きに起きた場合の上端高さ (wall_top) を返す。"""
    radii = {}
    for type_str, verts in shapes.items():
        t = int(type_str)
        if not verts:
            continue
        local_verts = [_vertex_xy(v) for v in verts]
        horiz = max(abs(v[0]) for v in local_verts)
        top = max(v[1] for v in local_verts)
        bottom = abs(min(v[1] for v in local_verts))
        wall_top = max(top, horiz)
        radii[t] = {
            "horiz": horiz,
            "top": top,
            "bottom": bottom,
            "wall_top": wall_top,
            "verts": local_verts,
        }
    return radii


def calc_nominal_deadline_radii():
    """Deadline判定用の名目外接。

    `soren-game-fixed` の Republic prefab から PolygonCollider2D 頂点と
    Transform scale を読んだ値を使う。併合判定は生の `r` を維持し、
    deadline 専用の着地/上端だけ Unity 実コライダー相当へ戻す。
    """
    radii = {}
    for t, source in UNITY_PREFAB_DEADLINE_RADII.items():
        info = dict(source)
        info["wall_top"] = max(info["top"], info["horiz"])
        radii[t] = info
    for t, r in TYPE_RADII.items():
        if t not in radii:
            radii[t] = {
                "horiz": r * COLLISION_POLY_FACTOR,
                "top": r,
                "bottom": r,
                "wall_top": r,
            }
    return radii


def build_deadline_radii(shapes=None):
    radii = calc_nominal_deadline_radii()
    if shapes:
        radii.update(calc_effective_radii(shapes))
    return radii


def piece_deadline_top_y(piece, deadline_radii=None):
    return float(piece.get("y", FLOOR_Y) or FLOOR_Y) + piece_deadline_top_radius(piece, deadline_radii)


def piece_deadline_extents(piece, deadline_radii=None):
    ry = piece.get("ry")
    rx = piece.get("rx")
    if rx is not None and ry is not None:
        try:
            return {
                "horiz": max(0.0, float(rx)),
                "top": max(0.0, float(ry)),
                "bottom": max(0.0, float(ry)),
            }
        except (TypeError, ValueError):
            pass
    p_type = piece.get("type", 0)
    fallback = min(float(piece.get("r", 0.5) or 0.5), TYPE_RADII.get(p_type, 0.5))
    horiz_fallback = float(piece.get("r", 0.5) or 0.5) * COLLISION_POLY_FACTOR
    extents = {"horiz": horiz_fallback, "top": fallback, "bottom": fallback}
    if deadline_radii and p_type in deadline_radii:
        info = deadline_radii[p_type]
        extents = {
            "horiz": info.get("horiz", horiz_fallback),
            "top": info.get("top", fallback),
            "bottom": info.get("bottom", fallback),
        }
        verts = info.get("verts")
        angle = piece.get("angle")
        if verts and angle is not None:
            try:
                theta = math.radians(float(angle))
            except (TypeError, ValueError):
                theta = 0.0
            if abs(math.sin(theta)) > 1e-6 or abs(math.cos(theta) - 1.0) > 1e-6:
                cos_a = math.cos(theta)
                sin_a = math.sin(theta)
                rotated = [(x * cos_a - y * sin_a, x * sin_a + y * cos_a) for x, y in verts]
                extents = {
                    "horiz": max(abs(x) for x, _ in rotated),
                    "top": max(y for _, y in rotated),
                    "bottom": abs(min(y for _, y in rotated)),
                }
    return extents


def piece_deadline_top_radius(piece, deadline_radii=None):
    ry = piece.get("ry")
    if ry is not None and piece.get("rx") is None:
        try:
            return max(0.0, float(ry))
        except (TypeError, ValueError):
            pass
    return piece_deadline_extents(piece, deadline_radii)["top"]


def piece_deadline_horiz_radius(piece, deadline_radii=None):
    rx = piece.get("rx")
    if rx is not None and piece.get("ry") is None:
        try:
            return max(0.0, float(rx))
        except (TypeError, ValueError):
            pass
    return piece_deadline_extents(piece, deadline_radii)["horiz"]


def get_type_top_radius(piece_type, shapes, eff_radii=None):
    if eff_radii and piece_type in eff_radii:
        return eff_radii[piece_type]["top"]
    return TYPE_RADII.get(piece_type, 0.5)


def _type_deadline_extents(piece_type, fallback_r, deadline_radii=None):
    info = (deadline_radii or {}).get(piece_type, {})
    return {
        "horiz": info.get("horiz", fallback_r * COLLISION_POLY_FACTOR),
        "top": info.get("top", fallback_r),
        "bottom": info.get("bottom", fallback_r),
    }


def get_landing_info(drop_x, drop_r, pieces, eff_radii=None, drop_type=0):
    """drop_x にピースを落とした時の着地Y と最初に衝突するピースIDを返す。

    Unity 側は PolygonCollider2D の OnCollisionEnter2D でマージする。
    ここでは deadline と同じ prefab/shapes 由来の外接を使い、横方向の
    overlap と縦方向の top/bottom で、最初に支えるピースを近似する。
    """
    drop_ext = _type_deadline_extents(drop_type, drop_r, eff_radii)
    drop_horiz = drop_ext["horiz"]
    drop_bottom = drop_ext["bottom"]
    landing_y = FLOOR_Y + drop_bottom  # 床
    hit_id = None  # None = 床に着地

    for p in pieces:
        p_horiz = piece_deadline_horiz_radius(p, eff_radii)
        if abs(drop_x - p["x"]) >= drop_horiz + p_horiz:
            continue
        collision_y = float(p.get("y", FLOOR_Y) or FLOOR_Y) + piece_deadline_top_radius(p, eff_radii) + drop_bottom
        if collision_y > landing_y:
            landing_y = collision_y
            hit_id = p["id"]

    return landing_y, hit_id


def polygon_contact_gap(x, y, drop_ext, target, deadline_radii):
    """AABB近似で2つの PolygonCollider2D がどれだけ離れているかを返す。"""
    target_ext = piece_deadline_extents(target, deadline_radii)
    dx_gap = abs(x - float(target.get("x", 0.0) or 0.0)) - (
        drop_ext["horiz"] + target_ext["horiz"]
    )
    drop_top = y + drop_ext["top"]
    drop_bottom = y - drop_ext["bottom"]
    target_top = float(target.get("y", FLOOR_Y) or FLOOR_Y) + target_ext["top"]
    target_bottom = float(target.get("y", FLOOR_Y) or FLOOR_Y) - target_ext["bottom"]
    if drop_bottom > target_top:
        dy_gap = drop_bottom - target_top
    elif target_bottom > drop_top:
        dy_gap = target_bottom - drop_top
    else:
        dy_gap = 0.0
    return max(dx_gap, 0.0), max(dy_gap, 0.0)


def get_deadline_landing_y(drop_x, drop_r, pieces, deadline_radii, drop_type=0):
    """Deadline判定用の着地Y。

    横長ピースを円として扱うと、横半径が縦方向の分離にも使われて
    「実際は横向きに置けるのに deadline 超過」と誤判定しやすい。
    deadline 判定では横幅の重なりだけを接触条件にし、縦方向は
    既知の上端/下端半径で積む。
    """
    drop_info = (deadline_radii or {}).get(drop_type, {})
    drop_horiz = drop_info.get("horiz", drop_r * COLLISION_POLY_FACTOR)
    drop_bottom = drop_info.get("bottom", drop_r)
    landing_y = FLOOR_Y + drop_bottom
    for p in pieces:
        p_type = p.get("type", 0)
        p_horiz = piece_deadline_horiz_radius(p, deadline_radii)
        if abs(drop_x - p["x"]) >= drop_horiz + p_horiz:
            continue
        p_top = piece_deadline_top_radius(p, deadline_radii)
        landing_y = max(landing_y, float(p.get("y", FLOOR_Y) or FLOOR_Y) + p_top + drop_bottom)
    return landing_y


def _vertical_lane_mode():
    """ANALYZE_BOARD_VERTICAL_LANE_DIRECT: 1=DIRECT 昇格 (既定), 2=NEAR 昇格, 0=旧挙動。毎回 env を読む
    (strategy_runner はゲーム毎の新プロセスなので .env 変更は次ゲームから効く)。"""
    raw = str(os.environ.get("ANALYZE_BOARD_VERTICAL_LANE_DIRECT", "1") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return 0
    if raw == "2":
        return 2
    return 1


def _merge_top_model_mode():
    """ANALYZE_BOARD_MERGE_TOP_MODEL: 2=併合拒否判定のみ較正 (既定), 1=risk_top_after_drop にも適用,
    0=旧式。毎回 env を読む (runner はゲーム毎プロセス → .env 変更は次ゲームから)。"""
    raw = str(os.environ.get("ANALYZE_BOARD_MERGE_TOP_MODEL", "2") or "").strip().lower()
    if raw in ("0", "false", "off", "no"):
        return 0
    if raw == "1":
        return 1
    return 2


def _merged_piece_landing_top(target, pieces, eff_radii, merged_type, merged_top_r):
    """Lw: ターゲット列 (±X_WINDOW) に、ターゲットを除いた盤面へ併合後ピースを落とした着地上端の最大値。
    merged_type が eff_radii に無い (例: T16 ソ連) / 非有限 / 例外 → None (呼び出し側で旧式に倒す)。"""
    try:
        if not eff_radii or merged_type not in eff_radii:
            return None
        tx = float(target["x"])
        if not math.isfinite(tx):
            return None
        others = [p for p in pieces if p.get("id") != target.get("id")]
        # merged_type は eff_radii に必ずある (上で確認済み) ので drop_r は形式上のフォールバックのみ
        drop_r = float(TYPE_RADII.get(merged_type, 0.5))
        tops = []
        for d in (-MERGE_TOP_MODEL_X_WINDOW, 0.0, MERGE_TOP_MODEL_X_WINDOW):
            ly = get_deadline_landing_y(tx + d, drop_r, others, eff_radii, merged_type)
            tops.append(float(ly) + float(merged_top_r))
        top = max(tops)
        return top if math.isfinite(top) else None
    except Exception:
        return None


def _calibrated_merge_top(legacy_top, target, ly_poly, merged_top_r, lw_top):
    """est = min(legacy, max(Lw, blend))。判定不能時は legacy をそのまま返す (fail-closed)。"""
    try:
        if lw_top is None:
            return legacy_top
        ty = float(target["y"])
        lyp = float(ly_poly)
        r = float(merged_top_r)
        if not all(math.isfinite(v) for v in (ty, lyp, r, float(lw_top), float(legacy_top))):
            return legacy_top
        blend = ty + MERGE_TOP_MODEL_BLEND_F * max(0.0, lyp - ty) + r + MERGE_TOP_MODEL_MARGIN
        est = min(float(legacy_top), max(float(lw_top), blend))
        return est if math.isfinite(est) else legacy_top
    except Exception:
        return legacy_top


def _vertical_lane_direct(x, ly, drop_ext, target, pieces, eff_radii=None, contact_gap=None):
    """v728: 落下駒が開放された垂直レーンをターゲット上端まで自由落下して直撃する形かを判定する。
    呼び出し側で hit_id == target (get_landing_info の最初の衝突相手がターゲット) が成立している
    前提で、has_obstruction / has_horizontal_obstruction による降格を無効化するためだけに使う。
    get_landing_info と同じ eff_radii で判定し、欠損・非有限・例外・各ゲート不成立は False
    (= 旧挙動) に倒す。"""
    try:
        if _vertical_lane_mode() == 0:
            return False
        if contact_gap is None:
            return False
        tx = float(target["x"])
        ty = float(target["y"])
        xf = float(x)
        lyf = float(ly)
        gap = float(contact_gap)
        if not all(math.isfinite(v) for v in (tx, ty, xf, lyf, gap)):
            return False
        drop_horiz = float(drop_ext.get("horiz", 0.0) or 0.0)
        drop_bottom = float(drop_ext.get("bottom", 0.0) or 0.0)
        target_horiz = float(piece_deadline_horiz_radius(target, eff_radii) or 0.0)
        if drop_horiz <= 0.0 or target_horiz <= 0.0:
            return False
        if gap > VERTICAL_LANE_MAX_GAP:  # G1
            return False
        if abs(xf - tx) > VERTICAL_LANE_OVERLAP_FRAC * min(drop_horiz, target_horiz):  # G2
            return False
        target_top_y = ty + float(piece_deadline_top_radius(target, eff_radii) or 0.0)
        if abs(lyf - (target_top_y + drop_bottom)) > VERTICAL_LANE_LANDING_TOL:  # G3
            return False
        for p in pieces:  # G4: 落下柱に重なるピースはターゲット上端より MIN_PROMINENCE 以上低い
            if p.get("id") == target.get("id"):
                continue
            px = float(p["x"])
            p_horiz = float(piece_deadline_horiz_radius(p, eff_radii) or 0.0)
            if abs(xf - px) >= (drop_horiz + p_horiz) * VERTICAL_LANE_CLEARANCE_MARGIN:
                continue
            p_top_y = float(piece_deadline_top_y(p, eff_radii))
            if target_top_y - p_top_y < VERTICAL_LANE_MIN_PROMINENCE:
                return False
        return True
    except Exception:
        return False


def has_obstruction(drop_x, drop_r, target, pieces, deadline_radii=None, drop_type=0):
    """DIRECT判定時に、ターゲットの上方にドロップ経路を妨害するピースがないか確認。
    ポリゴン形状を考慮した実効半径で判定し、5%の安全マージンを加算。
    旧実装: 生r値 × 1.2 → 大型ピースで巨大な排除ゾーンが発生しDIRECT誤否定多発。
    新実装: COLLISION_POLY_FACTOR適用後 × 1.05 → ポリゴン実効サイズに近い判定。"""
    MARGIN = 1.05
    drop_horiz = _type_deadline_extents(drop_type, drop_r, deadline_radii)["horiz"]
    for p in pieces:
        if p["id"] == target["id"]:
            continue
        if p["type"] == target["type"]:
            continue
        # ターゲットより上にあるピースのみチェック
        if piece_deadline_top_y(p, deadline_radii) < target["y"]:
            continue
        # ポリゴン補正後の実効半径で干渉チェック
        margin_r = (drop_horiz + piece_deadline_horiz_radius(p, deadline_radii)) * MARGIN
        if abs(drop_x - p["x"]) < margin_r:
            return True
    return False


def has_horizontal_obstruction(from_x, from_y, from_r, target, pieces, deadline_radii=None, drop_type=0):
    """NEAR判定時に、着地点からターゲットへの水平方向に障害ピースがないか確認。
    着地点(drop_x, landing_y)からターゲット(target_x, target_y)へ向かう経路で、
    Y範囲が重なり、X範囲が間に挟まる別タイプのピースがあれば物理的に接触不可。
    同typeピースは併合対象なので除外。"""
    MARGIN = 1.05
    target_x = target["x"]
    target_y = target["y"]
    target_ext = piece_deadline_extents(target, deadline_radii)
    drop_ext = _type_deadline_extents(drop_type, from_r, deadline_radii)
    x_min = min(from_x, target_x)
    x_max = max(from_x, target_x)
    for p in pieces:
        if p["id"] == target["id"]:
            continue
        # ドロップピース自身と同typeピースは併合候補なので障害ではない
        if p["type"] == target["type"]:
            continue
        # Y範囲が重ならないピースは障害にならない
        p_ext = piece_deadline_extents(p, deadline_radii)
        p_top = p["y"] + p_ext["top"]
        p_bot = p["y"] - p_ext["bottom"]
        from_top = from_y + drop_ext["top"]
        from_bot = from_y - drop_ext["bottom"]
        target_top = target_y + target_ext["top"]
        target_bot = target_y - target_ext["bottom"]
        combined_top = max(from_top, target_top)
        combined_bot = min(from_bot, target_bot)
        if p_top < combined_bot or p_bot > combined_top:
            continue
        # ポリゴン補正後の実効半径でX範囲チェック
        p_eff_r = p_ext["horiz"] * MARGIN
        p_x_min = p["x"] - p_eff_r
        p_x_max = p["x"] + p_eff_r
        # ピースがfromとtargetの間に挟まっているか
        if p_x_max > x_min and p_x_min < x_max:
            return True
    return False


def analyze_drops(pieces, next_type, next_r, shapes=None):
    """全サンプルXについて着地Y・併合可否を計算。
    物理挙動（ドリフト・爆発衝撃波）を考慮した拡張版。

    併合判定は3段階:
      DIRECT = 最初に衝突するのがターゲット自身で、経路上に妨害なし（確実併合）
      NEAR   = 着地後にターゲットと接触圏内（高確率併合）
      NO     = 到達不能
    """
    if shapes is None:
        shapes = {}
    eff_radii = build_deadline_radii(shapes)
    deadline_eff_radii = build_deadline_radii(shapes)
    same_type = [p for p in pieces if p["type"] == next_type]
    target_ids = {p["id"] for p in same_type}
    sample_xs = build_sample_xs(pieces, next_type, deadline_eff_radii)
    # ドロップピースの上端高さ（ポリゴン実効値）
    if deadline_eff_radii and next_type in deadline_eff_radii:
        next_top_r = deadline_eff_radii[next_type]["top"]
        next_wall_top_r = deadline_eff_radii[next_type].get("wall_top", next_top_r)
    else:
        next_top_r = next_r
        next_wall_top_r = next_r
    results = []
    # v729: 併合後ピースの列内着地上端 (Lw) は x に依らないのでターゲット id ごとに遅延計算して使い回す
    _merge_top_mode = _merge_top_model_mode()
    _merged_lw_cache = {}

    for x in sample_xs:
        if x < DROP_X_MIN - 0.01 or x > DROP_X_MAX + 0.01:
            continue

        # 併合判定用: Unity の PolygonCollider2D に合わせた外接で着地予測
        ly, hit_id = get_landing_info(x, next_r, pieces, eff_radii, next_type)
        # デッドライン判定用: ポリゴン補正で着地予測（crosses_deadline の偽陽性を抑制）
        ly_poly = get_deadline_landing_y(x, next_r, pieces, deadline_eff_radii, next_type)

        # ポリゴン形状によるドリフト推定
        drift_x, drift_unc = estimate_polygon_drift(
            x, ly, hit_id, next_r, pieces, shapes, next_type
        )
        # ドリフト後の推定最終X
        settled_x = x + drift_x

        # 各同typeピースへの併合判定（ドリフト考慮、元の円モデルの ly を使用）
        merges = []
        drop_ext = _type_deadline_extents(next_type, next_r, eff_radii)
        for t in same_type:
            target_ext = piece_deadline_extents(t, deadline_eff_radii)
            contact_r = max(
                drop_ext["horiz"] + target_ext["horiz"],
                drop_ext["top"] + target_ext["bottom"],
                drop_ext["bottom"] + target_ext["top"],
            )
            # 静的着地位置での距離
            dist_static = math.sqrt((x - t["x"]) ** 2 + (ly - t["y"]) ** 2)
            # ドリフト後の距離
            dist_drifted = math.sqrt((settled_x - t["x"]) ** 2 + (ly - t["y"]) ** 2)
            # 併合判定は両方の距離を考慮（どちらかで接触すれば併合可能）
            dist = min(dist_static, dist_drifted)
            gap_x, gap_y = polygon_contact_gap(x, ly, drop_ext, t, deadline_eff_radii)
            drift_gap_x, drift_gap_y = polygon_contact_gap(settled_x, ly, drop_ext, t, deadline_eff_radii)
            contact_gap = min(math.hypot(gap_x, gap_y), math.hypot(drift_gap_x, drift_gap_y))

            if hit_id == t["id"]:
                # 最初の衝突相手がターゲット → 妨害チェック
                # v728: 垂直開放レーンの自由落下直撃は has_obstruction/has_horizontal_obstruction の
                # 誤降格 (レーン外ピース / ターゲット自身の土台) を受けない。
                # ANALYZE_BOARD_VERTICAL_LANE_DIRECT=0 で完全に旧挙動。
                # mode 2 (NEAR 昇格) は旧 DIRECT を降格させない: has_obstruction が False なら従来どおり DIRECT。
                _vl_mode = _vertical_lane_mode()
                _vl_ok = bool(_vl_mode) and _vertical_lane_direct(x, ly, drop_ext, t, pieces, eff_radii, contact_gap)
                if _vl_ok and _vl_mode == 1:
                    grade = "DIRECT"
                elif has_obstruction(x, next_r, t, pieces, deadline_eff_radii, next_type):
                    # 経路上に妨害ピースあり → 降格、さらに水平障害があればNO
                    if _vl_ok:
                        grade = "NEAR"
                    elif contact_gap <= 0.20:
                        grade = (
                            "NO"
                            if has_horizontal_obstruction(x, ly, next_r, t, pieces, deadline_eff_radii, next_type)
                            else "NEAR"
                        )
                    else:
                        grade = "NO"
                else:
                    grade = "DIRECT"
            elif contact_gap <= 0.04:
                # 着地後にターゲットとほぼ接触 → 水平障害チェック
                if has_horizontal_obstruction(x, ly, next_r, t, pieces, deadline_eff_radii, next_type):
                    grade = "NO"
                else:
                    grade = "NEAR"
            elif contact_gap <= 0.20 and drift_unc > 0:
                # ドリフトで接触する可能性あり → 水平障害チェック
                if has_horizontal_obstruction(settled_x, ly, next_r, t, pieces, deadline_eff_radii, next_type):
                    grade = "NO"
                else:
                    grade = "NEAR"
            else:
                grade = "NO"

            merges.append(
                {
                    "id": t["id"],
                    "x": t["x"],
                    "y": t["y"],
                    "r": t["r"],
                    "tx": t["x"],
                    "ty": t["y"],
                    "tr": t["r"],
                    "dist": round(dist, 3),
                    "contact_r": round(contact_r, 3),
                    "contact_gap": round(contact_gap, 3),
                    "grade": grade,
                    "target_top_y": round(piece_deadline_top_y(t, deadline_eff_radii), 3),
                    "target_crosses_deadline": piece_deadline_top_y(t, deadline_eff_radii) >= DEADLINE_Y,
                    "target_redline_time": round(
                        float(t.get("redLineTime", 0) or 0), 3
                    ),
                    "target_is_danger": (
                        float(t.get("redLineTime", 0) or 0) > 0
                        or piece_deadline_top_y(t, deadline_eff_radii) >= DEADLINE_Y
                    ),
                }
            )

        best_grade = "NO"
        best_merge_dist = None
        for m in merges:
            if m["grade"] == "DIRECT":
                best_grade = "DIRECT"
                best_merge_dist = m["dist"]
                break
            elif m["grade"] == "NEAR" and best_grade != "DIRECT":
                best_grade = "NEAR"
                if best_merge_dist is None or m["dist"] < best_merge_dist:
                    best_merge_dist = m["dist"]

        has_merge = best_grade in ("DIRECT", "NEAR")
        # デッドライン判定にはポリゴン補正版の着地予測を使用
        top_after_drop = ly_poly + next_top_r
        wall_clearance = min(
            x - (WALL_LEFT + next_r),
            (WALL_RIGHT - next_r) - x,
        )
        wall_rotation_risk = wall_clearance <= 0.35
        edge_vertical_top_y = (
            ly_poly + next_wall_top_r if wall_rotation_risk else None
        )
        merge_top_candidates = []
        legacy_merge_top_candidates = []
        merge_result_top_r = get_type_top_radius(next_type + 1, shapes, eff_radii)
        for m in merges:
            if m["grade"] not in ("DIRECT", "NEAR"):
                continue
            # 併合後の大きいピースが縦に残るケースを保守的に見積もる。
            # T87のtype10->type11のように、落下上端は許容に見えても
            # 生成ピースの上端がデッドラインを超えることがある。
            merge_center_y = max(ly_poly, float(m.get("y", ly_poly) or ly_poly))
            legacy_top = merge_center_y + merge_result_top_r
            legacy_merge_top_candidates.append(legacy_top)
            if _merge_top_mode == 0:
                merge_top_candidates.append(legacy_top)
                continue
            # v729: 較正値 (旧式を上限とするので旧式より高くはならない)
            if m["id"] not in _merged_lw_cache:
                _merged_lw_cache[m["id"]] = _merged_piece_landing_top(
                    m, pieces, eff_radii, next_type + 1, merge_result_top_r
                )
            merge_top_candidates.append(
                _calibrated_merge_top(legacy_top, m, ly_poly, merge_result_top_r, _merged_lw_cache[m["id"]])
            )
        merge_result_top_y = min(merge_top_candidates) if merge_top_candidates else None
        # mode 2 では候補自身の crosses_deadline/deadline_margin は旧式の併合上端で計算する (併合拒否判定のみ較正)
        _risk_merge_top = (
            merge_result_top_y
            if _merge_top_mode == 1
            else (min(legacy_merge_top_candidates) if legacy_merge_top_candidates else None)
        )
        risk_top_after_drop = max(
            top_after_drop,
            edge_vertical_top_y if edge_vertical_top_y is not None else top_after_drop,
            _risk_merge_top if _risk_merge_top is not None else top_after_drop,
        )
        # v729 NOTE: 較正値は min(legacy, ·) で旧式を上限とするため、旧式より高い値は決して出ない
        # (299,798 候補で 0 件)。下の「過剰フラグ化 → DEADLINE_FALLBACK 嵐」は構造的に再発しない。
        # NOTE: 以前ここで max(ly, ly_poly)+next_top_r による「保守化」を試したが、
        # 実盤面でほぼ全候補を crosses_deadline=True に過剰フラグ化し、
        # decide() のガードが全候補を skip → L2189 フォールバックが約25%の
        # ターンで強制発火 (DEADLINE_FALLBACK) → 早期ゲームオーバー (score 77〜827)
        # を招いたため revert。crosses_deadline は元の risk_top_after_drop を使用。
        deadline_risk_top = risk_top_after_drop
        danger_direct_merge_available = any(
            m["grade"] == "DIRECT"
            and (
                float(
                    next(
                        (
                            p.get("redLineTime", 0)
                            for p in same_type
                            if p["id"] == m["id"]
                        ),
                        0,
                    )
                    or 0
                )
                > 0
                or float(
                    next(
                        (
                            piece_deadline_top_y(p, deadline_eff_radii)
                            for p in same_type
                            if p["id"] == m["id"]
                        ),
                        FLOOR_Y,
                    )
                )
                >= DEADLINE_Y
            )
            for m in merges
        )
        danger_merge_available = any(
            m["grade"] in ("DIRECT", "NEAR")
            and (
                float(
                    next(
                        (
                            p.get("redLineTime", 0)
                            for p in same_type
                            if p["id"] == m["id"]
                        ),
                        0,
                    )
                    or 0
                )
                > 0
                or float(
                    next(
                        (
                            piece_deadline_top_y(p, deadline_eff_radii)
                            for p in same_type
                            if p["id"] == m["id"]
                        ),
                        FLOOR_Y,
                    )
                )
                >= DEADLINE_Y
            )
            for m in merges
        )

        results.append(
            {
                "x": round(x, 2),
                "landing_y": round(ly, 3),
                    # The first shape-aware collision target is also useful to
                    # post-Russia impact planning.  Keeping it in the analyzer
                    # result avoids re-deriving polygon contact from sprite r.
                    "landing_hit_id": hit_id,
                    "top_y_after_drop": round(top_after_drop, 3),
                    "edge_vertical_top_y": round(edge_vertical_top_y, 3)
                    if edge_vertical_top_y is not None
                    else None,
                    "wall_rotation_risk": wall_rotation_risk,
                    "wall_clearance": round(wall_clearance, 3),
                    "risk_top_y_after_drop": round(risk_top_after_drop, 3),
                    "merge_result_top_y": round(merge_result_top_y, 3)
                    if merge_result_top_y is not None
                    else None,
                    "merge_result_crosses_deadline": (
                        merge_result_top_y is not None
                        and merge_result_top_y >= DEADLINE_Y
                    ),
                    "deadline_y": DEADLINE_Y,
                    "deadline_margin": round(DEADLINE_Y - deadline_risk_top, 3),
                    "crosses_deadline": deadline_risk_top >= DEADLINE_Y,
                "drift_x": drift_x,
                "drift_unc": drift_unc,
                "merges": merges,
                "has_merge": has_merge,
                "merge_grade": best_grade,
                "danger_merge_available": danger_merge_available,
                "danger_direct_merge_available": danger_direct_merge_available,
            }
        )

    return results, same_type


def ascii_board(pieces):
    """ASCII盤面図を生成"""
    W, H = 37, 20
    Y_TOP, Y_BOT = 3.5, -5.0
    grid = [[" "] * W for _ in range(H)]

    def col(x):
        return max(
            0, min(W - 1, int((x - WALL_LEFT) / (WALL_RIGHT - WALL_LEFT) * (W - 1)))
        )

    def row(y):
        return max(0, min(H - 1, int((Y_TOP - y) / (Y_TOP - Y_BOT) * (H - 1))))

    # 壁
    for r in range(H):
        grid[r][0] = "|"
        grid[r][W - 1] = "|"

    # 床
    fr = row(FLOOR_Y)
    for c in range(W):
        grid[fr][c] = "="

    # デッドライン
    dr = row(DEADLINE_Y)
    for c in range(1, W - 1):
        if grid[dr][c] == " ":
            grid[dr][c] = "-"

    # ピース（type を16進1文字で表示、大きいピースは範囲描画）
    for p in pieces:
        cx, cy, pr = col(p["x"]), row(p["y"]), p["r"]
        label = format(p["type"], "X") if p["type"] < 16 else "*"
        # 大きいピースは半径に応じた範囲を描画
        r_cols = max(1, int(pr / (WALL_RIGHT - WALL_LEFT) * (W - 1)))
        for dc in range(-r_cols, r_cols + 1):
            c = cx + dc
            if 0 < c < W - 1:
                r_ = cy
                if 0 <= r_ < H and grid[r_][c] in (" ", "-"):
                    grid[r_][c] = "." if dc != 0 else label

    lines = []
    for r in range(H):
        lines.append("".join(grid[r]))
    return "\n".join(lines)


def format_report(state, results, same_type, pieces, reactor=None):
    """解析結果をMarkdownレポートに整形（人工化学モデル対応版）"""
    nxt = state.get("next", {})
    nn = state.get("nextNext", {})
    nt, nr = nxt.get("type", 0), nxt.get("r", 0.5)
    nnt, nnr = nn.get("type", 0), nn.get("r", 0.5)
    score = state.get("score", 0)
    top_y = max((p["y"] for p in pieces), default=-5)
    if reactor is None:
        reactor = {}

    out = []
    out.append("# 盤面解析レポート")
    out.append("")
    out.append(f"スコア: {score} | ピース数: {len(pieces)} | 最高Y: {top_y:.2f}")
    out.append(f"next: type{nt}(r={nr:.3f}) | nextNext: type{nnt}(r={nnr:.3f})")
    out.append("")

    # 危険警告
    danger = [p for p in pieces if p.get("redLineTime", 0) > 0]
    if danger:
        out.append("## !! 危険ピース !!")
        for p in danger:
            out.append(
                f"- id{p['id']} type{p['type']} at ({p['x']:.2f},{p['y']:.2f}) redLine={p['redLineTime']:.1f}s"
            )
        out.append("")

    if top_y > 1.5:
        out.append(f"## !! 高さ警告 !! 最高Y={top_y:.2f} (デッドライン=2.5)")
        out.append("")

    # ASCII盤面
    out.append("## 盤面図")
    out.append("(type16進: 1-9,A=10,B=11,C=12,D=13,E=14,F=15)")
    out.append("```")
    out.append(ascii_board(pieces))
    out.append("```")
    out.append("")

    # ピース一覧（物理状態付き）
    out.append("## ピース一覧 (上→下)")
    for p in sorted(pieces, key=lambda p: -p["y"]):
        phys = ""
        vx, vy = p.get("vx", 0), p.get("vy", 0)
        av = p.get("av", 0)
        angle = p.get("angle", 0)
        if abs(vx) > 0.1 or abs(vy) > 0.1:
            phys += f" v=({vx:+.1f},{vy:+.1f})"
        if abs(av) > 1.0:
            phys += f" av={av:+.0f}"
        if angle != 0:
            phys += f" {angle:.0f}deg"
        out.append(
            f"  id{p['id']:>3d}  type{p['type']:>2d}  r={p['r']:.3f}  ({p['x']:+.2f}, {p['y']:+.2f}){phys}"
        )
    out.append("")

    # 併合判定
    out.append(f"## 併合判定 (next=type{nt})")
    if not same_type:
        out.append(f"盤面にtype{nt}なし → 併合不可。低い場所に整理して置け。")
    else:
        for target in same_type:
            # このターゲットに対するベストドロップを探す (DIRECT > NEAR > NO)
            best_direct = None
            best_near = None
            best_no = None
            for r in results:
                for m in r["merges"]:
                    if m["id"] != target["id"]:
                        continue
                    info = {
                        "x": r["x"],
                        "ly": r["landing_y"],
                        "dist": m["dist"],
                        "cr": m["contact_r"],
                    }
                    if m["grade"] == "DIRECT":
                        if best_direct is None or m["dist"] < best_direct["dist"]:
                            best_direct = info
                    elif m["grade"] == "NEAR":
                        if best_near is None or m["dist"] < best_near["dist"]:
                            best_near = info
                    else:
                        if best_no is None or m["dist"] < best_no["dist"]:
                            best_no = info

            if best_direct:
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[YES-直撃] DROP:{best_direct['x']:.2f} 着地y={best_direct['ly']:.2f} 距離={best_direct['dist']:.2f}"
                )
            elif best_near:
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[YES-近接] DROP:{best_near['x']:.2f} 着地y={best_near['ly']:.2f} 距離={best_near['dist']:.2f}"
                )
            else:
                info = ""
                if best_no:
                    info = f" 最接近x={best_no['x']:.1f} 距離={best_no['dist']:.2f}>接触距離{best_no['cr']:.2f}"
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[NO] 到達不能{info}"
                )
    out.append("")

    # nextNext 併合候補
    nn_same = [p for p in pieces if p["type"] == nnt]
    out.append(f"## 次手情報 (nextNext=type{nnt})")
    if not nn_same:
        out.append(f"盤面にtype{nnt}なし → nextNext併合保護不要")
    else:
        out.append(
            f"⚠ 盤面にtype{nnt}あり → nextNextで併合可能！今回のドロップで以下のピースの上・隣に積むな:"
        )
        for t in nn_same:
            out.append(
                f"  🛡 id{t['id']} at ({t['x']:+.2f},{t['y']:+.2f}) — この付近を塞ぐとnextNext併合機会を失う"
            )
        # 今回のnextTypeと同じ場合は特に警告
        if nt == nnt:
            out.append(
                f"  ⚠⚠ next=nextNext=type{nt} — 今回併合できても、併合後ピース(type{nt + 1})付近も確認せよ"
            )
    out.append("")

    # 併合可能ドロップ候補
    merge_results = [r for r in results if r["has_merge"]]
    out.append(f"## 併合可能ドロップ候補 ({len(merge_results)}件)")
    out.append("| X座標   | 着地Y  | ドリフト | 併合   |")
    out.append("|---------|--------|---------|----------|")
    for r in merge_results[:10]:
        grade = r["merge_grade"]
        mg = "[直撃]" if grade == "DIRECT" else "[近接]"
        drift = r.get("drift_x", 0)
        drift_s = f"{drift:+.2f}" if abs(drift) > 0.02 else "  0  "
        out.append(f"| {r['x']:+6.2f} | {r['landing_y']:+6.2f} | {drift_s} | {mg} |")
    out.append("")

    # 高さマップ (主要位置)
    key_xs = {-3.0, -2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0}
    out.append("## 高さマップ (着地Y at 主要X)")
    hm = []
    for r in results:
        if r["x"] in key_xs:
            bar_len = max(0, int((r["landing_y"] - FLOOR_Y) * 2))
            bar = "#" * bar_len
            hm.append(f"  x={r['x']:+4.1f} | y={r['landing_y']:+6.2f} {bar}")
    out.extend(hm)
    out.append("")

    # 物理予測（TOP3のドリフト警告）
    drift_warns = [r for r in results[:5] if abs(r.get("drift_x", 0)) > 0.1]
    if drift_warns:
        out.append("## 物理予測: 着地後ドリフト警告")
        for r in drift_warns[:3]:
            d = r["drift_x"]
            u = r.get("drift_unc", 0)
            direction = "右" if d > 0 else "左"
            out.append(
                f"  x={r['x']:+.2f} → 着地後{direction}に{abs(d):.2f}ドリフト (不確実性:{u:.2f})"
            )
        out.append("")

    # 反応器状態レポート（人工化学分析）
    if reactor:
        out.append("## 反応器状態 (人工化学分析)")

        # 分子種分布
        tc = reactor.get("type_count", {})
        if tc:
            species = " ".join(f"t{t}:{c}" for t, c in sorted(tc.items()))
            out.append(f"分子種: {species}")

        # 反応可能ペア
        rp = reactor.get("reactive_pairs", [])
        if rp:
            out.append(f"即時反応可能: {len(rp)}ペア")
            for a, b, t in rp:
                out.append(f"  id{a}+id{b} (type{t}) → type{t + 1}に反応可能")

        # 近接ペア（触媒で反応誘導可能）
        np_ = reactor.get("near_pairs", [])
        if np_:
            out.append(f"触媒誘導可能: {len(np_)}ペア")
            for a, b, t, gap in np_[:5]:
                out.append(
                    f"  id{a}+id{b} (type{t}) gap={gap:.2f} → シェイク/押し込みで併合可能"
                )

        # パイプライン健全性
        pl = reactor.get("pipeline", [])
        if pl:
            out.append("パイプライン:")
            for t1, t2, d in pl:
                status = "OK" if d < 3.0 else "WARN" if d < 5.0 else "BROKEN"
                out.append(f"  type{t1}→{t2}: 距離{d:.1f} [{status}]")

        soviet = reactor.get("soviet", {})
        if soviet:
            out.append(
                "ソ連進捗: "
                f"stage={soviet.get('stage')} "
                f"Russia={soviet.get('russia_count', 0)} "
                f"残存Russia換算={soviet.get('remaining_russia_equivalent', 0):.3f} "
                f"全体={soviet.get('soviet_progress', 0) * 100:.1f}%"
            )
            lane_x = soviet.get("second_russia_lane_x")
            if lane_x is not None:
                out.append(
                    f"2本目レーン: x={lane_x:+.2f} "
                    f"(主幹type{soviet.get('second_russia_lane_type')})"
                )
            if soviet.get("t15_gap") is not None:
                status = "MERGE READY" if soviet.get("t15_merge_ready") else "SEPARATED"
                out.append(
                    f"Russia間隔: gap={soviet.get('t15_gap'):.2f} [{status}]"
                )

        # 左右バランス
        lt = reactor.get("left_types", {})
        rt = reactor.get("right_types", {})
        left_big = sum(v for k, v in lt.items() if k >= 8)
        right_big = sum(v for k, v in rt.items() if k >= 8)
        if left_big + right_big > 0:
            out.append(f"大型ピース分布: LEFT={left_big} RIGHT={right_big}")

        out.append("")

    # 最終推奨 (DIRECT > NEAR > 最低着地点)
    best = None
    if results:
        directs = [r for r in results if r["merge_grade"] == "DIRECT"]
        nears = [r for r in results if r["merge_grade"] == "NEAR"]
        if directs:
            best = min(
                directs,
                key=lambda r: next(
                    (m["dist"] for m in r["merges"] if m["grade"] == "DIRECT"), 99
                ),
            )
        elif nears:
            best = min(
                nears,
                key=lambda r: next(
                    (m["dist"] for m in r["merges"] if m["grade"] == "NEAR"), 99
                ),
            )
        else:
            best = min(results, key=lambda r: r["landing_y"])

    if best:
        drift_note = ""
        if abs(best.get("drift_x", 0)) > 0.1:
            d = best["drift_x"]
            drift_note = f", ドリフト{'右' if d > 0 else '左'}{abs(d):.2f}"
        out.append(f"## 推奨ドロップ: DROP:{best['x']:.2f}")
        grade = best["merge_grade"]
        if grade == "DIRECT":
            merge_targets = [m for m in best["merges"] if m["grade"] == "DIRECT"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(
                f"理由: type{nt}直撃併合({ids}), 着地y={best['landing_y']:.2f}{drift_note}"
            )
        elif grade == "NEAR":
            merge_targets = [m for m in best["merges"] if m["grade"] == "NEAR"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(
                f"理由: type{nt}近接併合({ids}), 着地y={best['landing_y']:.2f}{drift_note}"
            )
        else:
            out.append(
                f"理由: 最も低い着地点(y={best['landing_y']:.2f}), 中央寄り{drift_note}"
            )

    return "\n".join(out)


def main():
    gs_path = sys.argv[1] if len(sys.argv) > 1 else "game_state.json"
    out_path = sys.argv[2] if len(sys.argv) > 2 else "tmp/board_analysis.md"

    state = load_game_state(gs_path)

    if state.get("state") not in ("MOVE", None):
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w") as f:
            f.write(f"# 状態: {state.get('state')}\n操作不可\n")
        print(f"State={state.get('state')}, skipped analysis → {out_path}")
        return

    pieces = state.get("pieces", [])
    shapes = state.get("shapes", {})
    nxt = state.get("next", {})
    nt = nxt.get("type", 0)
    nr = nxt.get("r", 0.5)

    results, same_type = analyze_drops(pieces, nt, nr, shapes)
    reactor = calc_reactor_state(pieces, shapes)
    report = format_report(state, results, same_type, pieces, reactor)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report)

    # 要約を標準出力
    merge_count = sum(1 for r in results if r["has_merge"])
    print(f"  → {len(results)}候補解析 (併合可能={merge_count}) → {out_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
"""analyze_board.py - 盤面空間解析プリプロセッサ (人工化学モデル対応版)

ゲームを空間的反応器 (S=ピース種, R=A+A→B, A=物理エンジン) として解析。
game_state.json を空間解析し、マージ可否・着地予測を計算。
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
DEADLINE_Y = 2.5

# 物理定数（Unity 2D Physics準拠）
GRAVITY = 9.81
EXPLOSION_FORCE = 450.0
EXPLOSION_RADIUS = 2.0
MASS_MULTIPLIER = 10.0  # RepublicController: mass *= 10

# 解析するX位置 (0.2刻み) + マージターゲット付近の精密サンプル
BASE_XS = [round(-3.0 + i * 0.2, 1) for i in range(31)]  # -3.0 to 3.0


# --- 物理予測関数 ---

def estimate_polygon_drift(drop_x, landing_y, hit_id, next_r, pieces, shapes, next_type):
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
    dists = [math.sqrt(v[0]**2 + v[1]**2) for v in verts]
    mean_r = sum(dists) / len(dists)
    variance = sum((d - mean_r)**2 for d in dists) / len(dists)
    eccentricity = math.sqrt(variance) / mean_r if mean_r > 0 else 0

    # 非円形度が高いほどドリフトが大きい
    # 着地面の傾斜を推定（ヒットしたピースの位置からの相対角度）
    slope = 0.0
    if hit_id is not None:
        hit_piece = next((p for p in pieces if p["id"] == hit_id), None)
        if hit_piece:
            dx = drop_x - hit_piece["x"]
            # ピース表面の傾斜: 中心からずれるほど急斜面
            norm_dx = dx / (next_r + hit_piece["r"]) if (next_r + hit_piece["r"]) > 0 else 0
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
    """マージ爆発衝撃波による周囲ピースの移動予測。
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
            displacements.append({
                "id": p["id"],
                "type": p["type"],
                "dx": round(actual_dx, 2),
                "dy": round(actual_dy, 2),
                "new_x": round(new_x, 2),
                "new_y": round(new_y, 2),
            })

    # 爆発後に新たなマージペアが生まれるか検査
    new_merges = []
    moved = {d["id"]: d for d in displacements}
    for i, p1 in enumerate(pieces):
        if p1["id"] in exclude_ids:
            continue
        for p2 in pieces[i+1:]:
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
            old_dist = math.sqrt((p1["x"]-p2["x"])**2 + (p1["y"]-p2["y"])**2)
            # 移動後距離
            new_dist = math.sqrt((x1-x2)**2 + (y1-y2)**2)
            contact_r = p1["r"] + p2["r"]
            if new_dist < contact_r * 1.2 and old_dist >= contact_r * 1.2:
                new_merges.append((p1["id"], p2["id"], p1["type"]))

    return displacements, new_merges


def calc_reactor_state(pieces):
    """人工化学としての反応器状態を解析。
    - 分子種ごとの濃度（個数）
    - 反応可能ペア数（接触圏内の同type）
    - パイプライン健全性（type連鎖の接続性）
    - 空間的分離度
    """
    if not pieces:
        return {}

    # 分子種カウント
    type_count = {}
    for p in pieces:
        type_count[p["type"]] = type_count.get(p["type"], 0) + 1

    # 反応可能ペア数（同typeで接触圏内 ×1.5）
    reactive_pairs = []
    near_pairs = []
    for i, p1 in enumerate(pieces):
        for p2 in pieces[i+1:]:
            if p1["type"] != p2["type"]:
                continue
            dist = math.sqrt((p1["x"]-p2["x"])**2 + (p1["y"]-p2["y"])**2)
            contact_r = p1["r"] + p2["r"]
            if dist < contact_r * 1.1:
                reactive_pairs.append((p1["id"], p2["id"], p1["type"]))
            elif dist < contact_r * 2.0:
                near_pairs.append((p1["id"], p2["id"], p1["type"], round(dist - contact_r, 2)))

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
                d = math.sqrt((a["x"]-b["x"])**2 + (a["y"]-b["y"])**2)
                if d < min_dist:
                    min_dist = d
        pipeline.append((t, t_next, round(min_dist, 2)))

    # 空間的分離度: LEFT vs RIGHT の分子分布
    left_types = {}
    right_types = {}
    for p in pieces:
        bucket = left_types if p["x"] < 0 else right_types
        bucket[p["type"]] = bucket.get(p["type"], 0) + 1

    return {
        "type_count": type_count,
        "reactive_pairs": reactive_pairs,
        "near_pairs": near_pairs,
        "pipeline": pipeline,
        "left_types": left_types,
        "right_types": right_types,
    }


def build_sample_xs(pieces, next_type):
    """基本サンプル + マージターゲット座標の精密サンプルを生成"""
    xs = set(BASE_XS)
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


def get_landing_info(drop_x, drop_r, pieces):
    """drop_x に半径 drop_r のピースを落とした時の着地Y と最初に衝突するピースIDを返す。
    円-円衝突と床衝突の最大値を取る。"""
    landing_y = FLOOR_Y + drop_r  # 床
    hit_id = None  # None = 床に着地

    for p in pieces:
        px, py, pr = p["x"], p["y"], p["r"]
        combined_r = drop_r + pr
        dx = drop_x - px
        if abs(dx) < combined_r:
            collision_y = py + math.sqrt(combined_r ** 2 - dx ** 2)
            if collision_y > landing_y:
                landing_y = collision_y
                hit_id = p["id"]

    return landing_y, hit_id


def has_obstruction(drop_x, drop_r, target, pieces):
    """DIRECT判定時に、ターゲットの上方にドロップ経路を妨害するピースがないか確認。
    厳密な衝突判定では検出されないが、実際のゲームでは干渉する
    ギリギリのピースを安全マージン(20%)で検出する。"""
    MARGIN = 1.2
    for p in pieces:
        if p["id"] == target["id"]:
            continue
        # ターゲットより上にあるピースのみチェック
        if p["y"] + p["r"] < target["y"]:
            continue
        # ドロップ経路との干渉チェック（マージン付き）
        margin_r = (drop_r + p["r"]) * MARGIN
        if abs(drop_x - p["x"]) < margin_r:
            return True
    return False


def analyze_drops(pieces, next_type, next_r, shapes=None):
    """全サンプルXについて着地Y・マージ可否を計算。
    物理挙動（ドリフト・爆発衝撃波）を考慮した拡張版。

    マージ判定は3段階:
      DIRECT = 最初に衝突するのがターゲット自身で、経路上に妨害なし（確実マージ）
      NEAR   = 着地後にターゲットと接触圏内（高確率マージ）
      NO     = 到達不能
    """
    if shapes is None:
        shapes = {}
    same_type = [p for p in pieces if p["type"] == next_type]
    target_ids = {p["id"] for p in same_type}
    sample_xs = build_sample_xs(pieces, next_type)
    results = []

    for x in sample_xs:
        if x < DROP_X_MIN - 0.01 or x > DROP_X_MAX + 0.01:
            continue

        ly, hit_id = get_landing_info(x, next_r, pieces)

        # ポリゴン形状によるドリフト推定
        drift_x, drift_unc = estimate_polygon_drift(
            x, ly, hit_id, next_r, pieces, shapes, next_type
        )
        # ドリフト後の推定最終X
        settled_x = x + drift_x

        # 各同typeピースへのマージ判定（ドリフト考慮）
        merges = []
        for t in same_type:
            contact_r = next_r + t["r"]  # 厳密接触距離
            # 静的着地位置での距離
            dist_static = math.sqrt((x - t["x"]) ** 2 + (ly - t["y"]) ** 2)
            # ドリフト後の距離
            dist_drifted = math.sqrt((settled_x - t["x"]) ** 2 + (ly - t["y"]) ** 2)
            # マージ判定は両方の距離を考慮（どちらかで接触すればマージ可能）
            dist = min(dist_static, dist_drifted)

            if hit_id == t["id"]:
                # 最初の衝突相手がターゲット → 妨害チェック
                if has_obstruction(x, next_r, t, pieces):
                    # 経路上に妨害ピースあり → 降格
                    grade = "NEAR" if dist < contact_r * 1.3 else "NO"
                else:
                    grade = "DIRECT"
            elif dist < contact_r * 1.1:
                # 着地後にターゲットとほぼ接触 → 高確率マージ
                grade = "NEAR"
            elif dist_drifted < contact_r * 1.3 and drift_unc > 0:
                # ドリフトで接触する可能性あり → 低確率NEAR
                grade = "NEAR"
            else:
                grade = "NO"

            merges.append({
                "id": t["id"],
                "tx": t["x"],
                "ty": t["y"],
                "tr": t["r"],
                "dist": round(dist, 3),
                "contact_r": round(contact_r, 3),
                "grade": grade,
            })

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

        results.append({
            "x": round(x, 2),
            "landing_y": round(ly, 3),
            "drift_x": drift_x,
            "drift_unc": drift_unc,
            "merges": merges,
            "has_merge": has_merge,
            "merge_grade": best_grade,
        })

    return results, same_type


def ascii_board(pieces):
    """ASCII盤面図を生成"""
    W, H = 37, 20
    Y_TOP, Y_BOT = 3.5, -5.0
    grid = [[" "] * W for _ in range(H)]

    def col(x):
        return max(0, min(W - 1, int((x - WALL_LEFT) / (WALL_RIGHT - WALL_LEFT) * (W - 1))))

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
            out.append(f"- id{p['id']} type{p['type']} at ({p['x']:.2f},{p['y']:.2f}) redLine={p['redLineTime']:.1f}s")
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
        out.append(f"  id{p['id']:>3d}  type{p['type']:>2d}  r={p['r']:.3f}  ({p['x']:+.2f}, {p['y']:+.2f}){phys}")
    out.append("")

    # マージ判定
    out.append(f"## マージ判定 (next=type{nt})")
    if not same_type:
        out.append(f"盤面にtype{nt}なし → マージ不可。低い場所に整理して置け。")
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
                    info = {"x": r["x"], "ly": r["landing_y"], "dist": m["dist"], "cr": m["contact_r"]}
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

    # nextNext マージ候補
    nn_same = [p for p in pieces if p["type"] == nnt]
    out.append(f"## 次手情報 (nextNext=type{nnt})")
    if not nn_same:
        out.append(f"盤面にtype{nnt}なし → nextNextマージ保護不要")
    else:
        out.append(f"⚠ 盤面にtype{nnt}あり → nextNextでマージ可能！今回のドロップで以下のピースの上・隣に積むな:")
        for t in nn_same:
            out.append(f"  🛡 id{t['id']} at ({t['x']:+.2f},{t['y']:+.2f}) — この付近を塞ぐとnextNextマージ機会を失う")
        # 今回のnextTypeと同じ場合は特に警告
        if nt == nnt:
            out.append(f"  ⚠⚠ next=nextNext=type{nt} — 今回マージできても、マージ後ピース(type{nt+1})付近も確認せよ")
    out.append("")

    # マージ可能ドロップ候補
    merge_results = [r for r in results if r["has_merge"]]
    out.append(f"## マージ可能ドロップ候補 ({len(merge_results)}件)")
    out.append("| X座標   | 着地Y  | ドリフト | マージ   |")
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
            out.append(f"  x={r['x']:+.2f} → 着地後{direction}に{abs(d):.2f}ドリフト (不確実性:{u:.2f})")
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
                out.append(f"  id{a}+id{b} (type{t}) → type{t+1}に反応可能")

        # 近接ペア（触媒で反応誘導可能）
        np_ = reactor.get("near_pairs", [])
        if np_:
            out.append(f"触媒誘導可能: {len(np_)}ペア")
            for a, b, t, gap in np_[:5]:
                out.append(f"  id{a}+id{b} (type{t}) gap={gap:.2f} → シェイク/押し込みでマージ可能")

        # パイプライン健全性
        pl = reactor.get("pipeline", [])
        if pl:
            out.append("パイプライン:")
            for t1, t2, d in pl:
                status = "OK" if d < 3.0 else "WARN" if d < 5.0 else "BROKEN"
                out.append(f"  type{t1}→{t2}: 距離{d:.1f} [{status}]")

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
            best = min(directs, key=lambda r: next((m["dist"] for m in r["merges"] if m["grade"] == "DIRECT"), 99))
        elif nears:
            best = min(nears, key=lambda r: next((m["dist"] for m in r["merges"] if m["grade"] == "NEAR"), 99))
        else:
            best = min(results, key=lambda r: r["landing_y"])

    if best:
        drift_note = ""
        if abs(best.get("drift_x", 0)) > 0.1:
            d = best["drift_x"]
            drift_note = f", ドリフト{'右' if d>0 else '左'}{abs(d):.2f}"
        out.append(f"## 推奨ドロップ: DROP:{best['x']:.2f}")
        grade = best["merge_grade"]
        if grade == "DIRECT":
            merge_targets = [m for m in best["merges"] if m["grade"] == "DIRECT"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(f"理由: type{nt}直撃マージ({ids}), 着地y={best['landing_y']:.2f}{drift_note}")
        elif grade == "NEAR":
            merge_targets = [m for m in best["merges"] if m["grade"] == "NEAR"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(f"理由: type{nt}近接マージ({ids}), 着地y={best['landing_y']:.2f}{drift_note}")
        else:
            out.append(f"理由: 最も低い着地点(y={best['landing_y']:.2f}), 中央寄り{drift_note}")

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
    reactor = calc_reactor_state(pieces)
    report = format_report(state, results, same_type, pieces, reactor)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report)

    # 要約を標準出力
    merge_count = sum(1 for r in results if r["has_merge"])
    print(f"  → {len(results)}候補解析 (マージ可能={merge_count}) → {out_path}")


if __name__ == "__main__":
    main()

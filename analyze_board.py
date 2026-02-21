#!/usr/bin/env python3
"""analyze_board.py - 盤面空間解析プリプロセッサ
game_state.json を空間解析し、マージ可否・着地予測・推奨ドロップを計算。
LLMが幾何学計算をしなくて済むように前処理する。

Usage: python3 analyze_board.py [game_state.json] [output.md]
"""

import json
import math
import os
import sys

# --- 定数 ---
WALL_LEFT = -3.5
WALL_RIGHT = 3.5
DROP_X_MIN = -3.2
DROP_X_MAX = 3.2
FLOOR_Y = -5.0
DROP_Y = 4.25
DEADLINE_Y = 2.5

# 解析するX位置 (0.2刻み)
SAMPLE_XS = [round(-3.2 + i * 0.2, 1) for i in range(33)]  # -3.2 to 3.2


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


def analyze_drops(pieces, next_type, next_r):
    """全サンプルXについて着地Y・マージ可否を計算。
    マージ判定は3段階:
      DIRECT = 最初に衝突するのがターゲット自身（確実マージ）
      NEAR   = 着地後にターゲットと接触圏内（高確率マージ）
      NO     = 到達不能
    """
    same_type = [p for p in pieces if p["type"] == next_type]
    target_ids = {p["id"] for p in same_type}
    results = []

    for x in SAMPLE_XS:
        if x < DROP_X_MIN - 0.01 or x > DROP_X_MAX + 0.01:
            continue

        ly, hit_id = get_landing_info(x, next_r, pieces)

        # 各同typeピースへのマージ判定
        merges = []
        for t in same_type:
            contact_r = next_r + t["r"]  # 厳密接触距離
            dist = math.sqrt((x - t["x"]) ** 2 + (ly - t["y"]) ** 2)

            if hit_id == t["id"]:
                # 最初の衝突相手がターゲット → 確実マージ
                grade = "DIRECT"
            elif dist < contact_r * 1.1:
                # 着地後にターゲットとほぼ接触 → 高確率マージ
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

        # スコア計算
        sc = 0
        if best_grade == "DIRECT":
            # 直撃マージは最優先。着地高さペナルティを大幅軽減
            sc += 200
            if best_merge_dist is not None:
                sc += max(0, 50 - best_merge_dist * 10)
            sc -= max(0, ly - DEADLINE_Y) * 100  # デッドライン超えのみペナルティ
        elif best_grade == "NEAR":
            sc += 130
            if best_merge_dist is not None:
                sc += max(0, 30 - best_merge_dist * 10)
            sc -= ly * 5  # 軽い高さペナルティ
        else:
            sc -= ly * 10  # 低い場所優先
        sc -= abs(x) * 2  # 中央寄せ
        if ly > DEADLINE_Y:
            sc -= 500  # デッドライン超えは常にペナルティ

        results.append({
            "x": round(x, 1),
            "landing_y": round(ly, 3),
            "merges": merges,
            "has_merge": has_merge,
            "merge_grade": best_grade,
            "score": round(sc, 1),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
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


def format_report(state, results, same_type, pieces):
    """解析結果をMarkdownレポートに整形"""
    nxt = state.get("next", {})
    nn = state.get("nextNext", {})
    nt, nr = nxt.get("type", 0), nxt.get("r", 0.5)
    nnt, nnr = nn.get("type", 0), nn.get("r", 0.5)
    score = state.get("score", 0)
    top_y = max((p["y"] for p in pieces), default=-5)

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

    # ピース一覧
    out.append("## ピース一覧 (上→下)")
    for p in sorted(pieces, key=lambda p: -p["y"]):
        out.append(f"  id{p['id']:>3d}  type{p['type']:>2d}  r={p['r']:.3f}  ({p['x']:+.2f}, {p['y']:+.2f})")
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
                    f"[YES-直撃] DROP:{best_direct['x']:.1f} 着地y={best_direct['ly']:.2f} 距離={best_direct['dist']:.2f}"
                )
            elif best_near:
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[YES-近接] DROP:{best_near['x']:.1f} 着地y={best_near['ly']:.2f} 距離={best_near['dist']:.2f}"
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
        out.append(f"盤面にtype{nnt}なし")
    else:
        for t in nn_same:
            out.append(f"  id{t['id']} at ({t['x']:+.2f},{t['y']:+.2f})")
        out.append("→ 今回のドロップで上記付近を塞がないよう注意")
    out.append("")

    # ドロップ推奨ランキング
    out.append("## ドロップ推奨ランキング TOP10")
    out.append("| 順位 | X座標  | 着地Y  | マージ   | スコア |")
    out.append("|------|--------|--------|----------|--------|")
    for i, r in enumerate(results[:10]):
        grade = r["merge_grade"]
        if grade == "DIRECT":
            mg = "[直撃]"
        elif grade == "NEAR":
            mg = "[近接]"
        else:
            mg = "  -   "
        out.append(f"| {i+1:>4d} | {r['x']:+5.1f} | {r['landing_y']:+6.2f} | {mg} | {r['score']:+6.1f} |")
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

    # 最終推奨
    if results:
        best = results[0]
        out.append(f"## 推奨ドロップ: DROP:{best['x']:.1f}")
        grade = best["merge_grade"]
        if grade == "DIRECT":
            merge_targets = [m for m in best["merges"] if m["grade"] == "DIRECT"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(f"理由: type{nt}直撃マージ({ids}), 着地y={best['landing_y']:.2f}")
        elif grade == "NEAR":
            merge_targets = [m for m in best["merges"] if m["grade"] == "NEAR"]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(f"理由: type{nt}近接マージ({ids}), 着地y={best['landing_y']:.2f}")
        else:
            out.append(f"理由: 最も低い着地点(y={best['landing_y']:.2f}), 中央寄り")

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
    nxt = state.get("next", {})
    nt = nxt.get("type", 0)
    nr = nxt.get("r", 0.5)

    results, same_type = analyze_drops(pieces, nt, nr)
    report = format_report(state, results, same_type, pieces)

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        f.write(report)

    # 要約を標準出力
    if results:
        best = results[0]
        mg = "merge" if best["has_merge"] else "no-merge"
        print(f"  → 推奨 DROP:{best['x']:.1f} (着地y={best['landing_y']:.2f}, {mg}) → {out_path}")
    else:
        print(f"  → 解析完了 → {out_path}")


if __name__ == "__main__":
    main()

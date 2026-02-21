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
MERGE_TOLERANCE = 1.3  # 接触判定の余裕係数

# 解析するX位置 (0.2刻み)
SAMPLE_XS = [round(-3.2 + i * 0.2, 1) for i in range(33)]  # -3.2 to 3.2


def load_game_state(path):
    with open(path) as f:
        return json.load(f)


def get_landing_y(drop_x, drop_r, pieces):
    """drop_x に半径 drop_r のピースを落とした時の着地Y（中心座標）を計算。
    円-円衝突と床衝突の最大値を取る。"""
    landing_y = FLOOR_Y + drop_r  # 床

    for p in pieces:
        px, py, pr = p["x"], p["y"], p["r"]
        combined_r = drop_r + pr
        dx = drop_x - px
        if abs(dx) < combined_r:
            # 垂直落下時の衝突Y = ピース中心Y + sqrt(合計半径² - 水平差²)
            collision_y = py + math.sqrt(combined_r ** 2 - dx ** 2)
            landing_y = max(landing_y, collision_y)

    return landing_y


def analyze_drops(pieces, next_type, next_r):
    """全サンプルXについて着地Y・マージ可否を計算"""
    same_type = [p for p in pieces if p["type"] == next_type]
    results = []

    for x in SAMPLE_XS:
        if x < DROP_X_MIN - 0.01 or x > DROP_X_MAX + 0.01:
            continue

        ly = get_landing_y(x, next_r, pieces)

        # 各同typeピースへのマージ判定
        merges = []
        for t in same_type:
            dist = math.sqrt((x - t["x"]) ** 2 + (ly - t["y"]) ** 2)
            threshold = (next_r + t["r"]) * MERGE_TOLERANCE
            merges.append({
                "id": t["id"],
                "tx": t["x"],
                "ty": t["y"],
                "tr": t["r"],
                "dist": round(dist, 3),
                "threshold": round(threshold, 3),
                "ok": dist < threshold,
            })

        has_merge = any(m["ok"] for m in merges)

        # マージ最短距離（近いほど確実）
        best_merge_ratio = None
        if has_merge:
            for m in merges:
                if m["ok"]:
                    ratio = m["dist"] / m["threshold"]  # 0〜1, 小さいほど確実
                    if best_merge_ratio is None or ratio < best_merge_ratio:
                        best_merge_ratio = ratio

        # スコア計算
        sc = 0
        if has_merge:
            # マージ距離が近いほど高スコア (100〜150)
            sc += 100 + (1 - best_merge_ratio) * 50
        if ly > DEADLINE_Y:
            sc -= 500  # デッドライン超え
        sc -= ly * 10  # 低い方が良い
        sc -= abs(x) * 2  # 中央寄せ

        results.append({
            "x": round(x, 1),
            "landing_y": round(ly, 3),
            "merges": merges,
            "has_merge": has_merge,
            "merge_ratio": round(best_merge_ratio, 3) if best_merge_ratio is not None else None,
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
            # このターゲットに対するベストドロップを探す
            best_yes = None
            best_no = None
            for r in results:
                for m in r["merges"]:
                    if m["id"] != target["id"]:
                        continue
                    if m["ok"]:
                        if best_yes is None or m["dist"] < best_yes["dist"]:
                            best_yes = {"x": r["x"], "ly": r["landing_y"], "dist": m["dist"]}
                    else:
                        if best_no is None or m["dist"] < best_no["dist"]:
                            best_no = {"x": r["x"], "ly": r["landing_y"], "dist": m["dist"], "th": m["threshold"]}

            if best_yes:
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[YES] DROP:{best_yes['x']:.1f} 着地y={best_yes['ly']:.2f} 距離={best_yes['dist']:.2f}"
                )
            else:
                info = ""
                if best_no:
                    info = f" 最接近x={best_no['x']:.1f} 着地y={best_no['ly']:.2f} 距離={best_no['dist']:.2f}>閾値{best_no['th']:.2f}"
                out.append(
                    f"  → id{target['id']} at ({target['x']:+.2f},{target['y']:+.2f}): "
                    f"[NO] 他ピースに阻まれて到達不能{info}"
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
    out.append("| 順位 | X座標  | 着地Y  | マージ | 信頼度 | スコア |")
    out.append("|------|--------|--------|--------|--------|--------|")
    for i, r in enumerate(results[:10]):
        if r["has_merge"]:
            mg = "[YES]"
            conf = f"{(1-r['merge_ratio'])*100:.0f}%"
        else:
            mg = "  -  "
            conf = "  -  "
        out.append(f"| {i+1:>4d} | {r['x']:+5.1f} | {r['landing_y']:+6.2f} | {mg} | {conf:>5s} | {r['score']:+6.1f} |")
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
        if best["has_merge"]:
            merge_targets = [m for m in best["merges"] if m["ok"]]
            ids = ",".join(f"id{m['id']}" for m in merge_targets)
            out.append(f"理由: type{nt}マージ可能({ids}), 着地y={best['landing_y']:.2f}")
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

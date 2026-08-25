#!/usr/bin/env python3
"""v731/v732 の「正直な効果指標」を game_history から集計する (スコアより先に動くはずの指標)。

  - T9〜T11 併合地点の最寄り T(N+1) アンカーに対する分類 (軸別 Unity 半径):
      BESIDE (水平ギャップ<=0.15 かつ 縦 AABB ギャップ<=0.5) / ABOVE_OPEN (開いたアンカー上端に乗る) /
      ABOVE_COVERED / FAR / NOANCHOR — GOOD=BESIDE+ABOVE_OPEN、BAD=ABOVE_COVERED+FAR
    と、その併合が同ターンで T(N+1) へ連鎖した率
  - 同ターン連鎖/試合、T10/T11/T12 ペア形成時の距離指標 (v731 指標) の中央値
  - 理由タグ率 (SAME_TYPE_SEED_CONTACT / ANCHOR_LANE_SEED_CONTACT)、DIRECT 次ターン併合率、decide エラー数

usage: tools/seed_metrics.py [--since HHMMSS] [--hash H[,H2]] "game_history/*.jsonl"
"""
import argparse
import glob
import json
import math
import os
import statistics as st
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from strategy_helpers import board_stats as bs  # noqa: E402

hr, tr, br = bs.seed_horiz_radius, bs.seed_top_radius, bs.seed_bottom_radius


def _is_open(P, aid):
    ax, ay, at = P[aid][:3]
    atop = ay + tr(at)
    for j, (jx, jy, jt) in P.items():
        if j == aid:
            continue
        if abs(jx - ax) <= hr(at) and jy - br(jt) >= atop - 0.25:
            return False
    return True


def _classify(P, tgt, N):
    """併合ターゲット tgt (type N) の最寄り T(N+1) アンカーに対する分類。"""
    ax, ay = P[tgt][0], P[tgt][1]
    anchors = [i for i, v in P.items() if v[2] == N + 1]
    if not anchors:
        return "NOANCHOR"
    rank = {"ABOVE_OPEN": 0, "BESIDE": 1, "ABOVE_COVERED": 2, "FAR": 3}
    best = None
    cbot, ctop = ay - br(N), ay + tr(N)
    others = {k: v for k, v in P.items() if k != tgt}
    for i in anchors:
        bx, by = P[i][0], P[i][1]
        atop, abot = by + tr(N + 1), by - br(N + 1)
        dx = abs(ax - bx)
        if dx <= hr(N + 1) and cbot >= atop - 0.25:
            cl = "ABOVE_OPEN" if (_is_open(others, i) and cbot <= atop + 0.25) else "ABOVE_COVERED"
        elif dx - (hr(N) + hr(N + 1)) <= 0.15 and max(cbot - atop, abot - ctop, 0.0) <= 0.5:
            cl = "BESIDE"
        else:
            cl = "FAR"
        if best is None or rank[cl] < rank[best]:
            best = cl
    return best


def _pair_index(a, b, t):
    return math.hypot(a[0] - b[0], a[1] - b[1]) - (hr(t) + hr(t))


def analyze(files, since=None, hashes=None):
    per = defaultdict(lambda: {"games": 0, "turns": 0, "scores": [], "t14": 0, "cls": Counter(), "cls_cascade": Counter(),
                               "cascades": 0, "pair_idx": defaultdict(list), "tags": Counter(), "direct": 0, "direct_ok": 0, "errors": 0})
    for f in sorted(files):
        b = os.path.basename(f)
        if since and b[9:15] < since:
            continue
        try:
            L = [json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        except Exception:
            continue
        if not L:
            continue
        h = L[0].get("strategy_hash", "?")
        if hashes and h not in hashes:
            continue
        s = per[h]
        s["games"] += 1
        s["turns"] += len(L)
        s["scores"].append(L[-1].get("score", 0))
        s["errors"] += sum(1 for l in L if "error" in l)
        maxt = 0
        seen_pairs = set()
        for a, c in zip(L, L[1:]):
            r = a.get("decision_reason", "")
            for tag in ("SAME_TYPE_SEED_CONTACT", "ANCHOR_LANE_SEED_CONTACT"):
                if tag in r:
                    s["tags"][tag] += 1
            if a.get("best_merge_grade") == "DIRECT":
                s["direct"] += 1
                if c.get("score_delta", 0) > 0 or c.get("piece_count", 0) < a.get("piece_count", 0):
                    s["direct_ok"] += 1
            P = {p["id"]: (p["x"], p["y"], p["type"]) for p in a["state_snapshot"]["pieces"]}
            Q = {p["id"]: (p["x"], p["y"], p["type"]) for p in c["state_snapshot"]["pieces"]}
            maxt = max([maxt] + [v[2] for v in Q.values()])
            N = a.get("next_type")
            gone = set(P) - set(Q)
            if N in (9, 10, 11):
                gt = [i for i in gone if P[i][2] == N]
                if gt:
                    cl = _classify(P, gt[0], N)
                    inturn = any(P[i][2] >= N + 1 for i in gone)
                    s["cls"][cl] += 1
                    if inturn:
                        s["cls_cascade"][cl] += 1
                        s["cascades"] += 1
            for t in (10, 11, 12):
                ps = [(i, v) for i, v in Q.items() if v[2] == t]
                if len(ps) >= 2:
                    ids = tuple(sorted(i for i, _ in ps[:2]))
                    if ids not in seen_pairs:
                        seen_pairs.add(ids)
                        s["pair_idx"][t].append(_pair_index(ps[0][1], ps[1][1], t))
        if maxt >= 14:
            s["t14"] += 1
    return per


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("globs", nargs="+")
    ap.add_argument("--since", default=None, help="HHMMSS (file stamp) 以降のみ")
    ap.add_argument("--hash", default=None, help="カンマ区切りの strategy_hash で絞る")
    args = ap.parse_args()
    files = [f for g in args.globs for f in glob.glob(g)]
    per = analyze(files, args.since, set(args.hash.split(",")) if args.hash else None)
    for h, s in sorted(per.items(), key=lambda kv: -kv[1]["games"]):
        sc = s["scores"]
        good = s["cls"]["BESIDE"] + s["cls"]["ABOVE_OPEN"]
        bad = s["cls"]["ABOVE_COVERED"] + s["cls"]["FAR"]
        gc = s["cls_cascade"]["BESIDE"] + s["cls_cascade"]["ABOVE_OPEN"]
        bc = s["cls_cascade"]["ABOVE_COVERED"] + s["cls_cascade"]["FAR"]
        pidx = {t: (round(st.median(v), 2) if v else None) for t, v in s["pair_idx"].items()}
        print("%s games=%d turns=%d | raw mean %.0f med %.0f max %d | T14+ %d" % (h, s["games"], s["turns"], st.mean(sc), st.median(sc), max(sc), s["t14"]))
        print("   T9-11 merge sites: GOOD %d (cascade %.0f%%) BAD %d (cascade %.0f%%) NOANCHOR %d | GOOD/(GOOD+BAD) %.1f%% | cascades/game %.2f" % (
            good, 100 * gc / good if good else 0, bad, 100 * bc / bad if bad else 0, s["cls"]["NOANCHOR"], 100 * good / (good + bad) if good + bad else 0, s["cascades"] / s["games"]))
        print("   pair index median T10 %s T11 %s T12 %s (n=%d/%d/%d) | tags/turn v731 %.3f v732 %.3f | DIRECT prec %.1f%% | errors %d" % (
            pidx.get(10), pidx.get(11), pidx.get(12), len(s["pair_idx"][10]), len(s["pair_idx"][11]), len(s["pair_idx"][12]),
            s["tags"]["SAME_TYPE_SEED_CONTACT"] / s["turns"], s["tags"]["ANCHOR_LANE_SEED_CONTACT"] / s["turns"],
            100 * s["direct_ok"] / s["direct"] if s["direct"] else 0, s["errors"]))


if __name__ == "__main__":
    main()

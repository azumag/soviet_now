#!/usr/bin/env python3
"""インターリーブ A/B の集計 (stdlib のみ)。

入力: tmp/state/ab_games.jsonl (主; game_history の保持数に依存しない)、game_history/*.jsonl (残存分の手ごと指標)、
tmp/state/ab_state.json。出力: 腕ごとの n / 平均 / 中央値 / p25 / SD (eval と raw)、手数、併合/手、複数併合率、
T14/T15 到達、ABBA ブロック差 (mean_B − mean_A) とその SE、符号反転並べ替え p 値、必要 n の目安。

使い方: python3 tools/ab_report.py [--games tmp/state/ab_games.jsonl] [--state tmp/state/ab_state.json]
        [--history game_history] [--sd 650] [--delta 150 300] [--json]
"""
import argparse
import glob
import json
import math
import os
import random
import statistics as st
import sys


def load_games(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _q(vals, q):
    if not vals:
        return None
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def dedupe_valid(rows):
    """idx 重複を除き、tainted を落とす。記録の統合ミスや再記録で同じ idx が
    2 度入ることがある (2026-08-29 の ab_20260829_144145 で idx 200 が重複)。
    同じ idx が複数あるときは最初の 1 件だけを採用する。"""
    seen = set()
    out = []
    for r in rows:
        if r.get("tainted"):
            continue
        try:
            i = int(r.get("idx"))
        except (TypeError, ValueError):
            continue
        if i in seen:
            continue
        seen.add(i)
        out.append(r)
    return out


def arm_summary(rows, key="eval"):
    """腕ごとの要約。ブロック統計と母集団をそろえるため tainted と idx 重複を同じ規則で除く。"""
    rows = dedupe_valid(rows)
    out = {}
    for arm in ("A", "B"):
        v = [r[key] for r in rows if r.get("arm") == arm and isinstance(r.get(key), (int, float))]
        t = [r["turns"] for r in rows if r.get("arm") == arm and isinstance(r.get("turns"), (int, float))]
        out[arm] = {
            "n": len(v),
            "mean": st.mean(v) if v else None,
            "median": st.median(v) if v else None,
            "p25": _q(v, 0.25),
            "sd": st.pstdev(v) if len(v) > 1 else None,
            "turns_mean": st.mean(t) if t else None,
        }
    return out


def blocks(rows, pattern, key="eval"):
    """完全なブロックごとの mean_B − mean_A。

    ブロックを「完全」と認めるのは次をすべて満たすときだけ (issue #132 P0-5):
      * tainted・idx 重複を除いたうえで、ちょうど pattern 長ぶんの記録がある
      * idx が連続 (欠番なし) で一意
      * 腕の構成が pattern の構成と一致する (ABBA なら A2 件・B2 件)
      * 指標が数値である
    以前は「pattern 長ぶんあり、A と B が 1 件以上」だけを見ていたため、AAAB のような
    偏った並びや欠番を含むブロックも採用され得た。
    """
    L = max(1, len(pattern))
    want = {}
    for ch in pattern:
        want[ch] = want.get(ch, 0) + 1
    by = {}
    for r in dedupe_valid(rows):
        if not isinstance(r.get(key), (int, float)):
            continue
        by.setdefault(int(r.get("idx", 0)) // L, []).append(r)
    diffs = []
    for k in sorted(by):
        g = by[k]
        if len(g) != L:
            continue
        idxs = sorted(int(r.get("idx", 0)) for r in g)
        if idxs != list(range(idxs[0], idxs[0] + L)):
            continue
        got = {}
        for r in g:
            got[r.get("arm")] = got.get(r.get("arm"), 0) + 1
        if got != want:
            continue
        a = [r[key] for r in g if r.get("arm") == "A"]
        b = [r[key] for r in g if r.get("arm") == "B"]
        diffs.append(st.mean(b) - st.mean(a))
    return diffs


def sign_flip_p(diffs, draws=10000, seed=1):
    """符号反転並べ替え検定 (両側): |mean(d)| が偶然で出る確率。"""
    if len(diffs) < 2:
        return None
    obs = abs(st.mean(diffs))
    rng = random.Random(seed)
    hit = 0
    for _ in range(draws):
        m = sum(d if rng.random() < 0.5 else -d for d in diffs) / len(diffs)
        if abs(m) >= obs - 1e-12:
            hit += 1
    return hit / draws


def required_n(sd, delta, alpha=0.05, power=0.8, two_sided=True):
    z_a = 1.959964 if two_sided else 1.644854
    z_b = {0.8: 0.841621, 0.9: 1.281552}.get(power, 0.841621)
    return int(math.ceil(2 * (z_a + z_b) ** 2 * sd ** 2 / delta ** 2))


def mde(sd, n_per_arm, alpha=0.05, power=0.8):
    return (1.959964 + 0.841621) * sd * math.sqrt(2.0 / max(1, n_per_arm))


def history_metrics(history_dir, rows):
    """残存 archive から手ごと指標 (併合/手、複数併合率、T14/T15 到達) を腕別に集計。"""
    by_arch = {r.get("archive"): r.get("arm") for r in rows if r.get("archive")}
    out = {"A": {"games": 0, "turns": 0, "merges": 0, "multi": 0, "t14": 0, "t15": 0}, "B": {"games": 0, "turns": 0, "merges": 0, "multi": 0, "t14": 0, "t15": 0}}
    for f in glob.glob(os.path.join(history_dir, "*_score*.jsonl")):
        arm = by_arch.get(os.path.basename(f))
        if arm not in out:
            continue
        rs = []
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rs.append(json.loads(line))
                except Exception:
                    pass
        if not rs:
            continue
        c = out[arm]
        c["games"] += 1
        c["turns"] += len(rs)
        mx = 0
        for i in range(1, len(rs)):
            d = (rs[i].get("piece_count") or 0) - (rs[i - 1].get("piece_count") or 0)
            m = 1 - d
            if m >= 1:
                c["merges"] += m
            if m >= 2:
                c["multi"] += 1
        for r in rs:
            for p in (r.get("state_snapshot") or {}).get("pieces") or []:
                mx = max(mx, p.get("type", 0) or 0)
        c["t14"] += mx >= 14
        c["t15"] += mx >= 15
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="tmp/state/ab_games.jsonl")
    ap.add_argument("--state", default="tmp/state/ab_state.json")
    ap.add_argument("--history", default="game_history")
    ap.add_argument("--sd", type=float, default=650.0)
    ap.add_argument("--delta", type=float, nargs="*", default=[150.0, 300.0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = load_games(args.games)
    try:
        state = json.load(open(args.state, encoding="utf-8"))
    except Exception:
        state = {}
    pattern = "".join(ch for ch in str(state.get("pattern") or "ABBA") if ch in "AB") or "AB"
    tainted = sum(1 for r in rows if r.get("tainted"))
    rep = {"pattern": pattern, "a_hash": state.get("a_hash"), "b_hash": state.get("b_hash"), "games": len(rows), "tainted": tainted}
    for key in ("eval", "score"):
        rep[key] = arm_summary(rows, key)
        d = blocks(rows, pattern, key)
        rep[key + "_blocks"] = {"k": len(d), "mean_diff": st.mean(d) if d else None, "se": (st.pstdev(d) / math.sqrt(len(d)) if len(d) > 1 else None), "p_signflip": sign_flip_p(d)}
    rep["required_n_per_arm"] = {str(int(dl)): required_n(args.sd, dl) for dl in args.delta}
    n_min = min(rep["eval"]["A"]["n"], rep["eval"]["B"]["n"])
    rep["mde_at_current_n"] = mde(args.sd, n_min) if n_min else None
    rep["history"] = history_metrics(args.history, rows)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print("A/B report: pattern=%s A=%s B=%s games=%d tainted=%d" % (pattern, (rep["a_hash"] or "")[:12], (rep["b_hash"] or "")[:12], len(rows), tainted))
    for key in ("eval", "score"):
        s = rep[key]
        for arm in ("A", "B"):
            a = s[arm]
            if a["n"]:
                print("  %-5s %s n=%3d mean %6.0f sd %5.0f med %6.0f p25 %6.0f turns %5.1f" % (key, arm, a["n"], a["mean"], a["sd"] or 0, a["median"], a["p25"], a["turns_mean"] or 0))
        b = rep[key + "_blocks"]
        if b["k"]:
            print("  %-5s blocks k=%d  mean(B-A)=%+.0f  SE=%s  p(sign-flip)=%s" % (key, b["k"], b["mean_diff"], ("%.0f" % b["se"]) if b["se"] is not None else "-", ("%.3f" % b["p_signflip"]) if b["p_signflip"] is not None else "-"))
    h = rep["history"]
    for arm in ("A", "B"):
        c = h[arm]
        if c["games"]:
            print("  hist %s games %d merges/turn %.3f multi %.1f%% T14+ %d T15 %d" % (arm, c["games"], c["merges"] / max(1, c["turns"]), 100 * c["multi"] / max(1, c["turns"]), c["t14"], c["t15"]))
    print("  required n/arm (sd=%.0f, 80%% power, two-sided): %s | MDE at current n=%s: %s" % (args.sd, rep["required_n_per_arm"], n_min, ("%.0f" % rep["mde_at_current_n"]) if rep["mde_at_current_n"] else "-"))
    return 0


if __name__ == "__main__":
    sys.exit(main())

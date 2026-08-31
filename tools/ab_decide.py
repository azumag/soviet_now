#!/usr/bin/env python3
"""インターリーブ A/B の逐次判定 (stdlib のみ、純関数)。

ab_games.jsonl (tools/ab_report.py と同じ形式) から ABBA ブロック差 d = mean_B − mean_A (raw score、非 tainted の
完全ブロックのみ) を取り、事前登録のルールで verdict を返す:
  ABORT              tainted > max_tainted、または即死の非対称 (B の即死が A より Fisher で有意に多い)
  CONTINUE           k < min_blocks
  REJECT_HARM        UCB90(d) = m + 1.28·se < 0                       (害: B が A より確実に悪い)
  REJECT_FUTILE      k >= futility_k かつ UCB90(d) < futility_delta  (無益: 目標効果に届く見込みなし)
  ADOPT              k が looks に含まれ n_min >= min_n_per_arm かつ k >= min_blocks_adopt かつ m > 0 かつ
                     符号反転 p < alpha/len(looks) かつ m >= MDE(sd, n_min) かつガードレール OK
  REJECT_INCONCLUSIVE 最終 look (k == max_blocks) で ADOPT に至らない
  CONTINUE           それ以外
使い方: python3 tools/ab_decide.py --games tmp/state/ab_games.jsonl --state tmp/state/ab_state.json [--json]
"""
import argparse
import json
import math
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import ab_report  # noqa: E402

try:
    from lib.eval_stats import fisher_one_sided, group_sequential_alpha  # noqa: E402
except Exception:  # pragma: no cover - 単体でも動くように
    def group_sequential_alpha(alpha, k_looks):
        return alpha / k_looks if k_looks else alpha

    def fisher_one_sided(dead_cur, n_cur, dead_ref, n_ref):
        return 1.0

DEFAULTS = {
    "pattern": "ABBA",
    "sd": 650.0,
    "alpha": 0.05,
    "looks": (19, 37),
    "max_blocks": 37,
    "min_blocks": 6,
    "min_blocks_adopt": 8,
    "min_n_per_arm": 30,
    "futility_k": 12,
    "futility_delta": 150.0,
    "max_tainted": 2,
    "z_ucb": 1.28,
    "dead_eval_threshold": 400.0,
    "instadeath_alpha": 0.01,
    "instadeath_min_blocks": 4,
}


def _rows_for(rows, arm):
    return [r for r in rows if r.get("arm") == arm and not r.get("tainted")]


def decide(rows, cfg=None):
    c = dict(DEFAULTS)
    if cfg:
        c.update({k: v for k, v in cfg.items() if v is not None})
    pattern = "".join(ch for ch in str(c["pattern"]) if ch in "AB") or "AB"
    looks = tuple(int(x) for x in c["looks"])
    d = ab_report.blocks(rows, pattern, key="score")
    k = len(d)
    a_rows, b_rows = _rows_for(rows, "A"), _rows_for(rows, "B")
    n_a, n_b = len(a_rows), len(b_rows)
    n_min = min(n_a, n_b)
    tainted = sum(1 for r in rows if r.get("tainted"))
    m = st.mean(d) if d else None
    se = (st.pstdev(d) / math.sqrt(k)) if k > 1 else None
    ucb = (m + c["z_ucb"] * se) if (m is not None and se is not None) else None
    out = {"k": k, "n_a": n_a, "n_b": n_b, "mean_diff": m, "se": se, "ucb90": ucb, "tainted": tainted, "p": None, "alpha_look": None, "mde": None, "reasons": []}

    def ret(v, why):
        out["verdict"] = v
        out["reasons"].append(why)
        return out

    if tainted > c["max_tainted"]:
        return ret("ABORT", "tainted=%d > %d" % (tainted, c["max_tainted"]))
    # 即死の非対称 (最初の数ブロックで B だけが極端に死ぬ)
    if k >= c["instadeath_min_blocks"]:
        dead_a = sum(1 for r in a_rows if (r.get("score") or 0) < c["dead_eval_threshold"])
        dead_b = sum(1 for r in b_rows if (r.get("score") or 0) < c["dead_eval_threshold"])
        if dead_b >= 2 and dead_b > dead_a:
            try:
                p_dead = fisher_one_sided(dead_b, n_b, dead_a, n_a)
            except Exception:
                p_dead = 1.0
            out["p_instadeath"] = p_dead
            if p_dead is not None and p_dead < c["instadeath_alpha"]:
                return ret("ABORT", "instadeath B=%d/%d vs A=%d/%d p=%.3f" % (dead_b, n_b, dead_a, n_a, p_dead))
    if k < c["min_blocks"]:
        return ret("CONTINUE", "k=%d < min_blocks %d" % (k, c["min_blocks"]))
    if ucb is not None and ucb < 0:
        return ret("REJECT_HARM", "UCB90=%.0f < 0 (mean %.0f se %.0f k=%d)" % (ucb, m, se, k))
    if k >= c["futility_k"] and ucb is not None and ucb < c["futility_delta"]:
        return ret("REJECT_FUTILE", "k=%d UCB90=%.0f < %.0f" % (k, ucb, c["futility_delta"]))
    if k in looks and n_min >= c["min_n_per_arm"] and k >= c["min_blocks_adopt"]:
        p = ab_report.sign_flip_p(d)
        alpha_look = group_sequential_alpha(c["alpha"], len(looks))
        mde = ab_report.mde(c["sd"], n_min)
        out.update({"p": p, "alpha_look": alpha_look, "mde": mde})
        guard_ok, guard_why = _guardrails(a_rows, b_rows)
        if m is not None and m > 0 and p is not None and p < alpha_look and m >= mde and guard_ok:
            return ret("ADOPT", "look k=%d mean %.0f >= MDE %.0f, p=%.3f < %.3f" % (k, m, mde, p, alpha_look))
        if not guard_ok:
            out["reasons"].append("guardrail: " + guard_why)
        if k >= c["max_blocks"]:
            return ret("REJECT_INCONCLUSIVE", "final look k=%d mean %.0f p=%s mde %.0f" % (k, m, ("%.3f" % p) if p is not None else "-", mde))
        return ret("CONTINUE", "look k=%d not adopted (mean %.0f p=%s mde %.0f)" % (k, m, ("%.3f" % p) if p is not None else "-", mde))
    if k >= c["max_blocks"]:
        return ret("REJECT_INCONCLUSIVE", "max_blocks reached k=%d (n_min=%d)" % (k, n_min))
    return ret("CONTINUE", "k=%d n=%d/%d" % (k, n_a, n_b))


def _guardrails(a_rows, b_rows):
    """採用時のガードレール: T15 到達が A より 2 以上少なくない、締切交差率が悪化していない (行に指標があれば)。"""
    def cnt(rows, key):
        return sum(1 for r in rows if (r.get(key) or 0) >= 1)
    t15_a, t15_b = cnt(a_rows, "t15"), cnt(b_rows, "t15")
    if t15_b < t15_a - 1:
        return False, "T15 reach B=%d < A=%d-1" % (t15_b, t15_a)
    ca = [r.get("crossings") for r in a_rows if isinstance(r.get("crossings"), (int, float))]
    cb = [r.get("crossings") for r in b_rows if isinstance(r.get("crossings"), (int, float))]
    if ca and cb and st.mean(cb) > 2 * max(0.5, st.mean(ca)):
        return False, "crossings B %.2f vs A %.2f" % (st.mean(cb), st.mean(ca))
    return True, ""


def trail(rows, cfg=None):
    """試合を 1 件ずつ足しながら verdict の推移を返す (simulate 用)。"""
    out = []
    for i in range(1, len(rows) + 1):
        v = decide(rows[:i], cfg)
        out.append((i, v["k"], v["verdict"], v["mean_diff"], v["ucb90"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", default="tmp/state/ab_games.jsonl")
    ap.add_argument("--state", default="tmp/state/ab_state.json")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--trail", action="store_true")
    ap.add_argument("--looks", default=None, help="例 19,37")
    ap.add_argument("--max-blocks", type=int, default=None)
    ap.add_argument("--futility-delta", type=float, default=None)
    ap.add_argument("--sd", type=float, default=None)
    args = ap.parse_args()
    rows = ab_report.load_games(args.games)
    try:
        state = json.load(open(args.state, encoding="utf-8"))
    except Exception:
        state = {}
    cfg = {"pattern": state.get("pattern") or "ABBA", "sd": args.sd, "max_blocks": args.max_blocks, "futility_delta": args.futility_delta}
    if args.looks:
        cfg["looks"] = tuple(int(x) for x in args.looks.split(","))
    if args.trail:
        for i, k, v, m, u in trail(rows, cfg):
            print("games=%3d k=%2d %-19s mean=%s ucb90=%s" % (i, k, v, ("%.0f" % m) if m is not None else "-", ("%.0f" % u) if u is not None else "-"))
        return 0
    v = decide(rows, cfg)
    if args.json:
        print(json.dumps(v, ensure_ascii=False))
    else:
        print("verdict=%s k=%d n=%d/%d mean=%s se=%s ucb90=%s p=%s mde=%s | %s" % (v["verdict"], v["k"], v["n_a"], v["n_b"], ("%.0f" % v["mean_diff"]) if v["mean_diff"] is not None else "-", ("%.0f" % v["se"]) if v["se"] is not None else "-", ("%.0f" % v["ucb90"]) if v["ucb90"] is not None else "-", ("%.3f" % v["p"]) if v["p"] is not None else "-", ("%.0f" % v["mde"]) if v["mde"] is not None else "-", "; ".join(v["reasons"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

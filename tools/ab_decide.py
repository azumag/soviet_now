#!/usr/bin/env python3
"""インターリーブ A/B の逐次判定 (stdlib のみ、純関数)。

ab_games.jsonl (tools/ab_report.py と同じ形式) から ABBA ブロック差 d = mean_B − mean_A (非 tainted の
完全ブロックのみ) を取り、事前登録のルールで verdict を返す。指標は experiment の primary
(ab_state.json の "primary"。既定は旧来の raw score。新規実験は eval) で、表示 (Strategy
Comparison / dashboard ヘッダー) と同じ tools/ab_report.py:primary_value を使う。

新規の自動 A/B は issue #132 で実測較正した害停止を使う:
  * k >= 10 までは通常の害停止をしない
  * REJECT_HARM は UCB99(d) = m + 2.3263·se < 0 の場合だけ

旧規則 (k>=6 / UCB90<0) は合成 A/A で score 37.5% / merges_per_turn 41.2% の誤停止を
起こしたため、新規実験では使わない。ただし、既に開始済みの ab_state.json に
``decision_rule`` が無い場合は「途中で事前登録を書き換えない」ため旧規則を維持する。
新規 state は decision_rule を固定保存し、途中の .env 変更で判定条件を変えない。

判定:
  ABORT              tainted > max_tainted、または即死の非対称 (B の即死が A より Fisher で有意に多い)
  CONTINUE           k < min_blocks
  REJECT_HARM        k >= harm_min_blocks かつ harm UCB < 0
  REJECT_FUTILE      k >= futility_k かつ futility UCB < futility_delta
  ADOPT              k が looks に含まれ n_min >= min_n_per_arm かつ k >= min_blocks_adopt かつ m > 0 かつ
                     符号反転 p < alpha/len(looks) かつ m >= MDE(sd, n_min) かつガードレール OK
  REJECT_INCONCLUSIVE 最終 look (k == max_blocks) で ADOPT に至らない
  CONTINUE           それ以外

害停止と無益停止は別の上側信頼境界を持つ。較正済み害停止を UCB99 に変えても、既存の
無益停止 UCB90 まで同時に変えない。

使い方: python3 tools/ab_decide.py --games tmp/state/ab_games.jsonl --state tmp/state/ab_state.json [--json]
        [--primary eval|score] [--sd 3700]
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

Z90 = 1.2816
Z99 = 2.3263
DECISION_RULE_VERSION = 2

DEFAULTS = {
    "pattern": "ABBA",
    "sd": 650.0,
    # 正準指標。state に primary が記録されていれば main() が上書きする。
    # 未記録の旧実験は raw score のまま。
    "primary": "score",
    "alpha": 0.05,
    "looks": (19, 37),
    "max_blocks": 37,
    "min_blocks": 6,
    "min_blocks_adopt": 8,
    "min_n_per_arm": 30,
    # issue #132 の A/A 較正済み害停止。旧 k>=6/UCB90 は帰無でも約4割を誤停止した。
    "harm_min_blocks": 10,
    "harm_z": Z99,
    # 無益停止は従来の UCB90 契約を独立して維持する。
    "futility_k": 12,
    "futility_z": Z90,
    "futility_delta": 150.0,
    "max_tainted": 2,
    "dead_eval_threshold": 400.0,
    "instadeath_alpha": 0.01,
    "instadeath_min_blocks": 4,
}

LEGACY_HARM_RULE = {
    "harm_min_blocks": 6,
    "harm_z": Z90,
    "futility_z": Z90,
}

_RULE_KEYS = (
    "alpha", "looks", "max_blocks", "min_blocks", "min_blocks_adopt",
    "min_n_per_arm", "harm_min_blocks", "harm_z", "futility_k",
    "futility_z", "futility_delta", "max_tainted", "dead_eval_threshold",
    "instadeath_alpha", "instadeath_min_blocks",
)


def config_from_state(state):
    """ab_state.json から判定規則を解決する。

    versioned decision_rule がある新規実験は開始時に固定した値だけを使う。
    rule が無い state は既に走っている旧実験とみなし、旧 k>=6/UCB90 を維持する。
    """
    c = dict(DEFAULTS)
    c["pattern"] = state.get("pattern") or c["pattern"]
    rule = state.get("decision_rule")
    try:
        version = int(state.get("decision_rule_version") or ((rule or {}).get("version") if isinstance(rule, dict) else 0) or 0)
    except (TypeError, ValueError):
        version = 0
    if version >= DECISION_RULE_VERSION and isinstance(rule, dict):
        for key in _RULE_KEYS:
            if key not in rule or rule[key] is None:
                continue
            if key == "looks":
                try:
                    c[key] = tuple(int(x) for x in rule[key])
                except (TypeError, ValueError):
                    continue
            else:
                c[key] = rule[key]
        c["legacy_rule"] = False
        c["decision_rule_version"] = version
        return c
    c.update(LEGACY_HARM_RULE)
    c["legacy_rule"] = True
    c["decision_rule_version"] = 1
    return c


def _rows_for(rows, arm):
    return [r for r in rows if r.get("arm") == arm and not r.get("tainted")]


def decide(rows, cfg=None):
    c = dict(DEFAULTS)
    if cfg:
        c.update({k: v for k, v in cfg.items() if v is not None})
    pattern = "".join(ch for ch in str(c["pattern"]) if ch in "AB") or "AB"
    looks = tuple(int(x) for x in c["looks"])
    primary = str(c.get("primary") or ab_report.LEGACY_PRIMARY).strip().lower()
    if primary not in ab_report.PRIMARY_SD_DEFAULTS:
        primary = ab_report.LEGACY_PRIMARY
    rows = ab_report.with_primary(rows, primary)
    d = ab_report.blocks(rows, pattern, key=ab_report.PRIMARY_KEY)
    k = len(d)
    # 即死判定・ガードレール (dead_a/dead_b, _guardrails) は raw score 等の別フィールド
    # を見るので、非 tainted 全行の母集団 (n_a_all/n_b_all) をそのまま使う。判定用の
    # n_a/n_b/n_min は、正準指標 (_primary) が欠測した行を混ぜない。
    a_rows, b_rows = _rows_for(rows, "A"), _rows_for(rows, "B")
    n_a_all, n_b_all = len(a_rows), len(b_rows)
    a_primary_rows = [r for r in a_rows if r.get(ab_report.PRIMARY_KEY) is not None]
    b_primary_rows = [r for r in b_rows if r.get(ab_report.PRIMARY_KEY) is not None]
    n_a, n_b = len(a_primary_rows), len(b_primary_rows)
    n_min = min(n_a, n_b)
    tainted = sum(1 for r in rows if r.get("tainted"))
    m = st.mean(d) if d else None
    se = (st.pstdev(d) / math.sqrt(k)) if k > 1 else None
    harm_ucb = (m + float(c["harm_z"]) * se) if (m is not None and se is not None) else None
    futility_ucb = (m + float(c["futility_z"]) * se) if (m is not None and se is not None) else None
    out = {
        "k": k, "n_a": n_a, "n_b": n_b, "mean_diff": m, "se": se,
        "harm_ucb": harm_ucb, "harm_z": float(c["harm_z"]),
        "futility_ucb": futility_ucb, "futility_z": float(c["futility_z"]),
        # 互換用。既存 dashboard / simulate は UCB90 を無益停止側として表示してきた。
        "ucb90": futility_ucb,
        "tainted": tainted, "primary": primary, "sd": c["sd"], "p": None,
        "alpha_look": None, "mde": None, "reasons": [],
    }

    def ret(v, why):
        out["verdict"] = v
        out["reasons"].append(why)
        return out

    if tainted > c["max_tainted"]:
        return ret("ABORT", "tainted=%d > %d" % (tainted, c["max_tainted"]))
    # 即死の非対称は通常の統計的害停止とは別の安全ガード。
    if k >= c["instadeath_min_blocks"]:
        dead_a = sum(1 for r in a_rows if (r.get("score") or 0) < c["dead_eval_threshold"])
        dead_b = sum(1 for r in b_rows if (r.get("score") or 0) < c["dead_eval_threshold"])
        if dead_b >= 2 and dead_b > dead_a:
            try:
                p_dead = fisher_one_sided(dead_b, n_b_all, dead_a, n_a_all)
            except Exception:
                p_dead = 1.0
            out["p_instadeath"] = p_dead
            if p_dead is not None and p_dead < c["instadeath_alpha"]:
                return ret("ABORT", "instadeath B=%d/%d vs A=%d/%d p=%.3f" % (dead_b, n_b_all, dead_a, n_a_all, p_dead))
    if k < c["min_blocks"]:
        return ret("CONTINUE", "k=%d < min_blocks %d" % (k, c["min_blocks"]))
    if k >= int(c["harm_min_blocks"]) and harm_ucb is not None and harm_ucb < 0:
        return ret(
            "REJECT_HARM",
            "k=%d >= %d and UCB(z=%.4f)=%.0f < 0 (mean %.0f se %.0f)" % (
                k, int(c["harm_min_blocks"]), float(c["harm_z"]), harm_ucb, m, se),
        )
    if k >= c["futility_k"] and futility_ucb is not None and futility_ucb < c["futility_delta"]:
        return ret(
            "REJECT_FUTILE",
            "k=%d UCB(z=%.4f)=%.0f < %.0f" % (
                k, float(c["futility_z"]), futility_ucb, c["futility_delta"]),
        )
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
    # legacy state の互換再生用。versioned state では事前登録を固定するため拒否する。
    ap.add_argument("--looks", default=None, help="legacy state only: 例 19,37")
    ap.add_argument("--max-blocks", type=int, default=None, help="legacy state only")
    ap.add_argument("--futility-delta", type=float, default=None, help="legacy state only")
    ap.add_argument("--sd", type=float, default=None)
    ap.add_argument("--primary", default=None, help="eval | score (既定は ab_state.json の primary)")
    args = ap.parse_args()
    rows = ab_report.load_games(args.games)
    try:
        state = json.load(open(args.state, encoding="utf-8"))
    except Exception:
        state = {}
    primary = (args.primary or ab_report.state_primary(state)).strip().lower()
    if primary not in ab_report.PRIMARY_SD_DEFAULTS:
        primary = ab_report.LEGACY_PRIMARY
    sd = args.sd if args.sd is not None else ab_report.state_primary_sd(state, primary)
    cfg = config_from_state(state)
    cfg.update({"pattern": state.get("pattern") or cfg["pattern"], "sd": sd, "primary": primary})
    rule_overrides = (args.looks is not None) or (args.max_blocks is not None) or (args.futility_delta is not None)
    if rule_overrides and not cfg.get("legacy_rule"):
        ap.error("versioned decision_rule is frozen; start a new experiment instead of overriding it")
    if args.looks:
        cfg["looks"] = tuple(int(x) for x in args.looks.split(","))
    if args.max_blocks is not None:
        cfg["max_blocks"] = args.max_blocks
    if args.futility_delta is not None:
        cfg["futility_delta"] = args.futility_delta
    if args.trail:
        for i, k, v, m, u in trail(rows, cfg):
            print("games=%3d k=%2d %-19s mean=%s ucb90=%s" % (i, k, v, ("%.0f" % m) if m is not None else "-", ("%.0f" % u) if u is not None else "-"))
        return 0
    v = decide(rows, cfg)
    v["decision_rule_version"] = cfg.get("decision_rule_version")
    v["legacy_rule"] = bool(cfg.get("legacy_rule"))
    if args.json:
        print(json.dumps(v, ensure_ascii=False))
    else:
        print("verdict=%s k=%d n=%d/%d %s mean=%s se=%s harm_ucb(z=%.4f)=%s futility_ucb(z=%.4f)=%s p=%s mde=%s | %s" % (
            v["verdict"], v["k"], v["n_a"], v["n_b"], v.get("primary", primary),
            ("%.0f" % v["mean_diff"]) if v["mean_diff"] is not None else "-",
            ("%.0f" % v["se"]) if v["se"] is not None else "-",
            v["harm_z"], ("%.0f" % v["harm_ucb"]) if v["harm_ucb"] is not None else "-",
            v["futility_z"], ("%.0f" % v["futility_ucb"]) if v["futility_ucb"] is not None else "-",
            ("%.3f" % v["p"]) if v["p"] is not None else "-",
            ("%.0f" % v["mde"]) if v["mde"] is not None else "-",
            "; ".join(v["reasons"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""experiment manifest と ledger だけから A/B の判定を再現する (issue #132 P0-5)。

これまで判定は VM 上の使い捨てスクリプトや手作業で行っており、リポジトリから再現できなかった。
このツールは manifest に固定した事前登録だけを読み、手で条件を足せないようにする。

  python3 tools/ab_verdict.py --manifest experiments/xxx.json --games tmp/state/ab_games.jsonl

出力は人が読む要約と、`--json` で機械可読な verdict。verdict には入力 ledger の SHA256 と
判定コード自身の SHA256 を含めるので、後から「何を見て何を決めたか」を照合できる。
"""
import argparse
import hashlib
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ab_report import blocks, dedupe_valid  # noqa: E402

Z90 = 1.2816  # 片側 90%


def _sha256(path):
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return None


def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                # 壊れた行は落とさず数える (欠測を黙って clean にしない)
                rows.append({"_malformed": True})
    return rows


def rule(manifest):
    """manifest の機械可読な事前登録。無ければ既定 (これまでの運用) を使う。"""
    pre = manifest.get("preregistration") or {}
    r = pre.get("rule") or {}
    return {
        "primary": r.get("primary", "score"),
        "primary_kind": r.get("primary_kind", "block_diff"),
        "adopt_ci_lower_gt": float(r.get("adopt_ci_lower_gt", 0.0)),
        "harm_min_blocks": int(r.get("harm_min_blocks", 6)),
        "harm_ucb_lt": float(r.get("harm_ucb_lt", 0.0)),
        "futility": bool(r.get("futility", False)),
        "final_blocks": int(r.get("final_blocks", 50)),
        "final_games_per_arm": int(r.get("final_games_per_arm", 100)),
        "guardrail_t15_ratio": float(r.get("guardrail_t15_ratio", 0.5)),
        "pattern": manifest.get("pattern", "ABBA"),
    }


def evaluate(manifest, rows):
    R = rule(manifest)
    malformed = sum(1 for r in rows if r.get("_malformed"))
    rows = [r for r in rows if not r.get("_malformed")]
    tainted = sum(1 for r in rows if r.get("tainted"))
    clean = dedupe_valid(rows)
    dropped_dup = len(rows) - tainted - len(clean)
    arms = {a: [r for r in clean if r.get("arm") == a] for a in ("A", "B")}
    n = {a: len(v) for a, v in arms.items()}

    diffs = blocks(clean, R["pattern"], key=R["primary"])
    k = len(diffs)
    mean = st.mean(diffs) if diffs else None
    se = (st.pstdev(diffs) / math.sqrt(k)) if k > 1 else None
    ci_lo = mean - Z90 * se if (mean is not None and se) else None
    ucb = mean + Z90 * se if (mean is not None and se) else None

    def t15(a):
        return sum(1 for r in arms[a] if (r.get("first_turn_t15") is not None) or ((r.get("max_type") or 0) >= 15))

    t15a, t15b = t15("A"), t15("B")
    guard = (t15b * max(1, n["A"])) >= (R["guardrail_t15_ratio"] * t15a * max(1, n["B"]))
    soviet = {a: sum(1 for r in arms[a] if r.get("soviet_created")) for a in ("A", "B")}

    verdict, why = "CONTINUE", "k=%d < final_blocks=%d" % (k, R["final_blocks"])
    if k >= R["harm_min_blocks"] and ucb is not None and ucb < R["harm_ucb_lt"]:
        verdict, why = "HARM_STOP", "k=%d >= %d and UCB90 %.1f < %.1f" % (k, R["harm_min_blocks"], ucb, R["harm_ucb_lt"])
    # 最終判定の起動条件は「計画した標本サイズに達したか」。事前登録では "k=50 (各 100 試合)" と
    # 同じ節目を 2 通りで書いているが、末尾に端数ブロックが出ると k=49 / n=100 のように片方だけ
    # 満たす状態が起こる (実際に v748 vs v752 がそうだった)。どちらかを満たせば判定に入る。
    elif k >= R["final_blocks"] or min(n.values()) >= R["final_games_per_arm"]:
        if ci_lo is not None and ci_lo > R["adopt_ci_lower_gt"] and guard:
            verdict, why = "ADOPT", "CI90 lower %.1f > %.1f and guardrail ok" % (ci_lo, R["adopt_ci_lower_gt"])
        else:
            verdict = "REJECT"
            why = "CI90 lower %s <= %.1f" % ("%.1f" % ci_lo if ci_lo is not None else "n/a", R["adopt_ci_lower_gt"])
            if not guard:
                why += " / guardrail failed (T15 %d vs %d)" % (t15a, t15b)
    return {
        "verdict": verdict, "reason": why, "rule": R,
        "k": k, "n": n, "primary_mean": mean, "primary_se": se,
        "ci90_lower": ci_lo, "ucb90": ucb,
        "t15": {"A": t15a, "B": t15b}, "guardrail_ok": guard,
        "soviet_created": soviet,
        "excluded": {"malformed": malformed, "tainted": tainted, "duplicate_idx": dropped_dup},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    manifest = json.load(open(a.manifest, encoding="utf-8"))
    out = evaluate(manifest, load_rows(a.games))
    out["inputs"] = {
        "manifest": a.manifest, "manifest_sha256": _sha256(a.manifest),
        "games": a.games, "games_sha256": _sha256(a.games),
        "verdict_code_sha256": _sha256(os.path.abspath(__file__)),
        "experiment": manifest.get("experiment"),
    }
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    print("experiment: %s" % manifest.get("experiment"))
    print("  k=%d n=A%d/B%d | primary(%s, %s) mean %s SE %s | CI90 lower %s | UCB90 %s"
          % (out["k"], out["n"]["A"], out["n"]["B"], out["rule"]["primary"], out["rule"]["primary_kind"],
             "%+.1f" % out["primary_mean"] if out["primary_mean"] is not None else "n/a",
             "%.1f" % out["primary_se"] if out["primary_se"] else "n/a",
             "%+.1f" % out["ci90_lower"] if out["ci90_lower"] is not None else "n/a",
             "%+.1f" % out["ucb90"] if out["ucb90"] is not None else "n/a"))
    print("  T15 A=%d B=%d guardrail=%s | 建国 A=%d B=%d | 除外 %s"
          % (out["t15"]["A"], out["t15"]["B"], out["guardrail_ok"],
             out["soviet_created"]["A"], out["soviet_created"]["B"], out["excluded"]))
    print("  VERDICT: %s (%s)" % (out["verdict"], out["reason"]))


if __name__ == "__main__":
    main()

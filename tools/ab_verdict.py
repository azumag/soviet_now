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

Z90 = 1.2816   # 片側 90%
Z99 = 2.3263   # 片側 99%

# 逐次害停止の既定値は実測較正による (issue #132)。
# 逐次害停止の較正 (2026-08-31 に測り直し)。
# 較正には真の A/A が要るが、実験 ledger に純粋な A/A は存在しない
# （2026-08-27 の ab_20260827_143245 を当初 A/A と誤認していたが、実際は
#  253cc67e0c1b vs 6bfc2fb0c486 の A/B。2026-08-30 の ab_20260830_060116 は
#  同一 strategy だが解析器 mode が腕で違う）。
# そこで root (a557db55896b、landing_arc=0) の 370 試合だけを時系列に並べ、
# 4 試合ごとに腕ラベルを無作為配分して合成 A/A を作り、逐次規則の発火率を測った:
#   旧規則 (k>=6 から毎ブロック UCB90<0): score 37.5% / merges_per_turn 41.2% で誤発火
#   新規則 (k>=10 かつ UCB99):            どちらも 9.8%
# 大きな害 (score -400 / merges_per_turn -0.08) に対する検出力はどちらも 100%。
# よって既定は k>=10 / UCB99 とする。既存 manifest が凍結した規則は変えない。
# 既存 manifest は rule に harm_z を持たないので、その実験では従来どおり Z90 を使う
# (凍結した事前登録を後から変えないため)。新しい実験は harm_z を明示すること。
HARM_MIN_BLOCKS_DEFAULT = 10
HARM_Z_DEFAULT = Z99


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
        "harm_min_blocks": int(r.get("harm_min_blocks", HARM_MIN_BLOCKS_DEFAULT)),
        "harm_ucb_lt": float(r.get("harm_ucb_lt", 0.0)),
        # 片側信頼水準。旧 manifest には無いので、その場合は当時の Z90 を使う。
        "harm_z": float(r.get("harm_z", Z90 if "harm_min_blocks" in r else HARM_Z_DEFAULT)),
        "futility": bool(r.get("futility", False)),
        # 無益停止 (条件付き検出力)。None なら無効＝従来と同じ。
        # 2026-09-03 の合成 A/A 測定: 真に横ばいの候補は中央 k=22 (約 7 時間) で打ち切れる。
        # ただし設計が非力だと本物の候補も殺すので、必要な k を確保した実験でのみ使う。
        "futility_cp_lt": (None if r.get("futility_cp_lt") is None
                           else float(r.get("futility_cp_lt"))),
        "futility_min_blocks": int(r.get("futility_min_blocks", 20)),
        "final_blocks": int(r.get("final_blocks", 50)),
        "final_games_per_arm": int(r.get("final_games_per_arm", 100)),
        "guardrail_t15_ratio": float(r.get("guardrail_t15_ratio", 0.5)),
        "pattern": manifest.get("pattern", "ABBA"),
    }



def conditional_power(mean, sd, k, final_blocks, adopt_z, adopt_gt):
    """今の推定のまま残りを消化したとき、最終的に採用条件を満たす確率。

    最終 mean は N(mean, sd^2 * (K-k) / K^2) に従うとみなし、
    採用ライン (adopt_gt + adopt_z * sd/sqrt(K)) を超える確率を返す。
    k >= K なら判定済みなので None。
    """
    if mean is None or not sd or k >= final_blocks or final_blocks <= 0:
        return None
    need = adopt_gt + adopt_z * sd / math.sqrt(final_blocks)
    sd_final = sd * math.sqrt(final_blocks - k) / final_blocks
    if sd_final <= 0:
        return 1.0 if mean > need else 0.0
    z = (need - mean) / sd_final
    return 0.5 * math.erfc(z / math.sqrt(2))

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
    sd = st.pstdev(diffs) if k > 1 else None
    se = (sd / math.sqrt(k)) if sd else None
    ci_lo = mean - Z90 * se if (mean is not None and se) else None
    ucb = mean + R["harm_z"] * se if (mean is not None and se) else None

    def t15(a):
        return sum(1 for r in arms[a] if (r.get("first_turn_t15") is not None) or ((r.get("max_type") or 0) >= 15))

    t15a, t15b = t15("A"), t15("B")
    guard = (t15b * max(1, n["A"])) >= (R["guardrail_t15_ratio"] * t15a * max(1, n["B"]))
    soviet = {a: sum(1 for r in arms[a] if r.get("soviet_created")) for a in ("A", "B")}

    verdict, why = "CONTINUE", "k=%d < final_blocks=%d" % (k, R["final_blocks"])
    if k >= R["harm_min_blocks"] and ucb is not None and ucb < R["harm_ucb_lt"]:
        verdict, why = "HARM_STOP", "k=%d >= %d and UCB(z=%.2f) %.1f < %.1f" % (
            k, R["harm_min_blocks"], R["harm_z"], ucb, R["harm_ucb_lt"])
    # 最終判定の起動条件は「計画した標本サイズに達したか」。事前登録では "k=50 (各 100 試合)" と
    # 同じ節目を 2 通りで書いているが、末尾に端数ブロックが出ると k=49 / n=100 のように片方だけ
    # 満たす状態が起こる (実際に v748 vs v752 がそうだった)。どちらかを満たせば判定に入る。
    elif (R["futility_cp_lt"] is not None and k >= R["futility_min_blocks"]
          and k < R["final_blocks"] and sd is not None
          and (cp := conditional_power(mean, sd, k, R["final_blocks"], Z90,
                                       R["adopt_ci_lower_gt"])) is not None
          and cp < R["futility_cp_lt"]):
        verdict = "FUTILITY_STOP"
        why = "k=%d >= %d and conditional power %.3f < %.3f" % (
            k, R["futility_min_blocks"], cp, R["futility_cp_lt"])
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

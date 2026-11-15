#!/usr/bin/env python3
"""ab_guard.py - runtime/ab-state の ab_status.json を評価し、strategy を変える PR への
警告コメント本文を生成する (soviet_now#865)。

使い方:
    python3 tools/ab_guard.py --file ab_status.json [--json] [--max-age-sec 3600]

verdict:
    active    実験が走っている (root は実験の A と一致)
    drifted   実験 state が残っているが root が A とずれている (退役/rebase 処理中)
    inactive  実験なし (最新の publish が active=false)
    stale     publish が古く現在の実験有無を保証できない
    unknown   status を取得/解析できない

常に rc=0 で終わる。--json なら判定結果を JSON で、それ以外は PR コメント用
Markdown 本文を stdout へ出す。
"""
import argparse
import json
import sys
import time


def _age_sec(observed_epoch):
    try:
        return max(0, int(time.time()) - int(observed_epoch))
    except Exception:
        return None


def evaluate(status, max_age_sec):
    """status dict → verdict + 説明フィールド。"""
    if not isinstance(status, dict) or not status:
        return {"verdict": "unknown"}
    # ab_status.sh が schema_version=1 で出す。取得失敗時の代替 JSON や未知 schema は
    # 「実験なし」ではなく「確認不能」として扱う (fail-open で消えないように)。
    if status.get("schema_version") != 1:
        return {"verdict": "unknown"}
    obs = status.get("observed_epoch")
    age = _age_sec(obs)
    out = {
        "verdict": "inactive",
        "age_sec": age,
        "observed_at": status.get("observed_at"),
        "a_hash": status.get("a_hash"),
        "b_hash": status.get("b_hash"),
        "phase": status.get("confirmation_phase") or ("selection" if status.get("active") else None),
        "games_recorded": status.get("games_recorded"),
        "candidate_pending": status.get("candidate_pending"),
        "aborted": status.get("aborted"),
        "root_matches_a": status.get("root_matches_a"),
    }
    if age is not None and max_age_sec and age > max_age_sec:
        out["verdict"] = "stale"
        return out
    if not isinstance(status.get("active"), bool):
        out["verdict"] = "unknown"
        return out
    if not status["active"]:
        if status.get("candidate_pending"):
            out["verdict"] = "candidate_pending"
        else:
            out["verdict"] = "inactive"
        return out
    if status.get("root_matches_a") is False:
        out["verdict"] = "drifted"
        return out
    out["verdict"] = "active"
    return out


def _h(v):
    return (v or "?")[:12]


def render_markdown(ev, repo_url):
    """PR コメント本文 (Markdown)。sticky comment の marker を先頭に置く。"""
    verdict = ev.get("verdict")
    lines = ["<!-- soren-ab-guard -->"]
    if verdict == "active":
        lines += [
            "⚠️ **本番で A/B 実験が進行中です**",
            "",
            "| | |",
            "|---|---|",
            "| phase | `%s` |" % (ev.get("phase") or "?"),
            "| A (root) | `%s` |" % _h(ev.get("a_hash")),
            "| B (candidate) | `%s` |" % _h(ev.get("b_hash")),
            "| games_recorded | `%s` |" % (ev.get("games_recorded") or "?"),
            "| observed_at | `%s` (%s 秒前) |" % (ev.get("observed_at") or "?", ev.get("age_sec") or "?"),
            "",
            "この PR は `strategy.py` / `strategy_helpers` を変更します。merge すると "
            "deploy reconcile で本番 root が置き換わり、実施中の実験は "
            "`AB_ON_ROOT_DRIFT` のポリシー (retire / rebase) に従って処理されます。",
            "実験の終了を待つか、retire/rebase の許容を確認してから merge してください。",
        ]
    elif verdict == "drifted":
        lines += [
            "⚠️ **本番の A/B state が残っていますが、root は実験の A とずれています**",
            "",
            "state: A=`%s` B=`%s` (phase=`%s`)" % (_h(ev.get("a_hash")), _h(ev.get("b_hash")), ev.get("phase") or "?"),
            "実験は stale 退役または rebase 処理中の可能性があります。状態を確認してから merge してください。",
        ]
    elif verdict == "candidate_pending":
        lines += [
            "ℹ️ 本番に A/B 候補が滞留しています (実験は未開始)。",
            "merge で root が変わると滞留候補は stale_base で破棄されます。",
        ]
    elif verdict == "stale":
        lines += [
            "⚠️ **A/B 実験の有無を確認できません** (status publish が古い)",
            "",
            "runtime/ab-state の最終観測: `%s` (%s 秒前)。本番 loop が止まっているか "
            "publisher が停止している可能性があります。実験の有無を本番で確認してから merge してください。"
            % (ev.get("observed_at") or "?", ev.get("age_sec") or "?"),
        ]
    elif verdict == "unknown":
        lines += [
            "⚠️ **A/B 実験の有無を確認できません** (status を取得できない)",
            "",
            "runtime/ab-state ブランチが無いか読めません。実験の有無を本番で確認してから merge してください。",
        ]
    else:
        lines += [
            "✅ 本番に実施中の A/B 実験はありません (観測: `%s`)。"
            % (ev.get("observed_at") or "?"),
        ]
    lines += [
        "",
        "手順: [A/B root-drift runbook](%s/blob/main/docs/ab_root_drift_runbook.md) / "
        "後追い実戦比較: `tools/ab_retroactive.sh`" % repo_url,
    ]
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="ab_status.json のパス (取得失敗時は存在しないパスを渡す)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-age-sec", type=int, default=3600)
    ap.add_argument("--repo-url", default="https://github.com/azumag/soviet_now")
    args = ap.parse_args()

    status = {}
    try:
        with open(args.file, encoding="utf-8") as fh:
            status = json.load(fh)
    except Exception:
        status = {}

    ev = evaluate(status, args.max_age_sec)
    if args.json:
        print(json.dumps(ev, ensure_ascii=False))
    else:
        print(render_markdown(ev, args.repo_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())

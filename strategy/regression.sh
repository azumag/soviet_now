# strategy/regression.sh - rolling scores, check_regression, rollback候補選定, postmortem生成

_write_rollback_analysis_file() {
	local current_hash="$1" rollback_hash="$2" regression_result="$3" rollback_note="$4" game_num="${5:-}"
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$current_hash" "$rollback_hash" "$regression_result" "$rollback_note" "$ROLLBACK_ANALYSIS_FILE" "score_history.txt" "$game_num" "eval_score_history.txt" <<'PY'
import json
import math
import os
import re
import statistics
import sys
import time

rolling_file, current_run_file, current_hash, rollback_hash, regression_result, rollback_note, out_file, score_history_file, game_num = sys.argv[1:10]

def parse_regression(text: str):
    text = (text or "").strip()
    if text.startswith("REGRESSION:"):
        text = text[len("REGRESSION:"):]
    out = {}
    for part in text.split(","):
        if "=" not in part:
            continue
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out

def to_scores(data):
    try:
        return [int(x) for x in (data or {}).get("scores", [])]
    except Exception:
        return []

def fmt_num(value, digits=1):
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "n/a"

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    if not scores:
        return None
    xs = [int(x) for x in scores]
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "mean": mean, "n": n}

def recent_archives(data):
    arcs = (data or {}).get("_recent_archives", []) or []
    return [os.path.basename(str(x)) for x in arcs[-5:]]

def progress_summary(data, score_count=None):
    data = data or {}
    try:
        max_types = [int(x) for x in data.get("max_types", [])]
    except Exception:
        max_types = []
    if score_count is not None and score_count > 0:
        max_types = max_types[-score_count:]
    frontier_hints = [str(x) for x in (data.get("frontier_hints", []) or [])]
    peak_high_type_counts = [str(x) for x in (data.get("peak_high_type_counts", []) or [])]
    try:
        deadline_guard_counts = [int(x) for x in (data.get("deadline_guard_counts", []) or [])]
    except Exception:
        deadline_guard_counts = []
    deadline_guard_reason_tops = [str(x) for x in (data.get("deadline_guard_reason_tops", []) or [])]
    if score_count is not None and score_count > 0:
        frontier_hints = frontier_hints[-score_count:]
        peak_high_type_counts = peak_high_type_counts[-score_count:]
        deadline_guard_counts = deadline_guard_counts[-score_count:]
        deadline_guard_reason_tops = deadline_guard_reason_tops[-score_count:]
    best_max_type = max([int(data.get("best_max_type", 0) or 0)] + max_types) if max_types or data.get("best_max_type") else 0
    if best_max_type >= 15 and int(data.get("russia_count", 0) or 0) <= 0:
        data["russia_count"] = 1
    if best_max_type >= 16 and int(data.get("soviet_count", 0) or 0) <= 0:
        data["soviet_count"] = 1
    return {
        "max_types": max_types,
        "best_max_type": best_max_type,
        "russia_count": int(data.get("russia_count", 0) or 0),
        "soviet_count": int(data.get("soviet_count", 0) or 0),
        "frontier_hints": frontier_hints,
        "peak_high_type_counts": peak_high_type_counts,
        "deadline_guard_counts": deadline_guard_counts,
        "deadline_guard_reason_tops": deadline_guard_reason_tops,
    }

def fmt_progress(p):
    recent = " ".join(map(str, p.get("max_types", [])[-12:])) or "n/a"
    frontier = " | ".join(p.get("frontier_hints", [])[-4:]) or "n/a"
    peaks = " | ".join(p.get("peak_high_type_counts", [])[-4:]) or "n/a"
    guards = p.get("deadline_guard_counts", [])[-12:]
    guard_text = " ".join(map(str, guards)) if guards else "n/a"
    guard_reasons = " | ".join(p.get("deadline_guard_reason_tops", [])[-4:]) or "n/a"
    return (
        f"best_max_type={p.get('best_max_type', 0)} "
        f"russia={p.get('russia_count', 0)} soviet={p.get('soviet_count', 0)} "
        f"recent_max_types={recent} "
        f"frontier_hints={frontier} "
        f"peak_high_type_counts={peaks} "
        f"deadline_guard_counts={guard_text} "
        f"deadline_guard_reason_tops={guard_reasons}"
    )

def read_score_history(path):
    vals = []
    if not os.path.exists(path):
        return vals
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    vals.append(int(raw.split("\t")[-1]))
                except Exception:
                    continue
    except Exception:
        return []
    return vals

def load_wildcard_origin_for_current(current_hash):
    path = os.environ.get("WILDCARD_ORIGIN_FILE", "tmp/state/wildcard_origin.json")
    if not path or not current_hash or not os.path.exists(path):
        return {}
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}
    origin = data.get(current_hash, {}) if isinstance(data, dict) else {}
    return origin if isinstance(origin, dict) else {}

def explain_reasons(reason_text):
    reasons = [r for r in (reason_text or "").split("+") if r]
    lines = []
    mapping = {
        "comp": "総合指標 comp が anchor を下回っていた。",
        "p50": "中央値寄りの典型性能 p50 が anchor を下回っていた。",
        "p25": "下振れ耐性 p25 が anchor を下回っていた。",
        "trend50": "直近50試合平均がその前50試合平均より落ちていた。",
        "trend100": "直近100試合平均がその前100試合平均より落ちていた。",
        "budget_exhausted": "探索 branch の予算を使い切っても anchor に届かなかった。",
        "budget_reset": "探索予算は使い切ったが anchor との差が小さく、今回は粛清を見送った。",
        "depth": "branch depth 上限に到達した。",
        "games": "branch games 上限に到達した。",
        "patience": "branch best が更新されない状態が続いた。",
        "soft_fail": "anchor 比の通常回帰閾値に達した。",
        "hard_fail": "anchor 比で明確な悪化が出て即時停止条件に触れた。",
        "branch": "単一戦略ではなく branch 全体の失敗として判定した。",
        "anchor_direct": "branch 状態なしで anchor 比の即時悪化として判定した。",
        "anchor_promoted": "現戦略が anchor を上回ったため anchor を更新した。",
        "objective_regression": "建国目標の進捗が anchor より後退した。",
        "lost_turkmenistan_gate": "anchor よりトルクメニスタン段階の到達率が後退した。",
        "lost_ukraine_gate": "anchor よりウクライナ段階の到達率が後退した。",
        "lost_kazakhstan_gate": "anchor よりカザフスタン段階の到達率が後退した。",
        "lost_russia_path": "anchor はロシア到達済みだが current はロシア未到達だった。",
        "lost_soviet_path": "anchor はソ連到達済みだが current はソ連未到達だった。",
    }
    for reason in reasons:
        if reason.startswith("rank") and reason[4:].isdigit():
            lines.append(f"成熟ランキングで上位{reason[4:]}位圏外に落ちた。")
        else:
            lines.append(mapping.get(reason, f"{reason} が悪化要因だった。"))
    return lines or ["詳細理由を特定できなかった。"]

def objective_triggered(reason_text):
    reasons = set(r for r in (reason_text or "").split("+") if r)
    objective_markers = {
        "objective_regression",
        "early_objective_regression",
        "archive_restart_objective_floor",
        "lost_turkmenistan_gate",
        "lost_ukraine_gate",
        "lost_kazakhstan_gate",
        "lost_russia_path",
        "lost_soviet_path",
    }
    return bool(reasons & objective_markers)

try:
    rolling = json.load(open(rolling_file))
except Exception:
    rolling = {}

current_data = rolling.get(current_hash, {})
rollback_data = rolling.get(rollback_hash, {})
reg = parse_regression(regression_result)
current_scores = to_scores(current_data)
if os.path.exists(current_run_file):
    try:
        current_run = json.load(open(current_run_file))
    except Exception:
        current_run = {}
    if str(current_run.get("hash", "") or "") == current_hash:
        current_scores = to_scores(current_run)
        current_data = current_run
comparison_hash = reg.get("anchor_hash") or reg.get("cutoff_hash") or reg.get("best_hash") or rollback_hash
if comparison_hash and comparison_hash in rolling:
    rollback_data = rolling.get(comparison_hash, {})
rollback_scores = to_scores(rollback_data)
current_metrics = metrics(current_scores)
rollback_metrics = metrics(rollback_scores)
current_progress = progress_summary(current_data, len(current_scores))
rollback_progress = progress_summary(rollback_data, len(rollback_scores))
objective_was_trigger = objective_triggered(reg.get("reasons", ""))
wildcard_origin = load_wildcard_origin_for_current(current_hash)
history_scores = read_score_history(score_history_file)

trend_lines = []
if len(history_scores) >= 100:
    recent50 = statistics.mean(history_scores[-50:])
    prev50 = statistics.mean(history_scores[-100:-50])
    trend_lines.append(f"- recent50={recent50:.1f} prev50={prev50:.1f}")
if len(history_scores) >= 200:
    recent100 = statistics.mean(history_scores[-100:])
    prev100 = statistics.mean(history_scores[-200:-100])
    trend_lines.append(f"- recent100={recent100:.1f} prev100={prev100:.1f}")

lines = []
lines.append("# Rollback Analysis")
lines.append("")
lines.append(f"- recorded_at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
if game_num:
    lines.append(f"- game: {game_num}")
lines.append(f"- reverted_from: {current_hash}")
lines.append(f"- reverted_to: {rollback_hash}")
if rollback_note:
    lines.append(f"- target_note: {rollback_note}")
if wildcard_origin:
    origin_type = str(wildcard_origin.get("origin_type") or "wildcard")
    applied = wildcard_origin.get("wildcard_applied") or []
    applied_label = ", ".join(map(str, applied[:8])) if isinstance(applied, list) else str(applied)
    lines.append(f"- escape_context: origin_type={origin_type}")
    if applied_label:
        lines.append(f"- escape_applied: {applied_label}")
    if wildcard_origin.get("wildcard_streak") is not None:
        lines.append(f"- escape_streak: {wildcard_origin.get('wildcard_streak')}")
lines.append(f"- trigger: {(reg.get('reasons') or 'unknown')}")
lines.append("")
lines.append("## Why Rollback Triggered")
for line in explain_reasons(reg.get("reasons", "")):
    lines.append(f"- {line}")
if wildcard_origin:
    origin_type = str(wildcard_origin.get("origin_type") or "wildcard")
    if origin_type == "wildcard":
        lines.append("- WILDCARD起源: 停滞・回帰連鎖から抜けるため、成績の良い過去戦略へ丸ごと戻したのではなく、現戦略の一部パラメータを短時間で揺さぶった試行だった。")
    elif origin_type == "escape_ai":
        lines.append("- escape_ai起源: 連続WILDCARD不発から、AIで小さな構造変異を入れた脱出試行だった。")
    elif origin_type == "archive_restart":
        lines.append("- archive_restart起源: 連続WILDCARD不発から、評価済みの過去版を起点にした大域脱出試行だった。")
if current_metrics:
    lines.append(
        f"- current: comp={fmt_num(current_metrics['comp'])} p50={fmt_num(current_metrics['p50'])} "
        f"p25={fmt_num(current_metrics['p25'])} mean={fmt_num(current_metrics['mean'])} n={current_metrics['n']}"
    )
if rollback_metrics:
    lines.append(
        f"- rollback_target: comp={fmt_num(rollback_metrics['comp'])} p50={fmt_num(rollback_metrics['p50'])} "
        f"p25={fmt_num(rollback_metrics['p25'])} mean={fmt_num(rollback_metrics['mean'])} n={rollback_metrics['n']}"
    )
if reg:
    ref_hash = reg.get("anchor_hash", reg.get("cutoff_hash", reg.get("best_hash", "n/a")))
    ref_comp = reg.get("anchor_comp", reg.get("cutoff_comp", reg.get("best_comp", "n/a")))
    ref_p50 = reg.get("anchor_p50", reg.get("cutoff_p50", reg.get("best_p50", "n/a")))
    ref_p25 = reg.get("anchor_p25", reg.get("cutoff_p25", reg.get("best_p25", "n/a")))
    ref_n = reg.get("anchor_n", reg.get("cutoff_n", reg.get("best_n", "n/a")))
    lines.append(
        f"- compared_anchor: hash={ref_hash} comp={ref_comp} "
        f"p50={ref_p50} p25={ref_p25} n={ref_n}"
    )
    if reg.get("branch_depth") or reg.get("branch_games") or reg.get("branch_patience"):
        lines.append(
            f"- branch_budget: depth={reg.get('branch_depth', 'n/a')} "
            f"games={reg.get('branch_games', 'n/a')} patience={reg.get('branch_patience', 'n/a')}"
        )
    if reg.get("comp_gap") or reg.get("p50_gap") or reg.get("p25_gap"):
        lines.append(
            f"- current_gap_vs_anchor: comp={reg.get('comp_gap', 'n/a')} p50={reg.get('p50_gap', 'n/a')} "
            f"p25={reg.get('p25_gap', 'n/a')} breaches={reg.get('breach_count', 'n/a')}/{reg.get('min_breach_count', 'n/a')}"
        )
    if reg.get("best_hash"):
        lines.append(
            f"- branch_best: hash={reg.get('best_hash')} comp={reg.get('best_comp', 'n/a')} "
            f"p50={reg.get('best_p50', 'n/a')} p25={reg.get('best_p25', 'n/a')} n={reg.get('best_n', 'n/a')}"
        )
        lines.append(
            f"- branch_best_gap_vs_anchor: comp={reg.get('best_comp_gap', 'n/a')} "
            f"p50={reg.get('best_p50_gap', 'n/a')} p25={reg.get('best_p25_gap', 'n/a')} "
            f"breaches={reg.get('best_breach_count', 'n/a')}/{reg.get('min_breach_count', 'n/a')}"
        )
lines.append("")
lines.append("## Defeat Delta")
if current_metrics and rollback_metrics:
    lines.append(
        f"- metric_gap_vs_target: comp={fmt_num(current_metrics['comp'] - rollback_metrics['comp'])} "
        f"p50={fmt_num(current_metrics['p50'] - rollback_metrics['p50'])} "
        f"p25={fmt_num(current_metrics['p25'] - rollback_metrics['p25'])} "
        f"mean={fmt_num(current_metrics['mean'] - rollback_metrics['mean'])}"
    )
if current_scores and rollback_scores:
    current_recent = current_scores[-12:]
    rollback_recent = rollback_scores[-12:]
    lines.append(
        f"- recent12_avg: bad={fmt_num(statistics.mean(current_recent))} "
        f"target={fmt_num(statistics.mean(rollback_recent))}"
    )
    lines.append(
        f"- recent12_floor: bad={min(current_recent)} target={min(rollback_recent)}"
    )
lines.append("")
lines.append("## Soviet Objective Delta")
if not objective_was_trigger:
    lines.append("- note: 今回の粛清トリガは建国未達ではなくスコア/comp系。以下は改善の参考情報で、粛清理由ではない。")
lines.append(f"- current_progress: {fmt_progress(current_progress)}")
if rollback_scores:
    lines.append(f"- rollback_target_progress: {fmt_progress(rollback_progress)}")
lines.append(
    f"- progress_gap_vs_target: best_max_type={current_progress['best_max_type'] - rollback_progress['best_max_type']} "
    f"russia={current_progress['russia_count'] - rollback_progress['russia_count']} "
    f"soviet={current_progress['soviet_count'] - rollback_progress['soviet_count']}"
)
if objective_was_trigger and current_progress["best_max_type"] < 15:
    lines.append("- hard_signal: current はロシア(type15)未到達。次改善ではスコア下振れだけでなく type14→15 の到達経路を復旧すること。")
elif objective_was_trigger and current_progress["soviet_count"] <= 0:
    lines.append("- hard_signal: current はソ連(type16)未到達。ロシア保護と二つ目のロシア育成を優先すること。")
elif not objective_was_trigger and current_progress["best_max_type"] < 15:
    lines.append("- context_signal: current はロシア(type15)未到達だが、今回は粛清理由ではない。スコア/comp悪化の原因分析を優先すること。")
elif not objective_was_trigger and current_progress["soviet_count"] <= 0:
    lines.append("- context_signal: current はソ連(type16)未到達だが、今回は粛清理由ではない。スコア/comp悪化の原因分析を優先すること。")
lines.append("")
lines.append("## Score Pattern")
if current_scores:
    lines.append(f"- bad_strategy_recent_scores: {' '.join(map(str, current_scores[-12:]))}")
    lines.append(f"- bad_strategy_recent_files: {', '.join(recent_archives(current_data)) or 'n/a'}")
if rollback_scores:
    lines.append(f"- rollback_target_recent_scores: {' '.join(map(str, rollback_scores[-12:]))}")
    lines.append(f"- rollback_target_recent_files: {', '.join(recent_archives(rollback_data)) or 'n/a'}")
if trend_lines:
    lines.extend(trend_lines)
lines.append("")
lines.append("## Next Improve Focus")
focus = []
reasons = set((reg.get("reasons") or "").split("+"))
if any(r.startswith("rank") for r in reasons):
    focus.append("- まず cutoff rank の戦略と current の差分を見て、順位を落とした主要因を特定すること。")
if "p25" in reasons:
    focus.append("- 下振れゲームで何を取りこぼしたかを優先分析すること。低スコア回の終盤8ターンと deadline 接近局面を読み直す。")
if "p50" in reasons:
    focus.append("- 典型性能が弱いので、普段の試合で頻出する選択 reason と score_delta のズレを見直すこと。")
if "comp" in reasons:
    focus.append("- comp 悪化なので、単発上振れより mature ranking に残れる再現性を重視すること。")
if "budget_exhausted" in reasons or "depth" in reasons or "games" in reasons or "patience" in reasons:
    focus.append("- branch 全体として伸びが止まった理由を確認すること。各世代で何が改善され、どこで頭打ちになったかを整理する。")
if "hard_fail" in reasons:
    focus.append("- anchor 比で急激に悪化した局面を重点的に調べること。特に p25 を落とした試合群の共通条件を抽出する。")
if "trend50" in reasons or "trend100" in reasons:
    focus.append("- 長期下降トレンドが出ているので、直近だけの上振れを追わず、過去の強戦略との差分を比較すること。")
if objective_was_trigger and current_progress["best_max_type"] < 15:
    focus.append("- 建国目標未達: current は type15 未到達なので、type14 を安全に併合してロシアへ届かせる経路を最優先で分析すること。")
elif objective_was_trigger and current_progress["soviet_count"] <= 0:
    focus.append("- 建国目標未達: ロシア到達後の保護と2個目のロシア育成を最優先で分析すること。")
elif not objective_was_trigger and current_progress["best_max_type"] < 15:
    focus.append("- 参考: ロシア未達は観測されているが今回の粛清理由ではない。まず comp/p25/p50 悪化の直接原因を特定し、その範囲で type14→15 の導線も壊していないか確認すること。")
elif not objective_was_trigger and current_progress["soviet_count"] <= 0:
    focus.append("- 参考: ソ連未達は観測されているが今回の粛清理由ではない。まず comp/p25/p50 悪化の直接原因を特定し、その範囲でロシア後の導線も壊していないか確認すること。")
if not focus:
    focus.append("- rollback の直前12試合と rollback 先の直近12試合を比較して、再発理由を特定すること。")
lines.extend(focus)
lines.append("")

os.makedirs(os.path.dirname(out_file), exist_ok=True)
with open(out_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

summary = []
summary.append(f"- rollback from {current_hash} to {rollback_hash} at game {game_num or '?'}")
summary.append(f"- reasons: {reg.get('reasons', 'unknown')}")
if current_metrics and rollback_metrics:
    summary.append(
        f"- current comp/p50/p25={current_metrics['comp']:.1f}/{current_metrics['p50']:.1f}/{current_metrics['p25']:.1f} "
        f"vs target {rollback_metrics['comp']:.1f}/{rollback_metrics['p50']:.1f}/{rollback_metrics['p25']:.1f}"
    )
if current_scores:
    summary.append(f"- bad recent scores: {' '.join(map(str, current_scores[-8:]))}")
summary.append(
    f"- soviet objective: current best_type={current_progress['best_max_type']} "
    f"russia={current_progress['russia_count']} soviet={current_progress['soviet_count']}"
)
print("\n".join(summary))
PY
}

_wait_pid_with_timeout() {
	local pid="$1" timeout_sec="${2:-30}" label="${3:-job}"
	local waited=0
	while kill -0 "$pid" 2>/dev/null; do
		if [ "$waited" -ge "$timeout_sec" ]; then
			log "[TIMEOUT] ${label} timed out after ${timeout_sec}s; terminating pid=${pid}"
			kill "$pid" 2>/dev/null || true
			sleep 1
			kill -9 "$pid" 2>/dev/null || true
			wait "$pid" 2>/dev/null || true
			return 124
		fi
		sleep 1
		waited=$((waited + 1))
	done
	wait "$pid" 2>/dev/null
}

_write_rollback_postmortem_context_file() {
	local current_hash="$1" rollback_hash="$2" game_num="$3" rollback_note="${4:-}"
	python3 - "$ROLLING_SCORES_FILE" "$STRATEGY_HASH_ARCHIVE_DIR" "$STRATEGY_VERSIONS_DIR" "$STRATEGY_FILE" "tmp/revert_strategy.py" "extract_decide_hash.py" "$current_hash" "$rollback_hash" "$game_num" "$rollback_note" "$ROLLBACK_POSTMORTEM_CONTEXT_FILE" <<'PY'
import json
import os
import re
import subprocess
import sys
import time

rolling_file, archive_dir, versions_dir, strategy_file, revert_file, hash_script, current_hash, rollback_hash, game_num, rollback_note, out_file = sys.argv[1:12]

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        return json.load(open(path))
    except Exception:
        return {}

def unique_existing(paths):
    out = []
    seen = set()
    for raw in paths or []:
        if not isinstance(raw, str):
            continue
        path = raw.strip()
        if not path or path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        out.append(path)
    return out

def score_from_path(path):
    m = re.search(r"_score([0-9]+)\.jsonl$", os.path.basename(path))
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None

def focus_bad_logs(paths):
    ranked = []
    for idx, path in enumerate(paths):
        score = score_from_path(path)
        ranked.append((score if score is not None else 10**9, idx, path))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [path for _, _, path in ranked[:4]] or paths[-4:]

def focus_target_logs(paths):
    return paths[-4:]

def decide_hash(path):
    if not path or not os.path.exists(path):
        return ""
    try:
        result = subprocess.run(
            ["python3", hash_script, path],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    out = result.stdout.strip()
    return out if result.returncode == 0 and out else ""

def find_strategy_file(target_hash):
    if not target_hash:
        return ""
    by_hash = os.path.join(archive_dir, f"{target_hash}.py")
    if os.path.exists(by_hash):
        return by_hash

    candidates = []
    for path in (strategy_file, revert_file):
        if path and os.path.exists(path):
            candidates.append(path)
    if os.path.isdir(versions_dir):
        for name in sorted(os.listdir(versions_dir), reverse=True):
            if name.endswith(".py"):
                candidates.append(os.path.join(versions_dir, name))

    seen = set()
    for path in candidates:
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        if decide_hash(path) == target_hash:
            return path
    return ""

rolling = load_json(rolling_file)
current_data = rolling.get(current_hash, {}) if current_hash else {}
rollback_data = rolling.get(rollback_hash, {}) if rollback_hash else {}

bad_recent = unique_existing((current_data.get("_recent_archives") or [])[-8:])
target_recent = unique_existing((rollback_data.get("_recent_archives") or [])[-8:])
bad_focus = focus_bad_logs(bad_recent)
target_focus = focus_target_logs(target_recent)

bad_strategy_file = find_strategy_file(current_hash)
target_strategy_file = find_strategy_file(rollback_hash)

lines = []
lines.append("# Rollback Postmortem Context")
lines.append("")
lines.append(f"- generated_at: {time.strftime('%Y-%m-%d %H:%M:%S')}")
if game_num:
    lines.append(f"- game: {game_num}")
lines.append(f"- bad_strategy_hash: {current_hash or 'n/a'}")
lines.append(f"- rollback_target_hash: {rollback_hash or 'n/a'}")
if rollback_note:
    lines.append(f"- rollback_target_note: {rollback_note}")
lines.append(f"- bad_strategy_file: {bad_strategy_file or 'n/a'}")
lines.append(f"- rollback_target_file: {target_strategy_file or 'n/a'}")
lines.append("")
lines.append("## Read Order")
lines.append("- まず tmp/state/last_rollback_analysis.md を読む。")
lines.append("- 次に bad strategy source と rollback target source を読む。")
lines.append("- その後 bad logs を最低2件、rollback target logs を最低2件読む。")
lines.append("- 各ログでは終盤8ターン、max_y>=2.0、merge_available、decision_reason を優先確認する。")
lines.append("")
lines.append("## Bad Strategy Logs")
for path in bad_focus:
    score = score_from_path(path)
    score_disp = "?" if score is None else str(score)
    lines.append(f"- {path} score={score_disp}")
if not bad_focus:
    lines.append("- n/a")
lines.append("")
lines.append("## Rollback Target Logs")
for path in target_focus:
    score = score_from_path(path)
    score_disp = "?" if score is None else str(score)
    lines.append(f"- {path} score={score_disp}")
if not target_focus:
    lines.append("- n/a")
lines.append("")
lines.append("## Notes")
lines.append("- bad logs は recent の中でも低スコア寄りを優先抽出している。")
lines.append("- target logs は rollback 先の直近挙動を見るため時系列の新しいものを優先している。")
lines.append("")

os.makedirs(os.path.dirname(out_file), exist_ok=True)
with open(out_file, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

ordered = []
seen = set()
for path in [bad_strategy_file, target_strategy_file, *bad_focus, *target_focus]:
    if not path or path in seen or not os.path.exists(path):
        continue
    seen.add(path)
    ordered.append(path)
for path in ordered:
    print(path)
PY
}

_generate_rollback_postmortem_with_ai() {
	local current_hash="$1" rollback_hash="$2" game_num="$3" rollback_note="${4:-}"
	[ -f "$ROLLBACK_ANALYSIS_FILE" ] || return 1

	mkdir -p "$TMP_STATE_DIR" "$TMP_DEBUG_DIR" 2>/dev/null || true
	local -a extra_files sandbox_ref_files
	local path
	while IFS= read -r path; do
		[ -n "$path" ] && extra_files+=("$path")
	done < <(_write_rollback_postmortem_context_file "$current_hash" "$rollback_hash" "$game_num" "$rollback_note" 2>/dev/null || true)

	sandbox_ref_files=(
		"prompts/rollback_postmortem.md"
		"$ROLLBACK_ANALYSIS_FILE"
		"$ROLLBACK_POSTMORTEM_CONTEXT_FILE"
		"$ROLLING_SCORES_FILE"
		"score_history.txt"
		"eval_score_history.txt"
		"analyze_board.py"
	)
	local f
	for f in "${extra_files[@]}"; do
		[ -f "$f" ] && sandbox_ref_files+=("$f")
	done

	local sandbox_dir=""
	sandbox_dir=$(create_sandbox "${sandbox_ref_files[@]}")
	[ -n "$sandbox_dir" ] && [ -d "$sandbox_dir" ] || return 1

	local rc=1
	if pushd "$sandbox_dir" >/dev/null; then
		mkdir -p "$PWD/$TMP_STATE_DIR" "$PWD/$TMP_DEBUG_DIR" 2>/dev/null || true

		local prev_log="${RUN_CMD_LOG_FILE-}"
		local prev_session_dir="${RUN_CMD_SESSION_DIR-}"
		local prev_tmp_dir="${RUN_CMD_TMP_DIR-}"
		local prev_permission="${RUN_CMD_OPENCODE_PERMISSION-}"
		local prev_retries="${RUN_AI_PRIMARY_RETRIES-}"

		RUN_CMD_LOG_FILE="$ROLLBACK_POSTMORTEM_AI_LOG_FILE"
		RUN_CMD_SESSION_DIR="$PWD/$TMP_STATE_DIR/.rollback_postmortem_sessions"
		RUN_CMD_TMP_DIR="$PWD/$TMP_STATE_DIR/.run_cmd_tmp"
		RUN_CMD_OPENCODE_PERMISSION="${IMPROVE_OPENCODE_PERMISSION:-}"
		RUN_AI_PRIMARY_RETRIES="${ROLLBACK_POSTMORTEM_PRIMARY_RETRIES:-3}"
		RUN_CMD_TIMEOUT_SEC="${ROLLBACK_POSTMORTEM_TIMEOUT_SEC:-300}"
		export RUN_CMD_LOG_FILE RUN_CMD_SESSION_DIR RUN_CMD_TMP_DIR RUN_CMD_OPENCODE_PERMISSION RUN_AI_PRIMARY_RETRIES RUN_CMD_TIMEOUT_SEC
		mkdir -p "$RUN_CMD_SESSION_DIR" "$RUN_CMD_TMP_DIR" 2>/dev/null || true

		run_ai "ROLLBACK-POSTMORTEM" "$ROLLBACK_POSTMORTEM_MODEL" "$ROLLBACK_POSTMORTEM_FALLBACK" \
			"prompts/rollback_postmortem.md" "$ROLLBACK_POSTMORTEM_FILE" \
			"$ROLLBACK_ANALYSIS_FILE" "$ROLLBACK_POSTMORTEM_CONTEXT_FILE"
		rc=$?
		if [ "$rc" -eq 0 ] && [ -s "$ROLLBACK_POSTMORTEM_FILE" ]; then
			mkdir -p "$(dirname "$ELOOP_LIB_DIR/$ROLLBACK_POSTMORTEM_FILE")" 2>/dev/null || true
			cp "$ROLLBACK_POSTMORTEM_FILE" "$ELOOP_LIB_DIR/$ROLLBACK_POSTMORTEM_FILE" 2>/dev/null || rc=1
		fi

		if [ -n "$prev_log" ]; then
			RUN_CMD_LOG_FILE="$prev_log"
			export RUN_CMD_LOG_FILE
		else
			unset RUN_CMD_LOG_FILE
		fi
		if [ -n "$prev_session_dir" ]; then
			RUN_CMD_SESSION_DIR="$prev_session_dir"
			export RUN_CMD_SESSION_DIR
		else
			unset RUN_CMD_SESSION_DIR
		fi
		if [ -n "$prev_tmp_dir" ]; then
			RUN_CMD_TMP_DIR="$prev_tmp_dir"
			export RUN_CMD_TMP_DIR
		else
			unset RUN_CMD_TMP_DIR
		fi
		if [ -n "$prev_permission" ]; then
			RUN_CMD_OPENCODE_PERMISSION="$prev_permission"
			export RUN_CMD_OPENCODE_PERMISSION
		else
			unset RUN_CMD_OPENCODE_PERMISSION
		fi
		if [ -n "$prev_retries" ]; then
			RUN_AI_PRIMARY_RETRIES="$prev_retries"
			export RUN_AI_PRIMARY_RETRIES
		else
			unset RUN_AI_PRIMARY_RETRIES
		fi

		popd >/dev/null || true
	fi

	destroy_sandbox "$sandbox_dir"
	return "$rc"
}

start_rollback_postmortem_worker() {
	local current_hash="$1" rollback_hash="$2" game_num="$3" rollback_note="${4:-}"
	[ -f "$ROLLBACK_ANALYSIS_FILE" ] || return 0

	# 粛清カスケード中は AI postmortem (opencode スロットを長時間占有・診断専用) を
	# スキップ。ルールベース ROLLBACK_ANALYSIS_FILE は既に書かれており下流は維持。
	# スロット競合で改善サイクルが長期化→soren91 代打が無限化するのを防ぐ。
	if [ "${ROLLBACK_POSTMORTEM_CASCADE_SKIP_ENABLED:-1}" = "1" ]; then
		local _pm_rstreak=0
		_pm_rstreak=$(python3 -c "
import json
try:
    print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('regression_streak', 0)))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
		if [ "${_pm_rstreak:-0}" -ge "${ROLLBACK_POSTMORTEM_CASCADE_SKIP_STREAK:-2}" ]; then
			log "[ROLLBACK-POSTMORTEM] カスケード中 (regression_streak=${_pm_rstreak} >= ${ROLLBACK_POSTMORTEM_CASCADE_SKIP_STREAK:-2}) → AI postmortem スキップ (ルールベース分析のみ)"
			return 0
		fi
	fi

	local running_pid=""
	if [ -f "$ROLLBACK_POSTMORTEM_PID_FILE" ]; then
		running_pid=$(cat "$ROLLBACK_POSTMORTEM_PID_FILE" 2>/dev/null || echo "")
		case "$running_pid" in
		'' | *[!0-9]*) running_pid="" ;;
		esac
	fi
	if [ -n "$running_pid" ] && kill -0 "$running_pid" 2>/dev/null; then
		log "[ROLLBACK-POSTMORTEM] 既存 worker 停止 (PID=$running_pid)"
		pkill -P "$running_pid" 2>/dev/null || true
		_stop_pid_with_fallback "$running_pid" "rollback_postmortem"
		wait "$running_pid" 2>/dev/null || true
	fi

	rm -f "$ROLLBACK_POSTMORTEM_PID_FILE" "$ROLLBACK_POSTMORTEM_FILE"
	(
		local worker_pid
		worker_pid=$(_my_pid)
		printf '%s\n' "$worker_pid" >"$ROLLBACK_POSTMORTEM_PID_FILE"
		trap 'rm -f "$ROLLBACK_POSTMORTEM_PID_FILE"' EXIT
		log "[ROLLBACK-POSTMORTEM] start: game=${game_num:-?} from=${current_hash:0:8} to=${rollback_hash:0:8}"
		if _generate_rollback_postmortem_with_ai "$current_hash" "$rollback_hash" "$game_num" "$rollback_note"; then
			log "[ROLLBACK-POSTMORTEM] written: $ROLLBACK_POSTMORTEM_FILE"
		else
			log "[ROLLBACK-POSTMORTEM] failed -> fallback to rule-based rollback analysis only"
		fi
	) >>"$ROLLBACK_POSTMORTEM_AI_LOG_FILE" 2>&1 &
}

#=== ローリングスコア & リグレッション検知 ===

_archive_strategy_snapshot_by_hash() {
	local source_file="$1" hash_value="$2"
	[ -f "$source_file" ] || return 0
	if [ -z "$hash_value" ] || [ "$hash_value" = "unknown" ]; then
		hash_value=$(python3 extract_decide_hash.py "$source_file" 2>/dev/null || echo "")
	fi
	[ -z "$hash_value" ] && return 0
	mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR"
	local dst="$STRATEGY_HASH_ARCHIVE_DIR/${hash_value}.py"
	if [ ! -f "$dst" ]; then
		cp "$source_file" "$dst" 2>/dev/null || true
	fi
	# 永続アーカイブにも保存（prune されない。backfill のフォールバック元）
	if [ -n "${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-}" ]; then
		mkdir -p "$STRATEGY_HASH_PERMANENT_ARCHIVE_DIR"
		local perm_dst="$STRATEGY_HASH_PERMANENT_ARCHIVE_DIR/${hash_value}.py"
		if [ ! -f "$perm_dst" ]; then
			cp "$source_file" "$perm_dst" 2>/dev/null || true
		fi
	fi
}

_strategy_file_hash_matches() {
	local expected_hash="$1" candidate_file="$2"
	[ -n "$expected_hash" ] || return 1
	[ -f "$candidate_file" ] || return 1
	local actual_hash
	actual_hash=$(python3 extract_decide_hash.py "$candidate_file" 2>/dev/null || echo "")
	[ "$actual_hash" = "$expected_hash" ]
}

_find_rollback_candidate_file_for_hash() {
	local target_hash="$1"
	[ -n "$target_hash" ] || return 1
	local primary_file="$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py"
	local permanent_file="${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}/${target_hash}.py"
	if _strategy_file_hash_matches "$target_hash" "$primary_file"; then
		echo "$primary_file"
		return 0
	fi
	if _strategy_file_hash_matches "$target_hash" "$permanent_file"; then
		if [ -f "$primary_file" ]; then
			log "[HASH-ARCHIVE] repairing stale by_hash archive: ${target_hash}" >&2
		fi
		mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR" 2>/dev/null || true
		cp "$permanent_file" "$primary_file" 2>/dev/null || true
		echo "$permanent_file"
		return 0
	fi
	[ -f "$primary_file" ] && echo "$primary_file" && return 0
	[ -f "$permanent_file" ] && echo "$permanent_file" && return 0
	return 1
}

_backfill_hash_archive_from_known_versions() {
	local include_permanent="${1:-${HASH_ARCHIVE_RESTORE_PERMANENT:-0}}"
	mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR"
	local f
	[ -f "$STRATEGY_FILE" ] && _archive_strategy_snapshot_by_hash "$STRATEGY_FILE"
	[ -f "tmp/revert_strategy.py" ] && _archive_strategy_snapshot_by_hash "tmp/revert_strategy.py"
	for f in "$STRATEGY_VERSIONS_DIR"/v*_strategy.py "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
		[ -f "$f" ] || continue
		_archive_strategy_snapshot_by_hash "$f"
	done
	# 永続アーカイブは大量になりやすい。毎試合復元すると直後の prune と
	# 復元削除ループになり、次ゲーム開始を遅らせるため明示時だけ戻す。
	if [ "$include_permanent" = "1" ] && [ -n "${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-}" ] && [ -d "$STRATEGY_HASH_PERMANENT_ARCHIVE_DIR" ]; then
		local perm_file base
		for perm_file in "$STRATEGY_HASH_PERMANENT_ARCHIVE_DIR"/*.py; do
			[ -f "$perm_file" ] || continue
			base=$(basename "$perm_file")
			if [ ! -f "$STRATEGY_HASH_ARCHIVE_DIR/$base" ] || ! _strategy_file_hash_matches "${base%.py}" "$STRATEGY_HASH_ARCHIVE_DIR/$base"; then
				cp "$perm_file" "$STRATEGY_HASH_ARCHIVE_DIR/$base" 2>/dev/null || true
			fi
		done
	fi
}

_find_strategy_file_by_hash() {
	local target_hash="$1"
	[ -z "$target_hash" ] && return 1
	_find_rollback_candidate_file_for_hash "$target_hash"
}

# E: 失敗 current の挙動シグネチャを tabu_signatures.jsonl に追記する
# 引数: $1=失敗した戦略 hash
# rolling_scores.json[hash]._recent_archives から jsonl を取得して shape を生成
# wildcard 起源の場合は decay 期間を半分にする
_record_tabu_signature() {
	type reload_runtime_toggles >/dev/null 2>&1 && reload_runtime_toggles
	local failed_hash="$1"
	[ -n "$failed_hash" ] || return 1
	[ "${TABU_ENABLED:-0}" = "1" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	python3 - "$ROLLING_SCORES_FILE" "$TABU_SIGNATURES_FILE" "$BEHAVIOR_SIGNATURES_FILE" "$failed_hash" \
		"$WILDCARD_ORIGIN_FILE" "${TABU_DECAY_GAMES:-192}" "${TABU_RETAIN:-20}" \
		"${MIN_GAMES_BEFORE_IMPROVE:-12}" "$(pwd)" <<'PY' 2>/dev/null
import json
import os
import sys
import time

rs_file = sys.argv[1]
tabu_file = sys.argv[2]
sig_cache_file = sys.argv[3]
failed_hash = sys.argv[4]
wildcard_file = sys.argv[5]
decay_games = int(sys.argv[6])
retain = int(sys.argv[7])
min_games_improve = int(sys.argv[8])
repo_root = sys.argv[9]

try:
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from lib.behavior_signature import compute_signature
except Exception:
    raise SystemExit(0)

try:
    rs = json.load(open(rs_file))
except Exception:
    raise SystemExit(0)
entry = rs.get(failed_hash) or {}
archives = entry.get("_recent_archives", []) or []
# git: 参照はそのまま渡す。通常パスは存在チェック
def _archive_usable(a):
    if not a:
        return False
    if a.startswith("git:"):
        return True
    return os.path.exists(a)
archives = [a for a in archives if _archive_usable(a)]
if not archives:
    raise SystemExit(0)

sig = compute_signature(archives[-12:])

# wildcard 起源 hash なら decay を半分に
half_decay = False
if wildcard_file and os.path.exists(wildcard_file):
    try:
        wo = json.load(open(wildcard_file))
        if failed_hash in wo:
            half_decay = True
    except Exception:
        pass

# 現在の総ゲーム数で expire 時刻を計算
now_games = sum(int((v or {}).get("games_total", 0)) for v in rs.values())
decay_until = now_games + (decay_games // 2 if half_decay else decay_games)

# 既存 tabu を読み、retain 件まで保持
existing = []
if os.path.exists(tabu_file):
    try:
        with open(tabu_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    existing.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        existing = []
# 失効済みは間引く
existing = [e for e in existing if int(e.get("decay_until_games", 0) or 0) > now_games]
# 同一 hash の旧エントリは置換
existing = [e for e in existing if e.get("hash") != failed_hash]
existing.append({
    "hash": failed_hash,
    "signature": sig,
    "decay_until_games": decay_until,
    "recorded_at": int(time.time()),
    "wildcard_origin": half_decay,
})
existing = existing[-retain:]

os.makedirs(os.path.dirname(tabu_file) or ".", exist_ok=True)
tmp_path = tabu_file + ".tmp"
with open(tmp_path, "w", encoding="utf-8") as f:
    for e in existing:
        f.write(json.dumps(e, ensure_ascii=False) + "\n")
os.replace(tmp_path, tabu_file)

# シグネチャキャッシュにも追記しておく (anchor refresh 側で再利用)
if sig_cache_file:
    try:
        cache = {}
        if os.path.exists(sig_cache_file):
            cache = json.load(open(sig_cache_file, encoding="utf-8"))
        cache[failed_hash] = sig
        os.makedirs(os.path.dirname(sig_cache_file) or ".", exist_ok=True)
        tmp = sig_cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, sig_cache_file)
    except Exception:
        pass
PY
}

_refresh_best_strategy_anchor() {
	type reload_runtime_toggles >/dev/null 2>&1 && reload_runtime_toggles
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	_backfill_hash_archive_from_known_versions >/dev/null 2>&1 || true
	local current_hash="${1:-}"
	python3 - "$ROLLING_SCORES_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$current_hash" "$STRATEGY_HASH_ARCHIVE_DIR" "$REJECTED_HASHES_FILE" \
		"${DIVERSITY_PREMIUM_ENABLED:-0}" "${DIVERSITY_PREMIUM_WEIGHT:-300}" "${EXPLORE_GAP_MAX_RATIO:-0.07}" \
		"${TABU_ENABLED:-0}" "${TABU_SIGNATURES_FILE:-tmp/state/tabu_signatures.jsonl}" "${TABU_DISTANCE_THRESHOLD:-0.15}" \
		"${BEHAVIOR_SIGNATURES_FILE:-tmp/state/behavior_signatures.json}" "${LAST_ANCHOR_CHANGE_FILE:-tmp/state/last_anchor_change.md}" \
		"$(pwd)" "${OBJECTIVE_ANCHOR_PRIORITY_ENABLED:-1}" "${OBJECTIVE_ANCHOR_MIN_COMP_RATIO:-0.90}" "${OBJECTIVE_ANCHOR_MAX_COMP_GAP:-1500}" \
		"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}" <<'PY'
import json
import math
import os
import sys
import time
from pathlib import Path

rs_file, anchor_file = sys.argv[1], sys.argv[2]
min_games = int(sys.argv[3])
lcb_z = float(sys.argv[4])
w_p50 = float(sys.argv[5])
w_p25 = float(sys.argv[6])
w_lcb = float(sys.argv[7])
current_hash = sys.argv[8] if len(sys.argv) > 8 else ""
archive_dir = sys.argv[9] if len(sys.argv) > 9 else ""
rejected_file = sys.argv[10] if len(sys.argv) > 10 else ""
# 帯域脱出機構 (D + E) 追加引数
diversity_enabled = sys.argv[11] == "1" if len(sys.argv) > 11 else False
diversity_weight = float(sys.argv[12]) if len(sys.argv) > 12 else 300.0
explore_gap_max_ratio = float(sys.argv[13]) if len(sys.argv) > 13 else 0.07
tabu_enabled = sys.argv[14] == "1" if len(sys.argv) > 14 else False
tabu_file = sys.argv[15] if len(sys.argv) > 15 else ""
tabu_distance_threshold = float(sys.argv[16]) if len(sys.argv) > 16 else 0.15
behavior_sigs_file = sys.argv[17] if len(sys.argv) > 17 else ""
last_anchor_change_file = sys.argv[18] if len(sys.argv) > 18 else ""
repo_root = sys.argv[19] if len(sys.argv) > 19 else ""
objective_anchor_enabled = (sys.argv[20] if len(sys.argv) > 20 else "1") == "1"
objective_anchor_min_comp_ratio = float(sys.argv[21]) if len(sys.argv) > 21 else 0.90
objective_anchor_max_comp_gap = float(sys.argv[22]) if len(sys.argv) > 22 else 1500.0
permanent_archive_dir = sys.argv[23] if len(sys.argv) > 23 else ""

# lib.behavior_signature の import (帯域脱出機構 ON 時のみ必要)
_compute_signature = None
_signature_distance = None
if (diversity_enabled or tabu_enabled) and repo_root:
    try:
        if repo_root not in sys.path:
            sys.path.insert(0, repo_root)
        from lib.behavior_signature import compute_signature as _cs, signature_distance as _sd
        _compute_signature = _cs
        _signature_distance = _sd
    except Exception:
        # ライブラリ読み込み失敗時は安全側で無効化
        diversity_enabled = False
        tabu_enabled = False

try:
    rs = json.load(open(rs_file))
except Exception:
    raise SystemExit(0)

rejected = set()
if rejected_file and os.path.exists(rejected_file):
    try:
        with open(rejected_file, encoding="utf-8", errors="ignore") as f:
            rejected = {line.strip() for line in f if line.strip()}
    except Exception:
        rejected = set()

# シグネチャキャッシュ (hash -> signature)
def _load_sig_cache():
    if not behavior_sigs_file or not os.path.exists(behavior_sigs_file):
        return {}
    try:
        return json.load(open(behavior_sigs_file, encoding="utf-8"))
    except Exception:
        return {}

def _save_sig_cache(cache):
    if not behavior_sigs_file:
        return
    try:
        os.makedirs(os.path.dirname(behavior_sigs_file) or ".", exist_ok=True)
        tmp = behavior_sigs_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, behavior_sigs_file)
    except Exception:
        pass

sig_cache = _load_sig_cache() if (diversity_enabled or tabu_enabled) else {}

def _signature_for(h, rs_entry):
    if not _compute_signature:
        return None
    cached = sig_cache.get(h)
    archives = rs_entry.get("_recent_archives", []) or []
    # git: 参照はそのまま、それ以外は存在チェック
    archives = [a for a in archives if a and (a.startswith("git:") or os.path.exists(a))]
    if cached and cached.get("n_games", 0) >= min(len(archives), 6):
        return cached
    if not archives:
        return None
    sig = _compute_signature(archives[-12:])
    sig_cache[h] = sig
    return sig

# Tabu リスト読み込み (活性なものだけ)
tabu_entries = []
if tabu_enabled and tabu_file and os.path.exists(tabu_file):
    # rolling_scores の games_total 合計を現在のゲーム数の近似として使う
    now_games = sum(int((v or {}).get("games_total", 0)) for v in rs.values())
    try:
        with open(tabu_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                decay_until = int(entry.get("decay_until_games", 0) or 0)
                if decay_until and decay_until <= now_games:
                    continue
                if not entry.get("signature"):
                    continue
                tabu_entries.append(entry)
    except Exception:
        tabu_entries = []

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    xs = [int(v) for v in scores]
    if len(xs) < min_games:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    comp = (w_p50 * p50) + (w_p25 * p25) + (w_lcb * lcb)
    return {
        "comp": comp,
        "p50": p50,
        "p25": p25,
        "lcb": lcb,
        "n": n,
    }

def objective_progress(data):
    max_types = []
    for x in data.get("max_types", []) or []:
        try:
            max_types.append(int(x))
        except Exception:
            pass
    best_max_type = max([int(data.get("best_max_type", 0) or 0)] + max_types) if max_types or data.get("best_max_type") else 0
    return {
        "best_max_type": best_max_type,
        "russia_count": int(data.get("russia_count", 0) or 0),
        "soviet_count": int(data.get("soviet_count", 0) or 0),
    }

def archive_is_runtime_stable(path):
    if not path:
        return True
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            src = f.read(200000)
    except Exception:
        return False
    # validate_strategy auto-injects this guard. Archives without it normalize to
    # a different hash on rollback, so they cannot be durable anchors.
    return "BEGIN DEADLINE GUARD" in src

# 候補リストを作る (raw metrics で comp 上位順)
candidates = []
for h, data in rs.items():
    if current_hash and h == current_hash:
        continue
    if h in rejected:
        continue
    archive_paths = []
    if archive_dir:
        archive_paths.append(os.path.join(archive_dir, f"{h}.py"))
    if permanent_archive_dir:
        archive_paths.append(os.path.join(permanent_archive_dir, f"{h}.py"))
    archive_path = next((p for p in archive_paths if os.path.exists(p) and archive_is_runtime_stable(p)), "")
    if (archive_dir or permanent_archive_dir) and not archive_path:
        continue
    m = metrics(data.get("scores", []))
    if not m:
        continue
    candidates.append((h, m, data))

if not candidates:
    raise SystemExit(0)

# E: Tabu フィルタ — rollback された挙動近傍を anchor 昇格不可にする
def _is_tabu(h, m, data):
    if not tabu_enabled or not tabu_entries:
        return False
    sig = _signature_for(h, data)
    if not sig or not _signature_distance:
        return False
    for entry in tabu_entries:
        d = _signature_distance(sig, entry.get("signature", {}))
        if d < tabu_distance_threshold:
            return True
    return False

candidates = [(h, m, data) for (h, m, data) in candidates if not _is_tabu(h, m, data)]
if not candidates:
    raise SystemExit(0)

# 現アンカー候補を先に決める (raw comp 最大)
candidates.sort(key=lambda t: (t[1]["comp"], t[1]["p50"], t[1]["p25"], t[1]["n"], t[0]))
top_anchor = candidates[-1]
top_anchor_comp = top_anchor[1]["comp"]

# D: 多様性プレミアム — selection_score は順位比較専用 (永続化しない)
explore_gap_max = top_anchor_comp * explore_gap_max_ratio if top_anchor_comp > 0 else 0.0
top_anchor_sig = _signature_for(top_anchor[0], top_anchor[2]) if diversity_enabled else None

# anchor sig が取れない場合のフォールバック: 候補全体のシグネチャ centroid を疑似 anchor とする
# 既存戦略の game_history が pruning で消えて sig 計算不能でも D を動かせるようにする
fallback_sigs = []
if diversity_enabled and not top_anchor_sig:
    for h, m, data in candidates:
        s = _signature_for(h, data)
        if s and s.get("n_turns", 0) > 0:
            fallback_sigs.append(s)

def _centroid_distance(target_sig, sigs_pool):
    """target が pool 全体から平均的にどれだけ離れているか (0..1)。
    シンプルに pool 内の各シグネチャとの平均 JSD を返す。
    """
    if not target_sig or not sigs_pool or not _signature_distance:
        return 0.0
    dists = [_signature_distance(target_sig, s) for s in sigs_pool]
    return sum(dists) / len(dists) if dists else 0.0

def _selection_score(h, m, data):
    base = m["comp"]
    if not diversity_enabled or explore_gap_max <= 0:
        return base, 0.0
    gap = top_anchor_comp - m["comp"]
    if gap >= explore_gap_max or gap <= 0:
        return base, 0.0
    sig = _signature_for(h, data)
    if not sig or not _signature_distance:
        return base, 0.0
    if top_anchor_sig:
        # 通常: anchor との距離で premium
        dist = _signature_distance(top_anchor_sig, sig)
    elif fallback_sigs:
        # フォールバック: 候補 pool centroid からの距離で premium
        # 自分自身を pool から除外
        peers = [s for s in fallback_sigs if s is not sig]
        if not peers:
            return base, 0.0
        dist = _centroid_distance(sig, peers)
    else:
        return base, 0.0
    premium = diversity_weight * dist
    return base + premium, premium

def _objective_tuple(data):
    p = objective_progress(data)
    return (
        int(p.get("soviet_count", 0) > 0),
        int(p.get("russia_count", 0) > 0),
        int(p.get("best_max_type", 0) or 0),
        int(p.get("soviet_count", 0) or 0),
        int(p.get("russia_count", 0) or 0),
    )

def _anchor_rank_key(h, m, data, selection_score):
    # Rollback anchors must stay score-mature, but the Soviet objective is the
    # real target. If an objective-progress candidate is still near the score
    # leader, prefer it over a score-only local optimum.
    objective_key = (0, 0, 0, 0, 0)
    if objective_anchor_enabled:
        score_gap = max(0.0, top_anchor_comp - float(m.get("comp", 0.0) or 0.0))
        near_score_leader = (
            float(m.get("comp", 0.0) or 0.0) >= top_anchor_comp * objective_anchor_min_comp_ratio
            or score_gap <= objective_anchor_max_comp_gap
        )
        if near_score_leader:
            objective_key = _objective_tuple(data)
    return (
        *objective_key,
        selection_score,
        m["p50"],
        m["p25"],
        m["n"],
        h,
    )

ranked = []
for h, m, data in candidates:
    sel, premium = _selection_score(h, m, data)
    ranked.append((_anchor_rank_key(h, m, data, sel), sel, m["comp"], m["p50"], m["p25"], m["n"], h, m, premium, data))

ranked.sort()
best = ranked[-1]
best_rank_key, _, _, _, _, _, best_hash, best_metrics, best_premium, best_data = best
existing = {}
anchor_path = Path(anchor_file)
if anchor_path.exists():
    try:
        existing = json.loads(anchor_path.read_text())
    except Exception:
        existing = {}

replace = False
if not existing:
    replace = True
else:
    existing_hash = str(existing.get("hash", "") or "")
    existing_live = None
    if existing_hash:
        existing_scores = []
        try:
            existing_scores = rs.get(existing_hash, {}).get("scores", []) or []
        except Exception:
            existing_scores = []
        existing_live = metrics(existing_scores)
    existing_key = (
        float(existing.get("comp", 0.0)),
        float(existing.get("p50", 0.0)),
        float(existing.get("p25", 0.0)),
        int(existing.get("n", 0)),
        existing_hash,
    )
    if existing_live:
        existing_key = (
            existing_live["comp"],
            existing_live["p50"],
            existing_live["p25"],
            existing_live["n"],
            existing_hash,
        )
    # 比較は目的優先 key + selection_score (premium 込み) で行う。Existing がランキング外なら premium は 0
    existing_data = rs.get(existing_hash, {}) if existing_hash else {}
    existing_key_select = _anchor_rank_key(existing_hash, {
        "comp": existing_key[0],
        "p50": existing_key[1],
        "p25": existing_key[2],
        "lcb": float(existing.get("lcb", 0.0)),
        "n": existing_key[3],
    }, existing_data, existing_key[0])
    existing_archive_paths = []
    if existing_hash and archive_dir:
        existing_archive_paths.append(os.path.join(archive_dir, f"{existing_hash}.py"))
    if existing_hash and permanent_archive_dir:
        existing_archive_paths.append(os.path.join(permanent_archive_dir, f"{existing_hash}.py"))
    existing_has_file = bool(existing_hash) and any(
        os.path.exists(path) and archive_is_runtime_stable(path)
        for path in existing_archive_paths
    )
    existing_rejected = bool(existing_hash) and existing_hash in rejected
    if not existing_has_file:
        replace = True
    elif existing_live is None:
        replace = True
    elif existing_rejected:
        replace = True
    elif current_hash and existing_hash == current_hash:
        replace = False
    elif existing_hash == best_hash:
        replace = True
    elif best_rank_key > existing_key_select:
        replace = True

if not replace:
    raise SystemExit(0)

best_objective = objective_progress(best_data)
payload = {
    "hash": best_hash,
    "comp": round(best_metrics["comp"], 4),
    "p50": round(best_metrics["p50"], 4),
    "p25": round(best_metrics["p25"], 4),
    "lcb": round(best_metrics["lcb"], 4),
    "n": int(best_metrics["n"]),
    "best_max_type": int(best_objective.get("best_max_type", 0) or 0),
    "russia_count": int(best_objective.get("russia_count", 0) or 0),
    "soviet_count": int(best_objective.get("soviet_count", 0) or 0),
    "updated_at": int(time.time()),
}
anchor_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

# 観測ログ: なぜこの戦略が選ばれたか (diversity premium の内訳)
if last_anchor_change_file:
    try:
        os.makedirs(os.path.dirname(last_anchor_change_file) or ".", exist_ok=True)
        prev_hash = str(existing.get("hash", "")) if existing else ""
        body = (
            f"# anchor change @ {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"- prev: {prev_hash}\n"
            f"- new: {best_hash}\n"
            f"- comp_raw: {best_metrics['comp']:.2f}\n"
            f"- diversity_premium: {best_premium:.2f}\n"
            f"- selection_score: {best_metrics['comp'] + best_premium:.2f}\n"
            f"- p50: {best_metrics['p50']:.2f}, p25: {best_metrics['p25']:.2f}, n: {best_metrics['n']}\n"
            f"- objective: best_type={best_objective.get('best_max_type', 0)} "
            f"russia={best_objective.get('russia_count', 0)} soviet={best_objective.get('soviet_count', 0)}\n"
            f"- objective_priority_enabled: {objective_anchor_enabled}, objective_rank_key: {best_rank_key[:6]}\n"
            f"- diversity_enabled: {diversity_enabled}, tabu_enabled: {tabu_enabled}, "
            f"tabu_active: {len(tabu_entries)}\n"
        )
        with open(last_anchor_change_file, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        pass

# シグネチャキャッシュ永続化 (computeしたものを残す)
if diversity_enabled or tabu_enabled:
    _save_sig_cache(sig_cache)

print(best_hash)
PY
}

_has_active_branch() {
	[ -f "$ACTIVE_BRANCH_FILE" ] || return 1
	python3 - "$ACTIVE_BRANCH_FILE" <<'PY' >/dev/null 2>&1
import json
import os
import sys

path = sys.argv[1]
if not os.path.exists(path):
    raise SystemExit(1)
try:
    data = json.load(open(path))
except Exception:
    raise SystemExit(1)
if str(data.get("head_hash", "") or ""):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

_clear_active_branch() {
	rm -f "$ACTIVE_BRANCH_FILE" 2>/dev/null || true
}

_promote_current_strategy_to_anchor() {
	local current_hash="$1"
	[ -n "$current_hash" ] || return 1
	local current_metrics=""
	current_metrics=$(_get_current_strategy_run_metrics "$current_hash" 2>/dev/null || true)
	[ -z "$current_metrics" ] && current_metrics=$(_get_rolling_metrics_for_hash "$current_hash" 2>/dev/null || true)
	[ -n "$current_metrics" ] || return 1
	python3 - "$BEST_STRATEGY_ANCHOR_FILE" "$current_hash" "$current_metrics" "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "${EARLY_OBJECTIVE_REGRESSION_MIN_BEST_TYPE:-15}" "${LAST_ANCHOR_CHANGE_FILE:-tmp/state/last_anchor_change.md}" <<'PY' >/dev/null 2>&1
import json
import os
import sys
import time

out_file, current_hash, metrics_line, rolling_file, current_run_file, min_best_type_raw = sys.argv[1:7]
last_anchor_change_file = sys.argv[7] if len(sys.argv) > 7 else ""
parts = (metrics_line or "").split("|")
if len(parts) < 5:
    raise SystemExit(1)
try:
    min_best_type = int(min_best_type_raw)
except Exception:
    min_best_type = 15

def load_json(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def objective_progress(data):
    data = data or {}
    max_types = []
    for x in data.get("max_types", []) or []:
        try:
            max_types.append(int(x))
        except Exception:
            pass
    best_max_type = max([int(data.get("best_max_type", 0) or 0)] + max_types) if max_types or data.get("best_max_type") else 0
    russia_count = int(data.get("russia_count", 0) or 0)
    soviet_count = int(data.get("soviet_count", 0) or 0)
    if best_max_type >= 15 and russia_count <= 0:
        russia_count = 1
    if best_max_type >= 16 and soviet_count <= 0:
        soviet_count = 1
    return {"best_max_type": best_max_type, "russia_count": russia_count, "soviet_count": soviet_count}

existing = load_json(out_file)
rolling = load_json(rolling_file)
current_data = rolling.get(current_hash, {}) if isinstance(rolling.get(current_hash, {}), dict) else {}
current_run = load_json(current_run_file)
if str(current_run.get("hash", "") or "") == current_hash:
    current_data = current_run
existing_progress = objective_progress(existing)
current_progress = objective_progress(current_data)
if int(existing_progress.get("soviet_count", 0) or 0) > 0 and int(current_progress.get("soviet_count", 0) or 0) <= 0:
    raise SystemExit(1)
if int(existing_progress.get("russia_count", 0) or 0) > 0 and int(current_progress.get("russia_count", 0) or 0) <= 0:
    raise SystemExit(1)
if int(existing_progress.get("best_max_type", 0) or 0) >= min_best_type and int(current_progress.get("best_max_type", 0) or 0) < min_best_type:
    raise SystemExit(1)

payload = {
    "hash": current_hash,
    "comp": round(float(parts[0]), 4),
    "p50": round(float(parts[1]), 4),
    "p25": round(float(parts[2]), 4),
    "lcb": round(float(parts[3]), 4),
    "n": int(float(parts[4])),
    "best_max_type": int(current_progress.get("best_max_type", 0) or 0),
    "russia_count": int(current_progress.get("russia_count", 0) or 0),
    "soviet_count": int(current_progress.get("soviet_count", 0) or 0),
    "updated_at": int(time.time()),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)

if last_anchor_change_file:
    try:
        os.makedirs(os.path.dirname(last_anchor_change_file) or ".", exist_ok=True)
        prev_hash = str(existing.get("hash", "") or "")
        body = (
            f"# anchor change @ {time.strftime('%Y-%m-%dT%H:%M:%S')}\n"
            f"- prev: {prev_hash}\n"
            f"- new: {current_hash}\n"
            f"- source: promote_current_strategy\n"
            f"- comp_raw: {float(parts[0]):.2f}\n"
            f"- p50: {float(parts[1]):.2f}, p25: {float(parts[2]):.2f}, n: {int(float(parts[4]))}\n"
            f"- objective: best_type={current_progress.get('best_max_type', 0)} "
            f"russia={current_progress.get('russia_count', 0)} soviet={current_progress.get('soviet_count', 0)}\n"
        )
        with open(last_anchor_change_file, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        pass
PY
}

_branch_transition_after_improve() {
	local base_hash="$1" new_hash="$2"
	[ -n "$new_hash" ] || return 1
	_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	python3 - "$ACTIVE_BRANCH_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$base_hash" "$new_hash" "$(date +%s)" <<'PY' 2>/dev/null
import json
import math
import os
import sys

active_file, run_file, anchor_file, base_hash, new_hash, now_raw = sys.argv[1:7]
now = int(now_raw)

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def metrics_from_run(path, target_hash):
    run = load_json(path)
    if str(run.get("hash", "") or "") != target_hash:
        return None
    scores = []
    for x in run.get("scores", []) or []:
        try:
            scores.append(int(x))
        except Exception:
            pass
    if not scores:
        return None
    xs = sorted(scores)
    n = len(xs)
    mean = sum(xs) / n
    if n == 1:
        p25 = p50 = float(xs[0])
        std = 0.0
    else:
        def q(p):
            pos = (n - 1) * p
            lo = int(pos)
            hi = min(lo + 1, n - 1)
            frac = pos - lo
            return xs[lo] * (1.0 - frac) + xs[hi] * frac
        p25 = q(0.25)
        p50 = q(0.50)
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {
        "comp": round(comp, 4),
        "p50": round(p50, 4),
        "p25": round(p25, 4),
        "lcb": round(lcb, 4),
        "n": int(n),
    }

def key(metrics):
    if not metrics:
        return (-10**18, -10**18, -10**18, -10**18)
    return (
        float(metrics.get("comp", 0.0)),
        float(metrics.get("p50", 0.0)),
        float(metrics.get("p25", 0.0)),
        int(metrics.get("n", 0)),
    )

active = load_json(active_file)
anchor = load_json(anchor_file)
base_metrics = metrics_from_run(run_file, base_hash) if base_hash else None

anchor_hash = str(anchor.get("hash", "") or "")
anchor_metrics = {
    "comp": float(anchor.get("comp", 0.0) or 0.0),
    "p50": float(anchor.get("p50", 0.0) or 0.0),
    "p25": float(anchor.get("p25", 0.0) or 0.0),
    "lcb": float(anchor.get("lcb", 0.0) or 0.0),
    "n": int(anchor.get("n", 0) or 0),
} if anchor_hash else {}
if not anchor_hash and base_hash and base_metrics:
    anchor_hash = base_hash
    anchor_metrics = dict(base_metrics)

if not anchor_hash:
    raise SystemExit(1)

existing_head = str(active.get("head_hash", "") or "")
existing_anchor_hash = str(active.get("anchor_hash", "") or "")
if existing_head and existing_head == base_hash and existing_anchor_hash:
    best_hash = str(active.get("best_hash", "") or "")
    best_metrics = active.get("best", {}) if isinstance(active.get("best"), dict) else {}
    patience = int(active.get("patience", 0) or 0)
    closed_games = int(active.get("closed_games", 0) or 0)
    depth = int(active.get("depth", 0) or 0)
    lineage = [str(x) for x in (active.get("lineage", []) or []) if str(x)]

    if base_metrics:
        closed_games += int(base_metrics.get("n", 0) or 0)
        if key(base_metrics) > key(best_metrics):
            best_hash = base_hash
            best_metrics = dict(base_metrics)
            patience = 0
        else:
            patience += 1
    payload = {
        "anchor_hash": existing_anchor_hash,
        "anchor": active.get("anchor", anchor_metrics) if isinstance(active.get("anchor"), dict) else anchor_metrics,
        "head_hash": new_hash,
        "best_hash": best_hash,
        "best": best_metrics,
        "depth": depth + 1,
        "closed_games": closed_games,
        "patience": patience,
        "lineage": (lineage + [new_hash])[-12:],
        "started_at": int(active.get("started_at", now) or now),
        "updated_at": now,
    }
    with open(active_file, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(
        f"continue|anchor={existing_anchor_hash[:8]}|head={new_hash[:8]}|"
        f"depth={payload['depth']}|closed={closed_games}|patience={patience}|best={(best_hash[:8] if best_hash else '-')}"
    )
    raise SystemExit(0)

payload = {
    "anchor_hash": anchor_hash,
    "anchor": anchor_metrics,
    "head_hash": new_hash,
    "best_hash": "",
    "best": {},
    "depth": 1,
    "closed_games": 0,
    "patience": 0,
    "lineage": [new_hash],
    "started_at": now,
    "updated_at": now,
}
if base_hash and base_hash != anchor_hash and base_metrics:
    payload["best_hash"] = base_hash
    payload["best"] = dict(base_metrics)
    payload["closed_games"] = int(base_metrics.get("n", 0) or 0)
    payload["depth"] = 2
    payload["lineage"] = [base_hash, new_hash]

with open(active_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
print(
    f"start|anchor={anchor_hash[:8]}|head={new_hash[:8]}|depth={payload['depth']}|"
    f"closed={payload['closed_games']}|patience={payload['patience']}|best={(payload['best_hash'][:8] if payload['best_hash'] else '-')}"
)
PY
}

_prune_expired_rejected_hashes() {
	[ -f "$REJECTED_HASHES_FILE" ] || return 0
	local prune_result=""
	prune_result=$(
		python3 - "$REJECTED_HASHES_FILE" "$REJECTED_HASH_META_FILE" "$REJECTED_REEVALUATE_TTL_SEC" <<'PY' 2>/dev/null
import json
import os
import sys
import time

rejected_file, meta_file, ttl_raw = sys.argv[1:4]
ttl_sec = int(ttl_raw or 0)

try:
    with open(rejected_file, encoding="utf-8", errors="ignore") as f:
        raw_hashes = [line.strip() for line in f if line.strip()]
except Exception:
    raise SystemExit(0)

if not raw_hashes:
    print("kept=0")
    raise SystemExit(0)

try:
    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
        if not isinstance(meta, dict):
            meta = {}
except Exception:
    meta = {}

now = int(time.time())
last_index = {}
for idx, hash_ in enumerate(raw_hashes):
    last_index[hash_] = idx

ordered = sorted(last_index.items(), key=lambda item: item[1])
kept = []
kept_set = set()
expired = []
legacy = []

for hash_, _ in ordered:
    entry = meta.get(hash_)
    if not isinstance(entry, dict):
        legacy.append(hash_)
        continue
    updated_at = int(entry.get("updated_at", 0) or 0)
    if updated_at <= 0:
        legacy.append(hash_)
        continue
    age = max(0, now - updated_at)
    if ttl_sec > 0 and age >= ttl_sec:
        expired.append(f"{hash_}|{age}|{ttl_sec}")
        continue
    kept.append(hash_)
    kept_set.add(hash_)

new_meta = {hash_: meta[hash_] for hash_ in kept if hash_ in meta}
file_changed = kept != raw_hashes
meta_changed = set(new_meta.keys()) != set(meta.keys())

if file_changed:
    with open(rejected_file, "w", encoding="utf-8") as f:
        if kept:
            f.write("\n".join(kept) + "\n")
        else:
            f.write("")

if meta_changed:
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(new_meta, f, ensure_ascii=False)

print(f"kept={len(kept)}")
for hash_ in legacy:
    print(f"legacy|{hash_}")
for row in expired:
    print(f"expired|{row}")
PY
	)
	[ -z "$prune_result" ] && return 0
	local line
	while IFS= read -r line; do
		case "$line" in
		expired'|'*)
			local payload hash_value age ttl
			payload="${line#expired|}"
			IFS='|' read -r hash_value age ttl <<<"$payload"
			log "[REGRESSION] rejected期限切れを再許可: ${hash_value} age=${age}s ttl=${ttl}s" >&2
			;;
		legacy'|'*)
			log "[REGRESSION] rejectedメタなしを再許可: ${line#legacy|}" >&2
			;;
		esac
	done <<EOF
$prune_result
EOF
}

_is_recently_rejected_for_rollback() {
	local h="$1"
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	[ -n "$h" ] || return 1
	[ -f "$REJECTED_HASHES_FILE" ] || return 1
	grep -qF "$h" "$REJECTED_HASHES_FILE" 2>/dev/null || return 1
	if [ ! -f "$REJECTED_HASH_META_FILE" ]; then
		return 1
	fi
	local recovered=""
	recovered=$(
		python3 - "$REJECTED_HASH_META_FILE" "$h" "$REJECTED_REEVALUATE_TTL_SEC" <<'PY' 2>/dev/null
import json
import os
import sys
import time

meta_file, target_hash, ttl_sec = sys.argv[1], sys.argv[2], int(sys.argv[3])
if not os.path.exists(meta_file):
    raise SystemExit(0)

try:
    meta = json.load(open(meta_file))
except Exception:
    raise SystemExit(0)

if target_hash not in meta:
    print("expired|legacy|0")
    raise SystemExit(0)

rej = meta.get(target_hash, {})
rejected_at = int(rej.get("updated_at", 0) or 0)
if rejected_at <= 0:
    raise SystemExit(0)

age = int(time.time()) - rejected_at
if age >= ttl_sec:
    print(f"expired|{age}|{ttl_sec}")
PY
	)
	case "$recovered" in
	expired*)
		log "[REGRESSION] rollback候補を再許可: $h (${recovered#expired|})" >&2
		return 1
		;;
	esac
	return 0
}

_is_blocked_reverse_rollback_pair() {
	local current_hash="$1"
	local candidate_hash="$2"
	[ -n "$current_hash" ] || return 1
	[ -n "$candidate_hash" ] || return 1
	[ -f "$LAST_ROLLBACK_PAIR_FILE" ] || return 1
	python3 - "$LAST_ROLLBACK_PAIR_FILE" "$current_hash" "$candidate_hash" <<'PY' >/dev/null 2>&1
import json
import sys

pair_file, current_hash, candidate_hash = sys.argv[1:4]
try:
    data = json.load(open(pair_file))
except Exception:
    raise SystemExit(1)

from_hash = str(data.get("from_hash", "") or "")
to_hash = str(data.get("to_hash", "") or "")
if to_hash == current_hash and from_hash == candidate_hash:
    raise SystemExit(0)
raise SystemExit(1)
PY
	}

_prune_rollback_target_cooldown() {
	local cooldown_file="${ROLLBACK_TARGET_COOLDOWN_FILE:-tmp/state/rollback_target_cooldown.json}"
	local ttl_sec="${ROLLBACK_TARGET_COOLDOWN_SEC:-3600}"
	[ -f "$cooldown_file" ] || return 0
	python3 - "$cooldown_file" "$ttl_sec" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

cooldown_file, ttl_raw = sys.argv[1:3]
try:
    ttl_sec = int(ttl_raw or 0)
except Exception:
    ttl_sec = 0
try:
    data = json.load(open(cooldown_file, encoding="utf-8"))
except Exception:
    raise SystemExit(0)
if not isinstance(data, dict):
    raise SystemExit(0)
now = int(time.time())
kept = {}
for hash_, entry in data.items():
    if not isinstance(entry, dict):
        continue
    updated_at = int(entry.get("updated_at", 0) or 0)
    if updated_at <= 0:
        continue
    if ttl_sec > 0 and now - updated_at >= ttl_sec:
        continue
    kept[hash_] = entry
if kept == data:
    raise SystemExit(0)
os.makedirs(os.path.dirname(cooldown_file) or ".", exist_ok=True)
tmp = cooldown_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(kept, f, ensure_ascii=False)
os.replace(tmp, cooldown_file)
PY
}

_is_rollback_target_on_cooldown() {
	local current_hash="$1"
	local candidate_hash="$2"
	local cooldown_file="${ROLLBACK_TARGET_COOLDOWN_FILE:-tmp/state/rollback_target_cooldown.json}"
	local ttl_sec="${ROLLBACK_TARGET_COOLDOWN_SEC:-3600}"
	[ -n "$candidate_hash" ] || return 1
	[ "$candidate_hash" = "$current_hash" ] && return 1
	_prune_rollback_target_cooldown >/dev/null 2>&1 || true
	[ -f "$cooldown_file" ] || return 1
	python3 - "$cooldown_file" "$candidate_hash" "$ttl_sec" <<'PY' >/dev/null 2>&1
import json
import os
import sys
import time

cooldown_file, target_hash, ttl_raw = sys.argv[1:4]
try:
    ttl_sec = int(ttl_raw or 0)
except Exception:
    ttl_sec = 0
if not os.path.exists(cooldown_file):
    raise SystemExit(1)
try:
    data = json.load(open(cooldown_file, encoding="utf-8"))
except Exception:
    raise SystemExit(1)
entry = data.get(target_hash) if isinstance(data, dict) else None
if not isinstance(entry, dict):
    raise SystemExit(1)
updated_at = int(entry.get("updated_at", 0) or 0)
if updated_at <= 0:
    raise SystemExit(1)
if ttl_sec > 0 and int(time.time()) - updated_at >= ttl_sec:
    raise SystemExit(1)
raise SystemExit(0)
PY
}

_record_rollback_target_cooldown() {
	local from_hash="$1"
	local target_hash="$2"
	local game_num="${3:-0}"
	local note="${4:-}"
	local cooldown_file="${ROLLBACK_TARGET_COOLDOWN_FILE:-tmp/state/rollback_target_cooldown.json}"
	[ -n "$target_hash" ] || return 0
	python3 - "$cooldown_file" "$from_hash" "$target_hash" "$game_num" "$note" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

cooldown_file, from_hash, target_hash, game_num, note = sys.argv[1:6]
try:
    data = json.load(open(cooldown_file, encoding="utf-8"))
except Exception:
    data = {}
if not isinstance(data, dict):
    data = {}
data[target_hash] = {
    "updated_at": int(time.time()),
    "from_hash": from_hash,
    "game": int(game_num or 0),
    "note": note,
}
os.makedirs(os.path.dirname(cooldown_file) or ".", exist_ok=True)
tmp = cooldown_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
os.replace(tmp, cooldown_file)
PY
}

_get_rolling_metrics_for_hash() {
	local target_hash="$1"
	[ -n "$target_hash" ] || return 1
	[ -f "$ROLLING_SCORES_FILE" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$target_hash" <<'PY' 2>/dev/null
import json
import math
import os
import sys

rolling_file, target_hash = sys.argv[1], sys.argv[2]
if not os.path.exists(rolling_file):
    raise SystemExit(1)
try:
    rolling = json.load(open(rolling_file))
except Exception:
    raise SystemExit(1)
if target_hash not in rolling:
    raise SystemExit(1)
scores = [int(x) for x in rolling[target_hash].get("scores", [])]
if not scores:
    raise SystemExit(1)
xs = sorted(scores)
n = len(xs)
mean = sum(xs) / n
if n == 1:
    p25 = p50 = float(xs[0])
    std = 0.0
else:
    def q(p):
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac
    p25 = q(0.25)
    p50 = q(0.50)
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
lcb = mean - 1.28 * (std / math.sqrt(n))
comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
games_total = int(rolling[target_hash].get("games_total", n) or n)
print(f"{comp:.2f}|{p50:.1f}|{p25:.1f}|{lcb:.1f}|{n}|{games_total}")
PY
}

_get_current_strategy_run_metrics() {
	local target_hash="$1"
	[ -n "$target_hash" ] || return 1
	[ -f "$CURRENT_STRATEGY_RUN_FILE" ] || return 1
	python3 - "$CURRENT_STRATEGY_RUN_FILE" "$target_hash" <<'PY' 2>/dev/null
import json
import math
import os
import sys

run_file, target_hash = sys.argv[1], sys.argv[2]
if not os.path.exists(run_file):
    raise SystemExit(1)
try:
    run = json.load(open(run_file))
except Exception:
    raise SystemExit(1)
if str(run.get("hash", "") or "") != target_hash:
    raise SystemExit(1)
scores = [int(x) for x in run.get("scores", [])]
if not scores:
    raise SystemExit(1)
xs = sorted(scores)
n = len(xs)
mean = sum(xs) / n
if n == 1:
    p25 = p50 = float(xs[0])
    std = 0.0
else:
    def q(p):
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac
    p25 = q(0.25)
    p50 = q(0.50)
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
lcb = mean - 1.28 * (std / math.sqrt(n))
comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
games_total = int(run.get("games_total", n) or n)
print(f"{comp:.2f}|{p50:.1f}|{p25:.1f}|{lcb:.1f}|{n}|{games_total}")
PY
}

_remove_unusable_rolling_score_hash() {
	local target_hash="$1" reason="${2:-unusable_rollback_candidate}" current_hash="${3:-}"
	[ -n "$target_hash" ] || return 0
	[ "$target_hash" = "$current_hash" ] && return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	local removed_line
	removed_line=$(
		python3 - "$ROLLING_SCORES_FILE" "$target_hash" "$reason" <<'PY' 2>/dev/null
import json
import os
import sys
import time

rs_file, target_hash, reason = sys.argv[1:4]
try:
    with open(rs_file, encoding="utf-8") as f:
        rs = json.load(f)
except Exception:
    raise SystemExit(0)

if not isinstance(rs, dict) or target_hash not in rs:
    raise SystemExit(0)

removed = rs.pop(target_hash)
tmp = rs_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(rs, f, ensure_ascii=False)
os.replace(tmp, rs_file)

audit_file = os.path.join(os.path.dirname(rs_file), "rolling_score_pruned_hashes.jsonl")
try:
    row = {
        "hash": target_hash,
        "reason": reason,
        "games_total": int((removed or {}).get("games_total", 0) or 0) if isinstance(removed, dict) else 0,
        "n": len((removed or {}).get("scores", []) or []) if isinstance(removed, dict) else 0,
        "updated_at": int(time.time()),
    }
    with open(audit_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
except Exception:
    pass

print(f"{target_hash}|{reason}")
PY
	)
	if [ -n "$removed_line" ]; then
		log "[ROLLING] pruned unusable rollback candidate: hash=${target_hash} reason=${reason}" || true
	fi
}

_prune_non_objective_rollback_scores() {
	local current_hash="$1"
	[ "${OBJECTIVE_MISS_PRUNE_ENABLED:-0}" = "1" ] || return 0
	[ "${OBJECTIVE_ANCHOR_PRIORITY_ENABLED:-1}" = "1" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	local pruned_hashes
	pruned_hashes=$(
		python3 - "$ROLLING_SCORES_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" <<'PY' 2>/dev/null
import json
import os
import sys
import time

rs_file, current_hash, min_games_raw = sys.argv[1:4]
try:
    min_games = int(min_games_raw)
except Exception:
    min_games = 12

try:
    with open(rs_file, encoding="utf-8") as f:
        rs = json.load(f)
except Exception:
    raise SystemExit(0)

if not isinstance(rs, dict):
    raise SystemExit(0)

def objective_progress(data):
    max_types = []
    for x in (data or {}).get("max_types", []) or []:
        try:
            max_types.append(int(x))
        except Exception:
            pass
    try:
        best_max_type = int((data or {}).get("best_max_type", 0) or 0)
    except Exception:
        best_max_type = 0
    if max_types:
        best_max_type = max([best_max_type] + max_types)
    try:
        russia_count = int((data or {}).get("russia_count", 0) or 0)
    except Exception:
        russia_count = 0
    try:
        soviet_count = int((data or {}).get("soviet_count", 0) or 0)
    except Exception:
        soviet_count = 0
    if best_max_type >= 15 and russia_count <= 0:
        russia_count = 1
    if best_max_type >= 16 and soviet_count <= 0:
        soviet_count = 1
    return best_max_type, russia_count, soviet_count

removed = []
for h, data in list(rs.items()):
    if not h or h == current_hash or not isinstance(data, dict):
        continue
    scores = data.get("scores", []) or []
    if len(scores) < min_games:
        continue
    best_max_type, russia_count, soviet_count = objective_progress(data)
    if soviet_count > 0 or russia_count > 0 or best_max_type >= 15:
        continue
    removed.append((h, data))
    rs.pop(h, None)

if not removed:
    raise SystemExit(0)

tmp = rs_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(rs, f, ensure_ascii=False)
os.replace(tmp, rs_file)

audit_file = os.path.join(os.path.dirname(rs_file), "rolling_score_pruned_hashes.jsonl")
now = int(time.time())
try:
    with open(audit_file, "a", encoding="utf-8") as f:
        for h, data in removed:
            row = {
                "hash": h,
                "reason": "objective_miss_no_russia",
                "games_total": int((data or {}).get("games_total", 0) or 0),
                "n": len((data or {}).get("scores", []) or []),
                "updated_at": now,
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
except Exception:
    pass

for h, _data in removed:
    print(h)
PY
	)
	if [ -n "$pruned_hashes" ]; then
		local h
		while IFS= read -r h; do
			[ -n "$h" ] || continue
			log "[ROLLING] pruned rollback objective-miss candidate: hash=${h} reason=objective_miss_no_russia" || true
		done <<EOF
$pruned_hashes
EOF
	fi
	return 0
}

_pick_best_rollback_candidate() {
	local current_hash="$1"
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	[ -f "$ROLLING_SCORES_FILE" ] || return 1
	_prune_non_objective_rollback_scores "$current_hash"
	local current_metrics current_comp
	current_metrics=$(_get_current_strategy_run_metrics "$current_hash" 2>/dev/null || true)
	[ -z "$current_metrics" ] && current_metrics=$(_get_rolling_metrics_for_hash "$current_hash" 2>/dev/null || true)
	current_comp="${current_metrics%%|*}"

	local ranked
	ranked=$(
		python3 - "$ROLLING_SCORES_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" \
			"${OBJECTIVE_ANCHOR_PRIORITY_ENABLED:-1}" "${OBJECTIVE_ANCHOR_MIN_COMP_RATIO:-0.90}" "${OBJECTIVE_ANCHOR_MAX_COMP_GAP:-1500}" <<'PY'
import json
import sys
import math

rs_file = sys.argv[1]
current_hash = sys.argv[2]
min_games = int(sys.argv[3])
keep_top = int(sys.argv[4])
lcb_z = float(sys.argv[5])
w_p50 = float(sys.argv[6])
w_p25 = float(sys.argv[7])
w_lcb = float(sys.argv[8])
objective_enabled = (sys.argv[9] if len(sys.argv) > 9 else "1") == "1"
objective_min_comp_ratio = float(sys.argv[10]) if len(sys.argv) > 10 else 0.90
objective_max_comp_gap = float(sys.argv[11]) if len(sys.argv) > 11 else 1500.0
rs = json.load(open(rs_file))

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    n = len(scores)
    mean = sum(scores) / n
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    composite = w_p50 * p50 + w_p25 * p25 + w_lcb * lcb
    return composite, p50, p25, lcb, n

def objective_tuple(data):
    max_types = []
    for x in data.get("max_types", []) or []:
        try:
            max_types.append(int(x))
        except Exception:
            pass
    try:
        best_max_type = int(data.get("best_max_type", 0) or 0)
    except Exception:
        best_max_type = 0
    if max_types:
        best_max_type = max([best_max_type] + max_types)
    try:
        russia_count = int(data.get("russia_count", 0) or 0)
    except Exception:
        russia_count = 0
    try:
        soviet_count = int(data.get("soviet_count", 0) or 0)
    except Exception:
        soviet_count = 0
    if best_max_type >= 15 and russia_count <= 0:
        russia_count = 1
    if best_max_type >= 16 and soviet_count <= 0:
        soviet_count = 1
    return (
        int(soviet_count > 0),
        int(russia_count > 0),
        best_max_type,
        soviet_count,
        russia_count,
    )

rows = []
for h, data in rs.items():
    if h == current_hash:
        continue
    scores = [int(x) for x in data.get("scores", [])]
    if len(scores) < min_games:
        continue
    comp, p50, p25, lcb, n = metrics(scores)
    rows.append((comp, p50, p25, lcb, n, h, data))

if not rows:
    raise SystemExit(0)

top_comp = max(r[0] for r in rows)

def rank_key(row):
    comp, p50, p25, lcb, n, h, data = row
    objective_key = (0, 0, 0, 0, 0)
    if objective_enabled:
        score_gap = max(0.0, top_comp - comp)
        near_score_leader = comp >= top_comp * objective_min_comp_ratio or score_gap <= objective_max_comp_gap
        if near_score_leader:
            objective_key = objective_tuple(data)
    return (*objective_key, comp, p50, p25, n, h)

rows.sort(key=rank_key, reverse=True)
for comp, p50, p25, lcb, n, h, _data in rows[:keep_top]:
    print(f"{h}|{comp:.2f}|{p50:.1f}|{p25:.1f}|{lcb:.1f}|{n}")
PY
	)
	[ -z "$ranked" ] && return 1

	local line h comp p50 p25 lcb n candidate_file
	while IFS= read -r line; do
		[ -z "$line" ] && continue
		IFS='|' read -r h comp p50 p25 lcb n <<<"$line"
		if [ -n "$current_comp" ] && ! awk "BEGIN{exit !($comp > $current_comp)}"; then
			continue
		fi
		if _is_recently_rejected_for_rollback "$h"; then
			log "[REGRESSION] rollback候補スキップ: $h はrejected TTL内" >&2
			continue
		fi
		if _is_blocked_reverse_rollback_pair "$current_hash" "$h"; then
			log "[REGRESSION] rollback候補スキップ: $h は直前rollbackの逆向き" >&2
			continue
		fi
		if _is_rollback_target_on_cooldown "$current_hash" "$h"; then
			log "[REGRESSION] rollback候補スキップ: $h はrollback先cooldown中" >&2
			continue
		fi
		candidate_file=$(_find_rollback_candidate_file_for_hash "$h" 2>/dev/null || echo "")
		if [ ! -f "$candidate_file" ]; then
			_remove_unusable_rolling_score_hash "$h" "missing_rollback_archive" "$current_hash"
			continue
		fi
		if ! _rollback_candidate_file_is_valid "$h" "$candidate_file"; then
			log "[REGRESSION] rollback候補スキップ: $h はvalidation失敗archive" >&2
			continue
		fi
		if [ -n "$candidate_file" ]; then
			echo "${h}|${comp}|${p50}|${p25}|${lcb}|${n}|${candidate_file}"
			return 0
		fi
	done <<EOF
$ranked
EOF
	return 1
}

_rollback_candidate_file_is_valid() {
	local expected_hash="$1" candidate_file="$2"
	[ -f "$candidate_file" ] || return 1
	local original_hash=""
	original_hash=$(python3 extract_decide_hash.py "$candidate_file" 2>/dev/null || echo "")
	if ! command -v validate_strategy >/dev/null 2>&1; then
		return 0
	fi
	local tmp_file="${TMP_STATE_DIR:-tmp/state}/rollback_candidate_validate_${expected_hash}_$$.py"
	mkdir -p "$(dirname "$tmp_file")" 2>/dev/null || true
	cp "$candidate_file" "$tmp_file" 2>/dev/null || return 1
	if ! validate_strategy "$tmp_file" >/dev/null 2>&1; then
		rm -f "$tmp_file"
		return 1
	fi
	local validated_hash=""
	validated_hash=$(python3 extract_decide_hash.py "$tmp_file" 2>/dev/null || echo "")
	rm -f "$tmp_file"
	[ -z "$expected_hash" ] || [ -z "$validated_hash" ] || [ "$expected_hash" = "$validated_hash" ] || [ "$expected_hash" = "$original_hash" ]
}

_pick_hall_of_fame_rollback_candidate() {
	local current_hash="$1"
	local current_metrics current_comp candidate_metrics candidate_comp
	current_metrics=$(_get_current_strategy_run_metrics "$current_hash" 2>/dev/null || true)
	[ -z "$current_metrics" ] && current_metrics=$(_get_rolling_metrics_for_hash "$current_hash" 2>/dev/null || true)
	current_comp="${current_metrics%%|*}"
	local line f score_num h
	while IFS='|' read -r score_num f; do
		[ -f "$f" ] || continue
		h=$(python3 extract_decide_hash.py "$f" 2>/dev/null || echo "")
		[ -n "$h" ] || continue
		[ "$h" = "$current_hash" ] && continue
		candidate_metrics=$(_get_rolling_metrics_for_hash "$h" 2>/dev/null || true)
		candidate_comp="${candidate_metrics%%|*}"
		[ -n "$candidate_comp" ] || continue
		if [ -n "$current_comp" ] && ! awk "BEGIN{exit !($candidate_comp > $current_comp)}"; then
			continue
		fi
		if _is_blocked_reverse_rollback_pair "$current_hash" "$h"; then
			log "[REGRESSION] hall-of-fame候補スキップ: $h は直前rollbackの逆向き" >&2
			continue
		fi
		if _is_rollback_target_on_cooldown "$current_hash" "$h"; then
			log "[REGRESSION] hall-of-fame候補スキップ: $h はrollback先cooldown中" >&2
			continue
		fi
		if ! _rollback_candidate_file_is_valid "$h" "$f"; then
			log "[REGRESSION] hall-of-fame候補スキップ: $h はvalidation失敗archive" >&2
			continue
		fi
		echo "${h}|hof|${score_num}|0|0|0|$f"
		return 0
	done < <(
		for f in "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
			[ -f "$f" ] || continue
			line=$(basename "$f" | sed -En 's/^best_score([0-9]+)_strategy\.py$/\1/p')
			[ -n "$line" ] || continue
			printf '%s|%s\n' "$line" "$f"
		done | sort -t'|' -k1,1nr
	)
	return 1
}

_prune_hash_archive_by_ranking() {
	[ -d "$STRATEGY_HASH_ARCHIVE_DIR" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0

	_backfill_hash_archive_from_known_versions

	local archive_count ranked_result ranked_hashes
	local meta_line mature_count ranked_count keep_hash_count expected_keep_count
	local min_keep_guard ratio_guard
	archive_count=$(find "$STRATEGY_HASH_ARCHIVE_DIR" -maxdepth 1 -type f -name '*.py' 2>/dev/null | wc -l | tr -d ' ')
	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	ranked_result=$(
		python3 - "$ROLLING_SCORES_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$current_hash" <<'PY' 2>/dev/null || true
import json
import sys
import math

rs_file = sys.argv[1]
min_games = int(sys.argv[2])
keep_top = int(sys.argv[3])
lcb_z = float(sys.argv[4])
w_p50 = float(sys.argv[5])
w_p25 = float(sys.argv[6])
w_lcb = float(sys.argv[7])
current_hash = sys.argv[8]
rs = json.load(open(rs_file))

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def composite_score(scores):
    n = len(scores)
    mean = sum(scores) / n
    p25 = quantile(scores, 0.25)
    p50 = quantile(scores, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in scores) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - lcb_z * (std / math.sqrt(n))
    return w_p50 * p50 + w_p25 * p25 + w_lcb * lcb, p50, p25, n

rows = []
for h, data in rs.items():
    if h == current_hash:
        continue
    scores = [int(x) for x in data.get("scores", [])]
    if len(scores) < min_games:
        continue
    comp, p50, p25, n = composite_score(scores)
    rows.append((comp, p50, p25, n, h))
rows.sort(key=lambda x: (x[0], x[1], x[2], x[3]), reverse=True)
print(f"META|{len(rows)}|{min(len(rows), keep_top)}")
for _, _, _, _, h in rows[:keep_top]:
    print(h)
PY
	)
	meta_line=$(printf '%s\n' "$ranked_result" | sed -n '1p')
	ranked_hashes=$(printf '%s\n' "$ranked_result" | sed '1d')
	mature_count=0
	ranked_count=0
	if printf '%s\n' "$meta_line" | grep -q '^META|'; then
		IFS='|' read -r _ mature_count ranked_count <<<"$meta_line"
	else
		ranked_hashes="$ranked_result"
	fi
	local keep_hashes
	keep_hashes=$(printf '%s\n%s\n' "$ranked_hashes" "$current_hash" | sed '/^$/d' | sort -u)
	keep_hash_count=$(printf '%s\n' "$keep_hashes" | sed '/^$/d' | wc -l | tr -d ' ')
	expected_keep_count=$ranked_count
	if [ -n "$current_hash" ] && ! printf '%s\n' "$ranked_hashes" | grep -qxF "$current_hash"; then
		expected_keep_count=$((expected_keep_count + 1))
	fi
	if [ "${archive_count:-0}" -gt 1 ] && [ "${expected_keep_count:-0}" -le 0 ]; then
		log "[HASH-ARCHIVE] prune skipped: empty keep set (archive=${archive_count})"
		return 0
	fi
	if [ "${archive_count:-0}" -gt 1 ] && [ "${mature_count:-0}" -gt 0 ] && [ "${expected_keep_count:-0}" -gt 0 ]; then
		if [ "$expected_keep_count" -le "$HASH_ARCHIVE_PRUNE_SAFETY_MIN_KEEP" ]; then
			min_keep_guard=$(((expected_keep_count + 1) / 2))
		else
			ratio_guard=$(((expected_keep_count * HASH_ARCHIVE_PRUNE_SAFETY_MIN_RATIO_PCT + 99) / 100))
			min_keep_guard=$ratio_guard
			[ "$min_keep_guard" -lt "$HASH_ARCHIVE_PRUNE_SAFETY_MIN_KEEP" ] && min_keep_guard=$HASH_ARCHIVE_PRUNE_SAFETY_MIN_KEEP
		fi
		[ "$min_keep_guard" -lt 1 ] && min_keep_guard=1
		if [ "${keep_hash_count:-0}" -lt "$min_keep_guard" ]; then
			log "[HASH-ARCHIVE] prune skipped: suspicious keep set (archive=${archive_count} mature=${mature_count} expected=${expected_keep_count} actual=${keep_hash_count} guard=${min_keep_guard})"
			return 0
		fi
	fi

	local removed=0
	local f base h
	while IFS= read -r f; do
		[ -f "$f" ] || continue
		base=$(basename "$f")
		h="${base%.py}"
		if ! grep -qxF "$h" <<<"$keep_hashes"; then
			rm -f "$f"
			removed=$((removed + 1))
		fi
	done < <(ls -1 "$STRATEGY_HASH_ARCHIVE_DIR"/*.py 2>/dev/null || true)

	if [ "$removed" -gt 0 ]; then
		log "[HASH-ARCHIVE] pruned ${removed} file(s): keep top ${HASH_ARCHIVE_KEEP_TOP} mature (+current)"
	fi
}

_start_hash_archive_prune_worker() {
	if [ "${HASH_ARCHIVE_PRUNE_BACKGROUND:-1}" != "1" ]; then
		_prune_hash_archive_by_ranking
		return $?
	fi

	local pid_file="${TMP_STATE_DIR:-tmp/state}/hash_archive_prune.pid"
	local running_pid=""
	if [ -f "$pid_file" ]; then
		running_pid=$(cat "$pid_file" 2>/dev/null || echo "")
		case "$running_pid" in
		'' | *[!0-9]*) running_pid="" ;;
		esac
	fi
	if [ -n "$running_pid" ] && kill -0 "$running_pid" 2>/dev/null; then
		log "[HASH-ARCHIVE] prune already running: PID=${running_pid}"
		return 0
	fi

	mkdir -p "$(dirname "$pid_file")" 2>/dev/null || true
	(
		trap 'rm -f "$pid_file"' EXIT
		_prune_hash_archive_by_ranking
	) &
	local worker_pid="$!"
	printf '%s\n' "$worker_pid" >"$pid_file"
	log "[HASH-ARCHIVE] prune worker started: PID=${worker_pid}"
}

_merge_rolling_scores_on_normalize() {
	local stale_hash="$1" actual_hash="$2"
	[ -n "$stale_hash" ] && [ -n "$actual_hash" ] && [ "$stale_hash" != "$actual_hash" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	local result
	result=$(
		python3 - "$ROLLING_SCORES_FILE" "$stale_hash" "$actual_hash" "${ROLLING_SCORE_KEEP:-20}" "${HOT_STREAK_ROLLING_KEEP:-200}" 2>/dev/null <<'PY'
import json
import os
import sys

rs_file, stale_hash, actual_hash, keep_raw, hot_keep_raw = sys.argv[1:6]
try:
    keep = int(keep_raw)
except Exception:
    keep = 20
try:
    hot_keep = int(hot_keep_raw)
except Exception:
    hot_keep = 200
keep = max(1, keep)
hot_keep = max(keep, hot_keep)

try:
    with open(rs_file, encoding="utf-8") as f:
        rs = json.load(f)
except Exception:
    raise SystemExit(0)

stale = rs.get(stale_hash)
if not stale or not isinstance(stale, dict):
    raise SystemExit(0)

stale_scores = [int(x) for x in stale.get("scores", []) or []]
stale_total = int(stale.get("games_total", len(stale_scores)) or len(stale_scores))
stale_archives = [str(x) for x in (stale.get("_recent_archives", []) or [])]

actual = rs.get(actual_hash, {}) or {}
actual_scores = [int(x) for x in actual.get("scores", []) or []]
actual_total = int(actual.get("games_total", len(actual_scores)) or len(actual_scores))
actual_archives = [str(x) for x in (actual.get("_recent_archives", []) or [])]

# stale scores are older; actual scores are newer
merged_scores = stale_scores + actual_scores
merged_total = stale_total + actual_total

# keep last hot_keep (stale may have had a hot streak window too)
merged_scores = merged_scores[-hot_keep:]

# dedup archives preserving order; keep last 25
seen = set()
merged_archives = []
for a in stale_archives + actual_archives:
    if a not in seen:
        seen.add(a)
        merged_archives.append(a)
merged_archives = merged_archives[-25:]

merged = dict(actual)
merged["scores"] = merged_scores
merged["games_total"] = merged_total
merged["_recent_archives"] = merged_archives
merged["best_max_type"] = max(
    int((stale.get("best_max_type") or 0)),
    int((actual.get("best_max_type") or 0)),
)
merged["russia_count"] = max(
    int((stale.get("russia_count") or 0)),
    int((actual.get("russia_count") or 0)),
)
merged["soviet_count"] = max(
    int((stale.get("soviet_count") or 0)),
    int((actual.get("soviet_count") or 0)),
)
if merged["best_max_type"] >= 15 and merged["russia_count"] <= 0:
    merged["russia_count"] = 1
if merged["best_max_type"] >= 16 and merged["soviet_count"] <= 0:
    merged["soviet_count"] = 1

rs[actual_hash] = merged
rs.pop(stale_hash, None)

tmp = rs_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(rs, f, ensure_ascii=False)
os.replace(tmp, rs_file)

print(f"merged {stale_hash[:12]}({len(stale_scores)}) + {actual_hash[:12]}({len(actual_scores)}) -> {actual_hash[:12]}({len(merged_scores)}) total={merged_total}")
PY
	)
	if [ -n "$result" ]; then
		log "[ROLLING] normalize-merge: ${result}"
	fi
}

update_rolling_scores() {
	local score="$1" archive_file="${2:-}"
	local strategy_source="${STRATEGY_FILE}.game_snapshot"
	[ ! -f "$strategy_source" ] && strategy_source="$STRATEGY_FILE"
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$strategy_source" 2>/dev/null || echo "unknown")
	_archive_strategy_snapshot_by_hash "$strategy_source" "$strategy_hash"
	local rolling_result="" rolling_err=""
	rolling_err="${TMP_STATE_DIR:-tmp/state}/rolling_scores_update.err"
	rolling_result=$(
		python3 - "$ROLLING_SCORES_FILE" "$strategy_hash" "$score" "$archive_file" "${ROLLING_SCORE_KEEP:-20}" "${HOT_STREAK_ROLLING_KEEP:-200}" "${HOT_STREAK_EXTEND_ENABLED:-1}" 2>"$rolling_err" <<'PY'
import json
import os
import sys

rs_file, h, score, archive_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
try:
    normal_keep = int(sys.argv[5])
except Exception:
    normal_keep = 20
try:
    hot_keep = int(sys.argv[6])
except Exception:
    hot_keep = 200
hot_enabled = str(sys.argv[7]).strip() == "1"
normal_keep = max(1, normal_keep)
hot_keep = max(normal_keep, hot_keep)
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}

if h not in rs:
    rs[h] = {"scores": [], "prev_hash": "", "games_total": 0}
if "games_total" not in rs[h]:
    rs[h]["games_total"] = len(rs[h].get("scores", []))
recent_archives = rs[h].get("_recent_archives", [])
if not isinstance(recent_archives, list):
    recent_archives = []

if archive_file and archive_file in recent_archives:
    print(f"{h}|{len(rs[h]['scores'])}|{rs[h]['games_total']}|dedup")
    raise SystemExit

def nation_progress(path):
    max_type = 0
    russia = False
    soviet = False
    peak_type_counts = {}
    deadline_guard_count = 0
    deadline_guard_reasons = {}
    if not path or not os.path.exists(path):
        return max_type, russia, soviet, "no-archive", "none", deadline_guard_count, "none"
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if "DEADLINE_GUARD" in str(row.get("decision_reason") or ""):
                    deadline_guard_count += 1
                    reason = str(row.get("decision_reason") or "")
                    deadline_guard_reasons[reason] = deadline_guard_reasons.get(reason, 0) + 1
                if row.get("russia_created"):
                    russia = True
                if row.get("soviet_created"):
                    soviet = True
                pieces = ((row.get("state_snapshot") or {}).get("pieces") or [])
                for piece in pieces:
                    try:
                        t = int(piece.get("type", 0) or 0)
                    except Exception:
                        continue
                    if t > max_type:
                        max_type = t
                    if t >= 10:
                        same_type_count = 0
                        for p in pieces:
                            try:
                                if int((p or {}).get("type", 0) or 0) == t:
                                    same_type_count += 1
                            except Exception:
                                pass
                        peak_type_counts[t] = max(peak_type_counts.get(t, 0), same_type_count)
                    if t >= 15:
                        russia = True
                    if t >= 16:
                        soviet = True
    except Exception:
        pass
    peak_counts = " ".join(f"T{t}x{peak_type_counts[t]}" for t in sorted(peak_type_counts, reverse=True)[:4]) or "none"
    frontier_hint = "no-high-type"
    if max_type >= 10:
        frontier_hint = f"T{max_type}_peak={peak_type_counts.get(max_type, 0)} prev_T{max_type - 1}_peak={peak_type_counts.get(max_type - 1, 0)}"
    guard_top = ", ".join(f"{name}x{count}" for name, count in sorted(deadline_guard_reasons.items(), key=lambda item: item[1], reverse=True)[:3]) or "none"
    return max_type, russia, soviet, frontier_hint, peak_counts, deadline_guard_count, guard_top

prev_scores = [int(x) for x in rs[h].get("scores", [])]
prev_best = max(prev_scores) if prev_scores else None
rs[h]["scores"].append(score)
rs[h]["games_total"] += 1
keep = hot_keep if hot_enabled and prev_best is not None and score > prev_best else normal_keep
rs[h]["scores"] = rs[h]["scores"][-keep:]
if archive_file:
    recent_archives.append(archive_file)
    recent_archives = recent_archives[-25:]
rs[h]["_recent_archives"] = recent_archives
progress_archives = recent_archives[-len(rs[h]["scores"]):] if rs[h]["scores"] else []
progress = [nation_progress(path) for path in progress_archives]
rs[h]["max_types"] = [item[0] for item in progress]
rs[h]["best_max_type"] = max([int(rs[h].get("best_max_type", 0) or 0)] + [item[0] for item in progress])
rs[h]["russia_count"] = max(int(rs[h].get("russia_count", 0) or 0), sum(1 for item in progress if item[1]))
rs[h]["soviet_count"] = max(int(rs[h].get("soviet_count", 0) or 0), sum(1 for item in progress if item[2]))
if rs[h]["best_max_type"] >= 15 and rs[h]["russia_count"] <= 0:
    rs[h]["russia_count"] = 1
if rs[h]["best_max_type"] >= 16 and rs[h]["soviet_count"] <= 0:
    rs[h]["soviet_count"] = 1
rs[h]["frontier_hints"] = [item[3] for item in progress]
rs[h]["peak_high_type_counts"] = [item[4] for item in progress]
rs[h]["deadline_guard_counts"] = [item[5] for item in progress]
rs[h]["deadline_guard_reason_tops"] = [item[6] for item in progress]

with open(rs_file, "w") as f:
    json.dump(rs, f)

print(f"{h}|{len(rs[h]['scores'])}|{rs[h]['games_total']}|updated")
PY
	)
	if [ -n "$rolling_result" ]; then
		local rolling_n="" rolling_total="" rolling_status=""
		IFS='|' read -r strategy_hash rolling_n rolling_total rolling_status <<<"$rolling_result"
		if [ "$rolling_status" = "dedup" ]; then
			log "[ROLLING] duplicate skip: hash=${strategy_hash} n=${rolling_n} total=${rolling_total} score=${score} file=${archive_file}"
		else
			log "[ROLLING] updated: hash=${strategy_hash} n=${rolling_n} total=${rolling_total} score=${score} file=${archive_file}"
		fi
	else
		log "[ROLLING] update failed: hash=${strategy_hash} score=${score}"
		[ -s "$rolling_err" ] && log "[ROLLING] update stderr: $(tr '\n' ' ' <"$rolling_err" | cut -c1-500)"
	fi
	rm -f "$rolling_err" 2>/dev/null || true
	# 帯域脱出機構 D + E: 現戦略のシグネチャを永続キャッシュに保存
	# (game_history が後で pruning されてもシグネチャが残るように)
	if [ "${DIVERSITY_PREMIUM_ENABLED:-0}" = "1" ] || [ "${TABU_ENABLED:-0}" = "1" ]; then
		_cache_strategy_signature "$strategy_hash" >/dev/null 2>&1 || true
	fi
	_start_hash_archive_prune_worker
}

# 帯域脱出機構ヘルパー: 指定 hash の挙動シグネチャを behavior_signatures.json に保存。
# rolling_scores._recent_archives + 直近 jsonl から compute_signature を呼ぶ。
_cache_strategy_signature() {
	local target_hash="$1"
	[ -n "$target_hash" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$BEHAVIOR_SIGNATURES_FILE" "$target_hash" "$(pwd)" <<'PY' 2>/dev/null
import json, os, sys
rs_file, sig_file, h, repo = sys.argv[1:5]
try:
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from lib.behavior_signature import compute_signature
except Exception:
    raise SystemExit(0)

try:
    rs = json.load(open(rs_file))
except Exception:
    raise SystemExit(0)
entry = rs.get(h) or {}
arc = entry.get("_recent_archives", []) or []
arc = [a for a in arc if a and (a.startswith("git:") or os.path.exists(a))]
if not arc:
    raise SystemExit(0)
sig = compute_signature(arc[-12:])
if not sig or sig.get("n_turns", 0) == 0:
    raise SystemExit(0)

cache = {}
if os.path.exists(sig_file):
    try:
        cache = json.load(open(sig_file, encoding="utf-8")) or {}
    except Exception:
        cache = {}
cache[h] = sig
os.makedirs(os.path.dirname(sig_file) or ".", exist_ok=True)
tmp = sig_file + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cache, f, ensure_ascii=False)
os.replace(tmp, sig_file)
PY
}

check_regression() {
	type reload_runtime_toggles >/dev/null 2>&1 && reload_runtime_toggles
	# top1 anchor を固定基準にして branch 単位で評価する。
	# 単世代の揺らぎでは戻さず、branch の budget が尽きても anchor から明確に劣後する場合だけ rollback。
	REGRESSION_ROLLBACK_DONE=0
	REGRESSION_ROLLBACK_HASH=""
	REGRESSION_ROLLBACK_RESULT=""
	# --- 粛清一時無効化 ---
	if [ "${REGRESSION_DISABLED:-0}" = "1" ]; then
		log "[REGRESSION] disabled (REGRESSION_DISABLED=1)"
		return 1
	fi
	# ------------------------
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true

	local result
	result=$(
		python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$ACTIVE_BRANCH_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$strategy_hash" "$MIN_GAMES_BEFORE_REGRESSION" "$STRATEGY_HASH_ARCHIVE_DIR" "$REGRESSION_MIN_COMP_GAP" "$REGRESSION_MIN_P50_GAP" "$REGRESSION_MIN_P25_GAP" "$REGRESSION_MIN_BREACH_COUNT" "$BRANCH_MAX_DEPTH" "$BRANCH_MAX_GAMES" "$BRANCH_PATIENCE" "$BRANCH_HARD_COMP_GAP" "$BRANCH_HARD_P50_GAP" "$BRANCH_HARD_P25_GAP" "$BRANCH_HARD_MIN_BREACH_COUNT" \
			"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" "${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" "${WILDCARD_ATTEMPT_STATE_FILE:-tmp/state/wildcard_attempt_state.json}" "${WILDCARD_OUTCOME_FILE:-tmp/state/wildcard_outcomes.jsonl}" \
			"${ANNEALING_OBSERVE_FILE:-tmp/state/annealing_candidates.jsonl}" "${ANNEALING_OBSERVE_ENABLED:-1}" "${ANNEALING_BASE_TEMP:-1800}" "${ANNEALING_DECAY:-0.85}" \
			"${EARLY_OBJECTIVE_REGRESSION_ENABLED:-1}" "${EARLY_OBJECTIVE_REGRESSION_MIN_GAMES:-4}" "${EARLY_OBJECTIVE_REGRESSION_MIN_BEST_TYPE:-15}" \
			"${SAME_HASH_BACKSLIDE_RESET_ENABLED:-1}" "${SAME_HASH_BACKSLIDE_MIN_EXTRA_GAMES:-4}" "${RUSSIA_OBJECTIVE_REGRESSION_ENABLED:-0}" \
			"${ROLLING_SCORE_RUSSIA_GRACE_RANK:-7}" "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" \
			"${ROLLBACK_TREND_GRACE_ENABLED:-1}" "${ROLLBACK_TREND_GRACE_WINDOW:-50}" "${ROLLBACK_TREND_GRACE_MIN_PRIOR:-50}" "${ROLLBACK_TREND_GRACE_MIN_DELTA:-0}" \
			"${ARCHIVE_RESTART_COOLDOWN_FILE:-tmp/state/archive_restart_cooldown.json}" "${ARCHIVE_RESTART_OBJECTIVE_FAIL_PERMANENT:-1}" \
			"${EARLY_COMP_TOP_GAP_ENABLED:-1}" "${EARLY_COMP_TOP_GAP_MIN_GAMES:-4}" "${EARLY_COMP_TOP_GAP_MIN_RATIO:-0.85}" <<'PY'
import json
import math
import os
import sys
import time

rs_file, current_run_file, active_branch_file, anchor_file, current_hash = sys.argv[1:6]
min_games_current = int(sys.argv[6])
archive_dir = sys.argv[7]
min_comp_gap = float(sys.argv[8])
min_p50_gap = float(sys.argv[9])
min_p25_gap = float(sys.argv[10])
min_breach_count = int(sys.argv[11])
branch_max_depth = int(sys.argv[12])
branch_max_games = int(sys.argv[13])
branch_patience = int(sys.argv[14])
hard_comp_gap = float(sys.argv[15])
hard_p50_gap = float(sys.argv[16])
hard_p25_gap = float(sys.argv[17])
hard_min_breach_count = int(sys.argv[18])
stagnation_file = sys.argv[19] if len(sys.argv) > 19 else ""
wildcard_origin_file = sys.argv[20] if len(sys.argv) > 20 else ""
wildcard_attempt_state_file = sys.argv[21] if len(sys.argv) > 21 else ""
wildcard_outcome_file = sys.argv[22] if len(sys.argv) > 22 else ""
annealing_observe_file = sys.argv[23] if len(sys.argv) > 23 else ""
annealing_observe_enabled = sys.argv[24] if len(sys.argv) > 24 else "1"
annealing_base_temp = float(sys.argv[25]) if len(sys.argv) > 25 else 1800.0
annealing_decay = float(sys.argv[26]) if len(sys.argv) > 26 else 0.85
early_objective_enabled = sys.argv[27] if len(sys.argv) > 27 else "1"
early_objective_min_games = int(sys.argv[28]) if len(sys.argv) > 28 else 4
early_objective_min_best_type = int(sys.argv[29]) if len(sys.argv) > 29 else 15
same_hash_backslide_enabled = sys.argv[30] if len(sys.argv) > 30 else "1"
same_hash_backslide_min_extra_games = int(sys.argv[31]) if len(sys.argv) > 31 else 4
russia_objective_regression_enabled = sys.argv[32] if len(sys.argv) > 32 else "0"
try:
    rolling_score_russia_grace_rank = max(0, int(sys.argv[33])) if len(sys.argv) > 33 else 7
except Exception:
    rolling_score_russia_grace_rank = 7
eval_score_history_file = sys.argv[34] if len(sys.argv) > 34 else "eval_score_history.txt"
rollback_trend_grace_enabled = (sys.argv[35] if len(sys.argv) > 35 else "1") == "1"
try:
    rollback_trend_grace_window = max(1, int(sys.argv[36])) if len(sys.argv) > 36 else 50
except Exception:
    rollback_trend_grace_window = 50
try:
    rollback_trend_grace_min_prior = max(1, int(sys.argv[37])) if len(sys.argv) > 37 else 50
except Exception:
    rollback_trend_grace_min_prior = 50
try:
    rollback_trend_grace_min_delta = float(sys.argv[38]) if len(sys.argv) > 38 else 0.0
except Exception:
    rollback_trend_grace_min_delta = 0.0
archive_restart_cooldown_file = sys.argv[39] if len(sys.argv) > 39 else ""
archive_restart_objective_fail_permanent = (sys.argv[40] if len(sys.argv) > 40 else "1") == "1"
early_comp_top_gap_enabled = (sys.argv[41] if len(sys.argv) > 41 else "1") == "1"
try:
    early_comp_top_gap_min_games = max(1, int(sys.argv[42])) if len(sys.argv) > 42 else 4
except Exception:
    early_comp_top_gap_min_games = 4
try:
    early_comp_top_gap_min_ratio = float(sys.argv[43]) if len(sys.argv) > 43 else 0.85
except Exception:
    early_comp_top_gap_min_ratio = 0.85

# 帯域脱出機構 F: stagnation_counter / wildcard origin override
_BASE_BRANCH_MAX_GAMES = branch_max_games
_BASE_BRANCH_PATIENCE = branch_patience
_WILDCARD_ORIGIN = {}
if wildcard_origin_file and os.path.exists(wildcard_origin_file):
    try:
        _WILDCARD_ORIGIN = json.load(open(wildcard_origin_file, encoding="utf-8")) or {}
    except Exception:
        _WILDCARD_ORIGIN = {}
if current_hash in _WILDCARD_ORIGIN:
    wo = _WILDCARD_ORIGIN[current_hash] or {}
    branch_max_games = int(wo.get("max_games_override", branch_max_games) or branch_max_games)
    branch_patience = int(wo.get("patience_override", branch_patience) or branch_patience)

def _update_wildcard_attempt_state(event):
    if not wildcard_attempt_state_file:
        return
    try:
        data = {}
        if os.path.exists(wildcard_attempt_state_file):
            try:
                data = json.load(open(wildcard_attempt_state_file, encoding="utf-8")) or {}
            except Exception:
                data = {}
        is_wildcard_origin = current_hash in _WILDCARD_ORIGIN
        origin = _WILDCARD_ORIGIN.get(current_hash, {}) if is_wildcard_origin else {}
        if event in ("PROMOTE", "OK_BEAT"):
            data["consecutive_wildcards"] = 0
            data["scale"] = 1.0
            data["last_reset_event"] = event
            data["last_reset_hash"] = current_hash
            data["last_reset_epoch"] = int(time.time())
            data["last_reason"] = "wildcard_success_reset"
        else:
            data["last_regression_event"] = event
            data["last_regression_hash"] = current_hash
            data["last_regression_epoch"] = int(time.time())
        if is_wildcard_origin:
            data["last_wildcard_outcome"] = event
            data["last_wildcard_outcome_hash"] = current_hash
            data["last_wildcard_outcome_epoch"] = int(time.time())
            data["last_wildcard_origin_type"] = str(origin.get("origin_type") or "wildcard")
        os.makedirs(os.path.dirname(wildcard_attempt_state_file) or ".", exist_ok=True)
        tmp = wildcard_attempt_state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, wildcard_attempt_state_file)
        if is_wildcard_origin and wildcard_outcome_file:
            current_payload = {
                "comp": current.get("comp"),
                "p50": current.get("p50"),
                "p25": current.get("p25"),
                "n": current.get("n"),
            } if isinstance(globals().get("current"), dict) else {}
            row = {
                "event": event,
                "epoch": int(time.time()),
                "hash": current_hash,
                "origin_type": str(origin.get("origin_type") or "wildcard"),
                "created_at_game": origin.get("created_at_game"),
                "wildcard_streak": origin.get("wildcard_streak"),
                "wildcard_applied": origin.get("wildcard_applied", []),
                "metrics": current_payload,
            }
            if origin.get("source_hash"):
                row["source_hash"] = origin.get("source_hash")
            for key in (
                "source_comp",
                "source_p50",
                "source_p25",
                "source_n",
                "source_russia_count",
                "source_soviet_count",
                "source_best_max_type",
            ):
                if key in origin:
                    row[key] = origin.get(key)
            os.makedirs(os.path.dirname(wildcard_outcome_file) or ".", exist_ok=True)
            with open(wildcard_outcome_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _close_successful_wildcard_origin(event):
    if event not in ("PROMOTE", "OK_BEAT"):
        return
    if not wildcard_origin_file or current_hash not in _WILDCARD_ORIGIN:
        return
    try:
        data = {}
        if os.path.exists(wildcard_origin_file):
            try:
                data = json.load(open(wildcard_origin_file, encoding="utf-8")) or {}
            except Exception:
                data = {}
        if current_hash in data:
            data.pop(current_hash, None)
            os.makedirs(os.path.dirname(wildcard_origin_file) or ".", exist_ok=True)
            tmp = wildcard_origin_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            os.replace(tmp, wildcard_origin_file)
        _WILDCARD_ORIGIN.pop(current_hash, None)
    except Exception:
        pass

def _update_stagnation(event):
    """Python ブロックを抜ける直前に呼ぶ。
    event: PROMOTE | REGRESSION | RESET | OK_BEAT | OK_IDLE | SAME_HASH_BACKSLIDE | OBJECTIVE_MISS
    PROMOTE / OK_BEAT → カウンタ 0、REGRESSION / RESET → +1、
    OBJECTIVE_MISS → +1 without rollback、OK_IDLE / SAME_HASH_BACKSLIDE → 変更なし
    """
    if not stagnation_file:
        return
    try:
        data = {}
        if os.path.exists(stagnation_file):
            try:
                data = json.load(open(stagnation_file, encoding="utf-8")) or {}
            except Exception:
                data = {}
        c = int(data.get("consecutive_no_improve", 0) or 0)
        if event in ("PROMOTE", "OK_BEAT"):
            c = 0
        elif event in ("REGRESSION", "RESET", "OBJECTIVE_MISS"):
            c += 1
        elif event in ("OK_IDLE", "SAME_HASH_BACKSLIDE"):
            pass
        data["consecutive_no_improve"] = c
        # counter 非依存の回帰ストリーク (WILDCARD masking 対策)。
        # OK_BEAT は現行戦略が許容範囲、または目的・トレンド面で守るべき
        # 成果を出した状態なので、古い回帰ストリークを残さない。
        # 通常 PROMOTE は -1 減衰のみ。ただし WILDCARD 起源の PROMOTE は
        # 脱出成功なので 0 に戻す。成功直後に regression_streak 経路で
        # もう一度 WILDCARD を撃つ churn を防ぐ。
        rs = int(data.get("regression_streak", 0) or 0)
        if event == "OK_BEAT":
            rs = 0
        elif event == "PROMOTE" and current_hash in _WILDCARD_ORIGIN:
            rs = 0
        elif event == "PROMOTE":
            rs = max(0, rs - 1)
        elif event in ("REGRESSION", "RESET", "OBJECTIVE_MISS"):
            rs += 1
        elif event == "OK_IDLE":
            rs = max(0, rs - 1)
        data["regression_streak"] = rs
        data["last_event"] = event
        data["updated_at"] = int(time.time())
        os.makedirs(os.path.dirname(stagnation_file) or ".", exist_ok=True)
        tmp = stagnation_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, stagnation_file)
    except Exception:
        pass
    _record_annealing_candidate(event)
    if current_hash in _WILDCARD_ORIGIN or event in ("PROMOTE", "OK_BEAT"):
        _update_wildcard_attempt_state(event)
        _close_successful_wildcard_origin(event)

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def quarantine_archive_restart_source(reason):
    if not archive_restart_cooldown_file:
        return
    origin = _WILDCARD_ORIGIN.get(current_hash, {}) if current_hash in _WILDCARD_ORIGIN else {}
    if str(origin.get("origin_type") or "") != "archive_restart":
        return
    selected_hash = str(origin.get("source_hash") or current_hash or "")
    if not selected_hash:
        return
    try:
        data = {}
        if os.path.exists(archive_restart_cooldown_file):
            try:
                data = json.load(open(archive_restart_cooldown_file, encoding="utf-8")) or {}
            except Exception:
                data = {}
        now = int(time.time())
        payload = {
            "epoch": now,
            "reason": reason,
            "from_hash": current_hash,
            "source_hash": selected_hash,
            "permanent": bool(archive_restart_objective_fail_permanent),
        }
        data[current_hash] = dict(payload, reason=reason + "_current")
        data[selected_hash] = payload
        os.makedirs(os.path.dirname(archive_restart_cooldown_file) or ".", exist_ok=True)
        tmp = archive_restart_cooldown_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, archive_restart_cooldown_file)
    except Exception:
        pass

def quantile(vals, p):
    xs = sorted(vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    xs = [int(v) for v in scores]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    if n > 1:
        var = sum((x - mean) ** 2 for x in xs) / n
        std = math.sqrt(var)
    else:
        std = 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {
        "comp": comp,
        "p50": p50,
        "p25": p25,
        "lcb": lcb,
        "n": n,
    }

def key(metrics_dict):
    if not metrics_dict:
        return (-10**18, -10**18, -10**18, -10**18)
    return (
        float(metrics_dict.get("comp", 0.0)),
        float(metrics_dict.get("p50", 0.0)),
        float(metrics_dict.get("p25", 0.0)),
        int(metrics_dict.get("n", 0)),
    )

def current_rolling_rank(metrics_dict):
    if not metrics_dict or int(metrics_dict.get("n", 0) or 0) < min_games_current:
        return None
    ranked = []
    seen_current = False
    for h, data in rolling.items():
        if h == current_hash:
            m = metrics_dict
            seen_current = True
        else:
            m = metrics((data or {}).get("scores", []) or [])
        if not m or int(m.get("n", 0) or 0) < min_games_current:
            continue
        ranked.append((key(m), h))
    if not seen_current:
        ranked.append((key(metrics_dict), current_hash))
    ranked.sort(reverse=True)
    for idx, (_, h) in enumerate(ranked, start=1):
        if h == current_hash:
            return idx
    return None

def rolling_comp_leader(current_metrics):
    ranked = []
    seen_current = False
    for h, data in rolling.items():
        if h == current_hash:
            m = current_metrics
            seen_current = True
        else:
            m = metrics((data or {}).get("scores", []) or [])
        if not m or int(m.get("n", 0) or 0) < min_games_current:
            continue
        ranked.append((key(m), h, m))
    if current_metrics and not seen_current and int(current_metrics.get("n", 0) or 0) >= min_games_current:
        ranked.append((key(current_metrics), current_hash, current_metrics))
    if not ranked:
        return "", None
    ranked.sort(reverse=True)
    _, h, m = ranked[0]
    return h, m

def russia_objective_graced(metrics_dict, objective):
    if rolling_score_russia_grace_rank <= 0:
        return False
    if int((objective or {}).get("russia_count", 0) or 0) > 0:
        return False
    rank = current_rolling_rank(metrics_dict)
    return rank is not None and rank <= rolling_score_russia_grace_rank

def nation_progress(path):
    max_type = 0
    russia = False
    soviet = False
    if not path or not os.path.exists(path):
        return max_type, russia, soviet
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if row.get("russia_created"):
                    russia = True
                if row.get("soviet_created"):
                    soviet = True
                pieces = ((row.get("state_snapshot") or {}).get("pieces") or [])
                for piece in pieces:
                    try:
                        t = int(piece.get("type", 0) or 0)
                    except Exception:
                        continue
                    if t > max_type:
                        max_type = t
                    if t >= 15:
                        russia = True
                    if t >= 16:
                        soviet = True
    except Exception:
        pass
    return max_type, russia, soviet

def objective_progress(data, scores):
    data = data or {}
    n = len(scores or [])
    max_types = []
    for raw in data.get("max_types", []) or []:
        try:
            max_types.append(int(raw))
        except Exception:
            pass
    if n > 0:
        max_types = max_types[-n:]
    if not max_types:
        archives = data.get("_recent_archives", []) or []
        if n > 0:
            archives = archives[-n:]
        progress = [nation_progress(path) for path in archives]
        max_types = [item[0] for item in progress]
        russia_count = sum(1 for _, russia, _ in progress if russia)
        soviet_count = sum(1 for _, _, soviet in progress if soviet)
    else:
        russia_count = int(data.get("russia_count", 0) or 0)
        soviet_count = int(data.get("soviet_count", 0) or 0)
    best_max_type = max([int(data.get("best_max_type", 0) or 0)] + max_types) if max_types or data.get("best_max_type") else 0
    if best_max_type >= 15 and russia_count <= 0:
        russia_count = 1
    if best_max_type >= 16 and soviet_count <= 0:
        soviet_count = 1
    return {
        "best_max_type": best_max_type,
        "russia_count": russia_count,
        "soviet_count": soviet_count,
        "max_types": max_types,
    }

def gap(anchor_metrics, target_metrics):
    return (
        max(0.0, float(anchor_metrics.get("comp", 0.0)) - float(target_metrics.get("comp", 0.0))),
        max(0.0, float(anchor_metrics.get("p50", 0.0)) - float(target_metrics.get("p50", 0.0))),
        max(0.0, float(anchor_metrics.get("p25", 0.0)) - float(target_metrics.get("p25", 0.0))),
    )

def breach_count(comp_gap, p50_gap, p25_gap, comp_th, p50_th, p25_th):
    return sum(
        [
            1 if comp_gap >= comp_th else 0,
            1 if p50_gap >= p50_th else 0,
            1 if p25_gap >= p25_th else 0,
        ]
    )

def load_score_history(path):
    scores = []
    if not path or not os.path.exists(path):
        return scores
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for raw in f:
                parts = raw.strip().split()
                if not parts:
                    continue
                try:
                    scores.append(int(float(parts[-1])))
                except Exception:
                    continue
    except Exception:
        return []
    return scores

def rollback_trend_grace():
    if not rollback_trend_grace_enabled:
        return None
    scores = load_score_history(eval_score_history_file)
    if len(scores) < rollback_trend_grace_window + rollback_trend_grace_min_prior:
        return None
    recent = scores[-rollback_trend_grace_window:]
    prior = scores[:-rollback_trend_grace_window]
    if len(prior) < rollback_trend_grace_min_prior:
        return None
    recent_avg = sum(recent) / len(recent)
    prior_avg = sum(prior) / len(prior)
    delta = recent_avg - prior_avg
    if delta > rollback_trend_grace_min_delta:
        return {
            "recent_avg": recent_avg,
            "prior_avg": prior_avg,
            "delta": delta,
            "window": rollback_trend_grace_window,
            "prior_n": len(prior),
        }
    return None

def _record_annealing_candidate(event):
    if event != "REGRESSION" or annealing_observe_enabled != "1" or not annealing_observe_file:
        return
    try:
        temp = max(1.0, annealing_base_temp)
        try:
            state = load_json(stagnation_file) if stagnation_file else {}
            regression_streak = max(0, int(state.get("regression_streak", 0) or 0))
        except Exception:
            regression_streak = 0
        decay = min(0.999, max(0.001, annealing_decay))
        cooled_temp = max(1.0, temp * (decay ** regression_streak))
        comp_gap_value = float(globals().get("curr_comp_gap", 0.0) or 0.0)
        probability = math.exp(-max(0.0, comp_gap_value) / cooled_temp)
        row = {
            "event": "ANNEALING_CANDIDATE",
            "epoch": int(time.time()),
            "hash": current_hash,
            "anchor_hash": globals().get("anchor_hash", ""),
            "n": (globals().get("current") or {}).get("n"),
            "comp": (globals().get("current") or {}).get("comp"),
            "anchor_comp": (globals().get("anchor") or {}).get("comp"),
            "comp_gap": comp_gap_value,
            "p50_gap": float(globals().get("curr_p50_gap", 0.0) or 0.0),
            "p25_gap": float(globals().get("curr_p25_gap", 0.0) or 0.0),
            "breach_count": int(globals().get("curr_breach", 0) or 0),
            "temperature": cooled_temp,
            "base_temperature": temp,
            "decay": decay,
            "regression_streak": regression_streak,
            "accept_probability": probability,
            "observe_only": True,
        }
        os.makedirs(os.path.dirname(annealing_observe_file) or ".", exist_ok=True)
        with open(annealing_observe_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

rolling = load_json(rs_file)
current_run = load_json(current_run_file)
current_scores = []
current_data = rolling.get(current_hash, {}) if isinstance(rolling.get(current_hash, {}), dict) else {}
if str(current_run.get("hash", "") or "") == current_hash:
    for x in current_run.get("scores", []) or []:
        try:
            current_scores.append(int(x))
        except Exception:
            pass
    current_data = current_run
if not current_scores:
    entry = rolling.get(current_hash, {})
    current_data = entry if isinstance(entry, dict) else {}
    for x in entry.get("scores", []) or []:
        try:
            current_scores.append(int(x))
        except Exception:
            pass
current = metrics(current_scores)

anchor_payload = load_json(anchor_file)
anchor_hash = str(anchor_payload.get("hash", "") or "")
if not anchor_hash:
    _update_stagnation("OK_IDLE")
    print("OK")
    raise SystemExit
anchor = {
    "comp": float(anchor_payload.get("comp", 0.0) or 0.0),
    "p50": float(anchor_payload.get("p50", 0.0) or 0.0),
    "p25": float(anchor_payload.get("p25", 0.0) or 0.0),
    "lcb": float(anchor_payload.get("lcb", 0.0) or 0.0),
    "n": int(anchor_payload.get("n", 0) or 0),
}

trend_grace = rollback_trend_grace()

def trend_grace_reason():
    if not trend_grace:
        return ""
    return (
        f"trend_grace_recent{int(trend_grace.get('window', 0))}_avg="
        f"{trend_grace.get('recent_avg', 0.0):.1f},prior_avg={trend_grace.get('prior_avg', 0.0):.1f},"
        f"delta={trend_grace.get('delta', 0.0):.1f},prior_n={int(trend_grace.get('prior_n', 0) or 0)}"
    )

origin_payload = _WILDCARD_ORIGIN.get(current_hash, {}) if current_hash in _WILDCARD_ORIGIN else {}
source_russia_count = int(origin_payload.get("source_russia_count", 0) or 0)
source_reliable_russia = bool(origin_payload.get("source_reliable_russia", False)) or source_russia_count >= 2
origin_objective_for_grace = {
    "russia_count": source_russia_count,
}
if (
    current_hash != anchor_hash
    and str(origin_payload.get("origin_type") or "") == "archive_restart"
    and (
        (
            int(anchor_payload.get("soviet_count", 0) or 0) > 0
            and int(origin_payload.get("source_soviet_count", 0) or 0) <= 0
            and not russia_objective_graced(current, origin_objective_for_grace)
        )
        or (
            russia_objective_regression_enabled == "1"
            and int(anchor_payload.get("russia_count", 0) or 0) > 0
            and not source_reliable_russia
            and not russia_objective_graced(current, origin_objective_for_grace)
        )
    )
):
    reasons = ["archive_restart_objective_floor"]
    if (
        int(anchor_payload.get("soviet_count", 0) or 0) > 0
        and int(origin_payload.get("source_soviet_count", 0) or 0) <= 0
        and not russia_objective_graced(current, origin_objective_for_grace)
    ):
        reasons.append("lost_soviet_path")
    if (
        russia_objective_regression_enabled == "1"
        and int(anchor_payload.get("russia_count", 0) or 0) > 0
        and not source_reliable_russia
        and not russia_objective_graced(current, origin_objective_for_grace)
    ):
        reasons.append("lost_russia_path")
    if "lost_russia_path" in reasons:
        quarantine_archive_restart_source("archive_restart_russia_not_reproduced")
    current = current or {"comp": 0.0, "p50": 0.0, "p25": 0.0, "lcb": 0.0, "n": len(current_scores)}
    # trend_grace は score-only rollback dampener。目的退行は免除しない。
    print(
        "REGRESSION:"
        f"mode=archive_objective_floor,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
        f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
        f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
        "comp_gap=0.0,p50_gap=0.0,p25_gap=0.0,breach_count=0,min_breach_count=0,"
        "best_hash=,best_comp=0.0,best_p50=0.0,best_p25=0.0,best_n=0,"
        "best_comp_gap=0.0,best_p50_gap=0.0,best_p25_gap=0.0,best_breach_count=0,"
        "branch_depth=0,branch_games=0,branch_patience=0,"
        f"anchor_best_max_type={int(anchor_payload.get('best_max_type', 0) or 0)},curr_best_max_type={int(origin_payload.get('source_best_max_type', 0) or 0)},"
        f"anchor_russia={int(anchor_payload.get('russia_count', 0) or 0)},curr_russia={int(origin_payload.get('source_russia_count', 0) or 0)},"
        f"anchor_soviet={int(anchor_payload.get('soviet_count', 0) or 0)},curr_soviet={int(origin_payload.get('source_soviet_count', 0) or 0)},"
        f"reasons={'+'.join(reasons)}"
    )
    _update_stagnation("REGRESSION")
    raise SystemExit

if not current:
    _update_stagnation("OK_IDLE")
    print("OK")
    raise SystemExit

active = load_json(active_branch_file)
branch_active = str(active.get("head_hash", "") or "") == current_hash and str(active.get("anchor_hash", "") or "")

def objective_tuple(progress):
    return (
        int(progress.get("soviet_count", 0) > 0),
        int(progress.get("russia_count", 0) > 0),
        int(progress.get("best_max_type", 0) or 0),
        int(progress.get("soviet_count", 0) or 0),
        int(progress.get("russia_count", 0) or 0),
    )

if branch_active:
    global_anchor_hash = anchor_hash
    global_anchor = dict(anchor)
    global_anchor_data = rolling.get(global_anchor_hash, {}) if isinstance(rolling.get(global_anchor_hash, {}), dict) else {}
    global_anchor_scores = []
    for x in (global_anchor_data.get("scores", []) or []):
        try:
            global_anchor_scores.append(int(x))
        except Exception:
            pass

    active_anchor_hash = str(active.get("anchor_hash", "") or anchor_hash)
    anchor_blob = active.get("anchor", {}) if isinstance(active.get("anchor"), dict) else {}
    active_anchor = {
        "comp": float(anchor_blob.get("comp", anchor.get("comp", 0.0)) or 0.0),
        "p50": float(anchor_blob.get("p50", anchor.get("p50", 0.0)) or 0.0),
        "p25": float(anchor_blob.get("p25", anchor.get("p25", 0.0)) or 0.0),
        "lcb": float(anchor_blob.get("lcb", anchor.get("lcb", 0.0)) or 0.0),
        "n": int(anchor_blob.get("n", anchor.get("n", 0)) or 0),
    }
    active_anchor_data = rolling.get(active_anchor_hash, {}) if isinstance(rolling.get(active_anchor_hash, {}), dict) else {}
    active_anchor_scores = []
    for x in (active_anchor_data.get("scores", []) or []):
        try:
            active_anchor_scores.append(int(x))
        except Exception:
            pass

    global_objective = objective_progress(global_anchor_data, global_anchor_scores)
    active_objective = objective_progress(active_anchor_data, active_anchor_scores)
    if objective_tuple(global_objective) > objective_tuple(active_objective):
        anchor_hash = global_anchor_hash
        anchor = global_anchor
        try:
            active["anchor_hash"] = global_anchor_hash
            active["anchor"] = global_anchor
            active["anchor_synced_from_global"] = int(time.time())
            active["anchor_sync_reason"] = "global_objective_anchor_better"
            with open(active_branch_file, "w", encoding="utf-8") as f:
                json.dump(active, f, ensure_ascii=False)
        except Exception:
            pass
    else:
        anchor_hash = active_anchor_hash
        anchor = active_anchor

anchor_data = rolling.get(anchor_hash, {}) if isinstance(rolling.get(anchor_hash, {}), dict) else {}
anchor_scores = []
for x in (anchor_data.get("scores", []) or []):
    try:
        anchor_scores.append(int(x))
    except Exception:
        pass
current_objective = objective_progress(current_data, current_scores)
anchor_objective = objective_progress(anchor_data, anchor_scores)

def objective_miss_against_anchor(anchor_progress, current_progress):
    if int(anchor_progress.get("soviet_count", 0) or 0) > 0 and int(current_progress.get("soviet_count", 0) or 0) <= 0:
        return True
    if int(anchor_progress.get("russia_count", 0) or 0) > 0 and int(current_progress.get("russia_count", 0) or 0) <= 0:
        return True
    if int(anchor_progress.get("best_max_type", 0) or 0) >= early_objective_min_best_type and int(current_progress.get("best_max_type", 0) or 0) < early_objective_min_best_type:
        return True
    return False

def ok_event_for_objective(anchor_progress, current_progress):
    return "OBJECTIVE_MISS" if objective_miss_against_anchor(anchor_progress, current_progress) else "OK_BEAT"

def objective_allows_anchor_promotion(anchor_progress, current_progress):
    if int(anchor_progress.get("soviet_count", 0) or 0) > 0 and int(current_progress.get("soviet_count", 0) or 0) <= 0:
        return False
    if int(anchor_progress.get("russia_count", 0) or 0) > 0 and int(current_progress.get("russia_count", 0) or 0) <= 0:
        return False
    if int(anchor_progress.get("best_max_type", 0) or 0) >= early_objective_min_best_type and int(current_progress.get("best_max_type", 0) or 0) < early_objective_min_best_type:
        return False
    return objective_tuple(current_progress) >= objective_tuple(anchor_progress)

STAGE_GATE_SEQUENCE = [
    (11, "lost_turkmenistan_gate"),
    (13, "lost_ukraine_gate"),
    (14, "lost_kazakhstan_gate"),
    (15, "lost_russia_path"),
    (16, "lost_soviet_path"),
]

def stage_gate_rate(progress, threshold):
    max_types = []
    for raw in (progress or {}).get("max_types", []) or []:
        try:
            max_types.append(int(raw))
        except Exception:
            pass
    if not max_types:
        best = int((progress or {}).get("best_max_type", 0) or 0)
        return 1.0 if best >= threshold else 0.0
    hits = sum(1 for value in max_types if value >= threshold)
    return hits / len(max_types)

def stage_gate_regression_reason(anchor_progress, current_progress, current_metrics):
    rank = current_rolling_rank(current_metrics)
    if rank is None or rank <= rolling_score_russia_grace_rank:
        return ""
    for threshold, reason in STAGE_GATE_SEQUENCE:
        current_rate = stage_gate_rate(current_progress, threshold)
        anchor_rate = stage_gate_rate(anchor_progress, threshold)
        current_best = int((current_progress or {}).get("best_max_type", 0) or 0)
        anchor_best = int((anchor_progress or {}).get("best_max_type", 0) or 0)
        current_unmet = current_rate < 1.0
        regressed = anchor_rate > current_rate or (anchor_best >= threshold and current_best < threshold)
        if current_unmet and regressed:
            return reason
    return ""

try:
    current_games_total = int(current_data.get("games_total", current.get("n", 0)) or current.get("n", 0) or 0)
except Exception:
    current_games_total = int(current.get("n", 0) or 0)

if current_hash == anchor_hash and not branch_active:
    same_hash_backslide_mature_n = max(
        int(anchor.get("n", 0) or 0) + max(0, same_hash_backslide_min_extra_games),
        min_games_current,
    )
    if (
        same_hash_backslide_enabled == "1"
        and current_games_total >= same_hash_backslide_mature_n
        and (
            float(current.get("comp", 0.0) or 0.0) < float(anchor.get("comp", 0.0) or 0.0)
            or float(current.get("p50", 0.0) or 0.0) < float(anchor.get("p50", 0.0) or 0.0)
            or float(current.get("p25", 0.0) or 0.0) < float(anchor.get("p25", 0.0) or 0.0)
        )
    ):
        _update_stagnation("SAME_HASH_BACKSLIDE")
        print("OK")
        raise SystemExit
    if (
        (
            int(anchor_objective.get("soviet_count", 0) or 0) > 0
            and int(current_objective.get("soviet_count", 0) or 0) <= 0
        )
        or (
            int(anchor_objective.get("russia_count", 0) or 0) > 0
            and int(current_objective.get("russia_count", 0) or 0) <= 0
        )
        or (
            int(anchor_objective.get("best_max_type", 0) or 0) >= early_objective_min_best_type
            and int(current_objective.get("best_max_type", 0) or 0) < early_objective_min_best_type
        )
    ):
        _update_stagnation("OK_IDLE")
        print("OK")
        raise SystemExit
    _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
    print("OK")
    raise SystemExit

curr_comp_gap, curr_p50_gap, curr_p25_gap = gap(anchor, current)
curr_breach = breach_count(curr_comp_gap, curr_p50_gap, curr_p25_gap, min_comp_gap, min_p50_gap, min_p25_gap)
hard_breach = breach_count(curr_comp_gap, curr_p50_gap, curr_p25_gap, hard_comp_gap, hard_p50_gap, hard_p25_gap)
top_hash, top_metrics = rolling_comp_leader(current)
top_comp = float((top_metrics or {}).get("comp", 0.0) or 0.0)
curr_comp = float(current.get("comp", 0.0) or 0.0)
frontier_grace_min_type = max(14, early_objective_min_best_type - 1)
frontier_grace_active = (
    current["n"] < min_games_current
    and int(current_objective.get("best_max_type", 0) or 0) >= frontier_grace_min_type
    and int(current_objective.get("russia_count", 0) or 0) <= 0
)

objective_reasons = []
russia_grace_active = russia_objective_graced(current, current_objective)
if (
    early_objective_enabled == "1"
    and current_hash != anchor_hash
    and current["n"] >= max(1, early_objective_min_games)
):
    if (
        anchor_objective.get("soviet_count", 0) > 0
        and current_objective.get("soviet_count", 0) <= 0
        and not russia_grace_active
    ):
        objective_reasons.append("lost_soviet_path")
    # NOTE(2026-05-18 ユーザー指示): lost_russia_path(type15経路喪失) は早期ゲートから除外。
    # RUSSIA_OBJECTIVE_REGRESSION_ENABLED=0 の間は通常ゲートでも粛清しない。
    # lost_soviet_path は従来どおり早期ゲートに残す。
    if objective_reasons:
        # trend_grace は score-only rollback dampener。目的退行は免除しない。
        print(
            "REGRESSION:"
            f"mode=early_objective_regression,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
            f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
            f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
            f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
            f"breach_count={curr_breach},min_breach_count={min_breach_count},"
            "best_hash=,best_comp=0.0,best_p50=0.0,best_p25=0.0,best_n=0,"
            f"best_comp_gap={curr_comp_gap:.1f},best_p50_gap={curr_p50_gap:.1f},best_p25_gap={curr_p25_gap:.1f},best_breach_count={curr_breach},"
            f"branch_depth=0,branch_games=0,branch_patience=0,"
            f"anchor_best_max_type={anchor_objective.get('best_max_type', 0)},curr_best_max_type={current_objective.get('best_max_type', 0)},"
            f"anchor_russia={anchor_objective.get('russia_count', 0)},curr_russia={current_objective.get('russia_count', 0)},"
            f"anchor_soviet={anchor_objective.get('soviet_count', 0)},curr_soviet={current_objective.get('soviet_count', 0)},"
            f"reasons=early_objective_regression+{'+'.join(objective_reasons)}"
        )
        _update_stagnation("REGRESSION")
        raise SystemExit

if (
    early_comp_top_gap_enabled
    and current_hash != top_hash
    and top_hash
    and top_comp > 0
    and current["n"] >= early_comp_top_gap_min_games
    and not frontier_grace_active
    and curr_comp < top_comp * early_comp_top_gap_min_ratio
):
    top_comp_gap, top_p50_gap, top_p25_gap = gap(top_metrics, current)
    top_breach = breach_count(top_comp_gap, top_p50_gap, top_p25_gap, min_comp_gap, min_p50_gap, min_p25_gap)
    print(
        "REGRESSION:"
        f"mode=early_comp_top_gap,rollback_hash={top_hash},anchor_hash={anchor_hash},"
        f"anchor_comp={top_metrics['comp']:.1f},anchor_p50={top_metrics['p50']:.1f},anchor_p25={top_metrics['p25']:.1f},anchor_n={top_metrics['n']},"
        f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
        f"comp_gap={top_comp_gap:.1f},p50_gap={top_p50_gap:.1f},p25_gap={top_p25_gap:.1f},"
        f"breach_count={top_breach},min_breach_count={min_breach_count},"
        "best_hash=,best_comp=0.0,best_p50=0.0,best_p25=0.0,best_n=0,"
        f"best_comp_gap={top_comp_gap:.1f},best_p50_gap={top_p50_gap:.1f},best_p25_gap={top_p25_gap:.1f},best_breach_count={top_breach},"
        "branch_depth=0,branch_games=0,branch_patience=0,"
        f"reasons=early_comp_top_gap+curr_comp_below_top_ratio+ratio={curr_comp / top_comp:.3f}+min_ratio={early_comp_top_gap_min_ratio:.3f}"
    )
    _update_stagnation("REGRESSION")
    raise SystemExit

# 最小サンプルガード: n<12 では p50/p25 の変動が大きすぎて通常 regression 判定できない。
# ただし上の早期目的退行ゲートだけは、ソ連経路喪失を短いサンプルで止める。
if current["n"] < min_games_current:
    _update_stagnation("OK_IDLE")
    print("OK")
    raise SystemExit

objective_reasons = []
if current_hash != anchor_hash:
    stage_gate_reason = stage_gate_regression_reason(anchor_objective, current_objective, current)
    if stage_gate_reason:
        objective_reasons.append(stage_gate_reason)
    if (
        anchor_objective.get("soviet_count", 0) > 0
        and current_objective.get("soviet_count", 0) <= 0
        and not russia_grace_active
        and "lost_soviet_path" not in objective_reasons
    ):
        objective_reasons.append("lost_soviet_path")
    if (
        russia_objective_regression_enabled == "1"
        and anchor_objective.get("best_max_type", 0) >= 15
        and current_objective.get("best_max_type", 0) < 15
        and not russia_grace_active
        and "lost_russia_path" not in objective_reasons
    ):
        objective_reasons.append("lost_russia_path")
if objective_reasons:
    # trend_grace は score-only rollback dampener。目的退行は免除しない。
    print(
        "REGRESSION:"
        f"mode=objective_regression,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
        f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
        f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
        f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
        f"breach_count={curr_breach},min_breach_count={min_breach_count},"
        "best_hash=,best_comp=0.0,best_p50=0.0,best_p25=0.0,best_n=0,"
        f"best_comp_gap={curr_comp_gap:.1f},best_p50_gap={curr_p50_gap:.1f},best_p25_gap={curr_p25_gap:.1f},best_breach_count={curr_breach},"
        f"branch_depth=0,branch_games=0,branch_patience=0,"
        f"anchor_best_max_type={anchor_objective.get('best_max_type', 0)},curr_best_max_type={current_objective.get('best_max_type', 0)},"
        f"anchor_russia={anchor_objective.get('russia_count', 0)},curr_russia={current_objective.get('russia_count', 0)},"
        f"anchor_soviet={anchor_objective.get('soviet_count', 0)},curr_soviet={current_objective.get('soviet_count', 0)},"
        f"reasons=objective_regression+{'+'.join(objective_reasons)}"
    )
    _update_stagnation("REGRESSION")
    raise SystemExit

if (
    current["n"] >= min_games_current
    and current_hash != anchor_hash
    and key(current) > key(anchor)
    and objective_allows_anchor_promotion(anchor_objective, current_objective)
):
    _update_stagnation("PROMOTE")
    print(
        "PROMOTE:"
        f"anchor_hash={anchor_hash},current_hash={current_hash},"
        f"anchor_comp={anchor['comp']:.1f},curr_comp={current['comp']:.1f},"
        f"anchor_p50={anchor['p50']:.1f},curr_p50={current['p50']:.1f},"
        f"anchor_p25={anchor['p25']:.1f},curr_p25={current['p25']:.1f},"
        f"anchor_n={anchor['n']},curr_n={current['n']},"
        "reasons=anchor_promoted"
    )
    raise SystemExit

# best_hash / best_metrics / best_comp_gap 等は early_branch_regression でも使うため、先に計算する
best_hash = str(active.get("best_hash", "") or "")
best_blob = active.get("best", {}) if isinstance(active.get("best"), dict) else {}
best_metrics = {
    "comp": float(best_blob.get("comp", 0.0) or 0.0),
    "p50": float(best_blob.get("p50", 0.0) or 0.0),
    "p25": float(best_blob.get("p25", 0.0) or 0.0),
    "lcb": float(best_blob.get("lcb", 0.0) or 0.0),
    "n": int(best_blob.get("n", 0) or 0),
} if best_hash else {}
best_comp_gap, best_p50_gap, best_p25_gap = gap(anchor, best_metrics if best_metrics else current)
best_breach = breach_count(best_comp_gap, best_p50_gap, best_p25_gap, min_comp_gap, min_p50_gap, min_p25_gap)
depth = int(active.get("depth", 0) or 0)
closed_games = int(active.get("closed_games", 0) or 0)
patience = int(active.get("patience", 0) or 0)
branch_games = closed_games + int(current.get("n", 0) or 0)

# Branch中の早期regression: n>=12 で明らかに劣後している場合に早期撤退
# 条件: branch_active AND curr_breach>=2 AND (best未更新 OR currentがbestを1500comp以上下回る)
# 注: n<12 は関数冒頭のサンプルガードで弾かれているのでここには来ない
if branch_active and curr_breach >= min_breach_count and current_hash != anchor_hash:
    no_improvement_signal = not best_hash
    current_worse_than_best = bool(best_hash and best_metrics and current["comp"] < best_metrics.get("comp", 0) - 1500)
    if no_improvement_signal or current_worse_than_best:
        if trend_grace:
            _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
            print(f"OK:{trend_grace_reason()}")
            raise SystemExit
        print(
            "REGRESSION:"
            f"mode=early_branch,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
            f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
            f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
            f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
            f"breach_count={curr_breach},min_breach_count={min_breach_count},"
            f"best_hash={best_hash},best_comp={best_metrics.get('comp', 0.0):.1f},best_p50={best_metrics.get('p50', 0.0):.1f},best_p25={best_metrics.get('p25', 0.0):.1f},best_n={best_metrics.get('n', 0)},"
            f"best_comp_gap={best_comp_gap:.1f},best_p50_gap={best_p50_gap:.1f},best_p25_gap={best_p25_gap:.1f},best_breach_count={best_breach},"
            f"branch_depth={depth},branch_games={branch_games},branch_patience={patience},"
            f"reasons=early_branch_regression+curr_breach"
            + ("" if no_improvement_signal else "+current_worse_than_best")
        )
        _update_stagnation("REGRESSION")
        raise SystemExit

# anchor_direct / budget_exhausted / hard_breach は成熟したサンプル (n>=min_games_current) のみ判定
if current["n"] < min_games_current:
    _update_stagnation("OK_IDLE")
    print("OK")
    raise SystemExit

if not branch_active:
    if curr_breach >= min_breach_count and current_hash != anchor_hash:
        if trend_grace:
            _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
            print(f"OK:{trend_grace_reason()}")
            raise SystemExit
        direct_reason = "hard_fail+soft_fail+anchor_direct" if hard_breach >= hard_min_breach_count else "soft_fail+anchor_direct"
        print(
            "REGRESSION:"
            f"mode=anchor_direct,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
            f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
            f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
            f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
            f"breach_count={curr_breach},min_breach_count={min_breach_count},"
            "best_hash=,best_comp=0.0,best_p50=0.0,best_p25=0.0,best_n=0,"
            f"best_comp_gap={curr_comp_gap:.1f},best_p50_gap={curr_p50_gap:.1f},best_p25_gap={curr_p25_gap:.1f},best_breach_count={curr_breach},"
            "branch_depth=0,branch_games=0,branch_patience=0,"
            f"reasons={direct_reason}"
        )
        _update_stagnation("REGRESSION")
        raise SystemExit
    if current_hash != anchor_hash and key(current) <= key(anchor):
        _update_stagnation("RESET")
        print("OK")
        raise SystemExit
    _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
    print("OK")
    raise SystemExit

best_hash = str(active.get("best_hash", "") or "")
best_blob = active.get("best", {}) if isinstance(active.get("best"), dict) else {}
best_metrics = {
    "comp": float(best_blob.get("comp", 0.0) or 0.0),
    "p50": float(best_blob.get("p50", 0.0) or 0.0),
    "p25": float(best_blob.get("p25", 0.0) or 0.0),
    "lcb": float(best_blob.get("lcb", 0.0) or 0.0),
    "n": int(best_blob.get("n", 0) or 0),
} if best_hash else {}
if key(current) > key(best_metrics):
    best_hash = current_hash
    best_metrics = dict(current)

best_comp_gap, best_p50_gap, best_p25_gap = gap(anchor, best_metrics if best_metrics else current)
best_breach = breach_count(best_comp_gap, best_p50_gap, best_p25_gap, min_comp_gap, min_p50_gap, min_p25_gap)
depth = int(active.get("depth", 0) or 0)
closed_games = int(active.get("closed_games", 0) or 0)
patience = int(active.get("patience", 0) or 0)
branch_games = closed_games + int(current.get("n", 0) or 0)
budget_reasons = []
if depth >= branch_max_depth:
    budget_reasons.append("depth")
if branch_games >= branch_max_games:
    budget_reasons.append("games")
if patience >= branch_patience:
    budget_reasons.append("patience")

if hard_breach >= hard_min_breach_count:
    if trend_grace:
        _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
        print(f"OK:{trend_grace_reason()}")
        raise SystemExit
    print(
        "REGRESSION:"
        f"mode=anchor_branch,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
        f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
        f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
        f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
        f"breach_count={curr_breach},min_breach_count={min_breach_count},"
        f"best_hash={best_hash},best_comp={best_metrics.get('comp', 0.0):.1f},best_p50={best_metrics.get('p50', 0.0):.1f},best_p25={best_metrics.get('p25', 0.0):.1f},best_n={best_metrics.get('n', 0)},"
        f"best_comp_gap={best_comp_gap:.1f},best_p50_gap={best_p50_gap:.1f},best_p25_gap={best_p25_gap:.1f},best_breach_count={best_breach},"
        f"branch_depth={depth},branch_games={branch_games},branch_patience={patience},"
        "reasons=hard_fail+branch"
    )
    _update_stagnation("REGRESSION")
    raise SystemExit

if budget_reasons:
    if best_breach >= min_breach_count:
        if trend_grace:
            _update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
            print(f"OK:{trend_grace_reason()}")
            raise SystemExit
        print(
            "REGRESSION:"
            f"mode=anchor_branch,rollback_hash={anchor_hash},anchor_hash={anchor_hash},"
            f"anchor_comp={anchor['comp']:.1f},anchor_p50={anchor['p50']:.1f},anchor_p25={anchor['p25']:.1f},anchor_n={anchor['n']},"
            f"curr_comp={current['comp']:.1f},curr_p50={current['p50']:.1f},curr_p25={current['p25']:.1f},curr_n={current['n']},"
            f"comp_gap={curr_comp_gap:.1f},p50_gap={curr_p50_gap:.1f},p25_gap={curr_p25_gap:.1f},"
            f"breach_count={curr_breach},min_breach_count={min_breach_count},"
            f"best_hash={best_hash},best_comp={best_metrics.get('comp', 0.0):.1f},best_p50={best_metrics.get('p50', 0.0):.1f},best_p25={best_metrics.get('p25', 0.0):.1f},best_n={best_metrics.get('n', 0)},"
            f"best_comp_gap={best_comp_gap:.1f},best_p50_gap={best_p50_gap:.1f},best_p25_gap={best_p25_gap:.1f},best_breach_count={best_breach},"
            f"branch_depth={depth},branch_games={branch_games},branch_patience={patience},"
            f"reasons=budget_exhausted+{'+'.join(budget_reasons)}"
        )
        _update_stagnation("REGRESSION")
        raise SystemExit
    print(
        "RESET:"
        f"anchor_hash={anchor_hash},current_hash={current_hash},"
        f"best_hash={best_hash},best_comp={best_metrics.get('comp', 0.0):.1f},best_p50={best_metrics.get('p50', 0.0):.1f},best_p25={best_metrics.get('p25', 0.0):.1f},best_n={best_metrics.get('n', 0)},"
        f"best_comp_gap={best_comp_gap:.1f},best_p50_gap={best_p50_gap:.1f},best_p25_gap={best_p25_gap:.1f},best_breach_count={best_breach},"
        f"branch_depth={depth},branch_games={branch_games},branch_patience={patience},"
        f"reasons=budget_reset+{'+'.join(budget_reasons)}"
    )
    _update_stagnation("RESET")
    raise SystemExit

_update_stagnation(ok_event_for_objective(anchor_objective, current_objective))
print("OK")
PY
		2>/dev/null
	)

	if echo "$result" | grep -q '^PROMOTE:'; then
		log "[BRANCH] anchor昇格: $result"
		if _promote_current_strategy_to_anchor "$strategy_hash"; then
			_clear_active_branch
			log "[BRANCH] current strategy promoted to anchor: ${strategy_hash}"
		fi
		return 1
	fi

	if echo "$result" | grep -q '^RESET:'; then
		log "[BRANCH] exploration budget reset: $result"
		_clear_active_branch
		return 1
	fi

	if echo "$result" | grep -q '^REGRESSION:'; then
		log "[REGRESSION] リグレッション検知: $result"
		local running_pid=0
		running_pid=$(_find_live_improve_pid 2>/dev/null || echo 0)
		if [ "${running_pid:-0}" -ne 0 ]; then
			log "[REGRESSION] 改善プロセス停止 (PID=$running_pid)"
			if ! _stop_improve_pid_if_running "$running_pid" "regression"; then
				log "[REGRESSION] 改善プロセス停止失敗: PID=$running_pid がまだ生存"
			fi
		fi
		if _find_live_improve_pid >/dev/null 2>&1; then
			_sync_improve_state_with_live_process >/dev/null 2>&1 || true
		else
			IMPROVE_PID=0
			_write_improve_state "idle" "0" ""
		fi
		log "[REGRESSION] 自動ロールバック開始"

		echo "$strategy_hash" >>"$REJECTED_HASHES_FILE"
		if [ -f "$REJECTED_HASHES_FILE" ]; then
			tail -20 "$REJECTED_HASHES_FILE" >"$REJECTED_HASHES_FILE.tmp"
			mv "$REJECTED_HASHES_FILE.tmp" "$REJECTED_HASHES_FILE"
		fi
		# E: 失敗 current の挙動シグネチャを tabu に追記 (帯域脱出機構)
		if [ "${TABU_ENABLED:-0}" = "1" ]; then
			_record_tabu_signature "$strategy_hash" >/dev/null 2>&1 || true
		fi
		python3 - "$ROLLING_SCORES_FILE" "$REJECTED_HASH_META_FILE" "$strategy_hash" <<'PY' 2>/dev/null
import json
import math
import os
import sys

rolling_file, meta_file, target_hash = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(rolling_file):
    raise SystemExit(0)
try:
    rolling = json.load(open(rolling_file))
except Exception:
    raise SystemExit(0)
if target_hash not in rolling:
    raise SystemExit(0)
scores = [int(x) for x in rolling[target_hash].get("scores", [])]
if not scores:
    raise SystemExit(0)
xs = sorted(scores)
n = len(xs)
mean = sum(xs) / n
if n == 1:
    p25 = p50 = float(xs[0])
    std = 0.0
else:
    def q(p):
        pos = (n - 1) * p
        lo = int(pos)
        hi = min(lo + 1, n - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac
    p25 = q(0.25)
    p50 = q(0.50)
    var = sum((x - mean) ** 2 for x in xs) / n
    std = math.sqrt(var)
lcb = mean - 1.28 * (std / math.sqrt(n))
comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
try:
    meta = json.load(open(meta_file))
except Exception:
    meta = {}
meta[target_hash] = {
    "comp": round(comp, 4),
    "games_total": int(rolling[target_hash].get("games_total", n) or n),
    "n": n,
    "updated_at": int(__import__("time").time()),
}
with open(meta_file, "w") as f:
    json.dump(meta, f)
PY

		local rollback_file="" rollback_note="" rollback_hash=""
		local best_candidate
		best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")
		if [ -n "$best_candidate" ]; then
			local best_comp best_p50 best_p25 best_lcb best_n
			IFS='|' read -r rollback_hash best_comp best_p50 best_p25 best_lcb best_n rollback_file <<<"$best_candidate"
			rollback_note="rolling_top hash=${rollback_hash} comp=${best_comp} p50=${best_p50} p25=${best_p25} lcb=${best_lcb} n=${best_n}"
		fi
		if [ -z "$rollback_file" ]; then
			rollback_hash=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*rollback_hash=([^,]+).*/\1/p')
				if [ -n "$rollback_hash" ]; then
					local anchor_comp anchor_p50 anchor_p25 anchor_n
					anchor_comp=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_comp=([^,]+).*/\1/p')
					anchor_p50=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_p50=([^,]+).*/\1/p')
					anchor_p25=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_p25=([^,]+).*/\1/p')
					anchor_n=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_n=([^,]+).*/\1/p')
					if _is_rollback_target_on_cooldown "$strategy_hash" "$rollback_hash"; then
						log "[REGRESSION] anchor_top1候補スキップ: $rollback_hash はrollback先cooldown中"
						rollback_hash=""
					else
						local anchor_candidate_file
						anchor_candidate_file=$(_find_rollback_candidate_file_for_hash "$rollback_hash" 2>/dev/null || echo "")
						if [ -n "$anchor_candidate_file" ] && _rollback_candidate_file_is_valid "$rollback_hash" "$anchor_candidate_file"; then
							rollback_file="$anchor_candidate_file"
							rollback_note="anchor_top1 hash=${rollback_hash} comp=${anchor_comp:-?} p50=${anchor_p50:-?} p25=${anchor_p25:-?} n=${anchor_n:-?}"
						else
							log "[REGRESSION] anchor_top1候補スキップ: $rollback_hash はvalidation失敗archive"
							rollback_hash=""
						fi
					fi
				fi
		fi

		if [ -z "$rollback_file" ]; then
			log "[REGRESSION] ロールバック候補なし → 現在戦略を維持"
			return 0
		fi

		local rollback_game_num rollback_analysis_summary
		rollback_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

		# 先に現在の戦略をバックアップ（失敗時はロールバック中止）
		cp "$STRATEGY_FILE" "${STRATEGY_FILE}.bak" || {
			log "[REGRESSION] CRITICAL: バックアップ失敗、ロールバック中止"
			return 1
		}
		if ! cp "$rollback_file" "$STRATEGY_FILE"; then
			log "[REGRESSION] CRITICAL: ロールバックファイルコピー失敗、復元中"
			cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE" 2>/dev/null || true
			return 1
		fi
		if ! validate_strategy "$STRATEGY_FILE"; then
			log "[REGRESSION] CRITICAL: ロールバック後バリデーション失敗、復元中"
			cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE" 2>/dev/null || true
			return 1
		fi
		local rolled_hash
		rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
		_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
		if [ -n "$rollback_hash" ] && [ -n "$rolled_hash" ] && [ "$rollback_hash" != "$rolled_hash" ]; then
			log "[REGRESSION] rollback target normalized: ${rollback_hash} -> ${rolled_hash}; exclude stale anchor candidate"
			_merge_rolling_scores_on_normalize "$rollback_hash" "$rolled_hash" || true
			echo "$rollback_hash" >>"$REJECTED_HASHES_FILE"
			if [ -f "$REJECTED_HASHES_FILE" ]; then
				tail -20 "$REJECTED_HASHES_FILE" >"$REJECTED_HASHES_FILE.tmp"
				mv "$REJECTED_HASHES_FILE.tmp" "$REJECTED_HASHES_FILE"
			fi
			python3 - "$REJECTED_HASH_META_FILE" "$rollback_hash" "$rolled_hash" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

meta_file, stale_hash, rolled_hash = sys.argv[1:4]
try:
    meta = json.load(open(meta_file))
except Exception:
    meta = {}
meta[stale_hash] = {
    **(meta.get(stale_hash, {}) if isinstance(meta.get(stale_hash), dict) else {}),
    "updated_at": int(time.time()),
    "normalized_to_hash": rolled_hash,
    "reason": "rollback_target_normalized",
}
os.makedirs(os.path.dirname(meta_file) or ".", exist_ok=True)
with open(meta_file, "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
PY
				local normalized_anchor_hash=""
				normalized_anchor_hash=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_hash=([^,]+).*/\1/p')
				if [ -n "$normalized_anchor_hash" ] && [ "$rollback_hash" != "$normalized_anchor_hash" ] && [ -f "$STRATEGY_HASH_ARCHIVE_DIR/${normalized_anchor_hash}.py" ]; then
					log "[REGRESSION] normalized fallback target rejected; retry anchor rollback: ${normalized_anchor_hash}"
					local normalized_anchor_file="$STRATEGY_HASH_ARCHIVE_DIR/${normalized_anchor_hash}.py"
					if cp "$normalized_anchor_file" "$STRATEGY_FILE" && validate_strategy "$STRATEGY_FILE"; then
						rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
						_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
						rollback_hash="$normalized_anchor_hash"
						rollback_file="$normalized_anchor_file"
						rollback_note="normalized_fallback_anchor hash=${rollback_hash}"
					else
						log "[REGRESSION] normalized fallback anchor retry failed; restoring pre-rollback strategy"
						cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE" 2>/dev/null || true
						return 1
					fi
				fi
			fi
			python3 - "$LAST_ROLLBACK_PAIR_FILE" "$strategy_hash" "$rolled_hash" "$rollback_note" <<'PY' 2>/dev/null
import json
import sys
import time

out_file, from_hash, to_hash, note = sys.argv[1:5]
payload = {
    "from_hash": from_hash,
    "to_hash": to_hash,
    "note": note,
    "updated_at": int(time.time()),
}
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
			_record_rollback_target_cooldown "$strategy_hash" "$rolled_hash" "$rollback_game_num" "$rollback_note"
			REGRESSION_ROLLBACK_DONE=1
		REGRESSION_ROLLBACK_HASH="$rolled_hash"
		REGRESSION_ROLLBACK_RESULT="$result"
		_clear_active_branch
		log "[REGRESSION] リバート完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"
		if [ -x ./overlay_notify.sh ]; then
			./overlay_notify.sh rollback "粛清 rollback (game ${rollback_game_num})" "粛清: ${strategy_hash:0:8} → 復帰 ${rolled_hash:0:8} | file=$(basename "${rollback_file:-?}") | ${rollback_note}" "warn" >/dev/null 2>&1 || true
		fi

		rollback_analysis_summary=$(_write_rollback_analysis_file "$strategy_hash" "$rolled_hash" "$result" "$rollback_note" "$rollback_game_num" 2>/dev/null || true)
		if [ -n "$rolled_hash" ]; then
			if [ "${ROLLBACK_REVALIDATE_TARGET_ENABLED:-1}" = "1" ]; then
				_reset_current_strategy_run "$rolled_hash"
				log "[CURRENT-RUN] rollback revalidate fresh cycle: hash=${rolled_hash}"
			elif _seed_current_strategy_run_from_rolling "$rolled_hash"; then
				log "[CURRENT-RUN] rollback seed from rolling: hash=${rolled_hash}"
			else
				_reset_current_strategy_run "$rolled_hash"
				log "[CURRENT-RUN] rollback seed missing -> reset: hash=${rolled_hash}"
			fi
		fi
		(_refresh_best_strategy_anchor "" >/dev/null 2>&1) &
		_wait_pid_with_timeout "$!" "${ROLLBACK_ANCHOR_REFRESH_TIMEOUT_SEC:-30}" "rollback_anchor_refresh" || true
		if [ -n "$rollback_analysis_summary" ]; then
			{
				echo "=== $(date '+%Y-%m-%d %H:%M') ROLLBACK Game#${rollback_game_num} ${strategy_hash} -> ${rolled_hash} ==="
				printf '%s\n' "$rollback_analysis_summary"
				echo ""
			} >>"logs/change_log.txt"
			if [ -f "logs/change_log.txt" ] && [ "$(wc -l <"logs/change_log.txt")" -gt 200 ]; then
				tail -200 "logs/change_log.txt" >"logs/change_log.txt.tmp"
				mv "logs/change_log.txt.tmp" "logs/change_log.txt"
			fi
		fi
		start_rollback_postmortem_worker "$strategy_hash" "$rolled_hash" "$rollback_game_num" "$rollback_note"

		local rollback_event_analysis=""
		rollback_event_analysis=$(_extract_rollback_analysis_for_phylo "$ROLLBACK_ANALYSIS_FILE")
		append_phyrogenetic_event "rollback" "$strategy_hash" "$rolled_hash" "$rollback_game_num" "" \
			"$rollback_analysis_summary" "$rollback_event_analysis"
		(refresh_phyrogenetic_tree --pending-edge rollback "$strategy_hash" "$rolled_hash" >/dev/null 2>&1) &
		_wait_pid_with_timeout "$!" "${ROLLBACK_PHYLO_REFRESH_TIMEOUT_SEC:-20}" "rollback_phylo_refresh" || true
		# 粛清ラジオ: AI生成して deferred queue に投入（audio_worker が再生）
		if [ -f "$ROLLBACK_ANALYSIS_FILE" ]; then
			(
				OPENCODE_RUN_LOCK_STALE_SEC="${ROLLBACK_POSTMORTEM_OPENCODE_LOCK_STALE_SEC:-240}" \
					RADIO_FORCE_DEFERRED=1 start_radio_corner_rollback "$ROLLBACK_ANALYSIS_FILE" "$rollback_game_num" "$strategy_hash" "$rolled_hash" || true
			) >>"$ROLLBACK_POSTMORTEM_AI_LOG_FILE" 2>&1 &
		fi

		# 粛清区切りでまとめてコミット: 戦略本体 + 試合アーカイブ + スコア履歴 + 系統樹
		git add \
			strategy.py strategy_helpers/ \
			"$PHYROGENETIC_TREE_FILE" "$PHYROGENETIC_EVENTS_FILE" \
			game_count.txt score_history.txt eval_score_history.txt \
			best_score.txt score_dashboard.html game_state.json \
			game_history/ strategy_versions/ strategy_versions_archive/ \
			2>/dev/null || true
		local phylo_push_ok=false
		if git commit -m "eloop Auto-revert: regression detected ($result, target=${rollback_note})" 2>/dev/null; then
			if git push 2>/dev/null; then
				phylo_push_ok=true
			fi
		fi
		if [ "$phylo_push_ok" = true ]; then
			_post_phyrogenetic_tree_link_to_chat "rollback" "$strategy_hash" "$rolled_hash"
		fi
		return 0
	fi

	return 1
}

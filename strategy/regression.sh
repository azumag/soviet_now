# strategy/regression.sh - rolling scores, check_regression, rollback候補選定, postmortem生成


_write_rollback_analysis_file() {
	local current_hash="$1" rollback_hash="$2" regression_result="$3" rollback_note="$4" game_num="${5:-}"
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$current_hash" "$rollback_hash" "$regression_result" "$rollback_note" "$ROLLBACK_ANALYSIS_FILE" "score_history.txt" "$game_num" <<'PY'
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
        "hard_fail": "anchor 比で明確な悪化が出て即時停止条件に触れた。",
        "branch": "単一戦略ではなく branch 全体の失敗として判定した。",
        "anchor_direct": "branch 状態なしで anchor 比の即時悪化として判定した。",
        "anchor_promoted": "現戦略が anchor を上回ったため anchor を更新した。",
    }
    for reason in reasons:
        if reason.startswith("rank") and reason[4:].isdigit():
            lines.append(f"成熟ランキングで上位{reason[4:]}位圏外に落ちた。")
        else:
            lines.append(mapping.get(reason, f"{reason} が悪化要因だった。"))
    return lines or ["詳細理由を特定できなかった。"]

try:
    rolling = json.load(open(rolling_file))
except Exception:
    rolling = {}

current_data = rolling.get(current_hash, {})
rollback_data = rolling.get(rollback_hash, {})
current_scores = to_scores(current_data)
if os.path.exists(current_run_file):
    try:
        current_run = json.load(open(current_run_file))
    except Exception:
        current_run = {}
    if str(current_run.get("hash", "") or "") == current_hash:
        current_scores = to_scores(current_run)
rollback_scores = to_scores(rollback_data)
current_metrics = metrics(current_scores)
rollback_metrics = metrics(rollback_scores)
reg = parse_regression(regression_result)
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
lines.append(f"- trigger: {(reg.get('reasons') or 'unknown')}")
lines.append("")
lines.append("## Why Rollback Triggered")
for line in explain_reasons(reg.get("reasons", "")):
    lines.append(f"- {line}")
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
print("\n".join(summary))
PY
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
		export RUN_CMD_LOG_FILE RUN_CMD_SESSION_DIR RUN_CMD_TMP_DIR RUN_CMD_OPENCODE_PERMISSION RUN_AI_PRIMARY_RETRIES
		mkdir -p "$RUN_CMD_SESSION_DIR" "$RUN_CMD_TMP_DIR" 2>/dev/null || true

		run_ai "ROLLBACK-POSTMORTEM" "$MODEL_PRIMARY" "$MODEL_FALLBACK" \
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

	local running_pid=""
	if [ -f "$ROLLBACK_POSTMORTEM_PID_FILE" ]; then
		running_pid=$(cat "$ROLLBACK_POSTMORTEM_PID_FILE" 2>/dev/null || echo "")
		case "$running_pid" in
		''|*[!0-9]*) running_pid="" ;;
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
	) &
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
}

_backfill_hash_archive_from_known_versions() {
	mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR"
	local f
	[ -f "$STRATEGY_FILE" ] && _archive_strategy_snapshot_by_hash "$STRATEGY_FILE"
	[ -f "tmp/revert_strategy.py" ] && _archive_strategy_snapshot_by_hash "tmp/revert_strategy.py"
	for f in "$STRATEGY_VERSIONS_DIR"/v*_strategy.py "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py; do
		[ -f "$f" ] || continue
		_archive_strategy_snapshot_by_hash "$f"
	done
}

_find_strategy_file_by_hash() {
	local target_hash="$1"
	[ -z "$target_hash" ] && return 1
	if [ -f "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py" ]; then
		echo "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py"
		return 0
	fi
	return 1
}

_refresh_best_strategy_anchor() {
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	[ -f "$ROLLING_SCORES_FILE" ] || return 0
	local current_hash="${1:-}"
	python3 - "$ROLLING_SCORES_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$current_hash" "$STRATEGY_HASH_ARCHIVE_DIR" "$REJECTED_HASHES_FILE" <<'PY'
import json
import math
import os
import sys
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

best = None
for h, data in rs.items():
    if current_hash and h == current_hash:
        continue
    if h in rejected:
        continue
    if archive_dir and not os.path.exists(os.path.join(archive_dir, f"{h}.py")):
        continue
    m = metrics(data.get("scores", []))
    if not m:
        continue
    row = (m["comp"], m["p50"], m["p25"], m["n"], h, m)
    if best is None or row > best:
        best = row

if best is None:
    raise SystemExit(0)

_, _, _, _, best_hash, best_metrics = best
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
    best_key = (best_metrics["comp"], best_metrics["p50"], best_metrics["p25"], best_metrics["n"], best_hash)
    existing_has_file = bool(existing_hash) and bool(archive_dir) and os.path.exists(os.path.join(archive_dir, f"{existing_hash}.py"))
    existing_rejected = bool(existing_hash) and existing_hash in rejected
    if current_hash and existing_hash == current_hash:
        replace = True
    elif not existing_has_file:
        replace = True
    elif existing_live is None:
        replace = True
    elif existing_rejected:
        replace = True
    elif existing_hash == best_hash:
        replace = True
    elif best_key > existing_key:
        replace = True

if not replace:
    raise SystemExit(0)

payload = {
    "hash": best_hash,
    "comp": round(best_metrics["comp"], 4),
    "p50": round(best_metrics["p50"], 4),
    "p25": round(best_metrics["p25"], 4),
    "lcb": round(best_metrics["lcb"], 4),
    "n": int(best_metrics["n"]),
    "updated_at": int(__import__("time").time()),
}
anchor_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
	python3 - "$BEST_STRATEGY_ANCHOR_FILE" "$current_hash" "$current_metrics" <<'PY' >/dev/null 2>&1
import json
import sys
import time

out_file, current_hash, metrics_line = sys.argv[1:4]
parts = (metrics_line or "").split("|")
if len(parts) < 5:
    raise SystemExit(1)
payload = {
    "hash": current_hash,
    "comp": round(float(parts[0]), 4),
    "p50": round(float(parts[1]), 4),
    "p25": round(float(parts[2]), 4),
    "lcb": round(float(parts[3]), 4),
    "n": int(float(parts[4])),
    "updated_at": int(time.time()),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
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
	prune_result=$(python3 - "$REJECTED_HASHES_FILE" "$REJECTED_HASH_META_FILE" "$REJECTED_REEVALUATE_TTL_SEC" <<'PY' 2>/dev/null
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
	recovered=$(python3 - "$REJECTED_HASH_META_FILE" "$h" "$REJECTED_REEVALUATE_TTL_SEC" <<'PY' 2>/dev/null
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

_pick_best_rollback_candidate() {
	local current_hash="$1"
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	[ -f "$ROLLING_SCORES_FILE" ] || return 1
	local current_metrics current_comp
	current_metrics=$(_get_current_strategy_run_metrics "$current_hash" 2>/dev/null || true)
	[ -z "$current_metrics" ] && current_metrics=$(_get_rolling_metrics_for_hash "$current_hash" 2>/dev/null || true)
	current_comp="${current_metrics%%|*}"

	local ranked
	ranked=$(python3 - "$ROLLING_SCORES_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" <<'PY'
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

rows = []
for h, data in rs.items():
    if h == current_hash:
        continue
    scores = [int(x) for x in data.get("scores", [])]
    if len(scores) < min_games:
        continue
    comp, p50, p25, lcb, n = metrics(scores)
    rows.append((comp, p50, p25, lcb, n, h))

rows.sort(key=lambda x: (x[0], x[1], x[2], x[4]), reverse=True)
for comp, p50, p25, lcb, n, h in rows[:keep_top]:
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
		candidate_file="$STRATEGY_HASH_ARCHIVE_DIR/${h}.py"
		[ -f "$candidate_file" ] || continue
		if [ -n "$candidate_file" ]; then
			echo "${h}|${comp}|${p50}|${p25}|${lcb}|${n}|${candidate_file}"
			return 0
		fi
	done <<EOF
$ranked
EOF
	return 1
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
	ranked_result=$(python3 - "$ROLLING_SCORES_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$current_hash" <<'PY' 2>/dev/null || true
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
			min_keep_guard=$(( (expected_keep_count + 1) / 2 ))
		else
			ratio_guard=$(( (expected_keep_count * HASH_ARCHIVE_PRUNE_SAFETY_MIN_RATIO_PCT + 99) / 100 ))
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
		if ! printf '%s\n' "$keep_hashes" | grep -qxF "$h"; then
			rm -f "$f"
			removed=$((removed + 1))
		fi
	done < <(ls -1 "$STRATEGY_HASH_ARCHIVE_DIR"/*.py 2>/dev/null || true)

	if [ "$removed" -gt 0 ]; then
		log "[HASH-ARCHIVE] pruned ${removed} file(s): keep top ${HASH_ARCHIVE_KEEP_TOP} mature (+current)"
	fi
}

update_rolling_scores() {
	local score="$1" archive_file="${2:-}"
	local strategy_source="${STRATEGY_FILE}.game_snapshot"
	[ ! -f "$strategy_source" ] && strategy_source="$STRATEGY_FILE"
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$strategy_source" 2>/dev/null || echo "unknown")
	_archive_strategy_snapshot_by_hash "$strategy_source" "$strategy_hash"
	_backfill_hash_archive_from_known_versions
	local rolling_result=""
	rolling_result=$(python3 - "$ROLLING_SCORES_FILE" "$strategy_hash" "$score" "$archive_file" <<'PY' 2>/dev/null
import json
import os
import sys

rs_file, h, score, archive_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
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

rs[h]["scores"].append(score)
rs[h]["games_total"] += 1
rs[h]["scores"] = rs[h]["scores"][-20:]
if archive_file:
    recent_archives.append(archive_file)
    recent_archives = recent_archives[-25:]
rs[h]["_recent_archives"] = recent_archives

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
	fi
	_prune_hash_archive_by_ranking
}

check_regression() {
	# top1 anchor を固定基準にして branch 単位で評価する。
	# 単世代の揺らぎでは戻さず、branch の budget が尽きても anchor から明確に劣後する場合だけ rollback。
	REGRESSION_ROLLBACK_DONE=0
	REGRESSION_ROLLBACK_HASH=""
	_prune_expired_rejected_hashes >/dev/null 2>&1 || true
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true

	local result
	result=$(python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$ACTIVE_BRANCH_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$strategy_hash" "$MIN_GAMES_BEFORE_REGRESSION" "$STRATEGY_HASH_ARCHIVE_DIR" "$REGRESSION_MIN_COMP_GAP" "$REGRESSION_MIN_P50_GAP" "$REGRESSION_MIN_P25_GAP" "$REGRESSION_MIN_BREACH_COUNT" "$BRANCH_MAX_DEPTH" "$BRANCH_MAX_GAMES" "$BRANCH_PATIENCE" "$BRANCH_HARD_COMP_GAP" "$BRANCH_HARD_P50_GAP" "$BRANCH_HARD_P25_GAP" "$BRANCH_HARD_MIN_BREACH_COUNT" <<'PY'
import json
import math
import os
import sys

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

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}

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

rolling = load_json(rs_file)
current_run = load_json(current_run_file)
current_scores = []
if str(current_run.get("hash", "") or "") == current_hash:
    for x in current_run.get("scores", []) or []:
        try:
            current_scores.append(int(x))
        except Exception:
            pass
if not current_scores:
    entry = rolling.get(current_hash, {})
    for x in entry.get("scores", []) or []:
        try:
            current_scores.append(int(x))
        except Exception:
            pass
current = metrics(current_scores)
if not current:
    print("OK")
    raise SystemExit

anchor_payload = load_json(anchor_file)
anchor_hash = str(anchor_payload.get("hash", "") or "")
if not anchor_hash:
    print("OK")
    raise SystemExit
anchor = {
    "comp": float(anchor_payload.get("comp", 0.0) or 0.0),
    "p50": float(anchor_payload.get("p50", 0.0) or 0.0),
    "p25": float(anchor_payload.get("p25", 0.0) or 0.0),
    "lcb": float(anchor_payload.get("lcb", 0.0) or 0.0),
    "n": int(anchor_payload.get("n", 0) or 0),
}

active = load_json(active_branch_file)
branch_active = str(active.get("head_hash", "") or "") == current_hash and str(active.get("anchor_hash", "") or "")
if branch_active:
    anchor_hash = str(active.get("anchor_hash", "") or anchor_hash)
    anchor_blob = active.get("anchor", {}) if isinstance(active.get("anchor"), dict) else {}
    anchor = {
        "comp": float(anchor_blob.get("comp", anchor.get("comp", 0.0)) or 0.0),
        "p50": float(anchor_blob.get("p50", anchor.get("p50", 0.0)) or 0.0),
        "p25": float(anchor_blob.get("p25", anchor.get("p25", 0.0)) or 0.0),
        "lcb": float(anchor_blob.get("lcb", anchor.get("lcb", 0.0)) or 0.0),
        "n": int(anchor_blob.get("n", anchor.get("n", 0)) or 0),
    }

if current_hash == anchor_hash and not branch_active:
    print("OK")
    raise SystemExit

curr_comp_gap, curr_p50_gap, curr_p25_gap = gap(anchor, current)
curr_breach = breach_count(curr_comp_gap, curr_p50_gap, curr_p25_gap, min_comp_gap, min_p50_gap, min_p25_gap)
hard_breach = breach_count(curr_comp_gap, curr_p50_gap, curr_p25_gap, hard_comp_gap, hard_p50_gap, hard_p25_gap)

if current["n"] >= min_games_current and current_hash != anchor_hash and key(current) > key(anchor):
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

if current["n"] < min_games_current:
    print("OK")
    raise SystemExit

if not branch_active:
    if hard_breach >= hard_min_breach_count and current_hash != anchor_hash:
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
            "reasons=hard_fail+anchor_direct"
        )
        raise SystemExit
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
    raise SystemExit

if budget_reasons:
    if best_breach >= min_breach_count:
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
        raise SystemExit
    print(
        "RESET:"
        f"anchor_hash={anchor_hash},current_hash={current_hash},"
        f"best_hash={best_hash},best_comp={best_metrics.get('comp', 0.0):.1f},best_p50={best_metrics.get('p50', 0.0):.1f},best_p25={best_metrics.get('p25', 0.0):.1f},best_n={best_metrics.get('n', 0)},"
        f"best_comp_gap={best_comp_gap:.1f},best_p50_gap={best_p50_gap:.1f},best_p25_gap={best_p25_gap:.1f},best_breach_count={best_breach},"
        f"branch_depth={depth},branch_games={branch_games},branch_patience={patience},"
        f"reasons=budget_reset+{'+'.join(budget_reasons)}"
    )
    raise SystemExit

print("OK")
PY
	2>/dev/null)

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
		if [ -f "$IMPROVE_STATE_FILE" ]; then
			running_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		fi
		if [ "${running_pid:-0}" -eq 0 ] && [ "${IMPROVE_PID:-0}" -ne 0 ]; then
			running_pid="$IMPROVE_PID"
		fi
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				log "[REGRESSION] 改善プロセス停止 (PID=$running_pid)"
				kill "$running_pid" 2>/dev/null || true
				wait "$running_pid" 2>/dev/null || true
			else
				log "[REGRESSION] PID=$running_pid は改善プロセスではないため停止スキップ: $pid_cmd"
			fi
		fi
		IMPROVE_PID=0
		_write_improve_state "idle" "0" ""
		log "[REGRESSION] 自動ロールバック開始"

		echo "$strategy_hash" >> "$REJECTED_HASHES_FILE"
		if [ -f "$REJECTED_HASHES_FILE" ]; then
			tail -20 "$REJECTED_HASHES_FILE" > "$REJECTED_HASHES_FILE.tmp"
			mv "$REJECTED_HASHES_FILE.tmp" "$REJECTED_HASHES_FILE"
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
		rollback_hash=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*rollback_hash=([^,]+).*/\1/p')
		if [ -n "$rollback_hash" ] && [ -f "$STRATEGY_HASH_ARCHIVE_DIR/${rollback_hash}.py" ]; then
			local anchor_comp anchor_p50 anchor_p25 anchor_n
			anchor_comp=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_comp=([^,]+).*/\1/p')
			anchor_p50=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_p50=([^,]+).*/\1/p')
			anchor_p25=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_p25=([^,]+).*/\1/p')
			anchor_n=$(printf '%s' "$result" | sed -En 's/^REGRESSION:.*anchor_n=([^,]+).*/\1/p')
			rollback_file="$STRATEGY_HASH_ARCHIVE_DIR/${rollback_hash}.py"
			rollback_note="anchor_top1 hash=${rollback_hash} comp=${anchor_comp:-?} p50=${anchor_p50:-?} p25=${anchor_p25:-?} n=${anchor_n:-?}"
		fi
		if [ -z "$rollback_file" ]; then
			local best_candidate
			best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")
			if [ -n "$best_candidate" ]; then
				local best_comp best_p50 best_p25 best_lcb best_n
				IFS='|' read -r rollback_hash best_comp best_p50 best_p25 best_lcb best_n rollback_file <<<"$best_candidate"
				rollback_note="fallback_best hash=${rollback_hash} comp=${best_comp} p50=${best_p50} p25=${best_p25} lcb=${best_lcb} n=${best_n}"
			fi
		fi

		if [ -z "$rollback_file" ]; then
			log "[REGRESSION] ロールバック候補なし → 現在戦略を維持"
			return 0
		fi

		local rollback_game_num rollback_analysis_summary
		rollback_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)

		cp "$rollback_file" "$STRATEGY_FILE"
		cp "$STRATEGY_FILE" "tmp/revert_strategy.py" 2>/dev/null || true
		local rolled_hash
		rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
		_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
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
		REGRESSION_ROLLBACK_DONE=1
		REGRESSION_ROLLBACK_HASH="$rolled_hash"
		_clear_active_branch
		log "[REGRESSION] リバート完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"

		rollback_analysis_summary=$(_write_rollback_analysis_file "$strategy_hash" "$rolled_hash" "$result" "$rollback_note" "$rollback_game_num" 2>/dev/null || true)
		if [ -n "$rolled_hash" ]; then
			if _seed_current_strategy_run_from_rolling "$rolled_hash"; then
				log "[CURRENT-RUN] rollback seed from rolling: hash=${rolled_hash}"
			else
				_reset_current_strategy_run "$rolled_hash"
				log "[CURRENT-RUN] rollback seed missing -> reset: hash=${rolled_hash}"
			fi
		fi
		_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
		if [ -n "$rollback_analysis_summary" ]; then
			{
				echo "=== $(date '+%Y-%m-%d %H:%M') ROLLBACK Game#${rollback_game_num} ${strategy_hash} -> ${rolled_hash} ==="
				printf '%s\n' "$rollback_analysis_summary"
				echo ""
			} >> "tmp/change_log.txt"
			if [ -f "tmp/change_log.txt" ] && [ "$(wc -l < "tmp/change_log.txt")" -gt 200 ]; then
				tail -200 "tmp/change_log.txt" > "tmp/change_log.txt.tmp"
				mv "tmp/change_log.txt.tmp" "tmp/change_log.txt"
			fi
		fi
		start_rollback_postmortem_worker "$strategy_hash" "$rolled_hash" "$rollback_game_num" "$rollback_note"

		local rollback_event_analysis=""
		rollback_event_analysis=$(_extract_rollback_analysis_for_phylo "$ROLLBACK_ANALYSIS_FILE")
		append_phyrogenetic_event "rollback" "$strategy_hash" "$rolled_hash" "$rollback_game_num" "" \
			"$rollback_analysis_summary" "$rollback_event_analysis"
		refresh_phyrogenetic_tree --pending-edge rollback "$strategy_hash" "$rolled_hash" >/dev/null 2>&1 || true
		git add strategy.py strategy_helpers/ "$PHYROGENETIC_TREE_FILE" "$PHYROGENETIC_EVENTS_FILE" 2>/dev/null || true
		local phylo_push_ok=false
		if git commit -m "eloop Auto-revert: regression detected ($result, target=${rollback_note})" 2>/dev/null; then
			if git push 2>/dev/null; then
				phylo_push_ok=true
			fi
		fi
		if [ "$phylo_push_ok" = true ]; then
			_post_phyrogenetic_tree_link_to_chat "rollback" "$strategy_hash" "$rolled_hash"
		fi
		[ -f "$ROLLBACK_ANALYSIS_FILE" ] && _save_pending_cycle_radio_rollback "$ROLLBACK_ANALYSIS_FILE" "$rollback_game_num" "$strategy_hash" "$rolled_hash"
		return 0
	fi

	return 1
}

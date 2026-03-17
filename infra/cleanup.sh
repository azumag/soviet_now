# infra/cleanup.sh - PID停止, 子プロセス収集, cleanup_all, cleanup_tmp


#=== tmp/ クリーンアップ ===

cleanup_tmp_files() {
	local cleaned=0

	# --- マーカーファイル: 古いものを削除 ---

	# .radio_done_* : 最新200個を残して削除
	local radio_done_count
	radio_done_count=$(ls -1 $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | wc -l)
	if [ "$radio_done_count" -gt 200 ]; then
		ls -1t $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | tail -n +201 | xargs rm -f 2>/dev/null
		cleaned=$((cleaned + radio_done_count - 200))
	fi

	# .timed_corner_done_* : 7日より古いものを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.timed_corner_done_*' -mtime +7 -delete 2>/dev/null
	# .radio_inflight_* : 1時間以上古い孤児ディレクトリを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.radio_inflight_*' -type d -mmin +60 -exec rm -rf {} + 2>/dev/null
	# .twitch_clip_game_* : 7日より古いものを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.twitch_clip_game_*' -type d -mtime +7 -exec rm -rf {} + 2>/dev/null

	# --- デバッグダンプ: 1日以上古いものを削除 ---
	find "$TMP_DEBUG_DIR" -maxdepth 1 -name 'radio_short_*.txt' -mtime +1 -delete 2>/dev/null
	find "$TMP_DEBUG_DIR" -maxdepth 1 -name 'radio_factcheck_failed_*.txt' -mtime +1 -delete 2>/dev/null

	# --- サンドボックス孤児: 1時間以上古いものを削除 ---
	find tmp -maxdepth 1 -name '.sandbox_harvest_*' -type d -mmin +60 -exec rm -rf {} + 2>/dev/null

	# --- 履歴ファイル: キャップ適用 ---
	# .past_news_titles.txt / .past_news_links.txt にもキャップ適用
	local hist_file
	for hist_file in $TMP_HISTORY_DIR/.past_news_titles.txt $TMP_HISTORY_DIR/.past_news_links.txt $PAST_NEWS_URL_HASHES; do
		if [ -f "$hist_file" ]; then
			local lc
			lc=$(wc -l < "$hist_file" | tr -d ' ')
			if [ "${lc:-0}" -gt 300 ]; then
				tail -200 "$hist_file" > "${hist_file}.tmp" && mv "${hist_file}.tmp" "$hist_file"
			fi
		fi
	done

	# --- レガシー/テスト用ファイル削除 ---
	rm -f tmp/test_*.txt tmp/v158_*.txt tmp/v159_*.txt tmp/monitor_v159.sh 2>/dev/null
	rm -f tmp/batch_test.sh tmp/accumulated_games.test.json 2>/dev/null

	# --- 古い .past_soviet_themes.txt を統合済みなので削除可 ---
	# (テーマが radio_themes.txt に移動済み。ただし _pick_radio_theme の重複防止用は残す)

	if [ "$cleaned" -gt 0 ]; then
		log "[CLEANUP] tmp/ クリーンアップ完了: ${cleaned}ファイル削除"
	fi
}

#=== プロセス管理 ===

_CLEANUP_ALL_RUNNING=0

_stop_pid_with_fallback() {
	local pid="$1" label="${2:-process}"
	case "$pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	kill "$pid" 2>/dev/null || true
	local i
	for i in $(seq 1 20); do
		if ! kill -0 "$pid" 2>/dev/null; then
			return 0
		fi
		sleep 0.1
	done
	if kill -0 "$pid" 2>/dev/null; then
		log "[CLEANUP] ${label} がTERMで停止しないためKILL (PID=$pid)"
		kill -9 "$pid" 2>/dev/null || true
	fi
}

_collect_descendant_pids() {
	local root_pid="$1"
	case "$root_pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	local queue=("$root_pid")
	local seen=" ${root_pid} "
	local descendants=()
	while [ "${#queue[@]}" -gt 0 ]; do
		local parent_pid="${queue[0]}"
		queue=("${queue[@]:1}")
		local child_pid
		while read -r child_pid; do
			case "$child_pid" in
			''|*[!0-9]*) continue ;;
			esac
			if [[ "$seen" == *" ${child_pid} "* ]]; then
				continue
			fi
			seen="${seen}${child_pid} "
			descendants+=("$child_pid")
			queue+=("$child_pid")
		done < <(ps -Ao pid=,ppid= 2>/dev/null | awk -v p="$parent_pid" '$2==p {print $1}')
	done
	printf '%s\n' "${descendants[@]}"
}

_is_audio_playback_process() {
	local pid="$1"
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	# Ctrl-C停止時でも再生中読み上げは途切れさせない
	if echo "$cmd" | grep -Eq '(^|[[:space:]])say([[:space:]]|$)|say_enqueue\.sh'; then
		return 0
	fi
	return 1
}

_stop_loop_descendants() {
	local root_pid="$1"
	case "$root_pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	local descendants=()
	local pid
	while read -r pid; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		descendants+=("$pid")
	done < <(_collect_descendant_pids "$root_pid")
	if [ "${#descendants[@]}" -eq 0 ]; then
		return 0
	fi
	local idx
	for ((idx=${#descendants[@]} - 1; idx>=0; idx--)); do
		pid="${descendants[$idx]}"
		[ "$pid" = "$$" ] && continue
		if _is_audio_playback_process "$pid"; then
			log "[CLEANUP] 再生プロセスは維持 (PID=$pid)"
			continue
		fi
		_stop_pid_with_fallback "$pid" "child"
	done
}

# IMPROVE_PID はグローバル変数として soren_loop.sh で管理
cleanup_all() {
	local reason="${1:-manual}"
	if [ "${_CLEANUP_ALL_RUNNING:-0}" -eq 1 ]; then
		return 0
	fi
	_CLEANUP_ALL_RUNNING=1

	log "クリーンアップ中... (reason=${reason})"

	local loop_pid
	loop_pid=$(_my_pid)
	if [ -f "tmp/soren_loop.lock" ]; then
		local lock_pid
		local lock_cmd
		lock_pid=$(cat "tmp/soren_loop.lock" 2>/dev/null || echo "")
		case "$lock_pid" in
		''|*[!0-9]*) lock_pid="" ;;
		esac
		if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
			lock_cmd=$(ps -p "$lock_pid" -o command= 2>/dev/null || echo "")
			if echo "$lock_cmd" | grep -q "soren_loop.sh"; then
				loop_pid="$lock_pid"
			fi
		fi
	fi

	# 状態ファイルからPIDを復元 (IMPROVE_PIDが0でも状態ファイルにPIDがある場合)
	if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ -f "$IMPROVE_STATE_FILE" ]; then
		local _cleanup_pid
		_cleanup_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		case "$_cleanup_pid" in
		''|*[!0-9]*) _cleanup_pid=0 ;;
		esac
		[ "${_cleanup_pid:-0}" -ne 0 ] && IMPROVE_PID=$_cleanup_pid
	fi

	# 改善プロセス停止
	if [ "${IMPROVE_PID:-0}" -ne 0 ] && kill -0 "$IMPROVE_PID" 2>/dev/null; then
		pkill -P "$IMPROVE_PID" 2>/dev/null || true
		_stop_pid_with_fallback "$IMPROVE_PID" "improve"
		wait "$IMPROVE_PID" 2>/dev/null || true
	fi
	_write_improve_state "idle" "0" ""

	local rollback_postmortem_pid=0
	if [ -f "$ROLLBACK_POSTMORTEM_PID_FILE" ]; then
		rollback_postmortem_pid=$(cat "$ROLLBACK_POSTMORTEM_PID_FILE" 2>/dev/null || echo 0)
		case "$rollback_postmortem_pid" in
		''|*[!0-9]*) rollback_postmortem_pid=0 ;;
		esac
	fi
	if [ "${rollback_postmortem_pid:-0}" -ne 0 ] && kill -0 "$rollback_postmortem_pid" 2>/dev/null; then
		pkill -P "$rollback_postmortem_pid" 2>/dev/null || true
		_stop_pid_with_fallback "$rollback_postmortem_pid" "rollback_postmortem"
		wait "$rollback_postmortem_pid" 2>/dev/null || true
	fi
	rm -f "$ROLLBACK_POSTMORTEM_PID_FILE"

	# コメント関連停止
	stop_comment_watcher
	_kill_comment_gen
	stop_comment_player

	# Twitchチャット停止
	./twitch_chat.sh stop 2>/dev/null || true

	# 最後に子孫プロセスを強制的に掃除
	_stop_loop_descendants "$loop_pid"

	# /tmp/eloop_* 一時ファイル一括削除
	rm -f /tmp/eloop_prompt.* /tmp/eloop_runner.* /tmp/eloop_radio_* /tmp/eloop_comment_* /tmp/eloop_fix_* /tmp/eloop_celebration_* /tmp/eloop_news_*
	# ロックファイル削除
	rm -f tmp/soren_loop.lock
	log "クリーンアップ完了"
}

recover_strategy_backup() {
	if [ ! -f "$STRATEGY_FILE" ] && [ -f "${STRATEGY_FILE}.bak" ]; then
		log "[RECOVER] .bak から復元"
		cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
	fi
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

_is_recently_rejected_for_rollback() {
	local h="$1"
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

	local ranked_hashes
	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	ranked_hashes=$(python3 - "$ROLLING_SCORES_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$current_hash" <<'PY'
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
for _, _, _, _, h in rows[:keep_top]:
    print(h)
PY
)
	local keep_hashes
	keep_hashes=$(printf '%s\n%s\n' "$ranked_hashes" "$current_hash" | sed '/^$/d' | sort -u)

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
		[ -f "$ROLLBACK_ANALYSIS_FILE" ] && start_radio_corner_rollback "$ROLLBACK_ANALYSIS_FILE" "$rollback_game_num" "$strategy_hash" "$rolled_hash" &
		return 0
	fi

	return 1
}

#=== 改善ステート管理 ===

_read_improve_state() {
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		cat "$IMPROVE_STATE_FILE"
	else
		echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}'
	fi
}

_write_improve_state() {
	local status="$1" pid="$2" hash="$3"
	local phase="${4:-}" progress="${5:-0}" detail="${6:-}" started_at="${7:-0}"
	local now
	now=$(date +%s)
	python3 - "$IMPROVE_STATE_FILE" "$status" "${pid:-0}" "${hash:-}" "$phase" "$progress" "$detail" "$started_at" "$now" <<'PY'
import json
import sys

out_file, status, pid_raw, hash_before, phase, progress_raw, detail, started_raw, now_raw = sys.argv[1:10]

try:
    pid = int(pid_raw)
except Exception:
    pid = 0
try:
    progress = int(float(progress_raw))
except Exception:
    progress = 0
progress = max(0, min(100, progress))
try:
    started_at = int(started_raw)
except Exception:
    started_at = 0
try:
    now = int(now_raw)
except Exception:
    now = 0

if started_at <= 0 and status == "running":
    started_at = now

data = {
    "status": status,
    "pid": pid,
    "strategy_hash_before": hash_before,
    "phase": phase,
    "progress": progress,
    "detail": detail,
    "started_at": started_at,
    "updated_at": now,
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
PY
}

check_and_harvest_improvement() {
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		local pid
		pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)

		# IMPROVE_PID を状態ファイルから同期 (再起動時の復元)
		if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ "${pid:-0}" -ne 0 ]; then
			IMPROVE_PID=$pid
		fi

		# PID再利用チェック: eloop_improve.sh のプロセスかどうか確認
		local pid_alive=false
		if [ "${pid:-0}" -ne 0 ] && kill -0 "$pid" 2>/dev/null; then
			# プロセスが存在する場合、eloop_improve.sh のプロセスか確認
			local pid_cmd
			pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				pid_alive=true
			else
				log "[IMPROVE] PID=$pid は別プロセス ($pid_cmd) → stale状態クリア"
			fi
		fi

		local watchdog_sec="${IMPROVE_STALE_WATCHDOG_SEC:-1200}"
		case "$watchdog_sec" in
		''|*[!0-9]*) watchdog_sec=1200 ;;
		esac
		if [ "$pid_alive" = true ] && [ "${watchdog_sec:-0}" -gt 0 ]; then
			local updated_at updated_age now_epoch log_age log_mtime prev_phase prev_detail
			updated_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('updated_at',0) or 0))" 2>/dev/null || echo 0)
			prev_phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
			prev_detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
			now_epoch=$(date +%s)
			updated_age=$(( now_epoch - ${updated_at:-0} ))
			log_age=$updated_age
			if [ -f "$IMPROVE_AI_LOG_FILE" ]; then
				log_mtime=$(stat -f '%m' "$IMPROVE_AI_LOG_FILE" 2>/dev/null || echo 0)
				if [ "${log_mtime:-0}" -gt 0 ]; then
					log_age=$(( now_epoch - log_mtime ))
				fi
			fi
			if [ "$updated_age" -ge "$watchdog_sec" ] && [ "$log_age" -ge "$watchdog_sec" ]; then
				log "[IMPROVE] watchdog発火: ${updated_age}s 状態更新なし / ${log_age}s ログ更新なし → 停止 (PID=$pid, phase=${prev_phase:-?}, detail=${prev_detail:-})"
				_stop_loop_descendants "$pid"
				_stop_pid_with_fallback "$pid" "improve_watchdog"
				if kill -0 "$pid" 2>/dev/null; then
					log "[IMPROVE] watchdog停止失敗: PID=$pid がまだ生存"
				else
					pid_alive=false
				fi
			fi
		fi

		if [ "$pid_alive" = false ]; then
			# プロセス完了 or stale → harvest
			local hash_before
			hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
			local prev_phase prev_detail prev_progress
			prev_phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
			prev_detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
			prev_progress=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('progress',0) or 0))" 2>/dev/null)
			local hash_now
			hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

			if [ "$hash_before" != "$hash_now" ]; then
				log "[IMPROVE] 戦略更新検出: $hash_before -> $hash_now"

				# リバート用候補はeloop_improve.shが tmp/revert_strategy.py に保存済み
				# ローリングスコアで新戦略のprev_hashを記録
				local new_decide_hash
				local prev_decide_hash=""
				new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
				if [ -f "tmp/revert_strategy.py" ]; then
					prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
				fi
				if [ -z "$prev_decide_hash" ] && [ -f "$CURRENT_STRATEGY_RUN_FILE" ]; then
					prev_decide_hash=$(python3 -c "import json; print(json.load(open('$CURRENT_STRATEGY_RUN_FILE')).get('hash',''))" 2>/dev/null || echo "")
				fi
				if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
					python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
				fi
				if [ -n "$new_decide_hash" ]; then
					local branch_transition=""
					branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" || true)
					[ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
				fi
				if [ -n "$new_decide_hash" ]; then
					_reset_current_strategy_run "$new_decide_hash"
				fi

				# 戦略が変わった → 蓄積データは旧戦略のものなので破棄
				local acc_count_discarded=0
				if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
					acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
				fi
				_clear_accumulated_data
				if [ "${acc_count_discarded:-0}" -gt 0 ]; then
					log "[IMPROVE] 蓄積${acc_count_discarded}試合を破棄 (旧戦略のデータ)"
				fi
			else
				log "[IMPROVE] failed_no_apply: 戦略変更なし (phase=${prev_phase:-?}, progress=${prev_progress:-0}, detail=${prev_detail:-})"
				# 戦略が変わっていない → 蓄積データはそのまま有効
			fi

			if [ "$hash_before" != "$hash_now" ]; then
				_write_improve_state "idle" "0" "" "" "0" ""
			else
				_write_improve_state "idle" "0" "" "failed_no_apply" "100" "${prev_detail:-process_exited_without_apply}"
			fi
			IMPROVE_PID=0
			log "[IMPROVE] 改善完了 → idle"
			# Twitch チャットに戦略改善終了を通知
			./twitch_chat.sh send "戦略改善終了しました。中華AIはコメントに戻れます" 2>/dev/null &
		fi
	fi
}

accumulate_game_data() {
	local archive_file="$1" score="$2" soviet="$3" strategy_hash="$4"

	python3 -c "
import json, os
acc_file = '$ACCUMULATED_GAMES_FILE'
if os.path.exists(acc_file):
    with open(acc_file) as f:
        acc = json.load(f)
else:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0, 'hash': ''}

curr_hash = '$strategy_hash'
if acc.get('hash') and curr_hash and acc.get('hash') != curr_hash:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0, 'hash': curr_hash}
elif curr_hash:
    acc['hash'] = curr_hash

acc['files'].append('$archive_file')
acc['scores'] = (acc['scores'] + ' $score').strip()
if '$soviet' == 'true':
    acc['soviet'] = True
acc['count'] += 1

with open(acc_file, 'w') as f:
    json.dump(acc, f)
print(f'[ACCUMULATE] 蓄積: {acc[\"count\"]}試合')
" 2>/dev/null
}

_read_accumulated_data() {
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		cat "$ACCUMULATED_GAMES_FILE"
	else
		echo '{"files":[],"scores":"","soviet":false,"count":0,"hash":""}'
	fi
}

_clear_accumulated_data() {
	rm -f "$ACCUMULATED_GAMES_FILE"
}

_reset_current_strategy_run() {
	local strategy_hash="$1"
	python3 - "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" <<'PY' >/dev/null 2>&1
import json
import sys

out_file, strategy_hash = sys.argv[1], sys.argv[2]
payload = {
    "hash": strategy_hash,
    "scores": [],
    "games_total": 0,
    "_recent_archives": [],
}
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
}

_seed_current_strategy_run_from_rolling() {
	local strategy_hash="$1"
	[ -n "$strategy_hash" ] || return 1
	[ -f "$ROLLING_SCORES_FILE" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" <<'PY' >/dev/null 2>&1
import json
import os
import sys

rolling_file, out_file, strategy_hash = sys.argv[1], sys.argv[2], sys.argv[3]
if not os.path.exists(rolling_file):
    raise SystemExit(1)
try:
    rolling = json.load(open(rolling_file))
except Exception:
    raise SystemExit(1)
entry = rolling.get(strategy_hash)
if not isinstance(entry, dict):
    raise SystemExit(1)
scores = []
for x in entry.get("scores", []) or []:
    try:
        scores.append(int(x))
    except Exception:
        pass
recent_archives = entry.get("_recent_archives", []) or []
if not isinstance(recent_archives, list):
    recent_archives = []
payload = {
    "hash": strategy_hash,
    "scores": scores[-20:],
    "games_total": int(entry.get("games_total", len(scores)) or len(scores)),
    "_recent_archives": recent_archives[-50:],
}
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
}

_update_current_strategy_run() {
	local strategy_hash="$1" score="$2" archive_file="${3:-}"
	[ -n "$strategy_hash" ] || return 1
	local run_result=""
	run_result=$(python3 - "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" "$score" "$archive_file" <<'PY' 2>/dev/null
import json
import os
import sys

run_file, strategy_hash, score, archive_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
if os.path.exists(run_file):
    try:
        run = json.load(open(run_file))
    except Exception:
        run = {}
else:
    run = {}

if run.get("hash") != strategy_hash:
    run = {
        "hash": strategy_hash,
        "scores": [],
        "games_total": 0,
        "_recent_archives": [],
    }

recent_archives = run.get("_recent_archives", [])
if not isinstance(recent_archives, list):
    recent_archives = []

if archive_file and archive_file in recent_archives:
    print(f"{strategy_hash}|{len(run.get('scores', []))}|{int(run.get('games_total', 0) or 0)}|dedup")
    raise SystemExit

scores = [int(x) for x in run.get("scores", [])]
scores.append(score)
run["scores"] = scores[-20:]
run["games_total"] = int(run.get("games_total", 0) or 0) + 1
if archive_file:
    recent_archives.append(archive_file)
    recent_archives = recent_archives[-50:]
run["_recent_archives"] = recent_archives

with open(run_file, "w") as f:
    json.dump(run, f)

print(f"{strategy_hash}|{len(run['scores'])}|{run['games_total']}|updated")
PY
)
	if [ -n "$run_result" ]; then
		local run_n="" run_total="" run_status=""
		IFS='|' read -r strategy_hash run_n run_total run_status <<<"$run_result"
		if [ "$run_status" = "dedup" ]; then
			log "[CURRENT-RUN] duplicate skip: hash=${strategy_hash} n=${run_n} total=${run_total} score=${score} file=${archive_file}"
		else
			log "[CURRENT-RUN] updated: hash=${strategy_hash} n=${run_n} total=${run_total} score=${score} file=${archive_file}"
		fi
	else
		log "[CURRENT-RUN] update failed: hash=${strategy_hash} score=${score}"
	fi
}

record_completed_game_for_adaptive_improvement() {
	local archive_file="$1" score="$2" soviet="$3"
	local played_hash="" current_hash=""
	if [ -f "${STRATEGY_FILE}.game_snapshot" ]; then
		played_hash=$(python3 extract_decide_hash.py "${STRATEGY_FILE}.game_snapshot" 2>/dev/null || echo "")
	fi
	if [ -z "$played_hash" ] && [ -f "$STRATEGY_FILE" ]; then
		played_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	fi
	if [ -f "$STRATEGY_FILE" ]; then
		current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	fi

	update_rolling_scores "$score" "$archive_file"

	if [ -n "$played_hash" ] && [ -n "$current_hash" ] && [ "$played_hash" != "$current_hash" ]; then
		log "[IMPROVE] current戦略と異なる試合を検出: played=${played_hash:0:8} current=${current_hash:0:8} → queuedをリセットしてこの試合は蓄積しない"
		_clear_accumulated_data
		_reset_current_strategy_run "$current_hash"
	else
		if [ -n "$current_hash" ]; then
			_update_current_strategy_run "$current_hash" "$score" "$archive_file"
		fi
		accumulate_game_data "$archive_file" "$score" "$soviet" "$played_hash"
	fi

	if ! _has_active_branch; then
		_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	fi
}

_start_improvement_job() {
	local all_history_files="$1" all_scores="$2" any_soviet="$3" acc_count="$4" reason="$5"

	# 既存の eloop_improve プロセスが残っていないか確認
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve" 2>/dev/null || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | xargs kill 2>/dev/null || true
		sleep 1
	fi

	if [ "$reason" = "post_regression" ]; then
		log "[IMPROVE] 回帰ロールバック直後の即時改善を開始"
	else
		log "[IMPROVE] ${acc_count}試合分のデータで改善開始"
	fi

	# Twitchコメント処理は comment watcher 側に一本化
	log "[NEWS] ニュース取得..."
	./fetch_news.sh

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
	local improve_ai_log="$IMPROVE_AI_LOG_FILE"
	mkdir -p "$(dirname "$improve_ai_log")" 2>/dev/null || true
	: >"$improve_ai_log"
	printf '[%s] [IMPROVE] job start reason=%s game=%s scores=%s\n' \
		"$(date '+%H:%M:%S')" "$reason" "${GAME_NUM:-?}" "${all_scores:-}" >>"$improve_ai_log" 2>/dev/null || true

	# バックグラウンド改善開始
	RUN_CMD_LOG_FILE="$improve_ai_log" ./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" &
	IMPROVE_PID=$!

	# 起動成功を確認してから状態更新
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash" "boot" "1" "job_started" "$(date +%s)"
		if [ "$reason" = "post_regression" ]; then
			log "[IMPROVE] 回帰ロールバック後の改善開始 (PID=$IMPROVE_PID, base=${REGRESSION_ROLLBACK_HASH:-unknown})"
		else
			log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		fi
		# Twitch チャットに戦略改善開始を通知
		./twitch_chat.sh send "戦略改善中。中華AIが忙しくしている間、メリケンAIが同志として代わりに返答します" 2>/dev/null &
		return 0
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死)"
		IMPROVE_PID=0
		return 1
	fi
}

trigger_adaptive_improvement() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] trigger_adaptive_improvementをスキップ（建国後停止中）"
		return
	fi

	local current_hash=""
	if [ -f "$STRATEGY_FILE" ]; then
		current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	fi

	# Step 2: リグレッション検知 (成熟ランキングで上位 REGRESSION_MAX_RANK 位圏外なら自動リバート)
	if check_regression; then
		# リグレッション検知 → リバート済み、蓄積データクリア
		_clear_accumulated_data
		return
	fi

	# Step 3: 改善プロセス実行中?
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		# PIDが本当に生きているか確認 (stale検出)
		local running_pid
		running_pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)
		local still_alive=false
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				still_alive=true
			fi
		fi
		if [ "$still_alive" = true ]; then
			log "[IMPROVE] 改善中 (PID=$running_pid), データ蓄積済み"
			return
		else
			log "[IMPROVE] stale検出: PID=$running_pid は既に終了 → harvest & 続行"
			check_and_harvest_improvement
		fi
	fi

	# Step 4: 最低10試合ゲート
	local acc_data
	acc_data=$(_read_accumulated_data)
	local acc_hash
	acc_hash=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('hash',''))" 2>/dev/null)
	local acc_count
	acc_count=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)
	if [ "${acc_count:-0}" -gt 0 ] && [ -n "$current_hash" ] && [ -z "$acc_hash" ]; then
		log "[IMPROVE] 旧形式queuedデータを検出（hashなし）→ 破棄"
		_clear_accumulated_data
		acc_data=$(_read_accumulated_data)
		acc_hash=""
		acc_count=0
	fi
	if [ -n "$acc_hash" ] && [ -n "$current_hash" ] && [ "$acc_hash" != "$current_hash" ]; then
		log "[IMPROVE] queuedデータの戦略が現行と不一致: queued=${acc_hash:0:8} current=${current_hash:0:8} → 破棄"
		_clear_accumulated_data
		acc_data=$(_read_accumulated_data)
		acc_hash=""
		acc_count=0
	fi
	acc_count=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

	if [ "${acc_count:-0}" -lt "$MIN_GAMES_BEFORE_IMPROVE" ]; then
		log "[IMPROVE] 蓄積 ${acc_count:-0}/${MIN_GAMES_BEFORE_IMPROVE} 試合 → 待機"
		return
	fi

	# Step 5: idle → 改善開始
	# 蓄積データから履歴ファイル・スコアを統合
	local all_history_files all_scores any_soviet
	all_history_files=$(echo "$acc_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
	all_scores=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
	any_soviet=$(echo "$acc_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)
	if _start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "normal"; then
		# 通常改善のみ、起動成功後に蓄積をクリア (即死時は保持)
		_clear_accumulated_data
	fi
}
cleanup_all() {
	local reason="${1:-manual}"
	if [ "${_CLEANUP_ALL_RUNNING:-0}" -eq 1 ]; then
		return 0
	fi
	_CLEANUP_ALL_RUNNING=1

	log "クリーンアップ中... (reason=${reason})"

	local loop_pid
	loop_pid=$(_my_pid)
	if [ -f "tmp/soren_loop.lock" ]; then
		local lock_pid
		local lock_cmd
		lock_pid=$(cat "tmp/soren_loop.lock" 2>/dev/null || echo "")
		case "$lock_pid" in
		''|*[!0-9]*) lock_pid="" ;;
		esac
		if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
			lock_cmd=$(ps -p "$lock_pid" -o command= 2>/dev/null || echo "")
			if echo "$lock_cmd" | grep -q "soren_loop.sh"; then
				loop_pid="$lock_pid"
			fi
		fi
	fi

	# 状態ファイルからPIDを復元 (IMPROVE_PIDが0でも状態ファイルにPIDがある場合)
	if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ -f "$IMPROVE_STATE_FILE" ]; then
		local _cleanup_pid
		_cleanup_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		case "$_cleanup_pid" in
		''|*[!0-9]*) _cleanup_pid=0 ;;
		esac
		[ "${_cleanup_pid:-0}" -ne 0 ] && IMPROVE_PID=$_cleanup_pid
	fi

	# 改善プロセス停止
	if [ "${IMPROVE_PID:-0}" -ne 0 ] && kill -0 "$IMPROVE_PID" 2>/dev/null; then
		pkill -P "$IMPROVE_PID" 2>/dev/null || true
		_stop_pid_with_fallback "$IMPROVE_PID" "improve"
		wait "$IMPROVE_PID" 2>/dev/null || true
	fi
	_write_improve_state "idle" "0" ""

	local rollback_postmortem_pid=0
	if [ -f "$ROLLBACK_POSTMORTEM_PID_FILE" ]; then
		rollback_postmortem_pid=$(cat "$ROLLBACK_POSTMORTEM_PID_FILE" 2>/dev/null || echo 0)
		case "$rollback_postmortem_pid" in
		''|*[!0-9]*) rollback_postmortem_pid=0 ;;
		esac
	fi
	if [ "${rollback_postmortem_pid:-0}" -ne 0 ] && kill -0 "$rollback_postmortem_pid" 2>/dev/null; then
		pkill -P "$rollback_postmortem_pid" 2>/dev/null || true
		_stop_pid_with_fallback "$rollback_postmortem_pid" "rollback_postmortem"
		wait "$rollback_postmortem_pid" 2>/dev/null || true
	fi
	rm -f "$ROLLBACK_POSTMORTEM_PID_FILE"

	# コメント関連停止
	stop_comment_watcher
	_kill_comment_gen
	stop_comment_player

	# Twitchチャット停止
	./twitch_chat.sh stop 2>/dev/null || true

	# 最後に子孫プロセスを強制的に掃除
	_stop_loop_descendants "$loop_pid"

	# /tmp/eloop_* 一時ファイル一括削除
	rm -f /tmp/eloop_prompt.* /tmp/eloop_runner.* /tmp/eloop_radio_* /tmp/eloop_comment_* /tmp/eloop_fix_* /tmp/eloop_celebration_* /tmp/eloop_news_*
	# ロックファイル削除
	rm -f tmp/soren_loop.lock
	log "クリーンアップ完了"
}

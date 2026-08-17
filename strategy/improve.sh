# strategy/improve.sh - improve_state管理, accumulate, trigger_adaptive_improvement


#=== spawn 排他 mutex (dual-spawner 二重起動レース防止) ===
# mkdir は POSIX atomic。owner ファイルに PID を記録し、解放/stale 回収は
# 所有者一致時のみ実行 (他 spawner の新規 lock を消さない)。

# 取得試行。成功で 0、別 spawner が保持中で取得不可なら 1。
_acquire_spawn_lock() {
	local d="$IMPROVE_SPAWN_LOCK_DIR"
	if mkdir "$d" 2>/dev/null; then
		echo "$$" >"$d/owner" 2>/dev/null || true
		return 0
	fi
	# 取得失敗 → stale 判定 (owner 死亡 or TTL 超過時のみ steal)
	local owner_pid lk_m now age
	owner_pid=$(cat "$d/owner" 2>/dev/null || echo "")
	lk_m=$(stat -f %m "$d" 2>/dev/null) \
		|| lk_m=$(stat -c %Y "$d" 2>/dev/null) \
		|| lk_m=0
	now=$(date +%s)
	age=$((now - lk_m))
	local stale=0
	if [ -n "$owner_pid" ] && ! kill -0 "$owner_pid" 2>/dev/null; then
		stale=1
	elif [ "$lk_m" -gt 0 ] && [ "$age" -ge "${IMPROVE_SPAWN_LOCK_TTL:-90}" ]; then
		stale=1
	fi
	if [ "$stale" -eq 1 ]; then
		# steal 前に owner を再確認 (別 contender が既に再取得していたら触らない)
		local owner_recheck
		owner_recheck=$(cat "$d/owner" 2>/dev/null || echo "")
		if [ "$owner_recheck" = "$owner_pid" ]; then
			rm -rf "$d" 2>/dev/null || true
			if mkdir "$d" 2>/dev/null; then
				echo "$$" >"$d/owner" 2>/dev/null || true
				log "[IMPROVE] stale spawn lock 回収し再取得 (旧owner=${owner_pid:-?})"
				return 0
			fi
		fi
	fi
	return 1
}

# 解放: owner が自 PID のときのみ削除 (他 spawner の lock を消さない)
_release_spawn_lock() {
	local d="$IMPROVE_SPAWN_LOCK_DIR"
	[ -d "$d" ] || return 0
	local owner_pid
	owner_pid=$(cat "$d/owner" 2>/dev/null || echo "")
	if [ "$owner_pid" = "$$" ]; then
		rm -rf "$d" 2>/dev/null || true
	fi
}

#=== ピーク時間帯回避ゲート (deepseek-v4-flash 2倍課金帯の改善ロック消費を遅延) ===

# "HH:MM" or "HHMM" → 0時からの分。不正なら -1 を出力し rc=1
_improve_hhmm_to_min() {
	local hhmm="${1//:/}"
	case "$hhmm" in
	[0-9][0-9][0-9][0-9]) ;;
	*) printf '%s\n' -1; return 1 ;;
	esac
	printf '%s\n' "$(( 10#${hhmm:0:2} * 60 + 10#${hhmm:2:2} ))"   # 10# で 08/09 の8進解釈を防ぐ
}

# ピーク帯判定 (純関数)。$1=HHMM (省略時 date -u +%H%M)、$2=ranges (省略時 env)
# 各レンジは半開区間 [start, end)。start>end は日跨ぎ。不正エントリはスキップ。
# 仕様: "01:00-04:00" は 01:00:00 <= t < 04:00:00。00:59は非ピーク、01:00はピーク、
# 03:59はピーク、04:00は非ピーク。
_is_peak_hour_utc() {
	local now_hhmm="${1:-$(date -u +%H%M)}"
	local ranges="${2:-${IMPROVE_PEAK_HOUR_UTC_RANGES:-01:00-04:00,06:00-10:00}}"
	local now_min start_min end_min range
	now_min=$(_improve_hhmm_to_min "$now_hhmm") || return 1
	ranges="${ranges// /}"          # 空白混入を許容
	local IFS=','
	for range in $ranges; do
		start_min=$(_improve_hhmm_to_min "${range%-*}") || continue
		end_min=$(_improve_hhmm_to_min "${range#*-}") || continue
		if [ "$start_min" -le "$end_min" ]; then
			[ "$now_min" -ge "$start_min" ] && [ "$now_min" -lt "$end_min" ] && return 0
		else
			{ [ "$now_min" -ge "$start_min" ] || [ "$now_min" -lt "$end_min" ]; } && return 0
		fi
	done
	return 1
}

# ログ用: ピーク中なら現レンジ終了までの残分を出力 (非ピークなら空文字)
_peak_remaining_min() {
	local now_hhmm="${1:-$(date -u +%H%M)}"
	local ranges="${2:-${IMPROVE_PEAK_HOUR_UTC_RANGES:-01:00-04:00,06:00-10:00}}"
	local now_min start_min end_min range
	now_min=$(_improve_hhmm_to_min "$now_hhmm") || { printf ''; return; }
	ranges="${ranges// /}"
	local IFS=','
	for range in $ranges; do
		start_min=$(_improve_hhmm_to_min "${range%-*}") || continue
		end_min=$(_improve_hhmm_to_min "${range#*-}") || continue
		if [ "$start_min" -le "$end_min" ]; then
			if [ "$now_min" -ge "$start_min" ] && [ "$now_min" -lt "$end_min" ]; then
				printf '%s' "$(( end_min - now_min ))"
				return
			fi
		else
			if [ "$now_min" -ge "$start_min" ]; then
				printf '%s' "$(( (1440 - now_min) + end_min ))"
				return
			elif [ "$now_min" -lt "$end_min" ]; then
				printf '%s' "$(( end_min - now_min ))"
				return
			fi
		fi
	done
	printf ''
}

_peak_defer_clear() {
	local reason="$1"
	local marker="$TMP_STATE_DIR/peak_hour_defer"
	[ -f "$marker" ] || return 0
	local started waited
	started=$(sed -n '1p' "$marker" 2>/dev/null || echo 0)
	case "$started" in ''|*[!0-9]*) started=0 ;; esac
	waited=$(( $(date +%s) - started ))
	log "[IMPROVE] peak-hour defer解除 (待機${waited}s, reason=${reason})"
	rm -f "$marker" "$TMP_STATE_DIR/peak_hour_defer_last_log" 2>/dev/null || true
	# 解除直後に check_and_harvest_improvement の孤立ロックGCが走ると、defer中
	# ずっと更新されなかった古いmtimeのせいで即座に削除されうる (improve.sh の
	# failed_no_apply retry保持と同じ precedent: touch でmtimeを更新し安全に猶予)。
	[ -f "$IMPROVE_LOCK_FILE" ] && touch "$IMPROVE_LOCK_FILE" 2>/dev/null || true
}

_peak_defer_note_waiting() {
	local marker="$TMP_STATE_DIR/peak_hour_defer"
	local last_log_file="$TMP_STATE_DIR/peak_hour_defer_last_log"
	local now started
	now=$(date +%s)
	if [ ! -f "$marker" ]; then
		printf '%s\n' "$now" >"$marker" 2>/dev/null || true
		local _rem
		_rem=$(_peak_remaining_min)
		log "[IMPROVE] peak-hour defer開始 (UTC $(date -u +%H:%M), ranges=${IMPROVE_PEAK_HOUR_UTC_RANGES:-01:00-04:00,06:00-10:00}, 現ウィンドウ残り約${_rem}分)"
		printf '%s\n' "$now" >"$last_log_file" 2>/dev/null || true
		return
	fi
	started=$(sed -n '1p' "$marker" 2>/dev/null || echo "$now")
	case "$started" in ''|*[!0-9]*) started="$now" ;; esac
	local last_log interval
	last_log=$(cat "$last_log_file" 2>/dev/null || echo 0)
	case "$last_log" in ''|*[!0-9]*) last_log=0 ;; esac
	interval="${IMPROVE_PEAK_DEFER_LOG_INTERVAL_SEC:-${IMPROVE_BACKOFF_LOG_INTERVAL_SEC:-900}}"
	case "$interval" in ''|*[!0-9]*) interval=900 ;; esac
	if [ "$last_log" -le 0 ] || [ $(( now - last_log )) -ge "$interval" ]; then
		local _rem
		_rem=$(_peak_remaining_min)
		log "[IMPROVE] peak-hour defer中 (経過$(( now - started ))s, 現ウィンドウ残り約${_rem}分)"
		printf '%s\n' "$now" >"$last_log_file" 2>/dev/null || true
	fi
}

# 安全弁: バイパスすべき理由があれば非空文字列を返す (stdout)。無ければ空文字。
_peak_defer_bypass_reason() {
	# (1) 緊急ロックはピーク回避の対象外
	if [ -f "$IMPROVE_LOCK_FILE" ]; then
		local _urgent
		_urgent=$(python3 -c "
import json, sys
try:
    with open('$IMPROVE_LOCK_FILE') as f:
        d = json.load(f)
except Exception:
    sys.exit(0)
if d.get('early_escape_lock') is True:
    print('early_escape_lock')
    sys.exit(0)
reason = d.get('improve_reason', '')
if reason and reason not in ('normal', ''):
    print(f'improve_reason:{reason}')
" 2>/dev/null)
		if [ -n "$_urgent" ]; then
			printf 'urgent_lock:%s' "$_urgent"
			return
		fi
	fi

	# (2) 最大待機時間
	local marker="$TMP_STATE_DIR/peak_hour_defer"
	if [ -f "$marker" ]; then
		local started waited max_wait
		started=$(sed -n '1p' "$marker" 2>/dev/null || echo 0)
		case "$started" in ''|*[!0-9]*) started=0 ;; esac
		waited=$(( $(date +%s) - started ))
		max_wait="${IMPROVE_PEAK_DEFER_MAX_WAIT_SEC:-16200}"
		case "$max_wait" in ''|*[!0-9]*) max_wait=16200 ;; esac
		if [ "$waited" -ge "$max_wait" ]; then
			printf 'max_wait:%ss' "$waited"
			return
		fi
	fi

	# (3) 蓄積強制実行: ロック内count(固定) + 現在のACCUMULATED_GAMES_FILEのcount
	if [ -f "$IMPROVE_LOCK_FILE" ]; then
		local _lock_count _acc_count _combined _threshold _pct
		_lock_count=$(python3 -c "
import json
try:
    with open('$IMPROVE_LOCK_FILE') as f:
        d = json.load(f)
    print(int(d.get('count', 0)))
except Exception:
    print(0)
" 2>/dev/null)
		case "$_lock_count" in ''|*[!0-9]*) _lock_count=0 ;; esac
		_acc_count=0
		if [ -f "${ACCUMULATED_GAMES_FILE:-}" ]; then
			_acc_count=$(python3 -c "
import json
try:
    with open('${ACCUMULATED_GAMES_FILE}') as f:
        d = json.load(f)
    print(len(d) if isinstance(d, list) else int(d.get('count', 0)))
except Exception:
    print(0)
" 2>/dev/null)
			case "$_acc_count" in ''|*[!0-9]*) _acc_count=0 ;; esac
		fi
		_combined=$(( _lock_count + _acc_count ))
		_pct="${IMPROVE_PEAK_DEFER_FORCE_ACC_PCT:-200}"
		case "$_pct" in ''|*[!0-9]*) _pct=200 ;; esac
		_threshold=$(( (${MIN_GAMES_BEFORE_IMPROVE:-12} * _pct) / 100 ))
		if [ "$_combined" -ge "$_threshold" ]; then
			printf 'acc_pct:%s/%s' "$_combined" "$_threshold"
			return
		fi
	fi

	printf ''
}

# rc=0: 今サイクルは改善を見送る (defer) / rc=1: 進行してよい
_improve_peak_gate_should_defer() {
	if [ "${IMPROVE_PEAK_HOUR_DEFER_ENABLED:-1}" != "1" ]; then
		_peak_defer_clear "disabled"
		return 1
	fi
	if ! _is_peak_hour_utc; then
		_peak_defer_clear "peak_window_ended"
		return 1
	fi
	local bypass
	bypass=$(_peak_defer_bypass_reason)
	if [ -n "$bypass" ]; then
		_peak_defer_clear "force:${bypass}"
		return 1
	fi
	_peak_defer_note_waiting
	return 0
}

# ループ側の独立期限切れ判定 (rate_limit_backoff の _expire_rate_limit_backoff_if_elapsed
# と同パターン)。soren_loop.sh の他のガード付き呼び出し箇所が peak_hour_defer 中に
# trigger_adaptive_improvement を呼ばなくなっても、ここを毎ループ無条件で呼べば
# デーモン不在時でも改善ロックが孤立しない。
#
# 解除判定 (disabled/peak_window_ended/max_wait/urgent_lock/acc_pct) はここで
# 個別に再実装せず trigger_adaptive_improvement 内のゲートに一元化する。
# ここで先にマーカだけ消してから改めて trigger を呼ぶと、次のゲート再評価時に
# マーカ不在で最大待機の経過時間を見失い待機クロックが再スタートしてしまい、
# バイパスしてもロックが実際に消費される保証がなくなる。マーカが在るまま
# trigger_adaptive_improvement に判定・消費まで一括して委ねることで防ぐ。
_expire_peak_hour_defer_if_stale() {
	local marker="$TMP_STATE_DIR/peak_hour_defer"
	[ -f "$marker" ] || return 0
	if [ ! -f "$IMPROVE_LOCK_FILE" ]; then
		# ロック不在でマーカだけ残るのは異常系 (孤立マーカ)。個別に掃除する。
		_peak_defer_clear "lock_missing"
		return 0
	fi
	IMPROVE_DAEMON_MODE=0 trigger_adaptive_improvement || true
}

# trigger_adaptive_improvement() の入口で idle を確認していても、別の
# spawner が metadata 準備中に先行して job を起動できる。spawn mutex を
# 取得した後に authoritative state をもう一度確認し、先行 job が live なら
# mutex を引き継いだ後発側を必ず止める。
_improve_spawn_state_blocks_start() {
	local state status pid
	state=$(_read_improve_state)
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null || echo idle)
	if [ "$status" = "manual" ]; then
		return 0
	fi
	[ "$status" = "running" ] || return 1
	pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null || echo 0)
	_is_live_improve_pid "$pid"
}

_improve_flow_notify() {
	local step="${1:-flow}" title="${2:-改善フロー}" body="${3:-}" chat="${4:-}" level="${5:-info}"
	local full_title="改善フロー: ${title}"
	[ -n "$body" ] || body="$step"
	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh worker "$full_title" "$body" "$level" >/dev/null 2>&1 || true
	fi
	if [ -n "$chat" ]; then
		enqueue_chat_message "$chat" "improve_flow" 4 || true
	fi
}

_archive_restart_should_run() {
	local wildcard_escape_streak="${1:-0}"
	[ "${ARCHIVE_RESTART_ENABLED:-1}" = "1" ] || return 1
	[ "$wildcard_escape_streak" -ge "${ARCHIVE_RESTART_STREAK:-3}" ] || return 1
	local marker="${ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_FILE:-tmp/state/.archive_restart_no_candidate}"
	local cooldown="${ARCHIVE_RESTART_NO_CANDIDATE_COOLDOWN_SEC:-900}"
	local marker_candidate_override=0
	if [ -f "$marker" ]; then
		local marker_m now age
		marker_m=$(stat -f %m "$marker" 2>/dev/null) \
			|| marker_m=$(stat -c %Y "$marker" 2>/dev/null) \
			|| marker_m=0
		now=$(date +%s)
		age=$((now - marker_m))
		if [ "$marker_m" -gt 0 ] && [ "$age" -lt "$cooldown" ]; then
			if _archive_restart_has_candidate; then
				marker_candidate_override=1
				log "[ARCHIVE-RESTART] no_candidate cooldown stale age=${age}s/${cooldown}s but candidate now exists → archive_restart を許可"
			else
				log "[ARCHIVE-RESTART] no_candidate cooldown active age=${age}s/${cooldown}s → archive_restart を飛ばして次の脱出手段へ"
				return 1
			fi
		fi
	fi
	if [ "$marker_candidate_override" != "1" ] && ! _archive_restart_has_candidate; then
		mkdir -p "$(dirname "$marker")" 2>/dev/null || true
		printf '%s\n' "preflight_no_candidate $(date +%s)" >"$marker" 2>/dev/null || true
		log "[ARCHIVE-RESTART] preflight no candidate → archive_restart を飛ばして次の脱出手段へ"
		return 1
	fi
	return 0
}

_archive_restart_has_candidate() {
	python3 - \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" \
		"${REJECTED_HASH_META_FILE:-tmp/state/rejected_hash_metrics.json}" \
		"${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" \
		"${ARCHIVE_RESTART_COOLDOWN_FILE:-tmp/state/archive_restart_cooldown.json}" \
		"${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}" \
		"${ARCHIVE_RESTART_MIN_COMP_RATIO:-0.92}" \
		"${MIN_GAMES_FOR_BEST_ROLLBACK:-12}" \
		"${ARCHIVE_RESTART_MIN_BEST_TYPE:-14}" \
		"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}" \
		"${ARCHIVE_RESTART_INCLUDE_PERMANENT:-1}" \
		"${ARCHIVE_RESTART_ALLOW_ORIGIN_RETRY:-1}" \
		"${ARCHIVE_RESTART_COOLDOWN_SEC:-21600}" \
		"${ARCHIVE_RESTART_MIN_RUSSIA_COUNT:-2}" \
		"${ARCHIVE_RESTART_MIN_RUSSIA_RATE:-0.15}" \
		"${ARCHIVE_RESTART_FRONTIER_MIN_BEST_TYPE:-15}" \
		"${ARCHIVE_RESTART_OBJECTIVE_FAIL_PERMANENT:-1}" <<'PY' >/dev/null 2>&1
import json
import math
import os
import sys
import time

rolling_file, anchor_file, rejected_file, origin_file, cooldown_file, archive_dir, min_ratio_raw, min_games_raw, min_best_type_raw, permanent_archive_dir, include_permanent_raw, allow_origin_retry_raw, cooldown_ttl_raw, min_russia_count_raw, min_russia_rate_raw, frontier_min_best_type_raw, objective_fail_permanent_raw = sys.argv[1:18]

def load(path, default):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if data is not None else default
    except Exception:
        pass
    return default

def as_int(value, default):
    try:
        return int(value)
    except Exception:
        return default

def as_float(value, default):
    try:
        return float(value)
    except Exception:
        return default

def quantile(vals, p):
    xs = sorted(float(v) for v in vals)
    if not xs:
        return 0.0
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac

def metrics(scores):
    vals = []
    for raw in scores or []:
        try:
            vals.append(float(raw))
        except Exception:
            pass
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    if len(vals) > 1:
        var = sum((x - mean) ** 2 for x in vals) / len(vals)
        lcb = mean - 1.28 * (math.sqrt(var) / math.sqrt(len(vals)))
    else:
        lcb = mean
    return {
        "n": len(vals),
        "comp": 0.55 * quantile(vals, 0.50) + 0.30 * quantile(vals, 0.25) + 0.15 * lcb,
    }

def archive_is_runtime_stable(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "BEGIN DEADLINE GUARD" in f.read(200000)
    except Exception:
        return False

def boolish(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() not in {"0", "false", "no", "off"}

include_permanent = boolish(include_permanent_raw, True)
allow_origin_retry = boolish(allow_origin_retry_raw, True)

def find_archive_path(h):
    paths = [os.path.join(archive_dir, f"{h}.py")]
    if include_permanent and permanent_archive_dir:
        paths.append(os.path.join(permanent_archive_dir, f"{h}.py"))
    for path in paths:
        if os.path.exists(path) and archive_is_runtime_stable(path):
            return path
    return ""

def is_cooled_down(h):
    if h not in cooldown:
        return False
    meta = cooldown.get(h) if isinstance(cooldown.get(h), dict) else {}
    if boolish(objective_fail_permanent_raw, True) and str(meta.get("reason") or "").startswith("archive_restart_russia_not_reproduced"):
        return True
    ttl = as_int(cooldown_ttl_raw, 21600)
    if ttl <= 0:
        return True
    epoch = as_int(meta.get("epoch", 0), 0)
    return epoch <= 0 or (int(time.time()) - epoch) < ttl

rolling = load(rolling_file, {})
anchor = load(anchor_file, {})
rejected = set(load(rejected_file, {}).keys())
origin_map = load(origin_file, {})
origin = set(origin_map.keys())
cooldown = load(cooldown_file, {})
anchor_hash = str(anchor.get("hash", "") or "")
anchor_comp = as_float(anchor.get("comp", 0.0), 0.0)
anchor_russia = as_int(anchor.get("russia_count", 0), 0)
anchor_soviet = as_int(anchor.get("soviet_count", 0), 0)
min_ratio = max(0.0, min(1.0, as_float(min_ratio_raw, 0.92)))
min_games = max(1, as_int(min_games_raw, 12))
min_best_type = max(0, as_int(min_best_type_raw, 14))
min_russia_count = max(1, as_int(min_russia_count_raw, 2))
min_russia_rate = max(0.0, as_float(min_russia_rate_raw, 0.15))
frontier_min_best_type = max(min_best_type, as_int(frontier_min_best_type_raw, 15))
threshold = anchor_comp * min_ratio if anchor_comp > 0 else 0.0

for h, entry in (rolling or {}).items():
    h = str(h)
    if not h or h == anchor_hash or h in rejected or is_cooled_down(h):
        continue
    path = find_archive_path(h)
    if not path:
        continue
    m = metrics((entry or {}).get("scores", []) or [])
    if not m or m["n"] < min_games or m["comp"] < threshold:
        continue
    russia = as_int((entry or {}).get("russia_count", 0), 0)
    soviet = as_int((entry or {}).get("soviet_count", 0), 0)
    best_type = as_int((entry or {}).get("best_max_type", 0), 0)
    russia_rate = (float(russia) / float(m["n"])) if m["n"] > 0 else 0.0
    reliable_russia = russia >= min_russia_count or russia_rate >= min_russia_rate
    frontier_candidate = best_type >= frontier_min_best_type
    if best_type >= 16 and soviet <= 0:
        soviet = 1
    if anchor_soviet > 0 and soviet <= 0:
        continue
    if anchor_russia > 0 and not (reliable_russia or frontier_candidate or russia > 0):
        continue
    if min_best_type > 0 and not reliable_russia and soviet <= 0 and not frontier_candidate and best_type < min_best_type:
        continue
    origin_type = str((origin_map.get(h) or {}).get("origin_type") or "") if isinstance(origin_map.get(h), dict) else ("legacy_origin" if h in origin else "")
    if origin_type and not (allow_origin_retry and (reliable_russia or soviet > 0 or frontier_candidate or best_type >= min_best_type)):
        continue
    raise SystemExit(0)

raise SystemExit(1)
PY
}

_escape_ai_seed_available() {
	[ "${WILDCARD_ESCAPE_AI_SEED_ENABLED:-1}" = "1" ] || return 1
	python3 - \
		"${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${REJECTED_HASH_META_FILE:-tmp/state/rejected_hash_metrics.json}" \
		"${STRATEGY_HASH_ARCHIVE_DIR:-strategy_versions/by_hash}" \
		"${WILDCARD_ESCAPE_AI_SEED_MIN_GAMES:-4}" \
		"${WILDCARD_ESCAPE_AI_SEED_MIN_BEST_TYPE:-14}" \
		"${STRATEGY_HASH_PERMANENT_ARCHIVE_DIR:-strategy_versions_archive/by_hash}" \
		"${ARCHIVE_RESTART_INCLUDE_PERMANENT:-1}" <<'PY' >/dev/null 2>&1
import json
import os
import sys

origin_file, rolling_file, rejected_file, archive_dir, min_games_raw, min_best_type_raw, permanent_archive_dir, include_permanent_raw = sys.argv[1:9]
include_permanent = str(include_permanent_raw).strip().lower() not in {"0", "false", "no", "off", ""}

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def archive_is_runtime_stable(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return "BEGIN DEADLINE GUARD" in f.read(200000)
    except Exception:
        return False

origin = load(origin_file)
rolling = load(rolling_file)
rejected = load(rejected_file)
min_games = max(1, as_int(min_games_raw, 4))
min_best_type = max(0, as_int(min_best_type_raw, 14))
for h, meta in (origin or {}).items():
    h = str(h)
    if not h:
        continue
    if str((meta or {}).get("origin_type") or "wildcard") != "wildcard":
        continue
    # Mirror _archive_restart_has_candidate/find_archive_path: wildcard-origin
    # seeds live almost entirely in the PERMANENT archive (by_hash is pruned to
    # ~16 entries), so a by_hash-only search makes escape_ai seed-starved and the
    # AI escape can never fire. Search both, gated by the same env archive_restart
    # already trusts.
    paths = [os.path.join(archive_dir, f"{h}.py")]
    if include_permanent and permanent_archive_dir:
        paths.append(os.path.join(permanent_archive_dir, f"{h}.py"))
    path = next((p for p in paths if os.path.exists(p) and archive_is_runtime_stable(p)), "")
    if not path:
        continue
    entry = rolling.get(h) or {}
    n = len(entry.get("scores", []) or [])
    rejected_meta = rejected.get(h) or {}
    n = max(n, as_int(rejected_meta.get("n", rejected_meta.get("games_total", 0)), 0))
    if n < min_games:
        continue
    russia = as_int(entry.get("russia_count", 0), 0)
    best_type = as_int(entry.get("best_max_type", 0), 0)
    if russia > 0 or best_type >= min_best_type:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

#=== 改善中判定 (soren_loop.sh のスキップ判定用) ===

_improve_state_claims_running_fresh() {
	python3 - "$IMPROVE_STATE_FILE" "${IMPROVE_STATE_RUNNING_FRESH_SEC:-1800}" <<'PY' 2>/dev/null
import json
import os
import sys
import time

state_file = sys.argv[1]
try:
    fresh_sec = int(sys.argv[2])
except Exception:
    fresh_sec = 1800
try:
    with open(state_file, encoding="utf-8") as f:
        state = json.load(f)
except Exception:
    raise SystemExit(1)

if state.get("status") not in {"running", "manual"}:
    raise SystemExit(1)

timestamps = []
for key in ("updated_at", "started_at"):
    try:
        value = int(float(state.get(key, 0) or 0))
    except Exception:
        value = 0
    if value > 0:
        timestamps.append(value)

if not timestamps:
    raise SystemExit(1)

age = time.time() - max(timestamps)
if age < 0:
    age = 0
if age <= max(1, fresh_sec):
    raise SystemExit(0)
raise SystemExit(1)
PY
}

_is_improve_running() {
	if _wildcard_parallel_active; then
		return 0
	fi
	# lock が欠落しても、improve_state が新鮮な running/manual なら main loop を止める。
	# stale state だけで永久停止しないよう、updated_at/started_at の鮮度を必ず見る。
	if [ -f "$IMPROVE_LOCK_FILE" ] &&
		grep -q '"status"[[:space:]]*:[[:space:]]*"running"\|"status"[[:space:]]*:[[:space:]]*"manual"' "$IMPROVE_STATE_FILE" 2>/dev/null; then
		return 0
	fi
	_improve_state_claims_running_fresh
}

_wildcard_parallel_active() {
	local status_file="${WILDCARD_PARALLEL_STATUS_FILE:-$TMP_STATE_DIR/wildcard_parallel_status.json}"
	[ -f "$status_file" ] || return 1
	# wildcard_parallel.py プロセス生存を python に渡す。連続wildcard(consecutive>1)では
	# 各ラウンド末に wildcard_parallel.py が終端phase(winner_selected/winner/won/finished/
	# no_candidate 等)を status に書いてから終了する。その「終端phase書込み〜プロセス終了」の
	# 窓で本線ループがゲートを再評価すると、phase が generating/running 以外なので一瞬 un-pause
	# し、slip ゲームを開始 → OBS sorengame が param overlay を奪い、overlay が消える。
	# プロセスが生きている間は finalize/次ラウンド準備中とみなし active を継続して slip を封じる。
	# プロセスが死ねば自然に inactive へ落ちる(終端phase + 死亡)ので永久pauseにはならない。
	# pgrep だけだと親プロセスが検出漏れ・終了後にスロットが残るケースで漏れる。
	# 状態ファイルベースのガード: session_dir 内の slot game_history 更新が新鮮な間は block を継続。
	local wp_alive=0
	if pgrep -f 'python.* wildcard_parallel\.py' >/dev/null 2>&1; then wp_alive=1; fi
	WP_PROC_ALIVE="$wp_alive" python3 - "$status_file" <<'PY' 2>/dev/null
import json
import os
import sys
import time
from pathlib import Path

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)

phase = str(data.get("phase") or "")
if data.get("block_main_loop") is False:
    raise SystemExit(1)
params = data.get("params")
if isinstance(params, dict) and params.get("block_main_loop") is False:
    raise SystemExit(1)
try:
    max_sec = int(float(os.environ.get("WILDCARD_PARALLEL_MAIN_BLOCK_MAX_SEC", "7800") or "7800"))
except Exception:
    max_sec = 7800
try:
    started_at = int(float(data.get("started_at", 0) or 0))
except Exception:
    started_at = 0
# セッション経年ガード(全phase共通): 起動から max_sec 超過なら解放し、orphan時の永久pauseを防ぐ。
if max_sec > 0 and started_at > 0 and (time.time() - started_at) > max_sec:
    raise SystemExit(1)
try:
    pidless_stale_sec = int(float(os.environ.get("WILDCARD_PARALLEL_PIDLESS_STALE_SEC", "600") or "600"))
except Exception:
    pidless_stale_sec = 600
if phase in {"generating", "running"} and not data.get("controller_pid"):
    # Pre-controller_pid legacy/orphan statuses can be refreshed forever by a
    # stranded parent. New runs always stamp controller_pid, so release old
    # pidless active statuses after a bounded grace window.
    if pidless_stale_sec > 0 and started_at > 0 and (time.time() - started_at) > pidless_stale_sec:
        raise SystemExit(1)

# 状態ファイルベースのスロット活性チェック:
# pgrep が親プロセスを検出漏れしても、スロットが game_history/latest.jsonl を
# 書き続けている間は main loop を止め続ける。
# WILDCARD_PARALLEL_SLOT_FRESH_SEC(デフォルト180)秒以内に更新があれば active とみなす。
try:
    slot_fresh_sec = int(float(os.environ.get("WILDCARD_PARALLEL_SLOT_FRESH_SEC", "180") or "180"))
except Exception:
    slot_fresh_sec = 180
slot_activity_fresh = False
try:
    session_dir = str(data.get("session_dir") or "")
    if session_dir and slot_fresh_sec > 0:
        now = time.time()
        for gh in Path(session_dir).glob("*/game_history/latest.jsonl"):
            try:
                if now - gh.stat().st_mtime < slot_fresh_sec:
                    slot_activity_fresh = True
                    break
            except OSError:
                pass
except Exception:
    pass

if phase in {"generating", "running"}:
    # Orphan guard (priority-2: recover the main game when a param trial crashes/
    # is killed). A running/generating status stamps controller_pid. If that PID is
    # gone AND no wildcard_parallel.py process is alive AND no slot is still writing
    # game_history, the orchestrator died mid-run — release the main loop NOW instead
    # of blocking it until max_sec (~130min). A LIVE controller_pid (or live pgrep /
    # fresh slot) keeps main blocked, so this never releases while a trial is really
    # running.
    def _pid_alive(pid):
        try:
            os.kill(int(pid), 0)
            return True
        except PermissionError:
            return True
        except Exception:
            return False
    _controller_pid = data.get("controller_pid")
    if (os.environ.get("WP_PROC_ALIVE") != "1"
            and not slot_activity_fresh
            and _controller_pid
            and not _pid_alive(_controller_pid)):
        raise SystemExit(1)
    raise SystemExit(0)
# 終了確定済みの復元/cleanup status は、直前の slot latest.jsonl が新鮮でも
# main loop を解放する。OBS再起動後や cleanup_stale 後に phase=restored +
# ended_at だけが残り、最後の slot mtime で永久pauseする事故を防ぐ。
try:
    ended_at = float(data.get("ended_at", 0) or 0)
except Exception:
    ended_at = 0.0
if ended_at > 0 and phase in {
    "restored", "cleanup_stale", "no_candidate", "infra_failed",
    "failed", "winner_selected", "winner", "won", "finished",
} and os.environ.get("WP_PROC_ALIVE") != "1":
    raise SystemExit(1)
# 終端/過渡phase: wildcard_parallel.py が生存中、またはスロットがまだゲーム中なら active を継続。
if os.environ.get("WP_PROC_ALIVE") == "1" or slot_activity_fresh:
    raise SystemExit(0)
raise SystemExit(1)
PY
}

_scheduled_meriken_time_should_run() {
	[ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ] || return 1
	[ "$(date +%H)" = "${MERIKEN_TIME_START_HOUR:-20}" ]
}

_improve_overlay_generate_once() {
	[ -x "./generate_improve_overlay.sh" ] || return 0
	./generate_improve_overlay.sh once >/dev/null 2>&1 || true
}

_improve_overlay_show() {
	printf 'visible.%s.%s\n' "$(date +%s)" "$$" >"$TMP_STATE_DIR/improve_overlay_hide_token" 2>/dev/null || true
	_improve_overlay_generate_once
	./obs_control.sh show soren "$IMPROVE_OVERLAY_SOURCE" 2>/dev/null &
	./obs_control.sh hide soren console4 2>/dev/null &
}

_improve_overlay_hide() {
	_improve_overlay_generate_once
	./obs_control.sh hide soren "$IMPROVE_OVERLAY_SOURCE" 2>/dev/null &
	./obs_control.sh hide soren console4 2>/dev/null &
}

_improve_overlay_hide_after() {
	local delay="${1:-0}" token_file="$TMP_STATE_DIR/improve_overlay_hide_token" token
	case "$delay" in ''|*[!0-9]*) delay=0 ;; esac
	token="$(date +%s).$$.$RANDOM"
	mkdir -p "$TMP_STATE_DIR" 2>/dev/null || true
	printf '%s\n' "$token" >"$token_file" 2>/dev/null || true
	(
		sleep "$delay"
		[ "$(cat "$token_file" 2>/dev/null || true)" = "$token" ] || exit 0
		_improve_overlay_hide
	) >/dev/null 2>&1 &
}

_improve_overlay_watch_start() {
	local pid="${1:-}"
	[ -x "./generate_improve_overlay.sh" ] || return 0
	./generate_improve_overlay.sh watch "$pid" >/dev/null 2>&1 &
	echo $!
}

_post_improve_soren91_session_improve() {
	local reason="${1:-normal}"
	if [ "${POST_IMPROVE_SOREN91_SESSION_IMPROVE_ENABLED:-0}" != "1" ]; then
		log "[SOREN91] post-improve session improve skipped (reason=${reason}, POST_IMPROVE_SOREN91_SESSION_IMPROVE_ENABLED=0)"
		return 0
	fi
	soren91_improve
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
	local phase="${4:-}" progress="${5:-0}" detail="${6:-}" started_at="${7:-0}" pid_birth_epoch="${8:-0}"
	local improve_reason="${9:-}"
	local now
	now=$(date +%s)
	python3 - "$IMPROVE_STATE_FILE" "$status" "${pid:-0}" "${hash:-}" "$phase" "$progress" "$detail" "$started_at" "$now" "${pid_birth_epoch:-0}" "$improve_reason" <<'PY'
import json
import sys

out_file, status, pid_raw, hash_before, phase, progress_raw, detail, started_raw, now_raw, pid_birth_raw, improve_reason = sys.argv[1:12]

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
try:
    pid_birth_epoch = int(pid_birth_raw)
except Exception:
    pid_birth_epoch = 0

if status == "running" and not improve_reason:
    previous_reason = ""
    try:
        with open(out_file, encoding="utf-8") as f:
            previous_reason = str(json.load(f).get("improve_reason") or "")
    except Exception:
        previous_reason = ""
    improve_reason = previous_reason or "normal"

if started_at <= 0 and status == "running":
    started_at = now

if status == "idle" and pid == 0 and progress <= 0:
    hash_before = ""
    phase = ""
    detail = ""
    started_at = 0
    pid_birth_epoch = 0
    improve_reason = ""

data = {
    "status": status,
    "pid": pid,
    "strategy_hash_before": hash_before,
    "phase": phase,
    "progress": progress,
    "detail": detail,
    "started_at": started_at,
    "updated_at": now,
    "pid_birth_epoch": pid_birth_epoch,
    "improve_reason": improve_reason,
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
PY
}

_strategy_decide_hash_or_md5() {
	local path="${1:-$STRATEGY_FILE}"
	local hash=""
	[ -n "$path" ] || return 0
	if [ -f "$path" ]; then
		hash=$(python3 extract_decide_hash.py "$path" 2>/dev/null || true)
		if [ -z "$hash" ]; then
			hash=$(md5 -q "$path" 2>/dev/null | cut -c1-8 || true)
		fi
	fi
	printf '%s\n' "$hash"
}

_snapshot_improve_retry_batch() {
	local source="${1:-$IMPROVE_LOCK_FILE}"
	local target="${IMPROVE_RETRY_BATCH_FILE:-$TMP_STATE_DIR/improve_retry_batch.json}"
	[ -s "$source" ] || return 1
	mkdir -p "$(dirname "$target")" 2>/dev/null || return 1
	python3 - "$source" "$target" <<'PY' 2>/dev/null
import json
import os
import sys
import time

source, target = sys.argv[1:3]
try:
    with open(source, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(1)
if not isinstance(data, dict):
    raise SystemExit(1)
try:
    count = int(data.get("count", 0) or 0)
except Exception:
    count = 0
batch_hash = str(data.get("hash") or data.get("strategy_hash") or "")
if count <= 0 or not batch_hash:
    raise SystemExit(1)
data["retry_snapshot_at"] = int(time.time())
tmp = f"{target}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
os.replace(tmp, target)
PY
}

_restore_improve_retry_batch_if_valid() {
	local current_hash="${1:-}"
	local source="${IMPROVE_RETRY_BATCH_FILE:-$TMP_STATE_DIR/improve_retry_batch.json}"
	local target="${IMPROVE_LOCK_FILE}"
	local min_games="${MIN_GAMES_BEFORE_IMPROVE:-12}"
	local result=""
	[ -n "$current_hash" ] || return 1
	[ -s "$source" ] || return 1
	result=$(python3 - "$source" "$target" "$current_hash" "$min_games" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

source, target, current_hash, min_games_raw = sys.argv[1:5]
try:
    min_games = max(1, int(min_games_raw))
except Exception:
    min_games = 12

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

existing = load(target)
try:
    existing_count = int(existing.get("count", 0) or 0)
except Exception:
    existing_count = 0
if existing_count > 0:
    print(f"existing:{existing_count}")
    raise SystemExit(0)

data = load(source)
try:
    count = int(data.get("count", 0) or 0)
except Exception:
    count = 0
batch_hash = str(data.get("hash") or data.get("strategy_hash") or "")
early = bool(data.get("early_escape_lock", False))
if not batch_hash or batch_hash != current_hash or count <= 0:
    raise SystemExit(1)
if count < min_games and not early:
    raise SystemExit(1)

data["retry_restored_at"] = int(time.time())
data["retry_restore_count"] = int(data.get("retry_restore_count", 0) or 0) + 1
tmp = f"{target}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
os.replace(tmp, target)
print(f"restored:{count}")
PY
	)
	case "$result" in
	restored:*)
		log "[IMPROVE] failed_no_apply retry batch restored (${result#*:} games, hash=${current_hash:0:12})"
		return 0
		;;
	existing:*) return 0 ;;
	*) return 1 ;;
	esac
}

_clear_improve_retry_batch() {
	rm -f "${IMPROVE_RETRY_BATCH_FILE:-$TMP_STATE_DIR/improve_retry_batch.json}" 2>/dev/null || true
}

_schedule_improve_retry_backoff() {
	local lock_file="${1:-$IMPROVE_LOCK_FILE}"
	local snapshot_file="${IMPROVE_RETRY_BATCH_FILE:-$TMP_STATE_DIR/improve_retry_batch.json}"
	local backoff_file="$TMP_STATE_DIR/rate_limit_backoff"
	python3 - "$lock_file" "$snapshot_file" "$backoff_file" <<'PY' 2>/dev/null
import json
import os
import sys
import time

lock_file, snapshot_file, backoff_file = sys.argv[1:4]

def load(path):
    try:
        value = json.load(open(path, encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}

def write_atomic(path, value):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False)
    os.replace(tmp, path)

lock = load(lock_file)
if not lock:
    raise SystemExit(1)
snapshot = load(snapshot_file)
try:
    previous = max(
        int(lock.get("retry_failure_count", 0) or 0),
        int(snapshot.get("retry_failure_count", 0) or 0),
    )
except Exception:
    previous = 0
count = previous + 1
now = int(time.time())
lock["retry_failure_count"] = count
lock["retry_last_failed_at"] = now
write_atomic(lock_file, lock)

lock_hash = str(lock.get("hash") or lock.get("strategy_hash") or "")
snapshot_hash = str(snapshot.get("hash") or snapshot.get("strategy_hash") or "")
if snapshot and lock_hash and snapshot_hash == lock_hash:
    snapshot["retry_failure_count"] = count
    snapshot["retry_last_failed_at"] = now
    write_atomic(snapshot_file, snapshot)

os.makedirs(os.path.dirname(backoff_file) or ".", exist_ok=True)
tmp = f"{backoff_file}.tmp.{os.getpid()}"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(f"{count}\n{now}\n")
os.replace(tmp, backoff_file)
print(count)
PY
}

_persist_improve_lock_reason() {
	local reason="${1:-normal}"
	case "$reason" in
	normal|post_regression|wildcard|escape_ai|archive_restart) ;;
	*) reason="normal" ;;
	esac
	[ -f "$IMPROVE_LOCK_FILE" ] || return 0
	python3 - "$IMPROVE_LOCK_FILE" "$reason" <<'PY' 2>/dev/null || true
import json
import os
import sys

path, reason = sys.argv[1:3]
try:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    raise SystemExit(0)
if not isinstance(data, dict):
    raise SystemExit(0)
data["improve_reason"] = reason
tmp = f"{path}.tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
os.replace(tmp, path)
PY
}

_main_strategy_runner_active_for_improve() {
	local marker="${MAIN_STRATEGY_RUNNER_ACTIVE_FILE:-${TMP_STATE_DIR:-tmp/state}/main_strategy_runner_active.json}"
	[ -f "$marker" ] || return 1
	local runner_pid=""
	runner_pid=$(python3 - "$marker" <<'PY' 2>/dev/null || true
import json
import sys
try:
    print(int(json.load(open(sys.argv[1], encoding="utf-8")).get("pid", 0) or 0))
except Exception:
    print("")
PY
)
	case "$runner_pid" in ''|0|*[!0-9]*) return 1 ;; esac
	kill -0 "$runner_pid" 2>/dev/null
}

_is_live_improve_pid() {
	local pid="$1"
	case "$pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac
	kill -0 "$pid" 2>/dev/null || return 1
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	if [ -z "$cmd" ]; then
		_is_recorded_running_improve_pid "$pid"
		return $?
	fi
	echo "$cmd" | grep -Eq "eloop_improve(_runtime\.[^ ]+)?\.sh"
}

_is_recorded_running_improve_pid() {
	local pid="$1"
	case "$pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac
	kill -0 "$pid" 2>/dev/null || return 1
	[ -f "$IMPROVE_STATE_FILE" ] || return 1
	python3 - "$IMPROVE_STATE_FILE" "$pid" "${IMPROVE_STALE_WATCHDOG_SEC:-3600}" <<'PY' 2>/dev/null
import json
import sys
import time

path, pid, max_age = sys.argv[1:4]
try:
    max_age = int(max_age)
except Exception:
    max_age = 3600
try:
    data = json.load(open(path, encoding="utf-8"))
except Exception:
    raise SystemExit(1)
if str(data.get("pid", "")) != str(pid):
    raise SystemExit(1)
if data.get("status") != "running":
    raise SystemExit(1)
try:
    updated_at = int(data.get("updated_at", 0) or 0)
except Exception:
    updated_at = 0
if updated_at <= 0 or int(time.time()) - updated_at > max(max_age, 300):
    raise SystemExit(1)
raise SystemExit(0)
PY
}

_log_improve_pid_cmd_missing_if_due() {
	local pid="$1" now last_ts interval state_file
	interval="${IMPROVE_PID_CMD_MISSING_LOG_INTERVAL_SEC:-300}"
	case "$interval" in
	''|*[!0-9]*) interval=300 ;;
	esac
	now=$(date +%s)
	state_file="$TMP_STATE_DIR/improve_pid_cmd_missing_${pid}.ts"
	last_ts=$(cat "$state_file" 2>/dev/null || echo 0)
	case "$last_ts" in
	''|*[!0-9]*) last_ts=0 ;;
	esac
	if [ "$last_ts" -le 0 ] || [ $((now - last_ts)) -ge "$interval" ]; then
		log "[IMPROVE] PID=$pid のcommand取得不可だが、記録済みrunning状態と一致 → live扱いを維持"
		printf '%s\n' "$now" >"$state_file" 2>/dev/null || true
	fi
}

_find_live_improve_pid() {
	local candidate=""
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		candidate=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		if _is_live_improve_pid "$candidate"; then
			printf '%s\n' "$candidate"
			return 0
		fi
	fi
	if _is_live_improve_pid "${IMPROVE_PID:-0}"; then
		printf '%s\n' "${IMPROVE_PID:-0}"
		return 0
	fi
	return 1
}

_sync_improve_state_with_live_process() {
	local live_pid=""
	live_pid=$(_find_live_improve_pid 2>/dev/null || true)
	case "$live_pid" in
	''|0|*[!0-9]*) return 1 ;;
	esac

	local state current_status current_pid hash_before started_at pid_birth_epoch improve_reason
	state=$(_read_improve_state)
	current_status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)
	current_pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)
	hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
	started_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('started_at',0) or 0))" 2>/dev/null || echo 0)
	pid_birth_epoch=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('pid_birth_epoch',0) or 0))" 2>/dev/null || echo 0)
	improve_reason=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('improve_reason',''))" 2>/dev/null || echo "")
	if [ -z "$improve_reason" ] || [ "$improve_reason" = "normal" ]; then
		improve_reason=$(python3 - "$IMPROVE_LOCK_FILE" <<'PY' 2>/dev/null || true
import json
import os
import sys

path = sys.argv[1]
if not path or not os.path.exists(path):
    raise SystemExit(0)
try:
    reason = json.load(open(path, encoding="utf-8")).get("improve_reason", "")
except Exception:
    reason = ""
if reason:
    print(reason)
PY
)
	fi
	[ -n "$hash_before" ] || hash_before=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")
	# PIDが変わった場合はbirth_epochを再計算
	if [ "${current_pid:-0}" != "$live_pid" ] || [ "${pid_birth_epoch:-0}" -eq 0 ]; then
		pid_birth_epoch=$(ps -p "$live_pid" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)
	fi

	if [ "$current_status" != "running" ] || [ "${current_pid:-0}" != "$live_pid" ]; then
		log "[IMPROVE] state self-heal: live PID=$live_pid を running に再同期 (was status=${current_status:-unknown}, pid=${current_pid:-0}, reason=${improve_reason:-})"
		_write_improve_state "running" "$live_pid" "$hash_before" "recovered" "1" "live_process_detected" "$started_at" "$pid_birth_epoch" "$improve_reason"
	fi
	IMPROVE_PID=$live_pid
	return 0
}

_stop_improve_pid_if_running() {
	local pid="$1" label="${2:-improve}"
	if ! _is_live_improve_pid "$pid"; then
		return 0
	fi
	local state phase detail progress pid_cmd
	state=$(_read_improve_state)
	phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
	detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
	progress=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('progress',0) or 0))" 2>/dev/null || echo 0)
	pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	log "[IMPROVE] stop request: label=${label} pid=${pid} phase=${phase:-?} detail=${detail:-?} progress=${progress:-0} cmd=${pid_cmd:-unknown}"
	_stop_loop_descendants "$pid"
	_stop_pid_with_fallback "$pid" "$label"
	wait "$pid" 2>/dev/null
	local wait_rc=$?
	log "[IMPROVE] stop result: label=${label} pid=${pid} wait_rc=${wait_rc}"
	if _is_live_improve_pid "$pid"; then
		log "[IMPROVE] stop result: label=${label} pid=${pid} still_alive=1"
		return 1
	fi
	return 0
}

check_and_harvest_improvement() {
	local state
	if command -v soren91_harvest_hung_improve >/dev/null 2>&1; then
		soren91_harvest_hung_improve || true
	fi
	_sync_improve_state_with_live_process >/dev/null 2>&1 || true
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	# 孤立ロックファイル検出: idle状態でeloop_improveも動いていないのにロックが長時間残っている場合は削除
	# ※ daemon poll間隔(デフォルト30s)より大幅に長い閾値にすること
	#   (lock作成直後はstatus=idleのままdaemonが拾うまで最大poll間隔かかる)
	# failed_no_apply の再試行ロックは rate-limit backoff と対になっている。
	# backoff が有効な間に orphan 扱いすると、指数待機が長い回ほど
	# retry 直前に入力バッチを失うため、通常の孤立ロックだけを回収する。
	if [ "$status" = "idle" ] && [ -f "$IMPROVE_LOCK_FILE" ] && [ ! -f "$TMP_STATE_DIR/rate_limit_backoff" ] && [ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]; then
		local _lock_age _lock_mtime _orphan_threshold
		_orphan_threshold="${IMPROVE_STALE_WATCHDOG_SEC:-600}"
		_lock_mtime=$(stat -f %m "$IMPROVE_LOCK_FILE" 2>/dev/null) \
			|| _lock_mtime=$(stat -c %Y "$IMPROVE_LOCK_FILE" 2>/dev/null) \
			|| _lock_mtime=0
		_lock_age=$(( $(date +%s) - ${_lock_mtime:-0} ))
		if [ "${_lock_age:-0}" -gt "${_orphan_threshold}" ]; then
			log "[IMPROVE] 孤立ロックファイル検出 (age=${_lock_age}s > ${_orphan_threshold}s, status=idle) → 削除"
			rm -f "$IMPROVE_LOCK_FILE"
		fi
	fi

	# 手動改善モード
	if [ "$status" = "manual" ]; then
		# フラグが存在する間は待機
		[ -f "$TMP_STATE_DIR/manual_improve_mode" ] && return 0
		# フラグ削除済み (manual_improve_off.sh 実行後) → main loop context で harvest
		log "[IMPROVE][MANUAL] フラグ削除検出 → harvest処理開始"
		local hash_before hash_now
		hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
		hash_now=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")
		if [ "$hash_before" != "$hash_now" ]; then
			log "[IMPROVE][MANUAL] 戦略更新検出: $hash_before -> $hash_now"
			local new_decide_hash prev_decide_hash=""
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
rs = json.load(open(rs_file)) if os.path.exists(rs_file) else {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
json.dump(rs, open(rs_file, 'w'))
" 2>/dev/null || true
			fi
			if [ -n "$new_decide_hash" ]; then
				local branch_transition=""
				branch_transition=$(_branch_transition_after_improve "$prev_decide_hash" "$new_decide_hash" || true)
				[ -n "$branch_transition" ] && log "[BRANCH] ${branch_transition}"
				_reset_current_strategy_run "$new_decide_hash" || true
			fi
			local acc_count_discarded=0
			[ -f "$ACCUMULATED_GAMES_FILE" ] && acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
			_clear_accumulated_data
			[ "${acc_count_discarded:-0}" -gt 0 ] && log "[IMPROVE][MANUAL] 蓄積${acc_count_discarded}試合を破棄"
			_write_improve_state "idle" "0" "" "" "0" ""
			rm -f "$TMP_STATE_DIR/last_improve_failed_at"
		else
			log "[IMPROVE][MANUAL] 戦略変更なし → failed_no_apply"
			date +%s > "$TMP_STATE_DIR/last_improve_failed_at"
			_write_improve_state "idle" "0" "" "failed_no_apply" "100" "manual_no_change"
		fi
		rm -f "$IMPROVE_LOCK_FILE"
		IMPROVE_PID=0
		log "[IMPROVE][MANUAL] 手動改善完了 → idle"
		_improve_overlay_hide
		if _improve_keep_main_game_running; then
			log "[IMPROVE][MANUAL] 継続プレイ設定のため、代打停止・交代処理なしでメインゲームを継続"
			rm -f "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
		elif command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
			log "[IMPROVE][MANUAL] manual_meriken_mode=on のため、メリケンAI継続"
		elif _scheduled_meriken_time_should_run; then
			log "[IMPROVE][MANUAL] 20時台: メリケンAIタイムに移行 → soren91継続"
			_post_improve_soren91_session_improve "manual_scheduled_meriken"
			MERIKEN_TIME_PENDING=1
			touch "tmp/state/meriken_time_pending"
		else
			soren91_stop
			[ "${POST_IMPROVE_MAINPLAY_ENABLED:-1}" = "1" ] && touch "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
			_post_improve_soren91_session_improve "manual"
		fi
		return 0
	fi

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
			local pid_cmd
			pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				# v40: 記録されたpid_birth_epochとプロセスlstartを直接照合してPID再利用を検出
				# updated_atは_improve_progress()で更新され続けるため比較に使えない
				local pid_start_epoch recorded_birth
				recorded_birth=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('pid_birth_epoch',0) or 0))" 2>/dev/null || echo 0)
				pid_start_epoch=$(ps -p "$pid" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)
				if [ "${recorded_birth:-0}" -ne 0 ] && [ "${pid_start_epoch:-0}" -ne 0 ] && [ "$pid_start_epoch" -ne "$recorded_birth" ]; then
					# PIDは生きているがlstartが記録値と異なる → PID再利用の可能性
					log "[IMPROVE] PID=$pid はlstart($pid_start_epoch)が記録値($recorded_birth)と不一致 → PID再利用とみなしstale状態クリア"
					pid_alive=false
				else
					pid_alive=true
				fi
			elif [ -z "$pid_cmd" ] && _is_recorded_running_improve_pid "$pid"; then
				_log_improve_pid_cmd_missing_if_due "$pid"
				pid_alive=true
			else
				log "[IMPROVE] PID=$pid は別プロセス ($pid_cmd) → stale状態クリア"
			fi
		fi

			local watchdog_sec="${IMPROVE_STALE_WATCHDOG_SEC:-3600}"
			case "$watchdog_sec" in
			''|*[!0-9]*) watchdog_sec=3600 ;;
			esac
		if [ "$pid_alive" = true ] && [ "${watchdog_sec:-0}" -gt 0 ]; then
			local updated_at updated_age now_epoch log_age log_mtime eval_age eval_mtime prev_phase prev_detail started_at elapsed_age wall_timeout
			updated_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('updated_at',0) or 0))" 2>/dev/null || echo 0)
			started_at=$(echo "$state" | python3 -c "import json,sys; print(int(json.load(sys.stdin).get('started_at',0) or 0))" 2>/dev/null || echo 0)
			prev_phase=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('phase',''))" 2>/dev/null)
			prev_detail=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('detail',''))" 2>/dev/null)
			now_epoch=$(date +%s)
			updated_age=$(( now_epoch - ${updated_at:-0} ))
			elapsed_age=$updated_age
			if [ "${started_at:-0}" -gt 0 ]; then
				elapsed_age=$(( now_epoch - started_at ))
			fi
			log_age=$updated_age
			if [ -f "$IMPROVE_AI_LOG_FILE" ]; then
				log_mtime=$(stat -f %m "$IMPROVE_AI_LOG_FILE" 2>/dev/null) \
					|| log_mtime=$(stat -c %Y "$IMPROVE_AI_LOG_FILE" 2>/dev/null) \
					|| log_mtime=0
				if [ "${log_mtime:-0}" -gt 0 ]; then
					log_age=$(( now_epoch - log_mtime ))
				fi
			fi
			eval_age=$updated_age
			if [ -f "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" ]; then
				eval_mtime=$(stat -f %m "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" 2>/dev/null) \
					|| eval_mtime=$(stat -c %Y "${EVAL_SCORE_HISTORY_FILE:-eval_score_history.txt}" 2>/dev/null) \
					|| eval_mtime=0
				if [ "${eval_mtime:-0}" -gt 0 ]; then
					eval_age=$(( now_epoch - eval_mtime ))
				fi
			fi
			wall_timeout="${IMPROVE_WALL_TIMEOUT:-3600}"
			case "$wall_timeout" in
			''|*[!0-9]*) wall_timeout=3600 ;;
			esac
			if [ "${prev_phase:-}" = "wildcard_parallel" ]; then
				wall_timeout=0
			fi
			if [ "${wall_timeout:-0}" -gt 0 ] && [ "$elapsed_age" -ge "$wall_timeout" ]; then
				log "[IMPROVE] wall timeout harvest: elapsed=${elapsed_age}s >= ${wall_timeout}s (PID=$pid, phase=${prev_phase:-?}, detail=${prev_detail:-})"
				mkdir -p "$(dirname "${IMPROVE_HUNG_QUARANTINE_FILE:-$TMP_STATE_DIR/improve_hung_quarantine.jsonl}")" 2>/dev/null || true
				python3 - "$IMPROVE_HUNG_QUARANTINE_FILE" "$pid" "$elapsed_age" "$wall_timeout" "$updated_age" "$log_age" "$eval_age" "${prev_phase:-}" "${prev_detail:-}" "$(ps -p "$pid" -o command= 2>/dev/null || echo "")" <<'PY' 2>/dev/null || true
import json
import sys
import time

out, pid, elapsed_age, threshold, updated_age, log_age, eval_age, phase, detail, cmd = sys.argv[1:11]
row = {
    "epoch": int(time.time()),
    "event": "improve_wall_timeout_harvest",
    "pid": int(pid),
    "elapsed_age": int(elapsed_age),
    "updated_age": int(updated_age),
    "log_age": int(log_age),
    "eval_age": int(eval_age),
    "threshold": int(threshold),
    "phase": phase,
    "detail": detail,
    "command": cmd,
}
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
				if _stop_improve_pid_if_running "$pid" "improve_wall_timeout"; then
					pid_alive=false
					enqueue_audio_text "通常改善が上限時間を超えたため、改善プロセスを回収して中華AIの進行を優先します。" "improve_wall_timeout" "${IMPROVE_AUDIO_SUMMARY_SPEAKER:-}" || true
				else
					log "[IMPROVE] wall timeout harvest: PID=$pid の停止に失敗したためrunning扱いを維持"
				fi
			fi
			if [ "$pid_alive" = true ] && [ "${prev_phase:-}" != "wildcard_parallel" ] && [ "$updated_age" -ge "$watchdog_sec" ] && [ "$log_age" -ge "$watchdog_sec" ]; then
				if [ "${IMPROVE_HUNG_REQUIRE_EVAL_STALE:-1}" = "1" ] && [ "$eval_age" -lt "$watchdog_sec" ]; then
					log "[IMPROVE] watchdog保留: state/log は古いが eval_score_history は進行中 (${eval_age}s < ${watchdog_sec}s, PID=$pid)"
				else
				log "[IMPROVE] watchdog警告: ${updated_age}s 状態更新なし / ${log_age}s ログ更新なし / eval ${eval_age}s 更新なし (PID=$pid, phase=${prev_phase:-?}, detail=${prev_detail:-})"
				if [ "${IMPROVE_HUNG_HARVEST_ENABLED:-1}" = "1" ]; then
					log "[IMPROVE] hung harvest: 無音改善ジョブをquarantineして停止 (PID=$pid)"
					mkdir -p "$(dirname "${IMPROVE_HUNG_QUARANTINE_FILE:-$TMP_STATE_DIR/improve_hung_quarantine.jsonl}")" 2>/dev/null || true
					python3 - "$IMPROVE_HUNG_QUARANTINE_FILE" "$pid" "$updated_age" "$log_age" "$eval_age" "$watchdog_sec" "${prev_phase:-}" "${prev_detail:-}" "$(ps -p "$pid" -o command= 2>/dev/null || echo "")" <<'PY' 2>/dev/null || true
import json
import sys
import time

out, pid, updated_age, log_age, eval_age, threshold, phase, detail, cmd = sys.argv[1:10]
row = {
    "epoch": int(time.time()),
    "event": "improve_hung_harvest",
    "pid": int(pid),
    "updated_age": int(updated_age),
    "log_age": int(log_age),
    "eval_age": int(eval_age),
    "threshold": int(threshold),
    "phase": phase,
    "detail": detail,
    "command": cmd,
}
with open(out, "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
					if _stop_improve_pid_if_running "$pid" "improve_hung"; then
						pid_alive=false
						enqueue_audio_text "通常改善が無音で固まったため、改善プロセスを回収して中華AIの進行を優先します。" "improve_hung" "${IMPROVE_AUDIO_SUMMARY_SPEAKER:-}" || true
					else
						log "[IMPROVE] hung harvest: PID=$pid の停止に失敗したためrunning扱いを維持"
					fi
				fi
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
			local prev_improve_reason
			prev_improve_reason=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('improve_reason','normal'))" 2>/dev/null || echo "normal")
			if [ -z "$prev_improve_reason" ] || [ "$prev_improve_reason" = "normal" ]; then
				local lock_improve_reason
				lock_improve_reason=$(python3 - "$IMPROVE_LOCK_FILE" <<'PY' 2>/dev/null || true
import json
import os
import sys

path = sys.argv[1]
if not path or not os.path.exists(path):
    raise SystemExit(0)
try:
    reason = json.load(open(path, encoding="utf-8")).get("improve_reason", "")
except Exception:
    reason = ""
if reason in {"wildcard", "archive_restart"}:
    print(reason)
PY
)
				if [ -n "$lock_improve_reason" ]; then
					prev_improve_reason="$lock_improve_reason"
					log "[IMPROVE] state理由欠落をlockから復元: improve_reason=${prev_improve_reason}"
				fi
			fi
			local hash_now
			hash_now=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")

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
				enqueue_audio_text "中華AI改善は適用可能な戦略変更を出せず終了しました。ロックを残してバックオフし、通常運転を続けます。" "improve_failed_no_apply" "${IMPROVE_AUDIO_SUMMARY_SPEAKER:-}" || true
				# 戦略が変わっていない → 蓄積データはそのまま有効
				# failed_no_apply タイムスタンプを記録 (連続再試行防止用)
				date +%s > "$TMP_STATE_DIR/last_improve_failed_at"
			fi

			if _improve_keep_main_game_running; then
				:
			elif command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
				:
			elif _scheduled_meriken_time_should_run; then
				:
			else
				if [ -n "${SOREN91_STOPPING_FILE:-}" ]; then
					touch "$SOREN91_STOPPING_FILE" 2>/dev/null || true
				fi
			fi

			if [ "$hash_before" != "$hash_now" ]; then
				_write_improve_state "idle" "0" "" "" "0" ""
				rm -f "$TMP_STATE_DIR/last_improve_failed_at"
				rm -f "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || true
				rm -f "$IMPROVE_LOCK_FILE"
				_clear_improve_retry_batch
			else
				_write_improve_state "idle" "0" "" "failed_no_apply" "100" "${prev_detail:-process_exited_without_apply}"
				# improve.lock は本来ジョブ完了まで残るが、別の回収経路や
				# 競合で消えると100試合分を失い、次の閾値まで再試行されない。
				# 開始時スナップショットから同一hashの有効バッチだけを復元する。
				_restore_improve_retry_batch_if_valid "$hash_now" || true
				local _failed_lock_meta="" _failed_lock_rest=""
				local _failed_lock_hash="" _failed_lock_count="" _failed_lock_early=""
				if [ -s "$IMPROVE_LOCK_FILE" ]; then
					_failed_lock_meta=$(python3 - "$IMPROVE_LOCK_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print("\t".join([
        str(data.get("hash", "") or ""),
        str(int(data.get("count", 0) or 0)),
        "1" if data.get("early_escape_lock") else "0",
    ]))
except Exception:
    pass
PY
)
					_failed_lock_hash="${_failed_lock_meta%%	*}"
					_failed_lock_rest="${_failed_lock_meta#*	}"
					_failed_lock_count="${_failed_lock_rest%%	*}"
					_failed_lock_early="${_failed_lock_rest#*	}"
				fi
				if [ -n "$_failed_lock_hash" ] && [ -n "$hash_now" ] && [ "$_failed_lock_hash" != "$hash_now" ]; then
					log "[IMPROVE] failed_no_apply lock hash stale (${_failed_lock_hash:0:12} != current ${hash_now:0:12}) → stale lock/backoff cleared"
					rm -f "$IMPROVE_LOCK_FILE" "$TMP_STATE_DIR/rate_limit_backoff" "$TMP_STATE_DIR/rate_limit_backoff_last_log" 2>/dev/null || true
					_clear_improve_retry_batch
				elif [ -n "$_failed_lock_count" ] && [ "${_failed_lock_early:-0}" != "1" ] && [ "${_failed_lock_count:-0}" -lt "${MIN_GAMES_BEFORE_IMPROVE:-12}" ]; then
					log "[IMPROVE] failed_no_apply partial lock cleared (count=${_failed_lock_count}/${MIN_GAMES_BEFORE_IMPROVE:-12})"
					rm -f "$IMPROVE_LOCK_FILE" "$TMP_STATE_DIR/rate_limit_backoff" "$TMP_STATE_DIR/rate_limit_backoff_last_log" 2>/dev/null || true
					_clear_improve_retry_batch
				else
					# failed_no_apply: 有効なlockだけを残す。空lockはmain loopを止めるだけなので作らない。
					if [ -s "$IMPROVE_LOCK_FILE" ]; then
						touch "$IMPROVE_LOCK_FILE" 2>/dev/null || true
						# バックオフを設定して即座にリトライしない (soren91 stop→start ループ防止)
						local _backoff_count
						if ! _backoff_count=$(_schedule_improve_retry_backoff "$IMPROVE_LOCK_FILE" 2>/dev/null); then
							_backoff_count=1
							printf '%d\n%d\n' "$_backoff_count" "$(date +%s)" > "$TMP_STATE_DIR/rate_limit_backoff"
						fi
						log "[IMPROVE] ロックファイル保持 → daemon再試行待ち (backoff count=${_backoff_count})"
					else
						log "[IMPROVE] failed_no_apply: valid lock absent → retry lock/backoff cleared"
						rm -f "$IMPROVE_LOCK_FILE" "$TMP_STATE_DIR/rate_limit_backoff" "$TMP_STATE_DIR/rate_limit_backoff_last_log" 2>/dev/null || true
						_clear_improve_retry_batch
					fi
				fi
			fi
			IMPROVE_PID=0
			log "[IMPROVE] 改善完了 → idle"
			# OBS: 改善中オーバーレイ非表示
			if _improve_keep_main_game_running; then
				log "[IMPROVE] 継続プレイ設定: soren91_stop/session improve/交代処理を行わず、メインゲームを継続"
				rm -f "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
				_improve_overlay_hide
				return 0
			fi
			case "$prev_improve_reason" in
			wildcard|archive_restart)
				log "[WILDCARD] ${prev_improve_reason} 完了: 高速脱出のため soren91_stop/soren91_improve/handover/bridge再起動をスキップ"
				_improve_overlay_hide_after "${IMPROVE_FAST_ESCAPE_OVERLAY_HOLD_SEC:-45}"
				return 0
				;;
			*)
				_improve_overlay_hide
				;;
			esac
			if command -v manual_meriken_mode_is_enabled >/dev/null 2>&1 && manual_meriken_mode_is_enabled; then
				log "[IMPROVE] manual_meriken_mode=on のため、メリケンAI継続"
			elif _scheduled_meriken_time_should_run; then
				# 20時台: メリケンAIタイムに移行するため停止しない
				log "[IMPROVE] 20時台: メリケンAIタイムに移行 → soren91継続"
				_post_improve_soren91_session_improve "scheduled_meriken"
				MERIKEN_TIME_PENDING=1
				touch "tmp/state/meriken_time_pending"
			else
				# soren91 (メリケンAI) を停止 → バックグラウンド改善開始
				soren91_stop
				# 改善完了マーカ: 次の (カスケード) 改善ロックに即 PAUSE する前に
				# soren_loop がメインゲームを最低1回走らせる窓を保証する
				[ "${POST_IMPROVE_MAINPLAY_ENABLED:-1}" = "1" ] && touch "${POST_IMPROVE_MAINPLAY_MARKER:-$TMP_STATE_DIR/.post_improve_mainplay}" 2>/dev/null || true
				_post_improve_soren91_session_improve "$prev_improve_reason"
				# 読み上げ + Twitch チャットに戦略改善終了を通知 (1サイクル1回のみ)
				local _handover_guard="$TMP_STATE_DIR/handover_announced"
				if [ ! -f "$_handover_guard" ]; then
					touch "$_handover_guard"
					enqueue_audio_text "戦略改善終了。交代します" "soren91_handover" "${SOREN91_VOICEVOX_SPEAKER:-46}"
					enqueue_chat_message "戦略改善終了。交代します" "improve"
				else
					log "[IMPROVE] 交代アナウンス重複スキップ (guard存在)"
				fi
			fi
		fi
	fi
}

accumulate_game_data() {
	local archive_file="$1" score="$2" soviet="$3" strategy_hash="$4" russia="${5:-false}"

	python3 -c "
import json, os
acc_file = '$ACCUMULATED_GAMES_FILE'
archive_file = '$archive_file'
if os.path.exists(acc_file):
    with open(acc_file) as f:
        acc = json.load(f)
else:
    acc = {'files': [], 'scores': '', 'soviet': False, 'soviet_count': 0, 'count': 0, 'hash': '', 'russia_count': 0}

curr_hash = '$strategy_hash'
if acc.get('hash') and curr_hash and acc.get('hash') != curr_hash:
    acc = {'files': [], 'scores': '', 'soviet': False, 'soviet_count': 0, 'count': 0, 'hash': curr_hash, 'russia_count': 0}
elif curr_hash:
    acc['hash'] = curr_hash

raw_score = os.environ.get('LAST_RAW_SCORE', '')
acc['files'].append(archive_file)
acc['scores'] = (acc['scores'] + ' $score').strip()
if raw_score:
    acc['raw_scores'] = (acc.get('raw_scores', '') + ' ' + raw_score).strip()
if '$soviet' == 'true':
    acc['soviet'] = True
    acc['soviet_count'] = acc.get('soviet_count', 0) + 1
if '$russia' == 'true':
    acc['russia_count'] = acc.get('russia_count', 0) + 1
acc['count'] += 1

def summarize_archive(path):
    max_type = 0
    peak_type_counts = {}
    deadline_guard_count = 0
    if not path or not os.path.exists(path):
        return max_type, 'no-high-type', 'none', deadline_guard_count
    try:
        with open(path, encoding='utf-8', errors='ignore') as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except Exception:
                    continue
                if 'DEADLINE_GUARD' in str(row.get('decision_reason') or ''):
                    deadline_guard_count += 1
                pieces = ((row.get('state_snapshot') or {}).get('pieces') or [])
                for piece in pieces:
                    try:
                        t = int(piece.get('type', 0) or 0)
                    except Exception:
                        continue
                    max_type = max(max_type, t)
                    if t >= 10:
                        same_type_count = 0
                        for p in pieces:
                            try:
                                if int((p or {}).get('type', 0) or 0) == t:
                                    same_type_count += 1
                            except Exception:
                                pass
                        peak_type_counts[t] = max(peak_type_counts.get(t, 0), same_type_count)
    except Exception:
        pass
    peak_counts = ' '.join(f'T{t}x{peak_type_counts[t]}' for t in sorted(peak_type_counts, reverse=True)[:4]) or 'none'
    frontier_hint = 'no-high-type'
    if max_type >= 10:
        frontier_hint = f'T{max_type}_peak={peak_type_counts.get(max_type, 0)} prev_T{max_type - 1}_peak={peak_type_counts.get(max_type - 1, 0)}'
    return max_type, frontier_hint, peak_counts, deadline_guard_count

max_type, frontier_hint, peak_counts, deadline_guard_count = summarize_archive(archive_file)
acc.setdefault('max_types', []).append(max_type)
acc.setdefault('frontier_hints', []).append(frontier_hint)
acc.setdefault('peak_high_type_counts', []).append(peak_counts)
acc.setdefault('deadline_guard_counts', []).append(deadline_guard_count)
acc['best_max_type'] = max([int(acc.get('best_max_type', 0) or 0), max_type])
acc['deadline_guard_total'] = int(acc.get('deadline_guard_total', 0) or 0) + deadline_guard_count

with open(acc_file, 'w') as f:
    json.dump(acc, f)
print(f'[ACCUMULATE] 蓄積: {acc[\"count\"]}試合')
" 2>/dev/null
}

queue_fresh_objective_same_hash_lock_if_needed() {
	[ "${CURRENT_RUN_FRESH_OBJECTIVE_REGRESSION_ENABLED:-1}" = "1" ] || return 1
	[ "${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_EARLY_LOCK_ENABLED:-1}" = "1" ] || return 1
	[ -f "$ACCUMULATED_GAMES_FILE" ] || return 1
	[ -f "${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" ] || return 1
	[ ! -f "$IMPROVE_LOCK_FILE" ] || return 1
	[ ! -f "$TMP_STATE_DIR/rate_limit_backoff" ] || return 1
	! _is_improve_running || return 1
	local _fresh_active_branch=0
	if _has_active_branch; then
		_fresh_active_branch=1
	fi

	# Keep the objective reference fresh before probing. A just-promoted mature
	# Russia/T15 anchor can otherwise appear only after this check and delay an
	# obvious R0 high-frontier miss by one extra game.
	command -v _refresh_best_strategy_anchor >/dev/null 2>&1 &&
		_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true
	local _fresh_archive_restart_preflight=0
	if _archive_restart_should_run 999; then
		_fresh_archive_restart_preflight=1
	fi
	local _fresh_probe _fresh_ok _fresh_rest _fresh_n _seeded_n _hist_russia _fresh_russia _fresh_best _fresh_t14_peak _fresh_hash _fresh_trigger _fresh_acc_count _fresh_anchor_russia _fresh_anchor_best _fresh_reference
	_fresh_probe=$(python3 - \
		"$ACCUMULATED_GAMES_FILE" \
		"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
		"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" \
		"${_fresh_archive_restart_preflight:-0}" \
		"${CURRENT_RUN_FRESH_OBJECTIVE_REGRESSION_MIN_GAMES:-2}" \
		"${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_MIN_BEST_TYPE:-14}" \
		"${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MIN_GAMES:-3}" \
		"${CURRENT_RUN_FRESH_OBJECTIVE_SAME_HASH_LOW_STAGE_MAX_BEST_TYPE:-13}" <<'PY' 2>/dev/null || echo "0:0:0:0:0:0:0:0:none:0:0:0:none"
import json
import os
import sys

acc_file, current_file, anchor_file, archive_restart_available_raw, min_games_raw, min_best_raw, low_stage_min_raw, low_stage_max_raw = sys.argv[1:9]

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def archive_progress(path):
    best = 0
    russia = False
    soviet = False
    peak_counts = {}
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
                russia = russia or bool(row.get("russia_created")) or bool(row.get("russia_announced"))
                soviet = soviet or bool(row.get("soviet_created")) or bool(row.get("soviet_announced"))
                pieces = ((row.get("state_snapshot") or {}).get("pieces") or [])
                counts = {}
                for piece in pieces:
                    try:
                        t = int((piece or {}).get("type", 0) or 0)
                    except Exception:
                        continue
                    best = max(best, t)
                    counts[t] = counts.get(t, 0) + 1
                for t, count in counts.items():
                    if t >= 10:
                        peak_counts[t] = max(peak_counts.get(t, 0), count)
    except Exception:
        pass
    if best >= 15:
        russia = True
    if best >= 16:
        soviet = True
    return best, int(russia), int(soviet), peak_counts

acc = load(acc_file)
current = load(current_file)
anchor = load(anchor_file)
min_games = max(1, as_int(min_games_raw, 4))
min_best = max(1, as_int(min_best_raw, 14))
low_stage_min_games = max(1, as_int(low_stage_min_raw, 3))
low_stage_max_best = max(1, as_int(low_stage_max_raw, 13))
sample_floor = min(min_games, low_stage_min_games)
acc_hash = str(acc.get("hash") or "")
current_hash = str(current.get("hash") or "")
acc_count = as_int(acc.get("count", 0))
seeded_n = as_int(current.get("_seeded_score_count", 0))
fresh_n = as_int(current.get("_fresh_score_count", 0))
historical_russia = as_int(current.get("russia_count", 0))
historical_best = as_int(current.get("best_max_type", 0))
anchor_russia = as_int(anchor.get("russia_count", 0))
anchor_best = as_int(anchor.get("best_max_type", 0))
archive_restart_available = as_int(archive_restart_available_raw)
objective_reference = "none"
if historical_russia > 0:
    objective_reference = "historical_russia"
elif historical_best >= 15:
    objective_reference = "historical_best"
elif anchor_russia > 0:
    objective_reference = "anchor_russia"
elif anchor_best >= 15:
    objective_reference = "anchor_best"
elif archive_restart_available > 0:
    objective_reference = "archive_restart_candidate"

eligible = False
fresh_best = 0
fresh_russia = 0
fresh_t14_peak = 0
trigger = "none"
if (
    acc_hash
    and current_hash
    and acc_hash == current_hash
    and fresh_n >= sample_floor
    and acc_count >= sample_floor
    and objective_reference != "none"
):
    archives = [str(x) for x in (current.get("_recent_archives") or []) if str(x)]
    fresh_archives = archives[-fresh_n:] if fresh_n > 0 else []
    if fresh_archives:
        for path in fresh_archives:
            best, russia, soviet, peaks = archive_progress(path)
            fresh_best = max(fresh_best, best)
            fresh_russia += int(bool(russia))
            fresh_t14_peak = max(fresh_t14_peak, int(peaks.get(14, 0) or 0))
    else:
        max_types = [as_int(x) for x in (current.get("max_types") or [])]
        fresh_types = max_types[-fresh_n:] if fresh_n > 0 else []
        fresh_best = max(fresh_types or [0])
        fresh_russia = sum(1 for value in fresh_types if value >= 15)
    frontier_min_games = min_games
    if objective_reference in {"historical_russia", "historical_best", "anchor_russia", "anchor_best"}:
        # When we have a proven Russia/T15 objective reference, a fresh R0 batch that
        # already reaches the T14 frontier is enough evidence at the low-stage sample
        # size. Waiting for the full frontier window burns an extra game on a known
        # no-Russia miss.
        frontier_min_games = min(min_games, low_stage_min_games)
    high_frontier_miss = bool(
        fresh_russia <= 0
        and fresh_n >= frontier_min_games
        and acc_count >= frontier_min_games
        and fresh_best >= min_best
    )
    low_stage_miss = bool(
        fresh_russia <= 0
        and fresh_n >= low_stage_min_games
        and acc_count >= low_stage_min_games
        and 0 < fresh_best <= low_stage_max_best
    )
    if high_frontier_miss:
        trigger = "high_frontier_miss"
    elif low_stage_miss:
        trigger = "low_stage_miss"
    eligible = bool(high_frontier_miss or low_stage_miss)

print(
    f"{1 if eligible else 0}:{fresh_n}:{seeded_n}:{historical_russia}:"
    f"{fresh_russia}:{fresh_best}:{fresh_t14_peak}:{current_hash}:{trigger}:{acc_count}:"
    f"{anchor_russia}:{anchor_best}:{objective_reference}"
)
PY
	)
	_fresh_ok="${_fresh_probe%%:*}"
	_fresh_rest="${_fresh_probe#*:}"
	_fresh_n="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_seeded_n="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_hist_russia="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_russia="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_best="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_t14_peak="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_hash="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_trigger="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_acc_count="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_anchor_russia="${_fresh_rest%%:*}"
	_fresh_rest="${_fresh_rest#*:}"
	_fresh_anchor_best="${_fresh_rest%%:*}"
	_fresh_reference="${_fresh_rest##*:}"
	[ "$_fresh_ok" = "1" ] || return 1
	if [ "$_fresh_active_branch" = "1" ] &&
		[ "$_fresh_archive_restart_preflight" != "1" ] &&
		[ "$_fresh_trigger" != "high_frontier_miss" ] &&
		[ "$_fresh_trigger" != "low_stage_miss" ]; then
		return 1
	fi

	local _fresh_improve_reason="normal" _fresh_archive_restart_available="${_fresh_archive_restart_preflight:-0}"
	if [ "${_fresh_archive_restart_available:-0}" = "1" ]; then
		_fresh_improve_reason="archive_restart"
	fi

	log "[FRESH_OBJECTIVE] 現行hashのfresh目的再評価でR0 (hash=${_fresh_hash:0:8} fresh=${_fresh_n}/${MIN_GAMES_BEFORE_IMPROVE:-12} seeded=${_seeded_n} ref=${_fresh_reference} histR=${_hist_russia} anchorR=${_fresh_anchor_russia} anchorBest=T${_fresh_anchor_best} fresh_best=T${_fresh_best} T14peak=${_fresh_t14_peak} trigger=${_fresh_trigger} route=${_fresh_improve_reason}) → 早期改善ロック作成"
	enrich_accumulated_game_metadata "$ACCUMULATED_GAMES_FILE" 2>/dev/null || true
	cp "$ACCUMULATED_GAMES_FILE" "$IMPROVE_LOCK_FILE"
	enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE" 2>/dev/null || true
	FRESH_OBJECTIVE_SAMPLE_N="${_fresh_n:-0}" \
		FRESH_OBJECTIVE_SEEDED_N="${_seeded_n:-0}" \
		FRESH_OBJECTIVE_HIST_RUSSIA="${_hist_russia:-0}" \
		FRESH_OBJECTIVE_ANCHOR_RUSSIA="${_fresh_anchor_russia:-0}" \
		FRESH_OBJECTIVE_ANCHOR_BEST="${_fresh_anchor_best:-0}" \
		FRESH_OBJECTIVE_FRESH_RUSSIA="${_fresh_russia:-0}" \
		FRESH_OBJECTIVE_FRESH_BEST="${_fresh_best:-0}" \
		FRESH_OBJECTIVE_T14_PEAK="${_fresh_t14_peak:-0}" \
		FRESH_OBJECTIVE_TRIGGER="${_fresh_trigger:-}" \
		FRESH_OBJECTIVE_REFERENCE="${_fresh_reference:-none}" \
		FRESH_OBJECTIVE_IMPROVE_REASON="${_fresh_improve_reason:-normal}" \
		FRESH_OBJECTIVE_ARCHIVE_RESTART_AVAILABLE="${_fresh_archive_restart_available:-0}" \
		python3 - "$IMPROVE_LOCK_FILE" <<'PY' 2>/dev/null || true
import json
import os
import sys
import time

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    data = json.load(f)

def as_int(name):
    try:
        return int(os.environ.get(name, "0") or 0)
    except Exception:
        return 0

data["started_at"] = int(time.time())
data["improve_reason"] = os.environ.get("FRESH_OBJECTIVE_IMPROVE_REASON", "normal") or "normal"
data["fresh_objective_same_hash_lock"] = True
data["fresh_objective_reason"] = "current_hash_fresh_no_russia"
data["fresh_objective_sample_n"] = as_int("FRESH_OBJECTIVE_SAMPLE_N")
data["fresh_objective_seeded_score_count"] = as_int("FRESH_OBJECTIVE_SEEDED_N")
data["fresh_objective_historical_russia_count"] = as_int("FRESH_OBJECTIVE_HIST_RUSSIA")
data["fresh_objective_anchor_russia_count"] = as_int("FRESH_OBJECTIVE_ANCHOR_RUSSIA")
data["fresh_objective_anchor_best_max_type"] = as_int("FRESH_OBJECTIVE_ANCHOR_BEST")
data["fresh_objective_fresh_russia_count"] = as_int("FRESH_OBJECTIVE_FRESH_RUSSIA")
data["fresh_objective_fresh_best_max_type"] = as_int("FRESH_OBJECTIVE_FRESH_BEST")
data["fresh_objective_t14_peak"] = as_int("FRESH_OBJECTIVE_T14_PEAK")
data["fresh_objective_trigger"] = os.environ.get("FRESH_OBJECTIVE_TRIGGER", "")
data["fresh_objective_reference"] = os.environ.get("FRESH_OBJECTIVE_REFERENCE", "none") or "none"
data["fresh_objective_archive_restart_available"] = bool(as_int("FRESH_OBJECTIVE_ARCHIVE_RESTART_AVAILABLE"))
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f)
PY
	# 視聴者向けシグナル: 早期改善トリガーをチャット＋画面通知で平易に告知（中華AI視点）。
	# 技術詳細(hash/reference/route)は上の [FRESH_OBJECTIVE] ログとlockファイルに残るので、
	# ここでは視聴者に伝わる文言にする。
	local _fresh_country
	case "${_fresh_best:-0}" in
		16) _fresh_country="ソ連" ;;
		15) _fresh_country="ロシア" ;;
		14) _fresh_country="カザフ" ;;
		13) _fresh_country="ウクライナ" ;;
		12) _fresh_country="ベラルーシ" ;;
		11) _fresh_country="トルクメン" ;;
		*) _fresh_country="最高T${_fresh_best:-0}" ;;
	esac
	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh worker "中華AI 早期改善を決断 (game ${GAME_NUM:-?})" "直近${_fresh_n}試合は最高${_fresh_country}でロシア建国0。建国実績のある安定版に追いつけていないため、通常の${MIN_GAMES_BEFORE_IMPROVE:-12}試合を待たず戦略を練り直します。" "warn" >/dev/null 2>&1 || true
	fi
	if command -v enqueue_chat_message >/dev/null 2>&1; then
		enqueue_chat_message "中華AI、戦略を早期見直し！直近${_fresh_n}試合は最高${_fresh_country}でロシア建国に届かず。建国実績のある安定版に追いつけていないので、通常の${MIN_GAMES_BEFORE_IMPROVE:-12}試合を待たずに改善を始めます" "fresh_objective" 4 || true
	fi
	_clear_accumulated_data
	return 0
}

enrich_accumulated_game_metadata() {
	local target_file="${1:-$ACCUMULATED_GAMES_FILE}"
	[ -f "$target_file" ] || return 0
	python3 - "$target_file" <<'PY' 2>/dev/null || true
import json
import os
import sys

target = sys.argv[1]
try:
    data = json.load(open(target, encoding="utf-8"))
except Exception:
    raise SystemExit(0)

files = [str(x) for x in data.get("files", []) or []]
if not files:
    raise SystemExit(0)

def summarize_archive(path):
    max_type = 0
    peak_type_counts = {}
    deadline_guard_count = 0
    if not path or not os.path.exists(path):
        return max_type, "no-high-type", "none", deadline_guard_count
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
                pieces = ((row.get("state_snapshot") or {}).get("pieces") or [])
                for piece in pieces:
                    try:
                        t = int(piece.get("type", 0) or 0)
                    except Exception:
                        continue
                    max_type = max(max_type, t)
                    if t >= 10:
                        same_type_count = 0
                        for p in pieces:
                            try:
                                if int((p or {}).get("type", 0) or 0) == t:
                                    same_type_count += 1
                            except Exception:
                                pass
                        peak_type_counts[t] = max(peak_type_counts.get(t, 0), same_type_count)
    except Exception:
        pass
    peak_counts = " ".join(f"T{t}x{peak_type_counts[t]}" for t in sorted(peak_type_counts, reverse=True)[:4]) or "none"
    frontier_hint = "no-high-type"
    if max_type >= 10:
        frontier_hint = f"T{max_type}_peak={peak_type_counts.get(max_type, 0)} prev_T{max_type - 1}_peak={peak_type_counts.get(max_type - 1, 0)}"
    return max_type, frontier_hint, peak_counts, deadline_guard_count

progress = [summarize_archive(path) for path in files]
data["max_types"] = [item[0] for item in progress]
data["frontier_hints"] = [item[1] for item in progress]
data["peak_high_type_counts"] = [item[2] for item in progress]
data["deadline_guard_counts"] = [item[3] for item in progress]
data["best_max_type"] = max(data["max_types"] or [0])
data["deadline_guard_total"] = sum(data["deadline_guard_counts"])

tmp = target + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(data, f)
os.replace(tmp, target)
PY
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
	# 予想もサイクルに連動: 蓄積リセット時に現予想を確定し、次サイクルで新規作成させる
	# (粛清やソ連建国で既にresolve済みの場合はファイルが消えているのでスキップされる)
	if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
		# 粛清フラグがある場合は best_outcome=3 に強制（レース対策）
		# soren_loop / improve.sh の粛清検出ブロックが check_regression 後に即書き込む
		if [ -f "$TMP_STATE_DIR/regression_pending" ]; then
			python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
try:
    d=json.load(open(f))
    if d.get('best_outcome',0) < 3:
        d['best_outcome']=3
        json.dump(d,open(f,'w'))
except Exception:
    pass
" 2>/dev/null || true
			rm -f "$TMP_STATE_DIR/regression_pending"
		fi
		# prediction_worker が state file を監視して cleanup する
	else
		rm -f "$TMP_STATE_DIR/regression_pending" 2>/dev/null || true
	fi
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
    "_seeded_score_count": 0,
    "_fresh_score_count": 0,
    "_fresh_games_total": 0,
    "_recent_archives": [],
    "frontier_hints": [],
    "peak_high_type_counts": [],
    "deadline_guard_counts": [],
    "deadline_guard_reason_tops": [],
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
seed_scores = scores[-20:]
payload = {
    "hash": strategy_hash,
    "scores": seed_scores,
    "games_total": int(entry.get("games_total", len(scores)) or len(scores)),
    "_seeded_score_count": len(seed_scores),
    "_fresh_score_count": 0,
    "_fresh_games_total": 0,
    "_recent_archives": recent_archives[-50:],
    "max_types": (entry.get("max_types", []) or [])[-20:],
    "russia_count": int(entry.get("russia_count", 0) or 0),
    "soviet_count": int(entry.get("soviet_count", 0) or 0),
    "best_max_type": int(entry.get("best_max_type", 0) or 0),
    "frontier_hints": (entry.get("frontier_hints", []) or [])[-20:],
    "peak_high_type_counts": (entry.get("peak_high_type_counts", []) or [])[-20:],
    "deadline_guard_counts": (entry.get("deadline_guard_counts", []) or [])[-20:],
    "deadline_guard_reason_tops": (entry.get("deadline_guard_reason_tops", []) or [])[-20:],
}
if payload["best_max_type"] >= 15 and payload["russia_count"] <= 0:
    payload["russia_count"] = 1
if payload["best_max_type"] >= 16 and payload["soviet_count"] <= 0:
    payload["soviet_count"] = 1
with open(out_file, "w") as f:
    json.dump(payload, f)
PY
}

_update_current_strategy_run() {
	local strategy_hash="$1" score="$2" archive_file="${3:-}"
	[ -n "$strategy_hash" ] || return 1
	local run_result="" run_err=""
	run_err="${TMP_STATE_DIR:-tmp/state}/current_run_update.err"
	run_result=$(python3 - "$CURRENT_STRATEGY_RUN_FILE" "$strategy_hash" "$score" "$archive_file" "${CURRENT_RUN_SCORE_KEEP:-20}" "${HOT_STREAK_CURRENT_RUN_KEEP:-200}" "${HOT_STREAK_EXTEND_ENABLED:-1}" 2>"$run_err" <<'PY'
import json
import os
import sys

run_file, strategy_hash, score, archive_file = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
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
        "_seeded_score_count": 0,
        "_fresh_score_count": 0,
        "_fresh_games_total": 0,
        "_recent_archives": [],
        "frontier_hints": [],
        "peak_high_type_counts": [],
        "deadline_guard_counts": [],
        "deadline_guard_reason_tops": [],
    }

recent_archives = run.get("_recent_archives", [])
if not isinstance(recent_archives, list):
    recent_archives = []

if archive_file and archive_file in recent_archives:
    print(f"{strategy_hash}|{len(run.get('scores', []))}|{int(run.get('games_total', 0) or 0)}|dedup")
    raise SystemExit

scores = [int(x) for x in run.get("scores", [])]
try:
    seeded_score_count = max(0, int(run.get("_seeded_score_count", 0) or 0))
except Exception:
    seeded_score_count = 0
seeded_score_count = min(seeded_score_count, len(scores))
prev_best = max(scores) if scores else None
scores.append(score)
keep = hot_keep if hot_enabled and prev_best is not None and score > prev_best else normal_keep
dropped_scores = max(0, len(scores) - keep)
run["scores"] = scores[-keep:]
run["_seeded_score_count"] = max(0, min(len(run["scores"]), seeded_score_count - dropped_scores))
run["_fresh_score_count"] = max(0, len(run["scores"]) - int(run.get("_seeded_score_count", 0) or 0))
run["_fresh_games_total"] = int(run.get("_fresh_games_total", 0) or 0) + 1
run["games_total"] = int(run.get("games_total", 0) or 0) + 1

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

if archive_file:
    recent_archives.append(archive_file)
    recent_archives = recent_archives[-50:]
run["_recent_archives"] = recent_archives
progress_archives = recent_archives[-len(run["scores"]):] if run["scores"] else []
progress = [nation_progress(path) for path in progress_archives]
run["max_types"] = [item[0] for item in progress]
run["best_max_type"] = max([int(run.get("best_max_type", 0) or 0)] + [item[0] for item in progress])
run["russia_count"] = max(int(run.get("russia_count", 0) or 0), sum(1 for item in progress if item[1]))
run["soviet_count"] = max(int(run.get("soviet_count", 0) or 0), sum(1 for item in progress if item[2]))
if run["best_max_type"] >= 15 and run["russia_count"] <= 0:
    run["russia_count"] = 1
if run["best_max_type"] >= 16 and run["soviet_count"] <= 0:
    run["soviet_count"] = 1
run["frontier_hints"] = [item[3] for item in progress]
run["peak_high_type_counts"] = [item[4] for item in progress]
run["deadline_guard_counts"] = [item[5] for item in progress]
run["deadline_guard_reason_tops"] = [item[6] for item in progress]

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
			[ -s "$run_err" ] && log "[CURRENT-RUN] update stderr: $(tr '\n' ' ' <"$run_err" | cut -c1-500)"
		fi
		rm -f "$run_err" 2>/dev/null || true
	}

_is_rank1_hot_streak() {
	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	[ -n "$current_hash" ] || return 1
	python3 - "$ROLLING_SCORES_FILE" "$CURRENT_STRATEGY_RUN_FILE" "$BEST_STRATEGY_ANCHOR_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" <<'PY' >/dev/null 2>&1
import json
import math
import os
import sys

rolling_file, current_run_file, anchor_file, current_hash, min_games_raw = sys.argv[1:6]
try:
    min_games = int(min_games_raw)
except Exception:
    min_games = 12

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
    xs = [int(x) for x in scores]
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n) if n > 1 else 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "p50": p50, "p25": p25, "lcb": lcb, "n": n}

def key(m):
    if not m:
        return (-10**18, -10**18, -10**18, -10**18)
    return (float(m.get("comp", 0.0)), float(m.get("p50", 0.0)), float(m.get("p25", 0.0)), int(m.get("n", 0)))

def scores_from(entry):
    out = []
    for raw in (entry or {}).get("scores", []) or []:
        try:
            out.append(int(raw))
        except Exception:
            pass
    return out

rolling = load_json(rolling_file)
run = load_json(current_run_file)
current_scores = scores_from(run if str(run.get("hash", "") or "") == current_hash else rolling.get(current_hash, {}))
if len(current_scores) < min_games:
    raise SystemExit(1)

# "更新し続けている" は直近ゲームがこの current run の評価スコア自己ベストを
# 厳密に更新したこと。同点では延長しない。
if len(current_scores) < 2 or current_scores[-1] <= max(current_scores[:-1]):
    raise SystemExit(1)

current_metrics = metrics(current_scores)
if not current_metrics:
    raise SystemExit(1)

ranked = []
for h, data in rolling.items():
    scores = scores_from(data)
    if len(scores) < min_games:
        continue
    ranked.append((key(metrics(scores)), h))

anchor = load_json(anchor_file)
anchor_hash = str(anchor.get("hash", "") or "")
anchor_metrics = {
    "comp": float(anchor.get("comp", 0.0) or 0.0),
    "p50": float(anchor.get("p50", 0.0) or 0.0),
    "p25": float(anchor.get("p25", 0.0) or 0.0),
    "lcb": float(anchor.get("lcb", 0.0) or 0.0),
    "n": int(anchor.get("n", 0) or 0),
} if anchor_hash else None
if anchor_metrics:
    ranked.append((key(anchor_metrics), anchor_hash))
ranked.append((key(current_metrics), current_hash))
ranked.sort(reverse=True)

top_hash = ranked[0][1] if ranked else ""
raise SystemExit(0 if top_hash == current_hash else 1)
PY
}

_rolling_keep_limit_for_hash() {
	local target_hash="$1"
	if [ "${HOT_STREAK_EXTEND_ENABLED:-1}" = "1" ] && [ -n "$target_hash" ] && _is_rank1_hot_streak; then
		echo "${HOT_STREAK_ROLLING_KEEP:-200}"
	else
		echo "${ROLLING_SCORE_KEEP:-20}"
	fi
}


record_completed_game_for_adaptive_improvement() {
	local archive_file="$1" score="$2" soviet="$3" russia="${4:-false}"
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

	if [ -n "$played_hash" ] && [ -n "$current_hash" ] && [ "$played_hash" != "$current_hash" ]; then
		log "[IMPROVE] current戦略と異なる試合を検出: played=${played_hash:0:8} current=${current_hash:0:8} → queuedをリセットしてこの試合は蓄積しない"
		_clear_accumulated_data
		_reset_current_strategy_run "$current_hash"
	else
		update_rolling_scores "$score" "$archive_file"
		if [ -n "$current_hash" ]; then
			_update_current_strategy_run "$current_hash" "$score" "$archive_file"
		fi
		accumulate_game_data "$archive_file" "$score" "$soviet" "$played_hash" "$russia"
		queue_fresh_objective_same_hash_lock_if_needed || true
	fi
	if [ "${CURRENT_RUN_AUTO_REPAIR_ENABLED:-1}" = "1" ] && [ -x ./repair_current_run_from_history.sh ]; then
		./repair_current_run_from_history.sh "${CURRENT_RUN_AUTO_REPAIR_LIMIT:-12}" >/dev/null 2>&1 ||
			log "[CURRENT-RUN] auto repair skipped/failed in adaptive bookkeeping"
	fi

	if ! _has_active_branch; then
		_refresh_best_strategy_anchor "" >/dev/null 2>&1 || true
	fi
}

_start_improvement_job() {
	local all_history_files="$1" all_scores="$2" any_soviet="$3" acc_count="$4" reason="$5"

	# 手動改善モード: プロセスを起動せず待機状態にする
	if [[ -f "$TMP_STATE_DIR/manual_improve_mode" ]]; then
		local strategy_hash
		strategy_hash=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")
		_write_improve_state "manual" "0" "$strategy_hash" "manual_wait" "0" "手動改善待ち" "$(date +%s)"
		log "[IMPROVE] 手動改善モード: strategy.py を編集後 ./manual_improve_off.sh を実行してください"
		log "[IMPROVE] 手動改善待ちは実改善PIDがないため soren91 代打は起動しない"
		# OBS: 改善中オーバーレイ表示
		_improve_overlay_show
		return 0
	fi

	# spawn 排他 mutex 取得 (dual-spawner 二重起動レース防止)
	# 別 spawner が spawn 中なら return 1 でスキップ (success 扱いにせず
	# caller の last_improve_failed_at クリアを誤発火させない)
	if ! _acquire_spawn_lock; then
		log "[IMPROVE] spawn lock を別 spawner が保持中 → 二重起動回避でスキップ"
		return 1
	fi
	# The pre-mutex status check is not sufficient: both soren_loop and
	# improve_daemon can observe idle while one of them is still preparing the
	# batch. Re-check under the mutex before touching an existing worker or any
	# shared staging/result files.
	if _improve_spawn_state_blocks_start; then
		log "[IMPROVE] spawn lock 取得後のstate再確認で既存running/manualを検出 → 後発起動をスキップ"
		_release_spawn_lock
		return 1
	fi

	# 既存の eloop_improve プロセスが残っていないか確認
	# 通常scriptだけでなく、実運用で固定実行する runtime snapshot も対象。
	# 自プロセス除外 (grep -v $$) で誤殺防止。
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve(_runtime\.[^ ]+)?\.sh" 2>/dev/null | grep -vw "$$" || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | while read -r spid; do
			kill "$spid" 2>/dev/null || true
		done
		sleep 1
	fi

	if [ "$reason" = "post_regression" ]; then
		log "[IMPROVE] 回帰ロールバック直後の即時改善を開始"
	else
		log "[IMPROVE] ${acc_count}試合分のデータで改善開始"
	fi

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(_strategy_decide_hash_or_md5 "$STRATEGY_FILE")
	local improve_ai_log="$IMPROVE_AI_LOG_FILE"
	mkdir -p "$(dirname "$improve_ai_log")" 2>/dev/null || true
	: >"$improve_ai_log"
	_persist_improve_lock_reason "$reason"
	if ! _snapshot_improve_retry_batch "$IMPROVE_LOCK_FILE"; then
		log "[IMPROVE] WARNING: retry batch snapshot failed; original improve.lock remains authoritative"
	fi
	printf '[%s] [IMPROVE] job start reason=%s game=%s scores=%s\n' \
		"$(date '+%H:%M:%S')" "$reason" "${GAME_NUM:-?}" "${all_scores:-}" >>"$improve_ai_log" 2>/dev/null || true
	_improve_overlay_generate_once
	if [ -x ./overlay_notify.sh ]; then
		local _ov_anchor
		_ov_anchor=$(python3 -c "import json;d=json.load(open('${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}'));print('%s comp=%.0f'%(d.get('hash','?')[:8],d.get('comp',0)))" 2>/dev/null || echo "")
		./overlay_notify.sh worker "改善開始 (${reason})" "reason=${reason} game=${GAME_NUM:-?} hash=${strategy_hash} acc=${acc_count:-?}${_ov_anchor:+ | anchor=${_ov_anchor}} scores=${all_scores:-}" "info" >/dev/null 2>&1 || true
	fi

	# デーモンコンテキストではファイルからフォールバック読み取り
	[ "${GAME_NUM:-0}" -eq 0 ] && GAME_NUM=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ "${LAST_TURNS:-0}" -eq 0 ] && LAST_TURNS=$(cat "tmp/state/last_turns.txt" 2>/dev/null || echo 0)

	# バックグラウンド改善開始 (reason は wildcard モード判定のため eloop_improve.sh に伝搬)
	# 実行中にCodex/人間が eloop_improve.sh を編集しても、bash が後半を読み直して
	# ジョブを壊さないよう、起動時点のスナップショットを固定実行する。
	local runtime_root runtime_dir runtime_script
	runtime_root="$PWD"
	runtime_dir="${TMP_STATE_DIR:-tmp/state}"
	case "$runtime_dir" in
	/*) ;;
	*) runtime_dir="$runtime_root/$runtime_dir" ;;
	esac
	mkdir -p "$runtime_dir" 2>/dev/null || true
	runtime_script=$(mktemp "$runtime_dir/eloop_improve_runtime.XXXXXX.sh" 2>/dev/null || true)
	if [ -n "$runtime_script" ] && cp ./eloop_improve.sh "$runtime_script" 2>/dev/null; then
		chmod +x "$runtime_script" 2>/dev/null || true
	else
		runtime_script="$runtime_root/eloop_improve.sh"
	fi
	if ! bash -n "$runtime_script" 2>>"$improve_ai_log"; then
		log "[IMPROVE] eloop runtime snapshot syntax invalid → 起動中止 (${runtime_script})"
		case "$runtime_script" in
		"$runtime_root"/tmp/state/eloop_improve_runtime.*.sh) rm -f "$runtime_script" 2>/dev/null || true ;;
		esac
		_release_spawn_lock
		return 1
	fi
	RUN_CMD_LOG_FILE="$improve_ai_log" SOREN_SCRIPT_ROOT="$runtime_root" ELOOP_RUNTIME_SCRIPT_FILE="$runtime_script" bash "$runtime_script" "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" "$reason" &
	IMPROVE_PID=$!
	local _pid_birth_epoch
	_pid_birth_epoch=$(ps -p "$IMPROVE_PID" -o lstart= 2>/dev/null | xargs -I{} date -j -f "%a %b %d %H:%M:%S %Y" "{}" +%s 2>/dev/null || echo 0)

	# 起動成功を確認してから状態更新
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		local _overlay_pid=""
		rm -f "$TMP_STATE_DIR/handover_announced" 2>/dev/null || true
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash" "boot" "1" "job_started" "$(date +%s)" "$_pid_birth_epoch" "$reason"
		_overlay_pid=$(_improve_overlay_watch_start "$IMPROVE_PID")
		# spawn 完了・state=running 書込済 → 既存ガードが他 spawner を弾くので
		# spawn mutex の役目は終了。daemon-mode の長い inline wait の前に解放。
		_release_spawn_lock
		if [ "$reason" = "post_regression" ]; then
			log "[IMPROVE] 回帰ロールバック後の改善開始 (PID=$IMPROVE_PID, base=${REGRESSION_ROLLBACK_HASH:-unknown})"
		elif [ "${IMPROVE_DAEMON_MODE:-0}" = "1" ]; then
			log "[IMPROVE] デーモンモード: フォアグラウンド実行開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		else
			log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		fi
		# OBS: 改善中オーバーレイ表示
		_improve_overlay_show
		# WILDCARD / archive_restart は AI を介さない脱出処理のため、
		# soren91 代打/ミュート/OBS切替/完了時bridge再起動 を一切起こさない。
		# (post-escape の bridge 再起動が commands 経路 desync=空転の発生源だった)
		if _improve_keep_main_game_running; then
			log "[IMPROVE] 継続プレイ設定: soren91代打を起動せず、メインゲームと改善を並行実行"
		elif [ "$reason" = "wildcard" ] || [ "$reason" = "archive_restart" ]; then
			log "[WILDCARD] ${reason} は高速脱出(AI不使用・短時間)互換の隔離脱出処理のため soren91 代打/PAUSE/bridge再起動 を全スキップ"
		else
			# soren91 (メリケンAI) を起動 — 中華AI改善中の代打プレイ
			soren91_start
			if command -v soren91_is_running >/dev/null 2>&1 && soren91_is_running 2>/dev/null; then
				# Twitch チャットに戦略改善開始を通知
				enqueue_chat_message "中華AIが戦略を改善中。その間、メリケンAIがソ連ゲーム91で同志を迎え撃ちます。挑戦お待ちしています ソ連ゲーム91 - たアケイク https://unityroom.com/games/sorengame91" "improve"
			else
				log "[IMPROVE] soren91 は停止処理中のため起動通知をスキップ"
			fi
		fi
		# デーモンモードではフォアグラウンド実行（完了まで wait → 即 harvest 可能になる）
		# run_cmd が stdout/stderr をログファイルにリダイレクトするため、
		# tail -f でログをターミナルに中継する
		if [ "${IMPROVE_DAEMON_MODE:-0}" = "1" ]; then
			tail -n +1 -f "$improve_ai_log" &
			local _tail_pid=$!
			wait "$IMPROVE_PID"
			local _wait_rc=$?
			kill "$_tail_pid" 2>/dev/null; wait "$_tail_pid" 2>/dev/null || true
			if [ -n "$_overlay_pid" ]; then
				kill "$_overlay_pid" 2>/dev/null; wait "$_overlay_pid" 2>/dev/null || true
			fi
			_improve_overlay_generate_once
			log "[IMPROVE] フォアグラウンド実行完了 (PID=$IMPROVE_PID, rc=${_wait_rc})"
			# daemon mode: wait 完了後に即 harvest して状態を idle に遷移
			# (次の poll で "running"+死PID を拾って繰り返し発火するのを防ぐ)
			check_and_harvest_improvement
		fi
		return 0
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死)"
		IMPROVE_PID=0
		_release_spawn_lock
		return 1
	fi
}

trigger_adaptive_improvement() {
	type reload_runtime_toggles >/dev/null 2>&1 && reload_runtime_toggles
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] trigger_adaptive_improvementをスキップ（建国後停止中）"
		return
	fi

	# ロックファイルが存在しない場合は何もしない（メインループが作成する）
	[ -f "$IMPROVE_LOCK_FILE" ] || return 0
	if _main_strategy_runner_active_for_improve; then
		log "[IMPROVE] strategy_runner active → improve lock consumption deferred until game boundary"
		return 0
	fi

	# 既に改善プロセス中（状態ファイルで確認）
	local state status
	state=$(_read_improve_state)
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)
	if [ "$status" = "running" ] || [ "$status" = "manual" ]; then
		log "[IMPROVE] 改善中 (status=$status) → スキップ"
		return 0
	fi

	# レートリミット指数バックオフ
	if [ -f "$TMP_STATE_DIR/rate_limit_backoff" ]; then
		local _rl_count _rl_ts _rl_now _rl_wait _rl_exp _rl_last_log_file _rl_last_log _rl_log_interval
		_rl_count=$(sed -n '1p' "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || echo 1)
		_rl_ts=$(sed -n '2p' "$TMP_STATE_DIR/rate_limit_backoff" 2>/dev/null || echo 0)
		_rl_now=$(date +%s)
		_rl_exp=$((_rl_count - 1 > 5 ? 5 : _rl_count - 1))
		_rl_wait=$((300 * (1 << _rl_exp)))
		if [ $((_rl_now - _rl_ts)) -lt "$_rl_wait" ]; then
			_rl_last_log_file="$TMP_STATE_DIR/rate_limit_backoff_last_log"
			_rl_last_log=$(cat "$_rl_last_log_file" 2>/dev/null || echo 0)
			case "$_rl_last_log" in ''|*[!0-9]*) _rl_last_log=0 ;; esac
			_rl_log_interval="${IMPROVE_BACKOFF_LOG_INTERVAL_SEC:-900}"
			case "$_rl_log_interval" in ''|*[!0-9]*) _rl_log_interval=900 ;; esac
			if [ "$_rl_last_log" -le 0 ] || [ $((_rl_now - _rl_last_log)) -ge "$_rl_log_interval" ]; then
				log "[IMPROVE] rate-limit backoff中 (残$((_rl_wait - (_rl_now - _rl_ts)))秒, count=${_rl_count})"
				printf '%s\n' "$_rl_now" >"$_rl_last_log_file" 2>/dev/null || true
			fi
			return
		else
			log "[IMPROVE] rate-limit backoff終了 → リトライ許可"
			rm -f "$TMP_STATE_DIR/rate_limit_backoff" "$TMP_STATE_DIR/rate_limit_backoff_last_log"
		fi
	fi

	# ピーク時間帯回避ゲート: deepseek-v4-flash はピーク帯(UTC 01-04, 06-10)で2倍課金。
	# 早期returnしてもロック・蓄積データは失われない (rate_limit_backoff と同パターン)。
	if _improve_peak_gate_should_defer; then
		return 0
	fi

	# ロックファイルから蓄積データを読む
	local lock_data acc_count all_history_files all_scores any_soviet
	enrich_accumulated_game_metadata "$IMPROVE_LOCK_FILE" 2>/dev/null || true
	lock_data=$(cat "$IMPROVE_LOCK_FILE" 2>/dev/null) || {
		log "[IMPROVE] ロックファイル読み込み失敗 → スキップ"
		rm -f "$IMPROVE_LOCK_FILE"
		return 1
	}
	if ! printf '%s' "$lock_data" | python3 -m json.tool >/dev/null 2>&1; then
		log "[IMPROVE] ロックファイルが空または壊れているため削除"
		rm -f "$IMPROVE_LOCK_FILE"
		return 1
	fi
	acc_count=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null || echo 0)
	all_history_files=$(echo "$lock_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
	all_scores=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
	any_soviet=$(echo "$lock_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)

	local early_escape_batch_ok
	early_escape_batch_ok=$(LOCK_DATA="$lock_data" python3 - \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" \
		"${MIN_GAMES_BEFORE_IMPROVE:-12}" \
		"${MIN_GAMES_BEFORE_REGRESSION:-12}" \
		"${EARLY_COMP_TOP_GAP_MIN_RATIO:-0.85}" <<'PY' 2>/dev/null || echo "0:0:0:0:0"
import json
import math
import os
import sys
import time

rolling_file, stagnation_file, improve_min_raw, regression_min_raw, min_ratio_raw = sys.argv[1:6]

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
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

def comp(scores):
    xs = [as_int(x) for x in scores]
    if not xs:
        return 0.0
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n) if n > 1 else 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    return 0.55 * p50 + 0.30 * p25 + 0.15 * lcb

def row_comp(row):
    explicit = as_float(row.get("comp", 0.0), 0.0)
    if explicit > 0:
        return explicit
    scores = row.get("scores", [])
    if isinstance(scores, str):
        scores = [x for x in scores.split() if x.strip()]
    if isinstance(scores, list):
        return comp(scores)
    return 0.0

try:
    lock = json.loads(os.environ.get("LOCK_DATA", "") or "{}")
except Exception:
    lock = {}

count = as_int(lock.get("count", 0), 0)
improve_min = max(1, as_int(improve_min_raw, 12))
if not bool(lock.get("early_escape_lock", False)) or count >= improve_min:
    print("0:0:0:0:0")
    raise SystemExit

scores = [as_int(x) for x in str(lock.get("scores", "") or "").split() if str(x).strip()]
batch_comp = comp(scores)
regression_min = max(1, as_int(regression_min_raw, 12))
min_ratio = as_float(min_ratio_raw, 0.85)
rolling = load(rolling_file)
leader_comp = 0.0
for h, row in (rolling or {}).items():
    if not isinstance(row, dict):
        continue
    n = as_int(row.get("n", row.get("games_total", 0)), 0)
    if n < regression_min:
        continue
    leader_comp = max(leader_comp, row_comp(row))

ok = bool(batch_comp > 0 and (leader_comp <= 0 or batch_comp >= leader_comp * min_ratio))
if ok and stagnation_file:
    data = load(stagnation_file)
    data["regression_streak"] = 0
    data["last_event"] = "EARLY_ESCAPE_BATCH_OK"
    data["updated_at"] = int(time.time())
    try:
        os.makedirs(os.path.dirname(stagnation_file) or ".", exist_ok=True)
        tmp = stagnation_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, stagnation_file)
    except Exception:
        pass

ratio = (batch_comp / leader_comp) if leader_comp > 0 else 0.0
print(f"{1 if ok else 0}:{batch_comp:.1f}:{leader_comp:.1f}:{ratio:.3f}:{count}")
PY
	)
	if [ "${early_escape_batch_ok%%:*}" = "1" ]; then
		local _batch_quality_rest _batch_comp _leader_comp _quality_ratio _quality_count
		_batch_quality_rest="${early_escape_batch_ok#*:}"
		_batch_comp="${_batch_quality_rest%%:*}"
		_batch_quality_rest="${_batch_quality_rest#*:}"
		_leader_comp="${_batch_quality_rest%%:*}"
		_batch_quality_rest="${_batch_quality_rest#*:}"
		_quality_ratio="${_batch_quality_rest%%:*}"
		_quality_count="${_batch_quality_rest##*:}"
		log "[IMPROVE] early_escape lock ignored: current batch is not bad enough (count=${_quality_count}/${MIN_GAMES_BEFORE_IMPROVE:-12} comp=${_batch_comp} leader=${_leader_comp} ratio=${_quality_ratio}); continue normal accumulation"
		rm -f "$IMPROVE_LOCK_FILE"
		return 1
	fi

	# F: stagnation 連続発生時は wildcard モードに切替
	local improve_reason="normal"
	improve_reason=$(echo "$lock_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('improve_reason','normal'))" 2>/dev/null || echo "normal")
	case "$improve_reason" in
	normal|post_regression|wildcard|escape_ai|archive_restart) ;;
	*) improve_reason="normal" ;;
	esac
	if [ "$improve_reason" = "post_regression" ]; then
		if echo "$lock_data" | python3 -c 'import json,sys; print(1 if json.load(sys.stdin).get("post_regression_direct_escape") else 0)' 2>/dev/null | grep -qx 1; then
			log "[IMPROVE] ロールバック直後の直接脱出ロックを処理"
		else
			log "[IMPROVE] legacy post_regression lock: ロールバック前バッチ由来のため可能なら復帰先再評価へ移行"
		fi
	fi
	local russia_recovery_mode russia_recovery_reason
	russia_recovery_reason=""
	russia_recovery_mode=$(LOCK_DATA="$lock_data" python3 - \
		"${RUSSIA_CREATION_HISTORY_FILE:-tmp/history/russia_creation_history.tsv}" \
		"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
		"${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${MIN_GAMES_BEFORE_IMPROVE:-12}" \
		"${WILDCARD_REGRESSION_STREAK:-2}" <<'PY' 2>/dev/null || echo 0
import json
import os
import sys
import time
from datetime import datetime

russia_history, current_run_file, stagnation_file, rolling_file, mature_raw, threshold_raw = sys.argv[1:7]
try:
    mature_n = max(1, int(mature_raw))
except Exception:
    mature_n = 12
try:
    regression_threshold = max(1, int(threshold_raw))
except Exception:
    regression_threshold = 3
now = time.time()
last_russia = 0.0
try:
    with open(russia_history, encoding="utf-8", errors="ignore") as f:
        for raw in f:
            cols = raw.rstrip("\n").split("\t")
            if not cols or not cols[0]:
                continue
            try:
                dt = datetime.fromisoformat(cols[0])
                last_russia = max(last_russia, dt.timestamp())
            except Exception:
                pass
except Exception:
    pass
no_russia_24h = not last_russia or (now - last_russia) >= 24 * 3600

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

current = load(current_run_file)
rolling = load(rolling_file)
try:
    lock = json.loads(os.environ.get("LOCK_DATA", "") or "{}")
except Exception:
    lock = {}
stagnation = load(stagnation_file)
direct_escape = bool(lock.get("post_regression_direct_escape", False))
current_hash = str(current.get("hash") or "")
rolling_current = rolling.get(current_hash) if current_hash else {}
if not isinstance(rolling_current, dict):
    rolling_current = {}
lock_hash = str(lock.get("hash") or lock.get("strategy_hash") or "")
lock_matches_current = bool(current_hash and lock_hash and lock_hash == current_hash)
current_russia = int(current.get("russia_count", 0) or 0)
rolling_russia = int(rolling_current.get("russia_count", 0) or 0)
lock_russia = int(lock.get("russia_count", 0) or 0) if lock_matches_current else 0
current_games = int(current.get("games_total", 0) or len(current.get("scores", []) or []))
rolling_allowed = current_games < mature_n
rstreak = int(stagnation.get("regression_streak", 0) or 0)
known_russia = max(current_russia, lock_russia, rolling_russia if rolling_allowed else 0)
if direct_escape:
    print("1:post_regression_direct_escape")
elif no_russia_24h:
    print("1:no_russia_24h")
elif rstreak >= regression_threshold and known_russia <= 0:
    if current_games >= mature_n and rolling_russia > 0:
        print(f"1:revalidate_mature_no_current_russia n={current_games}/{mature_n} rolling_russia={rolling_russia}")
    else:
        print("1:regression_streak_no_russia")
else:
    print("0:")
PY
)
	russia_recovery_reason="${russia_recovery_mode#*:}"
	russia_recovery_mode="${russia_recovery_mode%%:*}"
	case "$russia_recovery_mode" in 1) ;; *) russia_recovery_mode=0 ;; esac
	if [ "$russia_recovery_mode" = "1" ]; then
		log "[IMPROVE] Russia recovery mode active (${russia_recovery_reason:-unknown}) → mechanical wildcard suppressed"
		_improve_flow_notify \
			"russia_path_dead" \
			"russia path still alive? no" \
			"reason=${russia_recovery_reason:-unknown}; route escape sequence starts" \
			"改善フロー: russia path still alive? no。ロシア進捗なしのため脱出ルーティングに入ります。" \
			"warn"
		if _archive_restart_should_run 999; then
			improve_reason="archive_restart"
			log "[IMPROVE] Russia recovery mode: ${russia_recovery_reason:-unknown} → archive_restart を即時優先"
			_improve_flow_notify \
				"archive_restart_candidate_yes" \
				"archive_restart candidate? yes" \
				"Russia-capable archive candidate available; improve_reason=archive_restart" \
				"改善フロー: archive_restart candidate? yes。評価済みアーカイブからの復帰を試します。" \
				"warn"
		elif [ "${WILDCARD_ENABLED:-0}" = "1" ]; then
			improve_reason="wildcard"
			log "[IMPROVE] Russia recovery mode: archive candidate unavailable → WILDCARD で構造変異候補を評価"
			_improve_flow_notify \
				"wildcard_frontier" \
				"wildcard frontier recovery possible? yes" \
				"archive candidate unavailable; WILDCARD_ENABLED=1; improve_reason=wildcard" \
				"改善フロー: archive_restart candidate? no。WILDCARDで構造変異候補を評価します。" \
				"warn"
		elif [ "${WILDCARD_AI_ESCALATE_ENABLED:-1}" = "1" ] && _escape_ai_seed_available; then
			improve_reason="escape_ai"
			log "[IMPROVE] Russia recovery mode: archive/wildcard unavailable → seeded escape_ai で復旧"
			_improve_flow_notify \
				"seeded_escape_ai_yes" \
				"seeded escape_ai candidate? yes" \
				"archive/wildcard unavailable; valid WILDCARD seed found; improve_reason=escape_ai" \
				"改善フロー: seeded escape_ai candidate exists? yes。評価済みseedからescape_aiを実行します。" \
				"warn"
		else
			log "[IMPROVE] Russia recovery mode: 有効なarchive/wildcard/seeded escape_aiなし → 通常AI改善で復旧"
			_improve_flow_notify \
				"no_valid_escape_route" \
				"fallback: no valid escape route" \
				"no archive candidate, wildcard disabled, no valid seeded escape_ai" \
				"改善フロー: seeded escape_ai candidate exists? no。有効な脱出先なしとしてfallback改善に入ります。" \
				"warn"
		fi
	fi
	local current_russia_progress current_russia_progress_reason
	current_russia_progress=$(LOCK_DATA="$lock_data" python3 - \
		"${CURRENT_STRATEGY_RUN_FILE:-tmp/state/current_strategy_run.json}" \
		"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
		"${MIN_GAMES_BEFORE_IMPROVE:-12}" <<'PY' 2>/dev/null || echo "0:"
import json
import os
import sys

current_run_file, rolling_file, mature_raw = sys.argv[1:4]
try:
    mature_n = max(1, int(mature_raw))
except Exception:
    mature_n = 12

def load(path):
    try:
        if path and os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}

def as_int(value):
    try:
        return int(value)
    except Exception:
        return 0

try:
    lock = json.loads(os.environ.get("LOCK_DATA", "") or "{}")
except Exception:
    lock = {}
current = load(current_run_file)
rolling = load(rolling_file)
current_hash = str(current.get("hash") or "")
rolling_current = rolling.get(current_hash) if current_hash else {}
if not isinstance(rolling_current, dict):
    rolling_current = {}
lock_hash = str(lock.get("hash") or lock.get("strategy_hash") or "")
lock_matches_current = bool(current_hash and lock_hash and lock_hash == current_hash)
current_russia = as_int(current.get("russia_count", 0))
rolling_russia = as_int(rolling_current.get("russia_count", 0))
lock_russia = as_int(lock.get("russia_count", 0)) if lock_matches_current else 0
current_best = as_int(current.get("best_max_type", 0))
rolling_best = as_int(rolling_current.get("best_max_type", 0))
lock_best = as_int(lock.get("best_max_type", 0)) if lock_matches_current else 0
current_games = as_int(current.get("games_total", len(current.get("scores", []) or [])))
rolling_allowed = current_games < mature_n
best_type = max(current_best, lock_best, rolling_best if rolling_allowed else 0)
russia = max(current_russia, lock_russia, rolling_russia if rolling_allowed else 0)
if russia > 0 or best_type >= 15:
    suffix = "rolling_protected" if rolling_allowed and (rolling_russia > 0 or rolling_best >= 15) else "current"
    print(f"1:russia={russia},best_type={best_type},n={current_games}/{mature_n},{suffix}")
else:
    if current_games >= mature_n and (rolling_russia > 0 or rolling_best >= 15):
        print(f"0:revalidate_mature_no_current_progress n={current_games}/{mature_n} rolling_russia={rolling_russia} rolling_best={rolling_best}")
    else:
        print("0:")
PY
)
	current_russia_progress_reason="${current_russia_progress#*:}"
	current_russia_progress="${current_russia_progress%%:*}"
	case "$current_russia_progress" in 1) ;; *) current_russia_progress=0 ;; esac
	if [ "$current_russia_progress" = "1" ] && { [ "$improve_reason" = "normal" ] || [ "$improve_reason" = "post_regression" ]; }; then
		log "[WILDCARD] current strategy has Russia progress (${current_russia_progress_reason:-unknown}) → mechanical wildcard suppressed"
	fi
	# 粛清カスケード中は毎サイクル post_regression で起動するため、ゲートを
	# normal 限定にすると WILDCARD(脱出弾)に構造的に永遠に入れない。
	# 回帰ストリーク/停滞が閾値超なら post_regression でも WILDCARD へ昇格を
	# 許可する (まさに粛清連鎖からの脱出が WILDCARD の目的)。
	if { [ "$improve_reason" = "normal" ] || [ "$improve_reason" = "post_regression" ]; } && [ "${WILDCARD_ENABLED:-0}" = "1" ] && [ "$russia_recovery_mode" != "1" ] && [ "$current_russia_progress" != "1" ] && [ -f "${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}" ]; then
		local stag rstreak
		local monitor_status="" monitor_age="" monitor_streak="" monitor_eval="" monitor_anchor="" monitor_event=""
		local _monitor_ctx
		stag=$(python3 -c "
import json,sys
try:
    print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('consecutive_no_improve', 0)))
except Exception:
    print(0)
" 2>/dev/null || echo 0)
		rstreak=$(python3 -c "
import json,sys
try:
    print(int(json.load(open('${STAGNATION_COUNTER_FILE:-tmp/state/stagnation_counter.json}', encoding='utf-8')).get('regression_streak', 0)))
except Exception:
    print(0)
	" 2>/dev/null || echo 0)
		local wildcard_escape_streak
		wildcard_escape_streak=$(python3 - \
			"${WILDCARD_ATTEMPT_STATE_FILE:-tmp/state/wildcard_attempt_state.json}" \
			"${WILDCARD_ORIGIN_FILE:-tmp/state/wildcard_origin.json}" \
			"${REJECTED_HASH_META_FILE:-tmp/state/rejected_hash_metrics.json}" \
			"${ROLLING_SCORES_FILE:-tmp/state/rolling_scores.json}" \
			"${BEST_STRATEGY_ANCHOR_FILE:-tmp/state/best_strategy_anchor.json}" <<'PY' 2>/dev/null || echo 0
import json
import math
import os
import sys

attempt_file, origin_file, rejected_file, rolling_file, anchor_file = sys.argv[1:6]

def load(path, default):
    try:
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f) or default
    except Exception:
        pass
    return default

def as_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default

def as_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default

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
    xs = []
    for raw in scores or []:
        try:
            xs.append(int(raw))
        except Exception:
            pass
    if not xs:
        return None
    n = len(xs)
    mean = sum(xs) / n
    p25 = quantile(xs, 0.25)
    p50 = quantile(xs, 0.50)
    std = math.sqrt(sum((x - mean) ** 2 for x in xs) / n) if n > 1 else 0.0
    lcb = mean - 1.28 * (std / math.sqrt(n))
    comp = 0.55 * p50 + 0.30 * p25 + 0.15 * lcb
    return {"comp": comp, "n": n}

attempt = load(attempt_file, {})
origin = load(origin_file, {})
rejected = load(rejected_file, {})
rolling = load(rolling_file, {})
anchor = load(anchor_file, {})
streak = as_int(attempt.get("consecutive_wildcards", 0), 0)
last_reset = as_int(attempt.get("last_reset_epoch", 0), 0)
anchor_comp = as_float(anchor.get("comp", 0.0), 0.0)

failed = []
for h, meta in (origin or {}).items():
    created = as_int((meta or {}).get("created_at_epoch", 0), 0)
    if last_reset and created and created <= last_reset:
        continue
    updated = as_int((rejected.get(h) or {}).get("updated_at", 0), 0)
    is_failed = h in rejected
    if not is_failed:
        entry = rolling.get(h) or {}
        m = metrics(entry.get("scores", []))
        max_games = as_int((meta or {}).get("max_games_override", 12), 12)
        if m and m.get("n", 0) >= max_games and anchor_comp > 0 and m.get("comp", 0.0) < anchor_comp:
            # Older WILDCARDs may have been non-promoted without a rejected
            # metrics row. Mature origin + below-anchor metrics is enough to
            # reconstruct the failed escape for escalation routing.
            is_failed = True
    if not is_failed:
        continue
    if last_reset and max(created, updated) <= last_reset:
        continue
    failed.append((created, updated, h))
failed.sort(key=lambda item: (item[0], item[1]))

if failed:
    # Count recent rejected WILDCARD origins since the last success reset. This
    # also reconstructs failures from WILDCARDs fired before attempt-state
    # tracking existed, so the next escape can escalate instead of forgetting.
    streak = max(streak, len(failed))

print(streak)
PY
)
		if [ "$stag" -ge "${WILDCARD_TRIGGER_STAGNATION:-3}" ]; then
			improve_reason="wildcard"
			if _archive_restart_should_run "$wildcard_escape_streak"; then
				improve_reason="archive_restart"
				log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${ARCHIVE_RESTART_STREAK:-3} → archive_restart で過去版から大域脱出"
				_improve_flow_notify \
					"archive_restart_candidate_yes" \
					"archive_restart candidate? yes" \
					"stagnation=${stag}/${WILDCARD_TRIGGER_STAGNATION:-3}; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=archive_restart" \
					"改善フロー: archive_restart candidate? yes。評価済みアーカイブからの復帰を試します。" \
					"warn"
			elif [ "${WILDCARD_AI_ESCALATE_ENABLED:-1}" = "1" ]; then
				if [ "$wildcard_escape_streak" -ge "${WILDCARD_AI_ESCALATE_STREAK:-3}" ] && _escape_ai_seed_available; then
					improve_reason="escape_ai"
					log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${WILDCARD_AI_ESCALATE_STREAK:-3} → seeded escape_ai 構造変異モードで脱出"
					_improve_flow_notify \
						"seeded_escape_ai_yes" \
						"seeded escape_ai candidate? yes" \
						"archive unavailable; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=escape_ai" \
						"改善フロー: seeded escape_ai candidate exists? yes。評価済みseedからescape_aiを実行します。" \
						"warn"
				elif [ "$wildcard_escape_streak" -ge "${WILDCARD_AI_ESCALATE_STREAK:-3}" ]; then
					improve_reason="normal"
					log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${WILDCARD_AI_ESCALATE_STREAK:-3} だが escape_ai seed なし → 通常AI改善へフォールバック"
					_improve_flow_notify \
						"fallback_normal_ai" \
						"fallback normal AI? yes" \
						"archive unavailable; no valid escape_ai seed; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=normal" \
						"改善フロー: escape_ai seedなし。WILDCARD再試行を止め、通常AI改善へ戻します。" \
						"warn"
				fi
			fi
			if [ "$improve_reason" = "wildcard" ]; then
				_improve_flow_notify \
					"wildcard_frontier" \
					"wildcard frontier recovery possible? yes" \
					"archive candidate unavailable; stagnation=${stag}/${WILDCARD_TRIGGER_STAGNATION:-3}; improve_reason=wildcard" \
					"改善フロー: archive_restart candidate? no。WILDCARDで構造変異候補を評価します。" \
					"warn"
			fi
			log "[WILDCARD] stagnation=$stag >= ${WILDCARD_TRIGGER_STAGNATION:-3} → ${improve_reason} モードで起動"
		elif [ "$rstreak" -ge "${WILDCARD_REGRESSION_STREAK:-2}" ]; then
			# counter 非依存 回帰ストリーク経路 (OK_BEAT マスク回避)。
			# churn 緩和: cooldown マーカ経過時のみ発火し発火時に更新。
			local _wccd="${WILDCARD_STREAK_COOLDOWN_FILE:-tmp/state/.wildcard_streak_cooldown}"
			local _wccd_sec="${WILDCARD_STREAK_COOLDOWN_SEC:-1800}" _wccd_ok=1
			if [ -f "$_wccd" ]; then
				local _wm _wnow
				_wm=$(stat -f %m "$_wccd" 2>/dev/null) \
					|| _wm=$(stat -c %Y "$_wccd" 2>/dev/null) \
					|| _wm=0
				_wnow=$(date +%s)
				[ "$(( _wnow - _wm ))" -lt "$_wccd_sec" ] && _wccd_ok=0
			fi
			if [ "$_wccd_ok" -eq 1 ]; then
				improve_reason="wildcard"
				if _archive_restart_should_run "$wildcard_escape_streak"; then
					improve_reason="archive_restart"
					log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${ARCHIVE_RESTART_STREAK:-3} → archive_restart で過去版から大域脱出"
					_improve_flow_notify \
						"archive_restart_candidate_yes" \
						"archive_restart candidate? yes" \
						"regression_streak=${rstreak}/${WILDCARD_REGRESSION_STREAK:-2}; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=archive_restart" \
						"改善フロー: archive_restart candidate? yes。評価済みアーカイブからの復帰を試します。" \
						"warn"
				elif [ "${WILDCARD_AI_ESCALATE_ENABLED:-1}" = "1" ]; then
					if [ "$wildcard_escape_streak" -ge "${WILDCARD_AI_ESCALATE_STREAK:-3}" ] && _escape_ai_seed_available; then
						improve_reason="escape_ai"
						log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${WILDCARD_AI_ESCALATE_STREAK:-3} → seeded escape_ai 構造変異モードで脱出"
						_improve_flow_notify \
							"seeded_escape_ai_yes" \
							"seeded escape_ai candidate? yes" \
							"archive unavailable; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=escape_ai" \
							"改善フロー: seeded escape_ai candidate exists? yes。評価済みseedからescape_aiを実行します。" \
							"warn"
					elif [ "$wildcard_escape_streak" -ge "${WILDCARD_AI_ESCALATE_STREAK:-3}" ]; then
						improve_reason="normal"
						log "[WILDCARD] consecutive_wildcards=${wildcard_escape_streak} >= ${WILDCARD_AI_ESCALATE_STREAK:-3} だが escape_ai seed なし → 通常AI改善へフォールバック"
						_improve_flow_notify \
							"fallback_normal_ai" \
							"fallback normal AI? yes" \
							"archive unavailable; no valid escape_ai seed; wildcard_escape_streak=${wildcard_escape_streak}; improve_reason=normal" \
							"改善フロー: escape_ai seedなし。WILDCARD再試行を止め、通常AI改善へ戻します。" \
							"warn"
					fi
				fi
				if [ "$improve_reason" = "wildcard" ]; then
					_improve_flow_notify \
						"wildcard_frontier" \
						"wildcard frontier recovery possible? yes" \
						"archive candidate unavailable; regression_streak=${rstreak}/${WILDCARD_REGRESSION_STREAK:-2}; improve_reason=wildcard" \
						"改善フロー: archive_restart candidate? no。WILDCARDで構造変異候補を評価します。" \
						"warn"
				fi
				: >"$_wccd" 2>/dev/null || true
				log "[WILDCARD] regression_streak=$rstreak >= ${WILDCARD_REGRESSION_STREAK:-2} (counter非依存) → ${improve_reason} モード起動 (cooldown ${_wccd_sec}s)"
			else
				log "[WILDCARD] regression_streak=$rstreak だが cooldown 中 → 今回は通常改善 (churn緩和)"
			fi
		fi
	fi
	if [ "$russia_recovery_mode" = "1" ] && { [ "$improve_reason" = "wildcard" ] || [ "$improve_reason" = "escape_ai" ]; }; then
		if _archive_restart_should_run 999; then
			improve_reason="archive_restart"
			log "[IMPROVE] Russia recovery mode: ${russia_recovery_reason:-unknown} → archive_restart を優先"
			_improve_flow_notify \
				"archive_restart_candidate_yes" \
				"archive_restart candidate? yes" \
				"Russia recovery reroute selected archive_restart" \
				"改善フロー: archive_restart candidate? yes。評価済みアーカイブからの復帰を試します。" \
				"warn"
		elif [ "${WILDCARD_ENABLED:-0}" = "1" ]; then
			improve_reason="wildcard"
			log "[IMPROVE] Russia recovery mode: archive candidate unavailable → WILDCARD で構造変異候補を評価"
			_improve_flow_notify \
				"wildcard_frontier" \
				"wildcard frontier recovery possible? yes" \
				"archive candidate unavailable; WILDCARD_ENABLED=1; improve_reason=wildcard" \
				"改善フロー: archive_restart candidate? no。WILDCARDで構造変異候補を評価します。" \
				"warn"
		elif [ "${WILDCARD_AI_ESCALATE_ENABLED:-1}" = "1" ] && _escape_ai_seed_available; then
			improve_reason="escape_ai"
			log "[IMPROVE] Russia recovery mode: archive/wildcard unavailable → seeded escape_ai で復旧"
			_improve_flow_notify \
				"seeded_escape_ai_yes" \
				"seeded escape_ai candidate? yes" \
				"archive/wildcard unavailable; valid WILDCARD seed found; improve_reason=escape_ai" \
				"改善フロー: seeded escape_ai candidate exists? yes。評価済みseedからescape_aiを実行します。" \
				"warn"
		else
			improve_reason="normal"
			log "[IMPROVE] Russia recovery mode: 有効なarchive/wildcard/seeded escape_aiなし → 通常AI改善で復旧"
			_improve_flow_notify \
				"no_valid_escape_route" \
				"fallback: no valid escape route" \
				"no archive candidate, wildcard disabled, no valid seeded escape_ai" \
				"改善フロー: seeded escape_ai candidate exists? no。有効な脱出先なしとしてfallback改善に入ります。" \
				"warn"
		fi
	fi

	if _start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "$improve_reason"; then
		rm -f "$TMP_STATE_DIR/last_improve_failed_at"
	fi
}

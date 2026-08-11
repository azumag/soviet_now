#!/usr/bin/env bash
# soviet_watchdog.sh - soviet_local.mjs (Playwright ブリッジ) クラッシュ自動復旧
#
# ブラウザが落ちると node が port 8080 を掴んだままハング (SIGTERM 無視) し、
# soren_loop が死んだブリッジに retry し続け game_state.json が凍結する。
# soren_loop は soviet_local を管理しないため、本 watchdog が独立常駐して
# 「プロセス消失」または「現プロセス起動後の致命ログ」を検知し再起動する。
#
# 起動: nohup ./soviet_watchdog.sh > tmp/debug/soviet_watchdog.log 2>&1 &
#
# 設計 (codex レビュー反映):
#  - 検知は曖昧さゼロの2条件のみ (staleness 不採用 → 改善pause 等の誤検知を排除)
#  - backoff は「失敗した再起動の連打」だけ抑制。成功直後の即クラッシュは即復旧
#  - kill は cwd==SCRIPT_DIR の node soviet_local.mjs に厳密スコープ
#  - port 保持者が対象外なら relaunch 中止 (EADDRINUSE ループ回避)
#  - singleton は heartbeat 付き。健全= PID生存 かつ cmd一致 かつ mtime新鮮 の3条件

set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)
cd "$SCRIPT_DIR" || exit 1

INTERVAL="${SOVIET_WATCHDOG_INTERVAL:-60}"
PORT="${SOVIET_WATCHDOG_PORT:-8080}"
GAME_LOG="${SOVIET_WATCHDOG_LOG:-tmp/soviet_local.log}"
CDP_ENDPOINT_FILE="tmp/cdp_endpoint.json"
LOCK_DIR="tmp/state/.soviet_watchdog.lock"
HEARTBEAT_STALE=$((INTERVAL * 3))
RELAUNCH_BASE_GAP="${SOVIET_WATCHDOG_BASE_GAP:-90}"
RELAUNCH_MAX_GAP="${SOVIET_WATCHDOG_MAX_GAP:-600}"
FATAL_RE='Target page, context or browser has been closed|browser has been closed|EADDRINUSE|^Node\.js v'

mkdir -p tmp/state tmp/debug 2>/dev/null || true

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] [soviet_watchdog] $*"; }

# Fix0: 共有 recovery lease (guardian/_ensure_bridge_alive と同一プロトコル・同一パス)
RR_LOCK="tmp/state/.runtime_recovery.lock"
RR_TTL="${RUNTIME_RECOVERY_LOCK_TTL:-120}"
rr_acquire() {
	mkdir -p tmp/state 2>/dev/null || true
	if mkdir "$RR_LOCK" 2>/dev/null; then echo "soviet_watchdog $$ $(date +%s)" >"$RR_LOCK/owner" 2>/dev/null||true; return 0; fi
	local now lk; now=$(date +%s)
	lk=$(stat -f %m "$RR_LOCK" 2>/dev/null) \
		|| lk=$(stat -c %Y "$RR_LOCK" 2>/dev/null) \
		|| lk="$now"
	if [ $(( now - lk )) -ge "$RR_TTL" ]; then
		rm -rf "$RR_LOCK" 2>/dev/null || true
		mkdir "$RR_LOCK" 2>/dev/null && { echo "soviet_watchdog $$ $now stolen" >"$RR_LOCK/owner" 2>/dev/null||true; return 0; }
	fi
	return 1
}
rr_release() { rm -rf "$RR_LOCK" 2>/dev/null || true; }

# --- singleton (heartbeat 付き) ---
_cmd_of() { ps -o command= -p "$1" 2>/dev/null; }
_lock_mtime() {
	local mt
	mt=$(stat -f %m "$LOCK_DIR" 2>/dev/null) \
		|| mt=$(stat -c %Y "$LOCK_DIR" 2>/dev/null) \
		|| mt=0
	printf '%s\n' "$mt"
}

acquire_singleton() {
	if mkdir "$LOCK_DIR" 2>/dev/null; then
		echo "$$" >"$LOCK_DIR/owner"
		return 0
	fi
	local owner cmd mt now
	owner=$(cat "$LOCK_DIR/owner" 2>/dev/null || echo "")
	mt=$(_lock_mtime); now=$(date +%s)
	if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
		cmd=$(_cmd_of "$owner")
		case "$cmd" in
		*soviet_watchdog*)
			if [ "$mt" -gt 0 ] && [ "$((now - mt))" -lt "$HEARTBEAT_STALE" ]; then
				log "既存の健全な watchdog (PID=$owner) を検出 → 起動せず終了"
				return 1
			fi
			log "WARNING: watchdog PID=$owner は生存だが heartbeat 停滞 (age=$((now-mt))s) → wedged とみなし置換"
			kill -9 "$owner" 2>/dev/null || true
			;;
		esac
	fi
	# owner 死亡 / PID 再利用 (cmd 不一致) / wedged → steal
	rm -rf "$LOCK_DIR" 2>/dev/null || true
	if mkdir "$LOCK_DIR" 2>/dev/null; then
		echo "$$" >"$LOCK_DIR/owner"
		log "stale/wedged lock を回収し取得"
		return 0
	fi
	log "lock 取得失敗 (競合) → 終了"
	return 1
}

heartbeat() { [ -d "$LOCK_DIR" ] && touch "$LOCK_DIR" 2>/dev/null || true; }

release_singleton() {
	[ -d "$LOCK_DIR" ] || return 0
	local o; o=$(cat "$LOCK_DIR/owner" 2>/dev/null || echo "")
	[ "$o" = "$$" ] && rm -rf "$LOCK_DIR" 2>/dev/null || true
}
trap 'release_singleton; exit 0' INT TERM EXIT

# --- 対象プロセス (cwd==SCRIPT_DIR の node soviet_local.mjs) ---
target_pids() {
	local pids p cwd found ep
	pids=$(pgrep -f "soviet_local\.mjs" 2>/dev/null || true)
	for p in $pids; do
		cwd=$(lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
		[ "$cwd" = "$SCRIPT_DIR" ] && { echo "$p"; found=1; }
	done
	[ "${found:-0}" = "1" ] && return 0
	ep=$(sed -n 's/.*"pid":[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$CDP_ENDPOINT_FILE" 2>/dev/null | head -1)
	if [ -n "$ep" ]; then
		cwd=$(lsof -a -p "$ep" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
		[ "$cwd" = "$SCRIPT_DIR" ] && echo "$ep"
	fi
}

# --- 致命ログ署名 (現プロセス起動後に限定) ---
fatal_in_log() {
	[ -f "$GAME_LOG" ] || return 1
	local clean last_state_line
	clean=$(sed 's/\x1b\[[0-9;]*m//g' "$GAME_LOG" 2>/dev/null | tail -200)
	last_state_line=$(printf '%s\n' "$clean" | grep -n '^State:' | tail -1 | cut -d: -f1)
	if [ -n "$last_state_line" ]; then
		# 最後の State: 行より後だけを対象 (進捗後の新規致命のみ)
		printf '%s\n' "$clean" | tail -n +"$((last_state_line + 1))" | grep -qE "$FATAL_RE"
	else
		# State: 行がまだ無い = 起動時クラッシュ → truncate 済ログ全体を走査
		printf '%s\n' "$clean" | grep -qE "$FATAL_RE"
	fi
}

port_holder() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -Fpn 2>/dev/null | tr '\n' ' '; }
port_held() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }

relaunch() {
	local pids p
	# Fix0: 共有 lease 取得。他復旧アクター処理中なら今回は譲る (次 INTERVAL 再試行)。
	if ! rr_acquire; then
		log "recovery lease 他者保持中 → relaunch を譲る (次周期再試行)"
		return 0
	fi
	trap 'rr_release' RETURN
	pids=$(target_pids)
	if [ -n "$pids" ]; then
		for p in $pids; do
			log "kill -9 対象: PID=$p CMD=[$(_cmd_of "$p")]"
			kill -9 "$p" 2>/dev/null || true
		done
	fi
	# port 解放待ち (最大15s)
	local waited=0 holder
	while [ "$waited" -lt 15 ]; do
		[ -z "$(port_held)" ] && break
		sleep 1; waited=$((waited + 1))
	done
	if [ -n "$(port_held)" ]; then
		holder=$(port_holder)
		local hpid; hpid=$(port_held)
		local hcmd; hcmd=$(_cmd_of "$hpid")
		case "$hcmd" in
		*soviet_local.mjs*)
			log "WARNING: port $PORT を旧 soviet_local が保持継続 (PID=$hpid) → 強制 kill 再試行"
			kill -9 "$hpid" 2>/dev/null || true
			sleep 2
			;;
		*)
			log "ERROR: port $PORT を対象外プロセスが保持 ($holder) → relaunch 中止・次周期で再判定"
			return 1
			;;
		esac
		[ -n "$(port_held)" ] && { log "ERROR: port $PORT 解放できず → relaunch 中止"; return 1; }
	fi
	log "relaunch: node soviet_local.mjs (cwd=$SCRIPT_DIR)"
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t soren_bridge 2>/dev/null || true
		tmux new-session -d -s soren_bridge "cd '$SCRIPT_DIR' && exec node soviet_local.mjs > '$GAME_LOG' 2>&1"
	else
		nohup node soviet_local.mjs > "$GAME_LOG" 2>&1 &
	fi
	sleep 12
	# 起動検証: 新 PID 生存 + port LISTEN + 新ログに致命署名なし
	local newpid
	newpid=$(target_pids | head -1)
	if [ -n "$newpid" ] && kill -0 "$newpid" 2>/dev/null && [ -n "$(port_held)" ] && ! fatal_in_log; then
		log "復旧成功 (PID=$newpid, port=$PORT LISTEN)"
		return 0
	fi
	log "WARNING: 起動検証失敗 (pid_alive=$([ -n "$newpid" ] && kill -0 "$newpid" 2>/dev/null && echo y || echo n) port=$([ -n "$(port_held)" ] && echo up || echo down))"
	return 1
}

# --- main loop ---
acquire_singleton || exit 0
log "起動 (interval=${INTERVAL}s port=$PORT dir=$SCRIPT_DIR)"

consecutive_fail=0
last_attempt_epoch=0

while :; do
	heartbeat
	crash=""
	if [ -z "$(target_pids)" ]; then
		crash="プロセス消失"
	elif [ -z "$(port_held)" ]; then
		crash="port消失"
	elif fatal_in_log; then
		crash="致命ログ署名"
	fi

	if [ -n "$crash" ]; then
		now=$(date +%s)
		# backoff は「直前 relaunch が失敗した場合の連打」のみ抑制。
		# consecutive_fail==0 (前回成功 or 初回) なら gap 無視で即復旧。
		gap=0
		if [ "$consecutive_fail" -gt 0 ]; then
			gap=$((RELAUNCH_BASE_GAP * (1 << (consecutive_fail - 1))))
			[ "$gap" -gt "$RELAUNCH_MAX_GAP" ] && gap=$RELAUNCH_MAX_GAP
		fi
		if [ "$consecutive_fail" -gt 0 ] && [ "$((now - last_attempt_epoch))" -lt "$gap" ]; then
			log "クラッシュ検知 ($crash) だが連続失敗 ${consecutive_fail} 回・cooldown 残 $((gap - (now - last_attempt_epoch)))s → 次周期で再試行"
		else
			log "クラッシュ検知 ($crash) → 復旧開始 (consecutive_fail=$consecutive_fail)"
			last_attempt_epoch=$now
			if relaunch; then
				consecutive_fail=0
			else
				consecutive_fail=$((consecutive_fail + 1))
			fi
		fi
	else
		[ "$consecutive_fail" -ne 0 ] && { log "ブリッジ正常化を確認 → fail カウンタ reset"; consecutive_fail=0; }
	fi
	sleep "$INTERVAL"
done

# lib/bridge_recovery.sh - soviet_local.mjs ブリッジ生存監視＋自動復旧
#
# soren_loop.sh のメインループから _ensure_bridge_alive を呼ぶ (永続ホスト=soren_loop)。
# 呼び出しは全 pause continue (改善中/soren91/Meriken/stop) の後・play_one_game の前に
# 配置すること。これにより正当な game_state.json 停滞を誤復旧しない (codex#1,#5)。
#
# 検知 (codex#3): (a) cwd 一致 soviet_local.mjs プロセス消失 (即) (b) 致命ログ署名
#   (c) game_state.json mtime 停滞 >= BRIDGE_STALE_SEC
# 復旧: cwd 厳密スコープ kill (codex#2) → port 解放待ち/保持者ガード → nohup 再起動
# backoff (codex#4): 失敗連打のみ抑制。consecutive_fail==0 は即試行。
#   stale 検知 skip では last_attempt を更新しない (復旧を遅延させない)。

_BR_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd -P)
_BR_GAME_LOG="${_BR_GAME_LOG:-tmp/soviet_local.log}"
_BR_GAME_STATE="${_BR_GAME_STATE:-game_state.json}"
_BR_PORT="${SOVIET_BRIDGE_PORT:-8080}"
_BR_CDP_PORT="${SOREN_CDP_PORT:-9222}"
_BR_PROFILE="${SOREN_LOCAL_USER_DATA_DIR:-$_BR_ROOT/tmp/soviet_local_chromium_profile}"
_BR_STALE_SEC="${BRIDGE_STALE_SEC:-240}"
_BR_BASE_GAP="${BRIDGE_RELAUNCH_BASE_GAP:-90}"
_BR_MAX_GAP="${BRIDGE_RELAUNCH_MAX_GAP:-600}"
# soviet_local.mjs の [BRIDGE-FATAL] も検知署名に含める (clean-exit 連携)
_BR_FATAL_RE='\[BRIDGE-FATAL\]|Target page, context or browser has been closed|browser has been closed|EADDRINUSE|^Node\.js v'
# 毎周回 eloop_lib 再 source されても backoff 状態を保持 (未設定時のみ初期化)
: "${_BR_CONSEC_FAIL:=0}"
: "${_BR_LAST_ATTEMPT:=0}"

_br_log() { log "[BRIDGE] $*" 2>/dev/null || echo "[$(date '+%H:%M:%S')] [BRIDGE] $*"; }

# cwd が _BR_ROOT の node soviet_local.mjs PID のみ (他 checkout 巻き込み防止 codex#2)
_br_target_pids() {
	local p cwd
	for p in $(pgrep -f "soviet_local\.mjs" 2>/dev/null || true); do
		cwd=$(lsof -a -p "$p" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)
		[ "$cwd" = "$_BR_ROOT" ] && echo "$p"
	done
}

_br_port_pid() { lsof -nP -iTCP:"$_BR_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }
_br_cmd_of() { ps -o command= -p "$1" 2>/dev/null; }

# ---- Fix0: 共有 recovery lease (guardian/_ensure_bridge_alive/soviet_watchdog 共通) ----
# bridge/strategy_runner の kill・relaunch 前に取得。多重復旧アクターのレース防止。
# mkdir アトミック + owner + heartbeat(mtime) + stale TTL (IMPROVE_SPAWN_LOCK と同型)。
_RR_LOCK="${RUNTIME_RECOVERY_LOCK_DIR:-$_BR_ROOT/tmp/state/.runtime_recovery.lock}"
_RR_TTL="${RUNTIME_RECOVERY_LOCK_TTL:-120}"
# 引数: $1=owner識別子 (例 "ensure_bridge_alive")。取得成功 0 / 他者保持中 1
rr_lease_acquire() {
	local owner="${1:-bridge_recovery}" now lk
	mkdir -p "$(dirname "$_RR_LOCK")" 2>/dev/null || true
	if mkdir "$_RR_LOCK" 2>/dev/null; then
		if ! echo "$owner $$ $(date +%s)" >"$_RR_LOCK/owner" 2>/dev/null; then
			rm -rf "$_RR_LOCK" 2>/dev/null || true
			return 1
		fi
		return 0
	fi
	# 既存ロック: stale (TTL超過) なら奪取
	now=$(date +%s)
	lk=$(stat -f %m "$_RR_LOCK" 2>/dev/null || stat -c %Y "$_RR_LOCK" 2>/dev/null || echo "$now")
	if [ $(( now - lk )) -ge "$_RR_TTL" ]; then
		rm -rf "$_RR_LOCK" 2>/dev/null || true
		if mkdir "$_RR_LOCK" 2>/dev/null; then
			if ! echo "$owner $$ $now (stolen-stale)" >"$_RR_LOCK/owner" 2>/dev/null; then
				rm -rf "$_RR_LOCK" 2>/dev/null || true
				return 1
			fi
			return 0
		fi
	fi
	return 1
}
rr_lease_heartbeat() { [ -d "$_RR_LOCK" ] && touch "$_RR_LOCK" 2>/dev/null || true; }
rr_lease_release() { rm -rf "$_RR_LOCK" 2>/dev/null || true; }

# Chromium プロファイルの exit_type を Normal に修復し、復元バブルを抑止。
# kill -9 された直後の profile は exit_type=Crashed が残り、再起動時に
# 「Chromium が正しく終了しませんでした」バブルが配信画面隅に出続けるため。
_br_clean_profile_exit() {
	[ -d "$_BR_PROFILE" ] || return 0
	python3 - "$_BR_PROFILE" <<'PY' 2>/dev/null || true
import json, glob, os, sys
root = sys.argv[1]
for pref in glob.glob(os.path.join(root, "*", "Preferences")):
    try:
        with open(pref) as f:
            d = json.load(f)
        changed = False
        prof = d.setdefault("profile", {})
        if prof.get("exit_type") != "Normal" or prof.get("exited_clean") is not True:
            prof["exit_type"] = "Normal"
            prof["exited_clean"] = True
            changed = True
        # 翻訳バー(英語→日本語 このページを翻訳しますか)を恒久抑止。
        # --disable-features は Playwright 既定と重複し脆いため pref で確実化。
        tr = d.setdefault("translate", {})
        if tr.get("enabled") is not False:
            tr["enabled"] = False
            changed = True
        blk = d.get("translate_blocked_languages")
        if blk != ["en", "ja"]:
            d["translate_blocked_languages"] = ["en", "ja"]
            changed = True
        if d.get("translate_site_blacklist_with_time") is None and d.get("translate_site_blacklist") is None:
            d["translate_site_blacklist"] = ["localhost"]
            changed = True
        if not changed:
            continue
        tmp = pref + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, pref)
    except Exception:
        pass
PY
}

# 致命ログ署名 (最後の State: 行より後、無ければ全体。ANSI 除去)
_br_fatal_in_log() {
	[ -f "$_BR_GAME_LOG" ] || return 1
	local clean ls
	clean=$(sed 's/\x1b\[[0-9;]*m//g' "$_BR_GAME_LOG" 2>/dev/null | tail -200)
	ls=$(printf '%s\n' "$clean" | grep -n '^State:' | tail -1 | cut -d: -f1)
	if [ -n "$ls" ]; then
		printf '%s\n' "$clean" | tail -n +"$((ls + 1))" | grep -qE "$_BR_FATAL_RE"
	else
		printf '%s\n' "$clean" | grep -qE "$_BR_FATAL_RE"
	fi
}

_br_relaunch() {
	local pids p
	# Fix0: 共有 lease を取得してから kill/relaunch。他の復旧アクター
	# (guardian/soviet_watchdog) が処理中なら今回は譲り次周期再試行。
	if ! rr_lease_acquire "ensure_bridge_alive"; then
		_br_log "recovery lease 他者保持中 → 今回の relaunch を譲る (次周期再試行)"
		return 2
	fi
	trap 'rr_lease_release' RETURN
	pids=$(_br_target_pids)
	for p in $pids; do
		_br_log "kill -9 PID=$p CMD=[$(_br_cmd_of "$p")]"
		kill -9 "$p" 2>/dev/null || true
	done
	# Chromium 子プロセス (CDP 9222 / 当 profile) も掃除。
	# soviet_local が clean-exit しても Chromium が残ると profile ロック /
	# CDP ポートを掴み、再起動が profile-in-use で失敗するため (codex#7,#8)。
	for p in $(pgrep -f -- "--remote-debugging-port=$_BR_CDP_PORT" 2>/dev/null || true) \
		$(pgrep -f -- "$_BR_PROFILE" 2>/dev/null || true); do
		[ -n "$p" ] && kill -9 "$p" 2>/dev/null || true
	done
	# SERVE/CDP 両ポート解放待ち最大15s
	local w=0
	while [ "$w" -lt 15 ]; do
		[ -z "$(_br_port_pid)" ] && [ -z "$(lsof -nP -iTCP:"$_BR_CDP_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)" ] && break
		sleep 1; w=$((w + 1))
	done
	local hp; hp=$(_br_port_pid)
	if [ -n "$hp" ]; then
		local hc; hc=$(_br_cmd_of "$hp")
		case "$hc" in
		*soviet_local.mjs*) _br_log "port $_BR_PORT 旧 bridge 保持(PID=$hp) → 強制kill"; kill -9 "$hp" 2>/dev/null || true; sleep 2 ;;
		*) _br_log "ERROR: port $_BR_PORT を対象外プロセス保持(PID=$hp CMD=[$hc]) → 復旧中止・次ループ再判定"; return 1 ;;
		esac
		[ -n "$(_br_port_pid)" ] && { _br_log "ERROR: port $_BR_PORT 解放不可 → 復旧中止"; return 1; }
	fi
	# kill -9 で unclean になったプロファイルの「正常終了せず」復元バブルを抑止
	# (--hide-crash-restore-bubble と二重の保険。既存 unclean 状態も修復)
	_br_clean_profile_exit
	_br_log "relaunch: node soviet_local.mjs (cwd=$_BR_ROOT)"
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t soren_bridge 2>/dev/null || true
		tmux new-session -d -s soren_bridge "cd '$_BR_ROOT' && exec node soviet_local.mjs > '$_BR_GAME_LOG' 2>&1"
	else
		( cd "$_BR_ROOT" && nohup node soviet_local.mjs > "$_BR_GAME_LOG" 2>&1 < /dev/null & )
	fi
	sleep 12
	local m n; m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null || stat -c %Y "$_BR_GAME_STATE" 2>/dev/null || echo 0); n=$(date +%s)
	if [ -n "$(_br_target_pids)" ] && { [ -n "$(_br_port_pid)" ] || [ "$((n - m))" -lt 30 ]; } && ! _br_fatal_in_log; then
		_br_log "復旧成功 (port=$([ -n "$(_br_port_pid)" ] && echo up || echo '?') game_state_age=$((n-m))s)"
		return 0
	fi
	# 一過性 (profile/CDP/port ロックの掃け残り) は指数 cooldown を汚染しない (codex#8)
	if tail -40 "$_BR_GAME_LOG" 2>/dev/null | grep -qiE 'SingletonLock|ProcessSingleton|ProfileInUse|profile.*in use|EADDRINUSE|cdp.*in use'; then
		_br_log "WARNING: 一過性ロック (profile/CDP/port 掃け残り) → 次ループ短間隔で再試行"
		return 2
	fi
	_br_log "WARNING: 復旧後検証失敗"
	return 1
}

# soren_loop メインループから毎周回呼ぶ。pause 後・play_one_game 前に配置。
_ensure_bridge_alive() {
	# 防御: 明示停止中は監視しない (pause 述語の主ガードは呼び出し位置で担保済 codex#5)
	[ -f tmp/stop ] && return 0
	[ -f tmp/state/manual_improve_mode ] && return 0

	local crash="" m n
	if [ -z "$(_br_target_pids)" ]; then
		crash="プロセス消失"
	elif _br_fatal_in_log; then
		crash="致命ログ署名"
	else
		m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null || stat -c %Y "$_BR_GAME_STATE" 2>/dev/null || echo 0)
		n=$(date +%s)
		[ "$m" -gt 0 ] && [ "$((n - m))" -ge "$_BR_STALE_SEC" ] && crash="game_state停滞($((n-m))s)"
	fi
	[ -z "$crash" ] && { [ "$_BR_CONSEC_FAIL" -ne 0 ] && { _br_log "ブリッジ正常化 → fail reset"; _BR_CONSEC_FAIL=0; }; return 0; }

	local now gap
	now=$(date +%s)
	gap=0
	if [ "$_BR_CONSEC_FAIL" -gt 0 ]; then
		gap=$((_BR_BASE_GAP * (1 << (_BR_CONSEC_FAIL - 1))))
		[ "$gap" -gt "$_BR_MAX_GAP" ] && gap=$_BR_MAX_GAP
	fi
	if [ "$_BR_CONSEC_FAIL" -gt 0 ] && [ "$((now - _BR_LAST_ATTEMPT))" -lt "$gap" ]; then
		_br_log "クラッシュ検知($crash) 連続失敗${_BR_CONSEC_FAIL}・cooldown残$((gap-(now-_BR_LAST_ATTEMPT)))s → 次周回再試行"
		return 0
	fi
	_br_log "クラッシュ検知($crash) → 復旧開始 (consecutive_fail=$_BR_CONSEC_FAIL)"
	_BR_LAST_ATTEMPT=$now
	_br_relaunch; local rc=$?
	if [ "$rc" -eq 0 ]; then
		_BR_CONSEC_FAIL=0
	elif [ "$rc" -eq 2 ]; then
		# 一過性ロック: 指数化せず base gap で速やかに再試行 (cooldown 汚染回避)
		_BR_CONSEC_FAIL=1
	else
		_BR_CONSEC_FAIL=$((_BR_CONSEC_FAIL + 1))
	fi
	return 0
}

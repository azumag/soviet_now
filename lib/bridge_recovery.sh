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
# The tmux session hosting the bridge. Isolated rehearsals (isolated game
# state dir, isolated ports) must override this, otherwise a rehearsal-side
# bridge recovery kills the production soren_bridge session by name while
# every other recovery target (port/CDP/profile) is already env-scoped.
_BR_TMUX_SESSION="${SOREN_BRIDGE_TMUX_SESSION:-soren_bridge}"
_BR_STALE_SEC="${BRIDGE_STALE_SEC:-240}"
# Issue #500: game_state.json の mtime 停滞は bridge 障害の証明にならない。
# 稼働中の bridge は live page からの実観測 (tmp/state/game_observation.json) を
# 盤面が静止していても最長1秒ごとに更新する (docs/founding-stop-boundary.md)。
# 停滞を検知しても実観測で裏を取るまで kill/relaunch へ進まない。
_BR_OBSERVATION="${BRIDGE_OBSERVATION_FILE:-$_BR_ROOT/tmp/state/game_observation.json}"
_BR_OBSERVE_FRESH_SEC="${BRIDGE_OBSERVE_FRESH_SEC:-15}"
_BR_OBSERVE_WAIT_SEC="${BRIDGE_OBSERVE_WAIT_SEC:-5}"
# 同じ保留理由を毎周回ログしない。
_BR_STALE_NOTICE_SEC="${BRIDGE_STALE_NOTICE_SEC:-60}"
: "${_BR_STALE_HOLD_LOGGED_AT:=0}"
_BR_BASE_GAP="${BRIDGE_RELAUNCH_BASE_GAP:-90}"
_BR_MAX_GAP="${BRIDGE_RELAUNCH_MAX_GAP:-600}"
_BR_AUDIO_HEALTH="${BRIDGE_AUDIO_HEALTH_FILE:-$_BR_ROOT/tmp/state/local_audio_health.json}"
_BR_AUDIO_DIAG="${BRIDGE_AUDIO_DIAG_FILE:-$_BR_ROOT/tmp/audio_diag.log}"
_BR_AUDIO_STUCK_WINDOW="${BRIDGE_AUDIO_STUCK_WINDOW_SEC:-240}"
_BR_AUDIO_STUCK_RECOVER_COUNT="${BRIDGE_AUDIO_STUCK_RECOVER_COUNT:-4}"
# Wrong-sink detection: a running AudioContext bound to the default output
# (sinkId='') while the BlackHole sink is resolvable (routedDeviceId set) means
# game audio never reaches OBS — the "ゲーム音でてない" class. It does not crash
# and does not show suspended/interrupted, so _br_audio_stuck_reason misses it.
# Gate (default on), confirm twice before acting (debounce snapshots), and cap
# consecutive relaunches so a fundamentally-unsupported {sinkId} can't flap.
_BR_AUDIO_WRONG_SINK_RELAUNCH="${BRIDGE_AUDIO_WRONG_SINK_RELAUNCH:-1}"
_BR_AUDIO_WRONG_SINK_MAX="${BRIDGE_AUDIO_WRONG_SINK_MAX:-3}"
: "${_BR_WRONG_SINK_PENDING:=0}"
: "${_BR_WRONG_SINK_RELAUNCHES:=0}"
_BR_RELAUNCH_REASON="${_BR_RELAUNCH_REASON:-}"
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
_br_cdp_port_pid() { lsof -nP -iTCP:"$_BR_CDP_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1; }
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
	lk=$(stat -f %m "$_RR_LOCK" 2>/dev/null) \
		|| lk=$(stat -c %Y "$_RR_LOCK" 2>/dev/null) \
		|| lk=$now
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

_br_audio_stuck_reason() {
	[ -f "$_BR_AUDIO_HEALTH" ] || return 1
	[ -f "$_BR_AUDIO_DIAG" ] || return 1
	python3 - "$_BR_AUDIO_HEALTH" "$_BR_AUDIO_DIAG" "$_BR_AUDIO_STUCK_WINDOW" "$_BR_AUDIO_STUCK_RECOVER_COUNT" <<'PY'
import json
import re
import sys
import time
from datetime import datetime, timezone

health_path, diag_path, window_raw, count_raw = sys.argv[1:5]
try:
    window = max(60, int(window_raw))
except Exception:
    window = 240
try:
    threshold = max(1, int(count_raw))
except Exception:
    threshold = 4

try:
    with open(health_path, encoding="utf-8") as f:
        health = json.load(f)
except Exception:
    raise SystemExit(1)

view = health
if isinstance(health.get("after"), dict):
    view = health.get("after") or {}
before = health.get("before") if isinstance(health.get("before"), dict) else {}

if health.get("muted") or before.get("muted") or view.get("muted"):
    raise SystemExit(1)

states = []
if view.get("unityState"):
    states.append(str(view.get("unityState")))
for item in view.get("tracked") or []:
    if isinstance(item, dict) and item.get("state"):
        states.append(str(item.get("state")))
if not any(state in {"suspended", "interrupted"} for state in states):
    raise SystemExit(1)

now = time.time()
recent = 0
line_re = re.compile(r"^\[([0-9T:.\-]+Z)\]\s+\[AUDIO-WATCHDOG-RECOVER\]")
try:
    with open(diag_path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-200:]
except Exception:
    raise SystemExit(1)

for line in lines:
    match = line_re.match(line)
    if not match:
        continue
    try:
        ts = datetime.fromisoformat(match.group(1).replace("Z", "+00:00")).timestamp()
    except Exception:
        continue
    if now - ts <= window:
        recent += 1

if recent < threshold:
    raise SystemExit(1)

print(f"audio_context_stuck(states={','.join(states)}, recoveries={recent}/{threshold} in {window}s)")
PY
}

# Classify the live audio sink: prints one of
#   OK            a running context is bound to the resolved sink (routedDeviceId)
#   WRONG <info>  every running context is on a different/default device (sinkId!=routed)
#   NA            indeterminate (muted / no running context yet / sink unresolved)
_br_audio_sink_status() {
	[ -f "$_BR_AUDIO_HEALTH" ] || { echo NA; return 0; }
	python3 - "$_BR_AUDIO_HEALTH" <<'PY' 2>/dev/null || echo NA
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        health = json.load(f)
except Exception:
    print("NA"); raise SystemExit(0)
view = health.get("after") if isinstance(health.get("after"), dict) else health
before = health.get("before") if isinstance(health.get("before"), dict) else {}
if health.get("muted") or before.get("muted") or view.get("muted"):
    print("NA"); raise SystemExit(0)
if view.get("unityPresent") is False:
    print("NA"); raise SystemExit(0)
routed = str(view.get("routedDeviceId") or "")
running = [t for t in (view.get("tracked") or [])
          if isinstance(t, dict) and t.get("state") == "running"]
if not routed or not running:
    print("NA"); raise SystemExit(0)
if any(str(t.get("sinkId") or "") == routed for t in running):
    print("OK"); raise SystemExit(0)
print(f"WRONG routed={routed[:8]} running={len(running)} sink_empty")
PY
}

# game-only lifecycle が意図的にブリッジを止めている間 (停止済み/停止中) は、
# desync/phantom/audio/消失のどの検知でも再起動してはならない。watchdog と
# start_all は同じ述語で抑止済みだが、eloop 側の自己復旧が見ていなかったため、
# 試合中に停止されるとコーナー中に停止済みブリッジが復活した (soviet_now#599)。
_br_lifecycle_parked() {
	command -v game_lifecycle_bridge_parked >/dev/null 2>&1 && game_lifecycle_bridge_parked
}

_br_relaunch() {
	local pids p
	# 意図した停止を障害として復旧しない。rc=3 は呼び出し側で「失敗」扱い
	# (成功扱いにすると「bridge 再起動 成功」と誤記録され、試合が成立したように見える)。
	if _br_lifecycle_parked; then
		_br_log "game-only lifecycle handover中 (park) → bridge relaunch を保留"
		return 3
	fi
	# Fix0: 共有 lease を取得してから kill/relaunch。他の復旧アクター
	# (guardian/soviet_watchdog) が処理中なら今回は譲り次周期再試行。
	if ! rr_lease_acquire "ensure_bridge_alive"; then
		_br_log "recovery lease 他者保持中 → 今回の relaunch を譲る (次周期再試行)"
		return 2
	fi
	trap 'rr_lease_release' RETURN
	# lease 待ちの間に停止要求が来る競合を閉じる (kill 前に再判定)。
	if _br_lifecycle_parked; then
		_br_log "game-only lifecycle handover中 (park, lease取得後) → bridge relaunch を保留"
		return 3
	fi
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
	# tmux-hosted bridge can hide cwd/command attribution from _br_target_pids().
	# Stop the session before the port-free wait; otherwise the old pane keeps
	# 8080 bound and the relaunch path fails before it reaches the tmux reset below.
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t "$_BR_TMUX_SESSION" 2>/dev/null || true
	fi
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
			*)
				if [[ "$_BR_RELAUNCH_REASON" == audio_context_stuck* ]]; then
					_br_log "port $_BR_PORT 保持PIDの詳細不明だが audio_context_stuck 復旧のため強制kill(PID=$hp)"
					kill -9 "$hp" 2>/dev/null || true
					sleep 2
				else
					local live_m live_n
					live_m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null) \
						|| live_m=$(stat -c %Y "$_BR_GAME_STATE" 2>/dev/null) \
						|| live_m=0
					live_n=$(date +%s)
					if [ "$live_m" -gt 0 ] && [ "$((live_n - live_m))" -lt 30 ] && ! _br_fatal_in_log; then
						_br_log "port $_BR_PORT 保持PIDの詳細不明だが game_state fresh=$((live_n-live_m))s → 稼働中として復旧成功扱い"
						return 0
					fi
					_br_log "ERROR: port $_BR_PORT を対象外プロセス保持(PID=$hp CMD=[$hc]) → 復旧中止・次ループ再判定"; return 1
				fi
				;;
			esac
		[ -n "$(_br_port_pid)" ] && { _br_log "ERROR: port $_BR_PORT 解放不可 → 復旧中止"; return 1; }
	fi
	# kill -9 で unclean になったプロファイルの「正常終了せず」復元バブルを抑止
	# (--hide-crash-restore-bubble と二重の保険。既存 unclean 状態も修復)
	_br_clean_profile_exit
	_br_log "relaunch: node soviet_local.mjs (cwd=$_BR_ROOT)"
	# クラッシュ原因調査用に直前ログを退避する（次回 relaunch で上書きされるため）
	if [ -f "$_BR_GAME_LOG" ] && [ -s "$_BR_GAME_LOG" ]; then
		local _br_crash_log="$_BR_ROOT/tmp/debug/soviet_local.log.$(date +%Y%m%d_%H%M%S)"
		cp "$_BR_GAME_LOG" "$_br_crash_log" 2>/dev/null || true
		ls -1t "$_BR_ROOT"/tmp/debug/soviet_local.log.* 2>/dev/null | tail -n +31 | xargs -r rm -f 2>/dev/null || true
	fi
	if command -v tmux >/dev/null 2>&1; then
		tmux kill-session -t "$_BR_TMUX_SESSION" 2>/dev/null || true
		tmux new-session -d -s "$_BR_TMUX_SESSION" "cd '$_BR_ROOT' && exec node soviet_local.mjs > '$_BR_GAME_LOG' 2>&1"
	else
		( cd "$_BR_ROOT" && nohup node soviet_local.mjs > "$_BR_GAME_LOG" 2>&1 < /dev/null & )
	fi
	local verify_deadline m n serve_pid cdp_pid verify_sec
	verify_sec="${BRIDGE_RELAUNCH_VERIFY_SEC:-150}"
	case "$verify_sec" in ''|*[!0-9]*) verify_sec=150 ;; esac
	[ "$verify_sec" -lt 15 ] && verify_sec=15
	verify_deadline=$(($(date +%s) + verify_sec))
	while [ "$(date +%s)" -lt "$verify_deadline" ]; do
		sleep 2
		m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null) \
			|| m=$(stat -c %Y "$_BR_GAME_STATE" 2>/dev/null) \
			|| m=0
		n=$(date +%s)
		serve_pid=$(_br_port_pid)
		cdp_pid=$(_br_cdp_port_pid)
		if [ -n "$serve_pid" ] && [ -n "$cdp_pid" ] &&
			[ "$m" -gt 0 ] && [ "$((n - m))" -lt 30 ] && ! _br_fatal_in_log; then
			_br_log "復旧成功 (serve=up cdp=up game_state_age=$((n-m))s)"
			return 0
		fi
		_br_log "復旧検証待ち (serve=$([ -n "$serve_pid" ] && echo up || echo down) cdp=$([ -n "$cdp_pid" ] && echo up || echo down) game_state_age=$([ "$m" -gt 0 ] && echo $((n-m)) || echo NA)s)"
	done
	# 一過性 (profile/CDP/port ロックの掃け残り) は指数 cooldown を汚染しない (codex#8)
	if tail -40 "$_BR_GAME_LOG" 2>/dev/null | grep -qiE 'SingletonLock|ProcessSingleton|ProfileInUse|profile.*in use|EADDRINUSE|cdp.*in use'; then
		_br_log "WARNING: 一過性ロック (profile/CDP/port 掃け残り) → 次ループ短間隔で再試行"
		return 2
	fi
	serve_pid=$(_br_port_pid)
	cdp_pid=$(_br_cdp_port_pid)
	m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null) \
		|| m=$(stat -c %Y "$_BR_GAME_STATE" 2>/dev/null) \
		|| m=0
	n=$(date +%s)
	_br_log "WARNING: 復旧後検証失敗 (serve=$([ -n "$serve_pid" ] && echo up || echo down) cdp=$([ -n "$cdp_pid" ] && echo up || echo down) game_state_age=$([ "$m" -gt 0 ] && echo $((n-m)) || echo NA)s)"
	return 1
}

# ---- Issue #500: 実観測による停滞の裏取り (kill/relaunch 前の非破壊確認) ----
# game_state.json は盤面が変化した時だけ書かれる。休止・境界待ちで同じ盤面を
# 保持している間は進捗が無いので mtime も止まる。したがって mtime 停滞だけでは
# bridge 障害 (= 盤面喪失を伴う復旧が必要) と断定できない。稼働中の bridge は
# live page の実観測 (tmp/state/game_observation.json) を静止中も最長1秒ごとに
# 更新するので、こちらを非破壊な生存確認に使う。
_br_observation_age() {
	local f m n
	f="${_BR_OBSERVATION:-$_BR_ROOT/tmp/state/game_observation.json}"
	[ -f "$f" ] || return 1
	m=$(stat -f %m "$f" 2>/dev/null) \
		|| m=$(stat -c %Y "$f" 2>/dev/null) \
		|| return 1
	case "$m" in ''|0|*[!0-9]*) return 1 ;; esac
	n=$(date +%s)
	[ "$n" -ge "$m" ] || return 1
	printf '%s\n' "$((n - m))"
	return 0
}

# 最後の実観測が閾値以内か (実観測があるファイルの mtime のみを根拠にしない)。
_br_live_observation_fresh() {
	local age
	age=$(_br_observation_age 2>/dev/null) || return 1
	case "$age" in ''|*[!0-9]*) return 1 ;; esac
	[ "$age" -le "$_BR_OBSERVE_FRESH_SEC" ]
}

# mtime 停滞を検知した時の非破壊な再観測。live page を観測できている bridge は
# 1秒以内に実観測を更新するので、有界の待機で生存を確認できる (休止解除直後の
# 1周回で観測がまだ古い場合をここで吸収する)。kill も bridge への入力も送らない。
_br_live_page_reobserved() {
	_br_live_observation_fresh && return 0
	local waited=0 wait_sec
	wait_sec="${_BR_OBSERVE_WAIT_SEC:-5}"
	case "$wait_sec" in ''|*[!0-9]*) wait_sec=5 ;; esac
	while [ "$waited" -lt "$wait_sec" ]; do
		sleep 1
		waited=$((waited + 1))
		_br_live_observation_fresh && return 0
	done
	return 1
}

# 実観測中の盤面 (ログ用)。取得できなければ空。
_br_observation_board() {
	python3 - "${_BR_OBSERVATION:-}" <<'PY' 2>/dev/null
import json
import sys

try:
    record = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
board = record.get("board") if isinstance(record, dict) else None
if not isinstance(board, dict):
    raise SystemExit(1)
print("board=state:{} score:{} makeSorenCount:{}".format(
    board.get("state"), board.get("score"), board.get("makeSorenCount")))
PY
}

# 同じ保留理由を毎周回ログしない (周回は数秒間隔なので洪水になる)。
_br_log_stale_hold() {
	local stale_age="$1" obs_age="$2" board="$3" now
	now=$(date +%s)
	if [ "${_BR_STALE_HOLD_LOGGED_AT:-0}" -ne 0 ] &&
		[ "$((now - _BR_STALE_HOLD_LOGGED_AT))" -lt "$_BR_STALE_NOTICE_SEC" ]; then
		return 0
	fi
	_BR_STALE_HOLD_LOGGED_AT=$now
	_br_log "game_state停滞(${stale_age}s) だが実観測は生存(age=${obs_age}s ${board}) → 保持中の盤面と判定し kill/relaunch を保留 (盤面リセットへ自動で進まない)"
}

# soren_loop メインループから毎周回呼ぶ。pause 後・play_one_game 前に配置。
_ensure_bridge_alive() {
	# 防御: 明示停止中は監視しない (pause 述語の主ガードは呼び出し位置で担保済 codex#5)
	[ -f tmp/stop ] && return 0
	[ -f tmp/state/manual_improve_mode ] && return 0
	# 停止済みブリッジを監視しない。0 を返すと呼び出し側が試合を始めるので、
	# 復旧未完了(1)として試合開始を次周回へ延期させる。
	if _br_lifecycle_parked; then
		_br_log "game-only lifecycle handover中 (park) → ブリッジ監視/復旧を保留"
		return 1
	fi

	local crash="" audio_crash="" wrong_sink="" m n
	m=$(stat -f %m "$_BR_GAME_STATE" 2>/dev/null) \
		|| m=$(stat -c %Y "$_BR_GAME_STATE" 2>/dev/null) \
		|| m=0
	n=$(date +%s)
	audio_crash=$(_br_audio_stuck_reason 2>/dev/null || true)

	# 稼働中だが既定デバイスに繋がった (sinkId='') = ゲーム音が OBS に乗らない。
	# crash/suspended にならないので debounce で確認し、上限付きで復旧する。
	if [ "$_BR_AUDIO_WRONG_SINK_RELAUNCH" = "1" ]; then
		local sink_status; sink_status=$(_br_audio_sink_status)
		case "$sink_status" in
			OK)
				_BR_WRONG_SINK_PENDING=0
				_BR_WRONG_SINK_RELAUNCHES=0
				;;
			WRONG*)
				_BR_WRONG_SINK_PENDING=$((_BR_WRONG_SINK_PENDING + 1))
				if [ "$_BR_WRONG_SINK_PENDING" -lt 2 ]; then
					_br_log "audio_wrong_sink 検知($sink_status) debounce ${_BR_WRONG_SINK_PENDING}/2 → 次周回で確認"
				elif [ "$_BR_WRONG_SINK_RELAUNCHES" -ge "$_BR_AUDIO_WRONG_SINK_MAX" ]; then
					_br_log "audio_wrong_sink: 連続復旧上限(${_BR_AUDIO_WRONG_SINK_MAX})到達 → 自動再起動停止($sink_status)"
				else
					wrong_sink="audio_wrong_sink($sink_status)"
				fi
				;;
			*) : ;;  # NA: muted/未生成/未解決 → 判定保留
		esac
	fi

	if _br_fatal_in_log; then
		crash="致命ログ署名"
	elif [ -n "$audio_crash" ]; then
		crash="$audio_crash"
	elif [ -n "$wrong_sink" ]; then
		crash="$wrong_sink"
	elif [ "$m" -gt 0 ] && [ "$((n - m))" -lt "$_BR_STALE_SEC" ]; then
		# macOS privacy/sandbox boundaries can hide cwd/command details from
		# pgrep+lsof even while the bridge is actively writing game_state.json.
		# Fresh state is the stronger liveness signal; do not relaunch a moving
		# game just because PID attribution is unavailable.
		crash=""
	elif [ -z "$(_br_target_pids)" ]; then
		crash="プロセス消失"
	else
		[ "$m" -gt 0 ] && [ "$((n - m))" -ge "$_BR_STALE_SEC" ] && crash="game_state停滞($((n-m))s)"
	fi
	# Issue #500: mtime 停滞だけを根拠に bridge を kill/relaunch しない。実観測で
	# live page の生存を非破壊に確認できたら、凍結した game_state.json は休止や
	# 境界待ちで止まっている保持対象の盤面であり、bridge 障害ではない。復旧不能と
	# 確認できるまで盤面リセット (kill/relaunch) へは自動で進めない。
	if [[ "$crash" == game_state停滞* ]] && _br_live_page_reobserved; then
		_br_log_stale_hold "$((n - m))" "$(_br_observation_age 2>/dev/null || echo NA)" "$(_br_observation_board 2>/dev/null || true)"
		crash=""
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
		return 1
	fi
	_br_log "クラッシュ検知($crash) → 復旧開始 (consecutive_fail=$_BR_CONSEC_FAIL)"
	_BR_LAST_ATTEMPT=$now
	_BR_RELAUNCH_REASON="$crash"
	_br_relaunch; local rc=$?
	_BR_RELAUNCH_REASON=""
	if [[ "$crash" == audio_wrong_sink* ]]; then
		# debounce をリセットし、復旧成功(=bridge は起動)時のみ上限カウンタを進める。
		# 起動後も sink が直らなければ次の OK 観測まで蓄積し、上限で flap を止める。
		_BR_WRONG_SINK_PENDING=0
		[ "$rc" -eq 0 ] && _BR_WRONG_SINK_RELAUNCHES=$((_BR_WRONG_SINK_RELAUNCHES + 1))
	fi
	if [ "$rc" -eq 0 ]; then
		_BR_CONSEC_FAIL=0
		return 0
	elif [ "$rc" -eq 2 ]; then
		# 一過性ロック: 指数化せず base gap で速やかに再試行 (cooldown 汚染回避)
		_BR_CONSEC_FAIL=1
	else
		_BR_CONSEC_FAIL=$((_BR_CONSEC_FAIL + 1))
	fi
	return 1
}

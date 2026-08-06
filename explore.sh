#!/bin/bash
# explore.sh - 探索モード（配信なし）エントリポイント
#
# 実ゲーム (soviet_local.mjs + ブラウザ) を連続プレイして戦略を改善する。
# ラジオ・コメント・TTS・Twitch・OBS・soren91 など配信系を一切使わない。
# 試合プレイ→粛清判定→AI改善→繰り返し のループは従来の soren_loop.sh を流用する。
#
# 起動: ./explore.sh
# 停止: Ctrl-C または ./stop_soren.sh（tmp/stop）
#
# 設計:
#   - EXPLORE_MODE=1 を export し、配信系スクリプトの guard と
#     core/streaming_shim.sh の no-op 関数で配信系 sink を無効化する。
#   - tmp/state/explore_mode マーカでモードを固定（soren_loop.sh が毎周回
#     .env を再読込しても剥がれない）。
#   - soren_loop.sh を子プロセスで実行（多重起動ロックは soren_loop.sh が
#     保持するため、配信モードと同時起動は物理的に排他される）。
#   - ブリッジ (soviet_local.mjs)・improve_daemon.sh・soviet_watchdog.sh を
#     explore.sh が起動する。

# シグナル既定動作へ戻して再exec（soren_loop.sh と同方式。親が SIGINT/SIGTERM
# 無視状態を継承していると Ctrl-C が効かないケース対策）
if [ -z "${EXPLORE_SIGRESET_DONE:-}" ]; then
	export EXPLORE_SIGRESET_DONE=1
	exec python3 - "$0" "$@" <<'PY'
import os
import signal
import sys

targets = {signal.SIGINT, signal.SIGTERM}
if hasattr(signal, "SIGQUIT"):
    targets.add(signal.SIGQUIT)

for sig in targets:
    try:
        signal.signal(sig, signal.SIG_DFL)
    except Exception:
        pass

try:
    signal.pthread_sigmask(signal.SIG_UNBLOCK, targets)
except Exception:
    pass

os.execv("/bin/bash", ["/bin/bash", sys.argv[1], *sys.argv[2:]])
PY
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# --- モード決定 ---
export EXPLORE_MODE=1
mkdir -p tmp/state tmp/debug 2>/dev/null || true
touch tmp/state/explore_mode # .env 再読込で clobber されないマーカ

# --- 派生 env（配信系 silencer）---
export TWITCH_CLIP_ENABLED=0
export IMPROVE_AUDIO_SUMMARY_ENABLED=0
export WILDCARD_PROGRESS_AUDIO_ENABLED=0
export MONITOR_REPORT_AUDIO_ENABLED=0
export SOREN_OVERLAY_AUTORECOVER_ENABLED=0        # show_status*/overlay 自動復旧を停止
export IMPROVE_DAEMON_ALLOW_STANDALONE_HEADLESS=1 # explore.sh が daemon を起動する
export OBS_DASHBOARD_VISIBILITY_ENABLED=0         # 念のため（obs_control.sh guard と二重防御）
export HALT_STRATEGY_AFTER_SOVIET="${HALT_STRATEGY_AFTER_SOVIET:-0}"

# ゲーム音 (BGM/SE) を消音（配信はないので不要。環境変数で制御可能にしておく）
export SOREN_BGM_VOLUME="${SOREN_BGM_VOLUME:-0}"
export SOREN_SE_VOLUME="${SOREN_SE_VOLUME:-0}"

# 探索モードの改善サイクル長 (試合数)。明示指定時のみ有効。
# 未指定時は config.sh がデフォルト 12 を使う (配信モードの MIN_GAMES_BEFORE_IMPROVE には影響しない)。
export EXPLORE_MIN_GAMES_BEFORE_IMPROVE="${EXPLORE_MIN_GAMES_BEFORE_IMPROVE:-12}"

# --- 定数 ---
BRIDGE_PORT="${SOVIET_BRIDGE_PORT:-8080}"
BRIDGE_READY_TIMEOUT="${EXPLORE_BRIDGE_TIMEOUT:-180}"
GAME_STATE_FILE="game_state.json"
EXPLORE_PID_FILE="tmp/state/explore.pid"
EXPLORE_BRIDGE_PID_FILE="tmp/state/explore_bridge.pid"
BRIDGE_LOG_FILE="tmp/soviet_local.log"
EXPLORE_LOG_FILE="logs/explore.log"
EXPLORE_DAEMON_LOG_FILE="logs/improve_daemon.log"
EXPLORE_WATCHDOG_LOG_FILE="tmp/debug/soviet_watchdog.log"

mkdir -p logs 2>/dev/null || true
echo "$$" >"$EXPLORE_PID_FILE"

# 自分が起動したプロセスの管理フラグ（既存プロセスには触れない）
EXPLORE_STARTED_BRIDGE=0
EXPLORE_STARTED_WATCHDOG=0
EXPLORE_STARTED_DAEMON=0
_EXPLORE_BRIDGE_STARTED=0

_log() {
	echo "[explore $(date '+%H:%M:%S')] $*" | tee -a "$EXPLORE_LOG_FILE" >&2
}

# --- ブリッジ起動・生存判定 ---
_bridge_port_held() {
	# ポート保持プロセスPIDを出力。lsof が LISTEN プロセスを検出した場合のみ
	# 成功 (0) を返す。`lsof | head -1` のパイプは head が常に 0 を返すため、
	# 関数全体の終了コードを明示的に lsof の結果に基づかせる。
	local holder
	holder=$(lsof -nP -iTCP:"$BRIDGE_PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
	[ -n "$holder" ] || return 1
	printf '%s\n' "$holder"
	return 0
}

_bridge_state_fresh() {
	local age now
	[ -f "$GAME_STATE_FILE" ] || return 1
	now=$(date +%s)
	age=$(stat -f %m "$GAME_STATE_FILE" 2>/dev/null || stat -c %Y "$GAME_STATE_FILE" 2>/dev/null || echo 0)
	case "$age" in '' | *[!0-9]*) return 1 ;; esac
	[ $((now - age)) -lt 120 ]
}

_bridge_ready() {
	_bridge_port_held >/dev/null 2>&1 && _bridge_state_fresh
}

_start_bridge() {
	# 起動試行済みなら二重起動しない
	if [ "${_EXPLORE_BRIDGE_STARTED:-0}" -eq 1 ]; then
		return 0
	fi
	if _bridge_ready; then
		_log "既存ブリッジを検出（port=$BRIDGE_PORT）→ 再利用（探索モード所有として管理）"
		EXPLORE_STARTED_BRIDGE=1
		_EXPLORE_BRIDGE_STARTED=1
		return 0
	fi
	# 別プロセスが port を掴んでいるが game_state が古い場合は、watchdog が
	# 復旧するのに任せる（起動競合を防ぐ）。ここでは起動のみ。
	if _bridge_port_held >/dev/null 2>&1; then
		_log "port $BRIDGE_PORT は生存中だが game_state が古い → soviet_watchdog の復旧に任せる"
		EXPLORE_STARTED_BRIDGE=1
		_EXPLORE_BRIDGE_STARTED=1
		return 0
	fi
	_log "ブリッジ起動: node soviet_local.mjs"
	nohup node soviet_local.mjs >"$BRIDGE_LOG_FILE" 2>&1 &
	echo $! >"$EXPLORE_BRIDGE_PID_FILE"
	EXPLORE_STARTED_BRIDGE=1
	_EXPLORE_BRIDGE_STARTED=1
}

# ブリッジを確実に停止する: watchdog が tmux 経由で再起動すると PID が変わるため、
# PID ファイルではなく port 8080 のホルダーと tmux セッションを対象にする。
_stop_bridge() {
	if [ "${EXPLORE_LEAVE_BRIDGE_RUNNING:-0}" = "1" ]; then
		_log "EXPLORE_LEAVE_BRIDGE_RUNNING=1 → ブリッジを残す"
		return 0
	fi
	# 先に watchdog を止めないと tmux 経由で再起動される
	_stop_soviet_watchdog
	tmux kill-session -t soren_bridge 2>/dev/null || true
	local holder
	holder=$(_bridge_port_held 2>/dev/null || true)
	case "$holder" in
	'' | *[!0-9]*) holder=0 ;;
	esac
	if [ "$holder" -gt 0 ]; then
		_log "ブリッジ停止 (port $BRIDGE_PORT holder PID=$holder)"
		kill "$holder" 2>/dev/null || true
		sleep 2
		# Chrome プロセスも node 停止で終了するはず。残る場合は watchdog 任せ
	fi
	rm -f "$EXPLORE_BRIDGE_PID_FILE" 2>/dev/null || true
}

_wait_bridge_ready() {
	local deadline now
	deadline=$(($(date +%s) + BRIDGE_READY_TIMEOUT))
	while [ "$(date +%s)" -lt "$deadline" ]; do
		if _bridge_ready; then
			return 0
		fi
		# ポートが空いたなら (初期判定後に前セッション残骸が解放された場合等)
		# ブリッジを起動する。
		if ! _bridge_port_held >/dev/null 2>&1; then
			_start_bridge
		fi
		[ -f tmp/stop ] && return 1
		sleep 3
	done
	return 1
}

# --- 起動ヘルパー ---
_start_improve_daemon() {
	if [ -f "${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}" ]; then
		local pid
		pid=$(cat "${IMPROVE_DAEMON_PID_FILE}" 2>/dev/null)
		case "$pid" in '' | *[!0-9]*) pid=0 ;; esac
		if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
			_log "improve_daemon は既に稼働中 (PID=$pid)"
			EXPLORE_STARTED_DAEMON=0
			return 0
		fi
	fi
	_log "improve_daemon 起動"
	nohup ./improve_daemon.sh >"$EXPLORE_DAEMON_LOG_FILE" 2>&1 &
	disown 2>/dev/null || true
	EXPLORE_STARTED_DAEMON=1
	sleep 1
}

_start_soviet_watchdog() {
	# ブリッジ自動復旧（コア基盤）。ポート復旧は lsof で共存するため
	# explore.sh が起動したブリッジも問題なく監視できる。
	local pid
	pid=$(cat "tmp/state/.soviet_watchdog.lock/owner" 2>/dev/null || true)
	case "$pid" in '' | *[!0-9]*) pid=0 ;; esac
	if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
		_log "soviet_watchdog は既に稼働中 (PID=$pid)"
		EXPLORE_STARTED_WATCHDOG=0
		return 0
	fi
	_log "soviet_watchdog 起動"
	nohup ./soviet_watchdog.sh >"$EXPLORE_WATCHDOG_LOG_FILE" 2>&1 &
	disown 2>/dev/null || true
	EXPLORE_STARTED_WATCHDOG=1
}

# --- クリーンアップ ---
_stop_soviet_watchdog() {
	if [ "${EXPLORE_STARTED_WATCHDOG:-0}" != "1" ]; then
		return 0
	fi
	local pid
	pid=$(cat "tmp/state/.soviet_watchdog.lock/owner" 2>/dev/null || true)
	case "$pid" in '' | *[!0-9]*) pid=0 ;; esac
	if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
		_log "soviet_watchdog 停止 (PID=$pid)"
		kill "$pid" 2>/dev/null || true
		sleep 2
		if kill -0 "$pid" 2>/dev/null; then
			_log "soviet_watchdog が TERM に応答しない → KILL (PID=$pid)"
			kill -9 "$pid" 2>/dev/null || true
		fi
	fi
	rm -rf "tmp/state/.soviet_watchdog.lock" 2>/dev/null || true
}

_stop_improve_daemon() {
	if [ "${EXPLORE_STARTED_DAEMON:-0}" != "1" ]; then
		return 0
	fi
	local pid
	pid=$(cat "${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}" 2>/dev/null)
	case "$pid" in '' | *[!0-9]*) pid=0 ;; esac
	if [ "$pid" -gt 0 ] && kill -0 "$pid" 2>/dev/null; then
		_log "improve_daemon 停止 (PID=$pid)"
		kill "$pid" 2>/dev/null || true
		sleep 2
		if kill -0 "$pid" 2>/dev/null; then
			_log "improve_daemon が TERM に応答しない → KILL (PID=$pid)"
			kill -9 "$pid" 2>/dev/null || true
		fi
		# daemon が子プロセス (改善ジョブ) を spawn している場合、その子も掃除
		pkill -P "$pid" 2>/dev/null || true
	fi
	rm -f "${IMPROVE_DAEMON_PID_FILE:-tmp/state/improve_daemon.pid}" 2>/dev/null || true
	rm -rf "${DAEMON_LOCK_DIR:-tmp/state/improve_daemon.lockdir}" 2>/dev/null || true
}

_explore_cleanup() {
	local rc=$?
	if [ "${_EXPLORE_CLEANUP_DONE:-0}" -eq 1 ]; then
		exit "$rc"
	fi
	_EXPLORE_CLEANUP_DONE=1
	_log "クリーンアップ開始 (rc=$rc)"
	rm -f tmp/stop tmp/state/explore_mode "$EXPLORE_PID_FILE" 2>/dev/null || true
	# 子 soren_loop が残っていたら、その子孫 (strategy_runner 等) も確実に掃除する。
	# soren_loop 自体の cleanup_all が漏らす orphan を防ぐ防御。
	# ロックファイルは soren_loop の cleanup_all が先に消す可能性があるため、
	# ロック有無に依存せず strategy_runner は常に掃除する。
	if [ -f "tmp/.soren_loop.lock/pid" ]; then
		local loop_pid desc
		loop_pid=$(cat "tmp/.soren_loop.lock/pid" 2>/dev/null || true)
		case "$loop_pid" in
		'' | *[!0-9]*) loop_pid=0 ;;
		esac
		if [ "$loop_pid" -gt 0 ] && [ "$loop_pid" != "$$" ]; then
			for desc in $(pgrep -P "$loop_pid" 2>/dev/null || true); do
				kill "$desc" 2>/dev/null || true
			done
		fi
	fi
	# strategy_runner (python -u strategy_runner.py) が orphan 化していれば掃除。
	# コマンドラインは python3 のフルパスで始まるため、パターンは戦略名のみにマッチさせる。
	pkill -f "strategy_runner.py" 2>/dev/null || true
	# 自分が起動した improve_daemon の子プロセス (改善ジョブ) の orphan も掃除。
	# 探索モードは soren_loop ロック排他チェックを通過しているため、配信モードの
	# daemon が並走していることはない。
	if [ "${EXPLORE_STARTED_DAEMON:-0}" = "1" ]; then
		pkill -f "improve_daemon.sh" 2>/dev/null || true
	fi
	# 自分が起動したブリッジ・watchdog・improve_daemon を確実に停止する。
	# 既存のブリッジ/watchdog/daemon（探索前に既に動いていたもの）には触れない。
	if [ "${EXPLORE_STARTED_BRIDGE:-0}" = "1" ]; then
		_stop_bridge
	fi
	_stop_soviet_watchdog
	_stop_improve_daemon
	_log "クリーンアップ完了"
	exit "$rc"
}

# シグナル受信時は子プロセスの soren_loop.sh にも伝搬してからクリーンアップする
_explore_signal_handler() {
	local sig="${1:-INT}"
	_log "SIG${sig} を受信: 子プロセスへ伝搬して停止します"
	# 子 soren_loop が tmp/.soren_loop.lock を保持している場合は SIGINT を送る
	if [ -f "tmp/.soren_loop.lock/pid" ]; then
		local loop_pid
		loop_pid=$(cat "tmp/.soren_loop.lock/pid" 2>/dev/null || true)
		case "$loop_pid" in
		'' | *[!0-9]*) loop_pid=0 ;;
		esac
		if [ "$loop_pid" -gt 0 ] && [ "$loop_pid" != "$$" ] && kill -0 "$loop_pid" 2>/dev/null; then
			_log "soren_loop に SIG${sig} 送信 (PID=$loop_pid)"
			kill -"$sig" "$loop_pid" 2>/dev/null || true
		fi
	fi
	# soren_loop の終了を少し待ってから自分のクリーンアップへ
	sleep 2
	_explore_cleanup
}

trap '_explore_signal_handler INT' INT
trap '_explore_signal_handler TERM' TERM
trap '_explore_cleanup' EXIT

# --- 起動 ---
_log "=== Soren Explore Mode (配信なし) ==="
_log "EXPLORE_MODE=1 / 実ゲームプレイ + 戦略改善ループ"

# 配信モードとの排他確認: soren_loop が多重起動ロックを保持している場合は
# 配信モード（または別探索）が稼働中とみなし、誤ってブリッジを止めないよう
# ここで abort する。
if [ -f "tmp/.soren_loop.lock/pid" ]; then
	_existing_loop_pid=$(cat "tmp/.soren_loop.lock/pid" 2>/dev/null || true)
	case "$_existing_loop_pid" in
	'' | *[!0-9]*) _existing_loop_pid=0 ;;
	esac
	if [ "$_existing_loop_pid" -gt 0 ] && [ "$_existing_loop_pid" != "$$" ] &&
		kill -0 "$_existing_loop_pid" 2>/dev/null; then
		_log "ERROR: soren_loop が稼働中 (PID=$_existing_loop_pid)。配信モード/別探索と排他のため中止します"
		_log "探索モードを先に終了してから起動してください"
		exit 1
	fi
fi

# ブリッジ起動 → 起動待ち
_start_bridge
if ! _wait_bridge_ready; then
	_log "ERROR: ブリッジが ${BRIDGE_READY_TIMEOUT}s 以内に ready にならない → 中断"
	exit 1
fi
_log "ブリッジ ready"

# コア常駐プロセス
_start_soviet_watchdog
_start_improve_daemon

# 本ループ（soren_loop.sh が多重起動ロック・シグナル・ホットリロードを担当）
_log "soren_loop.sh を起動（配信モードとは多重起動ロックで排他）"
./soren_loop.sh "$@"
EXPLORE_LOOP_RC=$?

_log "soren_loop.sh 終了 (rc=$EXPLORE_LOOP_RC)"
exit "$EXPLORE_LOOP_RC"

# soren91_control.sh - soren91 (メリケンAI) の起動・停止・改善キック管理
#
# eloop_lib.sh から source される。
# SOREN91_ENABLED=1 (.env) でなければ全関数は即 return 0。

# --- 定数 ---
SOREN91_ENABLED="${SOREN91_ENABLED:-0}"
SOREN91_STOP_TIMEOUT="${SOREN91_STOP_TIMEOUT:-120}"
SOREN91_DIR="$ELOOP_LIB_DIR/soren91"
SOREN91_PID_FILE="$SOREN91_DIR/tmp/soren91.pid"
SOREN91_IMPROVE_PID_FILE="$SOREN91_DIR/tmp/soren91_improve.pid"
SOREN91_IMPROVE_LOCK="$SOREN91_DIR/tmp/soren91_improve.lock"
SOREN91_SESSION_FILE="$SOREN91_DIR/tmp/session_games.json"
SOREN91_STOP_FILE="$SOREN91_DIR/tmp/stop"

_soren91_enabled() {
	[ "${SOREN91_ENABLED:-0}" = "1" ]
}

soren91_is_running() {
	_soren91_enabled || return 1
	[ -f "$SOREN91_PID_FILE" ] || return 1
	local pid
	pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		return 1
	fi
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	if echo "$cmd" | grep -q "main.mjs"; then
		return 0
	fi
	return 1
}

soren91_start() {
	_soren91_enabled || return 0
	if soren91_is_running; then
		log "[SOREN91] Already running, skip start"
		return 0
	fi

	log "[SOREN91] Starting soren91 (メリケンAI)..."
	rm -f "$SOREN91_STOP_FILE"
	mkdir -p "$SOREN91_DIR/tmp" 2>/dev/null || true

	# セッション開始時のゲーム番号を記録
	local start_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		start_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	printf '{"start_game":%d,"start_time":"%s"}\n' "$start_game" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		> "$SOREN91_SESSION_FILE"

	# soren91 ディレクトリで node main.mjs をバックグラウンド起動
	(
		cd "$SOREN91_DIR" && \
		SOREN91_EXTERNAL_IMPROVE=1 node main.mjs >> "$SOREN91_DIR/tmp/soren91.log" 2>&1
	) &
	local pid=$!
	echo "$pid" > "$SOREN91_PID_FILE"

	# 5秒後に生存チェック
	sleep 5
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Started successfully (PID=$pid, start_game=$start_game)"
	else
		log "[SOREN91] WARNING: Process died immediately (PID=$pid)"
		rm -f "$SOREN91_PID_FILE"
		return 1
	fi
	return 0
}

soren91_stop() {
	_soren91_enabled || return 0
	if ! soren91_is_running; then
		log "[SOREN91] Not running, skip stop"
		rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
		return 0
	fi

	local pid
	pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
	log "[SOREN91] Stopping soren91 (PID=$pid)..."

	# graceful stop: stop ファイルを作成して現在のゲーム終了を待つ
	touch "$SOREN91_STOP_FILE"

	local waited=0
	while [ "$waited" -lt "$SOREN91_STOP_TIMEOUT" ]; do
		if ! kill -0 "$pid" 2>/dev/null; then
			log "[SOREN91] Stopped gracefully after ${waited}s"
			break
		fi
		sleep 2
		waited=$((waited + 2))
	done

	# タイムアウト: 子プロセス含めて強制停止
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Timeout after ${SOREN91_STOP_TIMEOUT}s, force stopping..."
		_stop_loop_descendants "$pid"
		_stop_pid_with_fallback "$pid" "soren91"
	fi

	# セッション終了時のゲーム番号を記録
	local end_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		end_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	if [ -f "$SOREN91_SESSION_FILE" ]; then
		python3 -c "
import json, sys
with open('$SOREN91_SESSION_FILE') as f:
    sess = json.load(f)
sess['end_game'] = $end_game
sess['end_time'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open('$SOREN91_SESSION_FILE', 'w') as f:
    json.dump(sess, f)
" 2>/dev/null || true
	fi

	rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
	log "[SOREN91] Stopped (end_game=$end_game)"
	return 0
}

soren91_improve() {
	_soren91_enabled || return 0

	# ロック + PID生存チェック
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				log "[SOREN91] Improvement already running (PID=$imp_pid), skip"
				return 0
			fi
			;;
		esac
		# stale lock cleanup
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	fi

	# セッションデータからゲーム範囲を取得
	if [ ! -f "$SOREN91_SESSION_FILE" ]; then
		log "[SOREN91] No session file, skip improve"
		return 0
	fi

	local start_game end_game
	start_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('start_game',0))" 2>/dev/null || echo 0)
	end_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('end_game',0))" 2>/dev/null || echo 0)

	local games_played=$((end_game - start_game))
	if [ "$games_played" -le 0 ]; then
		log "[SOREN91] No games played in session (start=$start_game, end=$end_game), skip improve"
		return 0
	fi

	log "[SOREN91] Starting improvement for games $start_game-$end_game ($games_played games)..."
	touch "$SOREN91_IMPROVE_LOCK"

	(
		cd "$SOREN91_DIR" && \
		node improve.mjs --standalone "$start_game" "$end_game" \
			>> "$SOREN91_DIR/tmp/soren91_improve.log" 2>&1
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	) &
	local pid=$!
	echo "$pid" > "$SOREN91_IMPROVE_PID_FILE"
	log "[SOREN91] Improvement started (PID=$pid, games=$start_game-$end_game)"
	return 0
}

soren91_cleanup() {
	_soren91_enabled || return 0

	# プレイヤープロセス停止
	if [ -f "$SOREN91_PID_FILE" ]; then
		local pid
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$pid" 2>/dev/null; then
				log "[SOREN91] Cleanup: stopping player (PID=$pid)"
				_stop_loop_descendants "$pid"
				_stop_pid_with_fallback "$pid" "soren91_player"
			fi
			;;
		esac
	fi

	# 改善プロセス停止
	if [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				log "[SOREN91] Cleanup: stopping improve (PID=$imp_pid)"
				_stop_loop_descendants "$imp_pid"
				_stop_pid_with_fallback "$imp_pid" "soren91_improve"
			fi
			;;
		esac
	fi

	# ファイルクリーンアップ
	rm -f "$SOREN91_PID_FILE" "$SOREN91_IMPROVE_PID_FILE" \
		"$SOREN91_IMPROVE_LOCK" "$SOREN91_STOP_FILE"
}

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
SOREN91_RUNNER_SCRIPT="$SOREN91_DIR/run_player_loop.sh"
SOREN91_VOICEVOX_SPEAKER="${SOREN91_VOICEVOX_SPEAKER:-46}"

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
	if echo "$cmd" | grep -Eq 'main\.mjs|run_player_loop\.sh'; then
		return 0
	fi
	return 1
}

_soren91_is_improve_process() {
	# PIDが soren91 improve プロセスかどうか確認
	local pid="$1"
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	kill -0 "$pid" 2>/dev/null || return 1
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	echo "$cmd" | grep -q "improve.mjs" && return 0
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

	# 前回の soren91 improve がまだ実行中なら session_games.json を上書きしない
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local prev_imp_pid
		prev_imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		if _soren91_is_improve_process "$prev_imp_pid"; then
			log "[SOREN91] Previous improve still running (PID=$prev_imp_pid), keeping session_games.json"
		fi
	fi

	# セッション開始時のゲーム番号を記録
	local start_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		start_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	printf '{"start_game":%d,"start_time":"%s"}\n' "$start_game" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		> "$SOREN91_SESSION_FILE"

	# 再試行付きランナーをバックグラウンド起動
	(
		cd "$SOREN91_DIR" && \
		/bin/bash "$SOREN91_RUNNER_SCRIPT"
	) &
	local pid=$!
	echo "$pid" > "$SOREN91_PID_FILE"

	# 5秒後に生存チェック
	sleep 5
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Started successfully (PID=$pid, start_game=$start_game)"
		# 読み上げアナウンス (バックグラウンド)
		{
			local announce_file
			announce_file=$(mktemp /tmp/eloop_soren91_announce.XXXXXX)
			printf '%s\n' "中華AIが戦略を改善中。その間、メリケンAIがソ連ゲーム91で同志を迎え撃ちます。挑戦お待ちしています" > "$announce_file"
			SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:announce" ./say_enqueue.sh "$announce_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
			rm -f "$announce_file"
		} &
	else
		log "[SOREN91] WARNING: Process died immediately (PID=$pid)"
		rm -f "$SOREN91_PID_FILE"
		return 1
	fi
	return 0
}

_soren91_record_end_game() {
	# セッション終了時のゲーム番号を記録 (stop/早期終了の両方から呼ばれる)
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
	echo "$end_game"
}

soren91_stop() {
	_soren91_enabled || return 0

	local pid=""
	if [ -f "$SOREN91_PID_FILE" ]; then
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in ''|*[!0-9]*) pid="" ;; esac
	fi

	if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
		# プロセスが既に終了 → end_game だけ記録して終了
		log "[SOREN91] Not running, recording end_game"
		local eg
		eg=$(_soren91_record_end_game)
		rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
		log "[SOREN91] Stopped (already exited, end_game=$eg)"
		return 0
	fi

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

	local eg
	eg=$(_soren91_record_end_game)

	rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
	log "[SOREN91] Stopped (end_game=$eg)"
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

	# プレイヤープロセス停止 (コマンド名を検証して誤kill防止)
	if [ -f "$SOREN91_PID_FILE" ]; then
		local pid
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$pid" 2>/dev/null; then
				local cmd
				cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
				if echo "$cmd" | grep -Eq 'main\.mjs|run_player_loop\.sh'; then
					log "[SOREN91] Cleanup: stopping player (PID=$pid)"
					_stop_loop_descendants "$pid"
					_stop_pid_with_fallback "$pid" "soren91_player"
				else
					log "[SOREN91] Cleanup: PID=$pid is not soren91 player ($cmd), skipping"
				fi
			fi
			;;
		esac
	fi

	# 改善プロセス停止 (コマンド名を検証)
	if [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				local cmd
				cmd=$(ps -p "$imp_pid" -o command= 2>/dev/null || echo "")
				if echo "$cmd" | grep -q "improve.mjs"; then
					log "[SOREN91] Cleanup: stopping improve (PID=$imp_pid)"
					_stop_loop_descendants "$imp_pid"
					_stop_pid_with_fallback "$imp_pid" "soren91_improve"
				else
					log "[SOREN91] Cleanup: PID=$imp_pid is not soren91 improve ($cmd), skipping"
				fi
			fi
			;;
		esac
	fi

	# ファイルクリーンアップ
	rm -f "$SOREN91_PID_FILE" "$SOREN91_IMPROVE_PID_FILE" \
		"$SOREN91_IMPROVE_LOCK" "$SOREN91_STOP_FILE"
}

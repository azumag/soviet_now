# broadcast/comment_worker.sh - player/watcherデーモン管理


_recover_orphan_comment_playing_files() {
	# コメント用 say_enqueue が動作中なら .playing は現役の可能性が高いので触らない
	if pgrep -f "say_enqueue.sh --no-preempt .*comment_.*\\.playing" >/dev/null 2>&1; then
		return
	fi
	for orphan in "$COMMENT_QUEUE_DIR"/comment_*.playing; do
		[ -f "$orphan" ] || continue
		local now mtime age
		now=$(date +%s)
		mtime=$(stat -f %m "$orphan" 2>/dev/null || echo "$now")
		age=$((now - mtime))
		# 直近で生成された .playing はリネーム直後の可能性があるためスキップ
		[ "$age" -lt 30 ] && continue
		local recovered="${orphan%.playing}.txt"
		mv "$orphan" "$recovered" 2>/dev/null
		echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] リカバリ: $orphan → $recovered" >> tmp/.say_queue/debug.log
	done
}

_play_comment_queue() {
	# debug.log ローテーション (500行超→200行に切り詰め)
	local dbg="tmp/.say_queue/debug.log"
	if [ -f "$dbg" ] && [ "$(wc -l < "$dbg")" -gt 500 ]; then
		tail -200 "$dbg" > "${dbg}.tmp" && mv "${dbg}.tmp" "$dbg"
	fi
	_recover_orphan_comment_playing_files
	for qf in $(ls -1t "$COMMENT_QUEUE_DIR"/comment_*.txt 2>/dev/null | sort); do
		if [ -f "$qf" ]; then
			local expected_mode="" current_mode=""
			expected_mode=$(_broadcast_read_expected_mode "$qf" 2>/dev/null || true)
			current_mode=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
			if [ -n "$expected_mode" ] && [ "$expected_mode" != "$current_mode" ]; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] mode不一致で破棄: $qf expected=$expected_mode current=$current_mode" >> tmp/.say_queue/debug.log
				_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
				rm -f "$qf"
				continue
			fi

			# 重複チェック: 同じ内容を再度再生しない
			local file_hash
			file_hash=$(md5 -q "$qf" 2>/dev/null)
			if [ -n "$file_hash" ] && grep -qF "$file_hash" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 重複スキップ: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
				rm -f "$qf"
				continue
			fi

			# 再生前にリネームして他プレイヤーとの二重再生を防ぐ
			local playing_file="${qf%.txt}.playing"
			if mv "$qf" "$playing_file" 2>/dev/null; then
				expected_mode=$(_broadcast_read_expected_mode "$playing_file" 2>/dev/null || true)
				current_mode=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
				if [ -n "$expected_mode" ] && [ "$expected_mode" != "$current_mode" ]; then
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] mode不一致で再生前破棄: $playing_file expected=$expected_mode current=$current_mode" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$playing_file" 2>/dev/null || true
					rm -f "$playing_file"
					continue
				fi
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 再生開始: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				# ハッシュを記録（再生開始前に記録して、kill時にも重複防止）
				echo "$file_hash" >> "$COMMENT_PLAYED_HASHES_FILE"
				# ハッシュファイルを最新50件に制限
				tail -50 "$COMMENT_PLAYED_HASHES_FILE" > "${COMMENT_PLAYED_HASHES_FILE}.tmp" 2>/dev/null && \
					mv "${COMMENT_PLAYED_HASHES_FILE}.tmp" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null
					# soren91 (メリケンAI) プレイ中は声を切り替え
				local _cw_vo_speaker=""
				if soren91_is_running 2>/dev/null; then
					_cw_vo_speaker="${SOREN91_VOICEVOX_SPEAKER:-46}"
				fi
				if SAY_VOICEVOX_SPEAKER_OVERRIDE="${_cw_vo_speaker:-}" SAY_CONTEXT_LABEL="comment" ./say_enqueue.sh --no-preempt "$playing_file" "$RADIO_SAY_RATE" 0; then
						_remember_spoken_comment "$playing_file"
					fi
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 再生完了: $playing_file" >> tmp/.say_queue/debug.log
				_broadcast_clear_expected_mode "$playing_file" 2>/dev/null || true
				rm -f "$playing_file"
			fi
		fi
	done

	# コメントが空のタイミングで deferred ラジオを1本だけ流す
	process_external_audio_triggers
	_play_deferred_radio_queue_once
}

COMMENT_PLAYER_PID_FILE="tmp/.comment_queue/player.pid"
COMMENT_PLAYER_TOKEN_FILE="tmp/.comment_queue/player.token"
COMMENT_WATCHER_TOKEN_FILE="tmp/.comment_queue/watcher.token"

_is_comment_worker_healthy() {
	local pid_file="$1" heartbeat_file="$2" ttl="${3:-30}"
	[ -f "$pid_file" ] || return 1

	local pid
	pid=$(cat "$pid_file" 2>/dev/null)
	[ -n "$pid" ] || return 1
	case "$pid" in
	''|*[!0-9]*|0) return 1 ;;
	esac
	kill -0 "$pid" 2>/dev/null || return 1
	# ttl<=0 の場合は PID 生存のみでヘルシー判定
	if [ "$ttl" -le 0 ]; then
		return 0
	fi

	[ -f "$heartbeat_file" ] || return 1
	local hb now age
	hb=$(cat "$heartbeat_file" 2>/dev/null)
	case "$hb" in
	''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	age=$((now - hb))
	[ "$age" -le "$ttl" ] || return 1
	return 0
}

start_comment_player() {
	# 既存プレイヤーが生存中なら重複起動しない（再生中はheartbeatが止まり得るためPID優先）
	if _is_comment_worker_healthy "$COMMENT_PLAYER_PID_FILE" "$COMMENT_PLAYER_HEARTBEAT_FILE" 0; then
		return
	fi
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] 再生プロセスPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
	rm -f "$COMMENT_PLAYER_TOKEN_FILE"
	mkdir -p "$(dirname "$COMMENT_PLAYER_PID_FILE")"
	local player_token
	player_token="player_$(date +%s)_$$_$RANDOM"
	echo "$player_token" > "$COMMENT_PLAYER_TOKEN_FILE"

	(
		# バックグラウンド subshell の実PIDは親が保持する $! とズレることがあるため、
		# 置き換え判定は PID ではなく ownership token で行う。
		_cp_my_pid=$(_my_pid 2>/dev/null || echo $$)
		_cp_owner_token="$player_token"
		_recover_orphan_comment_playing_files
		while true; do
			_cp_file_token=$(cat "$COMMENT_PLAYER_TOKEN_FILE" 2>/dev/null)
			if [ "$_cp_file_token" != "$_cp_owner_token" ]; then
				exit 0
			fi
			if ! source ./eloop_lib.sh 2>/dev/null; then
				echo "[COMMENT] WARNING: eloop_lib.sh の再読込に失敗 (前回定義で継続)" >> tmp/.say_queue/debug.log
			fi
			date +%s >"$COMMENT_PLAYER_HEARTBEAT_FILE" 2>/dev/null || true
			_play_comment_queue
			sleep 5
		done
	) &
	local cpid=$!
	echo "$cpid" > "$COMMENT_PLAYER_PID_FILE"
	log "[COMMENT] 再生プロセス開始 (PID=$cpid)"
}

stop_comment_player() {
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local cpid
		cpid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$cpid" ] && [ "$cpid" != "$$" ] && kill -0 "$cpid" 2>/dev/null; then
			kill "$cpid" 2>/dev/null
			wait "$cpid" 2>/dev/null
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
	rm -f "$COMMENT_PLAYER_TOKEN_FILE"
}

#=== コメント監視デーモン ===
# 10秒ごとにTwitchコメントをポーリングし、新コメントがあれば即座に生成→キュー追加

start_comment_watcher() {
	# 既存ウォッチャーが生存中なら重複起動しない（PID + heartbeat で判定）
	if _is_comment_worker_healthy "$COMMENT_WATCHER_PID_FILE" "$COMMENT_WATCHER_HEARTBEAT_FILE" "$COMMENT_WORKER_HEALTH_TTL"; then
		return
	fi
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] ウォッチャーPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
	rm -f "$COMMENT_WATCHER_TOKEN_FILE"
	mkdir -p "$(dirname "$COMMENT_WATCHER_PID_FILE")"
	local watcher_token
	watcher_token="watcher_$(date +%s)_$$_$RANDOM"
	echo "$watcher_token" > "$COMMENT_WATCHER_TOKEN_FILE"

	(
		_cw_my_pid=$(_my_pid 2>/dev/null || echo $$)
		_cw_owner_token="$watcher_token"
		while true; do
			_cw_file_token=$(cat "$COMMENT_WATCHER_TOKEN_FILE" 2>/dev/null)
			if [ "$_cw_file_token" != "$_cw_owner_token" ]; then
				exit 0
			fi
			source ./eloop_lib.sh 2>/dev/null || true
			date +%s >"$COMMENT_WATCHER_HEARTBEAT_FILE" 2>/dev/null || true

			# コメント生成が進行中なら今回はスキップ
			local gen_pidfile="tmp/.twitch_chat/comment_gen.pid"
			local gen_running=false
			if [ -f "$gen_pidfile" ]; then
				local gen_pid
				gen_pid=$(cat "$gen_pidfile" 2>/dev/null)
				gen_pid="${gen_pid%%|*}"
				case "$gen_pid" in
				''|*[!0-9]*) gen_pid="" ;;
				esac
				if [ -n "$gen_pid" ] && kill -0 "$gen_pid" 2>/dev/null; then
					gen_running=true
				fi
			fi

			if [ "$gen_running" = "true" ]; then
				# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
				./twitch_chat.sh fetch 2>/dev/null
			else
				# idle時は pending から生成（成功時に処理済み行のみ削除）
				generate_comment_response
				# コメントがなく soren91 プレイ中なら、ゲーム感想を生成
				local _new_gen_running=false
				if [ -f "$gen_pidfile" ]; then
					local _ng_pid
					_ng_pid=$(cat "$gen_pidfile" 2>/dev/null)
					_ng_pid="${_ng_pid%%|*}"
					case "$_ng_pid" in ''|*[!0-9]*) ;; *)
						kill -0 "$_ng_pid" 2>/dev/null && _new_gen_running=true
					;; esac
				fi
				if [ "$_new_gen_running" = "false" ]; then
					generate_soren91_game_commentary 2>/dev/null || true
				fi
			fi

			sleep "$COMMENT_WATCHER_INTERVAL"
		done
	) &
	local wpid=$!
	echo "$wpid" > "$COMMENT_WATCHER_PID_FILE"
	disown "$wpid"
	log "[COMMENT] ウォッチャー開始 (PID=$wpid, interval=${COMMENT_WATCHER_INTERVAL}s)"
}

stop_comment_watcher() {
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local wpid
		wpid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$wpid" ] && [ "$wpid" != "$$" ] && kill -0 "$wpid" 2>/dev/null; then
			kill "$wpid" 2>/dev/null
			wait "$wpid" 2>/dev/null
			log "[COMMENT] ウォッチャー停止 (PID=$wpid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
	rm -f "$COMMENT_WATCHER_TOKEN_FILE"
}

#=== プロセス管理 ===

_CLEANUP_ALL_RUNNING=0

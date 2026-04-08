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
					_comment_clear_generation_meta "$qf" 2>/dev/null || true
					rm -f "$qf"
					continue
				fi

			# 重複チェック: 同じ内容を再度再生しない
			local file_hash
			file_hash=$(md5 -q "$qf" 2>/dev/null)
				if [ -n "$file_hash" ] && grep -qF "$file_hash" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null; then
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 重複スキップ: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
					_comment_clear_generation_meta "$qf" 2>/dev/null || true
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
						_comment_clear_generation_meta "$playing_file" 2>/dev/null || true
							rm -f "$playing_file"
							continue
						fi
					local _comment_meta_summary=""
					_comment_meta_summary=$(_comment_generation_debug_summary "$playing_file" 2>/dev/null || true)
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 再生開始: $qf (hash=$file_hash${_comment_meta_summary:+, ${_comment_meta_summary}})" >> tmp/.say_queue/debug.log
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
					_comment_clear_generation_meta "$playing_file" 2>/dev/null || true
					rm -f "$playing_file"
				fi
			fi
	done

	# コメントが空のタイミングで deferred ラジオを1本だけ流す
	process_external_audio_triggers
	_play_deferred_radio_queue_once
}

# Legacy PID/token files (no longer used — workers are managed externally)
COMMENT_PLAYER_PID_FILE="tmp/.comment_queue/player.pid"

# Legacy stubs — workers are now managed externally via workers/*.sh
start_comment_player() { :; }
stop_comment_player() { :; }
start_comment_watcher() { :; }
stop_comment_watcher() { :; }
}

#=== プロセス管理 ===

_CLEANUP_ALL_RUNNING=0

# broadcast/radio_state.sh - ラジオ状態管理, 音声割り込み, キュー


_radio_gc_stale_state() {
	local current mode corner ts owner_pid now age
	current=$(cat "$RADIO_STATE_FILE" 2>/dev/null) || return 0
	IFS=':' read -r mode corner ts owner_pid _ <<<"$current"
	case "$ts" in
	''|*[!0-9]*) return 0 ;;
	esac
	now=$(date +%s)
	age=$((now - ts))
	[ "$age" -le "$RADIO_STATE_STALE_SEC" ] && return 0
	case "$owner_pid" in
	''|*[!0-9]*) owner_pid="" ;;
	esac
	if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
		return 0
	fi
	rm -f "$RADIO_STATE_FILE"
	log "[RADIO:${corner:-unknown}] stale state clear: mode=${mode:-unknown} age=${age}s"
}

_radio_set_state() {
	local mode="$1" corner="$2"
	[ -n "$mode" ] || return 1
	[ -n "$corner" ] || return 1
	_radio_gc_stale_state
	printf '%s:%s:%s:%s\n' "$mode" "$corner" "$(date +%s)" "$$" >"$RADIO_STATE_FILE"
}

# 自分のコーナーの状態ファイルだけ安全に削除 (並列実行の競合防止)
_radio_clear_state() {
	local my_corner="$1" reason="${2:-}"
	local current
	_radio_gc_stale_state
	current=$(cat "$RADIO_STATE_FILE" 2>/dev/null) || return 0
	case "$current" in
	*":${my_corner}:"*)
		rm -f "$RADIO_STATE_FILE"
		[ -n "$reason" ] && log "[RADIO:${my_corner}] state clear: ${reason}"
		;;
	esac
}

_interrupt_current_audio_playback() {
	local reason="${1:-priority_audio}"
	local cs_line owner owner_pid say_pid
	cs_line=$(cat "tmp/.say_queue/current_source" 2>/dev/null || true)
	owner=$(printf '%s' "$cs_line" | awk -F'|' 'NR==1{print $1}')
	owner_pid="${owner%%:*}"
	say_pid=$(cat "tmp/.say_queue/pid" 2>/dev/null || true)

	case "$say_pid" in
	''|*[!0-9]*) say_pid="" ;;
	esac
	case "$owner_pid" in
	''|*[!0-9]*) owner_pid="" ;;
	esac

	if [ -n "$say_pid" ] && kill -0 "$say_pid" 2>/dev/null; then
		log "[AUDIO] child停止: pid=${say_pid} reason=${reason}"
		kill -9 "$say_pid" 2>/dev/null || true
	fi
	if [ -n "$owner_pid" ] && kill -0 "$owner_pid" 2>/dev/null; then
		log "[AUDIO] enqueue停止: pid=${owner_pid} reason=${reason}"
		kill "$owner_pid" 2>/dev/null || true
		sleep 1
		kill -9 "$owner_pid" 2>/dev/null || true
	fi

	local waited=0
	while [ -d "tmp/.say_queue/.lock" ] && [ "$waited" -lt 30 ]; do
		sleep 0.2
		waited=$((waited + 1))
	done
	rm -f "tmp/.say_queue/pid" 2>/dev/null || true
}

_play_priority_audio_file() {
	local audio_file="$1" corner_name="$2"
	[ -s "$audio_file" ] || return 1
	_interrupt_current_audio_playback "priority:${corner_name}"
	_radio_set_state "playing" "$corner_name"
	_refresh_radio_intro_for_playback_file "$audio_file" "$corner_name"
	SAY_CONTEXT_LABEL="radio:${corner_name}" ./say_enqueue.sh "$audio_file" "$RADIO_SAY_RATE" 0
}

_cancel_russia_celebration_worker() {
	local worker_pid=""
	worker_pid=$(cat "$RUSSIA_CELEBRATION_WORKER_PID_FILE" 2>/dev/null || true)
	case "$worker_pid" in
	''|*[!0-9]*) worker_pid="" ;;
	esac
	if [ -n "$worker_pid" ] && kill -0 "$worker_pid" 2>/dev/null; then
		log "[RUSSIA] worker停止: pid=${worker_pid}"
		kill "$worker_pid" 2>/dev/null || true
		sleep 1
		kill -9 "$worker_pid" 2>/dev/null || true
	fi
	rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE" "$TMP_DEBUG_DIR/radio_russia_celebration.txt" 2>/dev/null || true
}

_radio_mark_done() {
	local done_marker="$1"
	[ -n "$done_marker" ] || return 0
	touch "$done_marker"
	# 古い重複防止マーカーを掃除（最新200件だけ保持）
	local old_markers
	old_markers=$(ls -1t $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | tail -n +201 || true)
	if [ -n "$old_markers" ]; then
		echo "$old_markers" | xargs rm -f 2>/dev/null || true
	fi
}

_enqueue_deferred_radio_talk() {
	local talk_file="$1" game_num="$2" corner_name="$3"
	[ -s "$talk_file" ] || return 1
	mkdir -p "$RADIO_DEFERRED_QUEUE_DIR" 2>/dev/null || true
	local deferred_file
	deferred_file="$RADIO_DEFERRED_QUEUE_DIR/radio_$(date +%s)_${game_num}_${corner_name}_${RANDOM}.txt"
	cp "$talk_file" "$deferred_file" 2>/dev/null || return 1
	echo "$deferred_file"
}

_run_jiji_corner_guarded() {
	local game_num="$1" score="$2"
	local jiji_lock_dir="$TMP_STATE_DIR/.jiji_inflight"
	local jiji_last_file="$TMP_STATE_DIR/.jiji_last_run"

	if ! mkdir "$jiji_lock_dir" 2>/dev/null; then
		log "[JIJI] duplicate skip: already in-flight"
		return 0
	fi

	if start_radio_corner_jiji "$game_num" "$score"; then
		echo "$(date +%s)" >"$jiji_last_file"
		log "[JIJI] completed: next interval starts now"
	else
		log "[JIJI] failed before playback/queue completion -> will retry next loop"
	fi

	rmdir "$jiji_lock_dir" 2>/dev/null || true
}

_play_deferred_radio_queue_once() {
	# コメント未消化がある間は deferred ラジオを再生しない
	local comment_queued=0 comment_playing=0 comment_total=0
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	[ "$comment_total" -gt 0 ] && return 0

	# say_enqueue プロセスが溜まりすぎている場合はスキップ（蓄積→重複再生を防止）
	local _say_proc_count
	_say_proc_count=$(pgrep -fc 'say_enqueue.sh' 2>/dev/null || echo 0)
	if [ "${_say_proc_count:-0}" -gt 3 ]; then
		log "[RADIO:deferred] say_enqueue プロセス過多 (${_say_proc_count}) → スキップ"
		return 0
	fi

	local stale_playing=""
	for stale_playing in "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.playing; do
		[ -f "$stale_playing" ] || continue
		local stale_mtime="" stale_age=0 retry_file=""
		stale_mtime=$(stat -f '%m' "$stale_playing" 2>/dev/null || true)
		case "$stale_mtime" in
		''|*[!0-9]*) continue ;;
		esac
		stale_age=$(( $(date +%s) - stale_mtime ))
		[ "$stale_age" -le "$RADIO_STATE_STALE_SEC" ] && continue
		# say_enqueue がまだ動いている場合は stale 復帰しない（重複再生の原因になる）
		if pgrep -f "say_enqueue.sh.*$(basename "$stale_playing")" >/dev/null 2>&1; then
			log "[RADIO:deferred] stale だが say_enqueue 実行中 → スキップ: $(basename "$stale_playing") age=${stale_age}s"
			continue
		fi
		retry_file="${stale_playing%.playing}.txt"
		if [ -f "$retry_file" ]; then
			rm -f "$stale_playing"
			log "[RADIO:deferred] stale playing削除: $(basename "$stale_playing") age=${stale_age}s"
		else
			mv "$stale_playing" "$retry_file" 2>/dev/null || true
			log "[RADIO:deferred] stale playing復帰: $(basename "$retry_file") age=${stale_age}s"
		fi
	done

	local qf
	qf=$(ls -1 "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.txt 2>/dev/null | sort | head -n 1)
	[ -n "$qf" ] || return 0
	[ -f "$qf" ] || return 0

	local playing_file="${qf%.txt}.playing"
	if mv "$qf" "$playing_file" 2>/dev/null; then
		local deferred_corner=""
			deferred_corner=$(basename "$playing_file" | sed -E 's/^radio_[0-9]+_[0-9]+_([^_]+)_.*/\1/' )
			# CC表記をTwitchチャットに投稿（deferred再生開始タイミング）
			local news_title_file="${playing_file%.playing}.news_title"
			local news_cc_file="${playing_file%.playing}.cc_text"
			if [ "$deferred_corner" = "news" ] && [ -f "$news_cc_file" ]; then
				local deferred_cc_text
				deferred_cc_text=$(cat "$news_cc_file" 2>/dev/null)
				[ -n "$deferred_cc_text" ] && _post_cc_text_to_chat "$deferred_cc_text" &
			elif [ "$deferred_corner" = "news" ] && [ -f "$news_title_file" ]; then
				local deferred_news_title
				deferred_news_title=$(cat "$news_title_file" 2>/dev/null)
				[ -n "$deferred_news_title" ] && _post_cc_attribution_to_chat "$deferred_news_title" &
			fi
			_refresh_radio_intro_for_playback_file "$playing_file" "$deferred_corner"
			log "[RADIO:deferred] 再生開始: $(basename "$playing_file")"
			# deferred radio is executed by the comment player itself, so it must not
			# yield to comments queued after this point or playback deadlocks.
			if SAY_DISABLE_COMMENT_YIELD=1 SAY_CONTEXT_LABEL="radio:${deferred_corner:-deferred}" ./say_enqueue.sh --no-preempt "$playing_file" "$RADIO_SAY_RATE" 0; then
				rm -f "$playing_file" "${playing_file%.playing}.news_title" "${playing_file%.playing}.cc_text" "${playing_file%.playing}.voice"
				log "[RADIO:deferred] 再生完了: $(basename "$playing_file")"
		else
			if [ -f "tmp/.say_queue/kill_flag" ]; then
				rm -f "tmp/.say_queue/kill_flag" "$playing_file" "${playing_file%.playing}.voice"
				log "[RADIO:deferred] 外部killにより破棄: $(basename "$playing_file")"
			else
				local retry_file="${playing_file%.playing}.txt"
				mv "$playing_file" "$retry_file" 2>/dev/null || true
				log "[RADIO:deferred] 再生失敗 → キューへ戻す: $(basename "$retry_file")"
			fi
		fi
	fi
}

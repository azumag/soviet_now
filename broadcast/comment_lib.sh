# broadcast/comment_lib.sh - コメント再生・生成の関数ライブラリ (source される)


_recover_orphan_comment_playing_files() {
	# コメント用 say_enqueue が動作中なら .playing は現役の可能性が高いので触らない
	if pgrep -f "say_enqueue.sh --no-preempt .*\\.comment_queue/.*\\.playing" >/dev/null 2>&1; then
		return
	fi
	for orphan in "$COMMENT_QUEUE_DIR"/*.playing; do
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

_comment_speaker_sidecar_candidates() {
	local target="$1" original="${2:-}" base=""
	[ -n "$target" ] || return 0
	printf '%s\n' "${target}.speaker"
	case "$target" in
	*.playing)
		base="${target%.playing}"
		printf '%s\n' "${base}.speaker" "${base}.txt.speaker"
		;;
	*.txt)
		base="${target%.txt}"
		printf '%s\n' "${base}.speaker"
		;;
	esac
	if [ -n "$original" ] && [ "$original" != "$target" ]; then
		printf '%s\n' "${original}.speaker"
		case "$original" in
		*.txt) printf '%s\n' "${original%.txt}.speaker" ;;
		esac
	fi
}

_comment_read_speaker_override() {
	local target="$1" original="${2:-}" sidecar value
	while IFS= read -r sidecar; do
		[ -n "$sidecar" ] || continue
		[ -f "$sidecar" ] || continue
		value=$(cat "$sidecar" 2>/dev/null | tr -d '[:space:]')
		[ -n "$value" ] || continue
		printf '%s' "$value"
		return 0
	done < <(_comment_speaker_sidecar_candidates "$target" "$original" | awk '!seen[$0]++')
	return 1
}

_comment_clear_speaker_sidecars() {
	local target="$1" original="${2:-}" sidecar
	while IFS= read -r sidecar; do
		[ -n "$sidecar" ] || continue
		rm -f "$sidecar" 2>/dev/null || true
	done < <(_comment_speaker_sidecar_candidates "$target" "$original" | awk '!seen[$0]++')
}

_comment_playback_context_label() {
	local target="$1" sidecar label base
	sidecar=$(_comment_meta_sidecar_path "$target")
	if [ -f "$sidecar" ]; then
		label=$(python3 - "$sidecar" <<'PY' 2>/dev/null
import json
import sys
try:
    with open(sys.argv[1], "r", encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
print(data.get("contextLabel") or data.get("sourceLabel") or "", end="")
PY
)
		[ -n "$label" ] && { printf '%s' "$label"; return 0; }
	fi
	base=$(basename "$target")
	case "$base" in
	*improve_progress*)         printf '%s' "improve_progress" ;;
	*soren91_ranking_comment*) printf '%s' "soren91:ranking_comment" ;;
	*soren91_midgame_comment*)  printf '%s' "soren91:midgame_comment" ;;
	*)                         printf '%s' "comment" ;;
	esac
}

_comment_improve_progress_key() {
	python3 - "${IMPROVE_STATE_FILE:-tmp/state/improve_state.json}" <<'PY' 2>/dev/null
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        data = json.load(f)
except Exception:
    data = {}
pid = str(data.get("pid") or "0")
started = str(data.get("started_at") or "0")
print(f"{pid}:{started}", end="")
PY
}

_comment_improve_progress_marker_file() {
	printf '%s' "${IMPROVE_AUDIO_SUMMARY_PLAYED_MARKER:-tmp/state/improve_progress_audio_played}"
}

_comment_improve_progress_already_played() {
	local key marker
	key=$(_comment_improve_progress_key)
	[ -n "$key" ] || return 1
	marker=$(_comment_improve_progress_marker_file)
	[ "$(cat "$marker" 2>/dev/null || true)" = "$key" ]
}

_comment_mark_improve_progress_played() {
	local key marker
	key=$(_comment_improve_progress_key)
	[ -n "$key" ] || return 0
	marker=$(_comment_improve_progress_marker_file)
	mkdir -p "$(dirname "$marker")" 2>/dev/null || true
	printf '%s' "$key" >"$marker" 2>/dev/null || true
}

_comment_queue_priority() {
	local target="$1" label
	label=$(_comment_playback_context_label "$target" 2>/dev/null || printf '%s' "comment")
	case "$label" in
	soren91:ranking_comment) printf '%s' "00" ;;
	soren91:midgame_comment)  printf '%s' "10" ;;
	*)                       printf '%s' "20" ;;
	esac
}

_comment_queue_ordered_files() {
	find "$COMMENT_QUEUE_DIR" -maxdepth 1 -type f -name '*.txt' ! -name 'played_hashes.txt' -print 2>/dev/null |
	while IFS= read -r qf; do
		[ -f "$qf" ] || continue
		printf '%s\t%s\n' "$(_comment_queue_priority "$qf")" "$qf"
	done |
	sort -k1,1 -k2,2 |
	cut -f2-
}

_play_comment_queue() {
	# debug.log ローテーション (500行超→200行に切り詰め)
	local dbg="tmp/.say_queue/debug.log"
	if [ -f "$dbg" ] && [ "$(wc -l < "$dbg")" -gt 500 ]; then
		tail -200 "$dbg" > "${dbg}.tmp" && mv "${dbg}.tmp" "$dbg"
	fi
	_recover_orphan_comment_playing_files
	for qf in $(_comment_queue_ordered_files); do
		if [ -f "$qf" ]; then
			local expected_mode="" current_mode=""
			expected_mode=$(_broadcast_read_expected_mode "$qf" 2>/dev/null || true)
			current_mode=$(_broadcast_host_mode 2>/dev/null || printf '%s' "main")
				if [ -n "$expected_mode" ] && [ "$expected_mode" != "$current_mode" ]; then
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] mode不一致で破棄: $qf expected=$expected_mode current=$current_mode" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
					_comment_clear_generation_meta "$qf" 2>/dev/null || true
					_comment_clear_speaker_sidecars "$qf" 2>/dev/null || true
					rm -f "$qf"
					continue
				fi

			# 重複チェック: 同じ内容を再度再生しない
			local file_hash
			file_hash=$(md5 -q "$qf" 2>/dev/null)
			local _comment_context_label_for_dedupe=""
			_comment_context_label_for_dedupe=$(_comment_playback_context_label "$qf" 2>/dev/null || printf '%s' "comment")
			local _skip_duplicate_check=0
			case "$_comment_context_label_for_dedupe" in
			soren91:ranking_comment|soren91:midgame_comment) _skip_duplicate_check=1 ;;
			esac
				if [ "$_comment_context_label_for_dedupe" = "improve_progress" ] && _comment_improve_progress_already_played; then
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] improve_progress重複スキップ: $qf" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
					_comment_clear_generation_meta "$qf" 2>/dev/null || true
					_comment_clear_speaker_sidecars "$qf" 2>/dev/null || true
					rm -f "$qf"
					continue
				fi
				if [ "$_skip_duplicate_check" -eq 0 ] && [ -n "$file_hash" ] && grep -qF "$file_hash" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null; then
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 重複スキップ: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$qf" 2>/dev/null || true
					_comment_clear_generation_meta "$qf" 2>/dev/null || true
					_comment_clear_speaker_sidecars "$qf" 2>/dev/null || true
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
						_comment_clear_speaker_sidecars "$playing_file" "$qf" 2>/dev/null || true
							rm -f "$playing_file"
							continue
						fi
					local _comment_meta_summary=""
					_comment_meta_summary=$(_comment_generation_debug_summary "$playing_file" 2>/dev/null || true)
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 再生開始: $qf (hash=$file_hash${_comment_meta_summary:+, ${_comment_meta_summary}})" >> tmp/.say_queue/debug.log
					if [ -x ./overlay_notify.sh ]; then
						local _ov_spoken
						_ov_spoken=$(grep -m1 -E '\S' "$playing_file" 2>/dev/null | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //')
						[ "${#_ov_spoken}" -gt 90 ] && _ov_spoken="${_ov_spoken:0:90}…"
						./overlay_notify.sh chat "コメント返信 playback" "$(basename "$playing_file")${_comment_meta_summary:+ | ${_comment_meta_summary}}${_ov_spoken:+ | 内容:${_ov_spoken}}" "info" >/dev/null 2>&1 || true
					fi
				# ハッシュを記録（再生開始前に記録して、kill時にも重複防止）
				if [ "$_skip_duplicate_check" -eq 0 ]; then
					echo "$file_hash" >> "$COMMENT_PLAYED_HASHES_FILE"
					# ハッシュファイルを最新50件に制限
					tail -50 "$COMMENT_PLAYED_HASHES_FILE" > "${COMMENT_PLAYED_HASHES_FILE}.tmp" 2>/dev/null && \
						mv "${COMMENT_PLAYED_HASHES_FILE}.tmp" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null
				fi
					# speaker/context override: サイドカーファイル > soren91判定
				local _cw_vo_speaker=""
				_cw_vo_speaker=$(_comment_read_speaker_override "$playing_file" "$qf" 2>/dev/null || true)
				if [ -z "$_cw_vo_speaker" ] && [ "$expected_mode" != "main" ] && soren91_is_running 2>/dev/null; then
					_cw_vo_speaker="${SOREN91_VOICEVOX_SPEAKER:-46}"
				fi
				local _cw_context_label=""
				_cw_context_label=$(_comment_playback_context_label "$playing_file" 2>/dev/null || printf '%s' "comment")
				if SAY_VOICEVOX_SPEAKER_OVERRIDE="${_cw_vo_speaker:-}" SAY_CONTEXT_LABEL="${_cw_context_label:-comment}" ./say_enqueue.sh --no-preempt "$playing_file" "$RADIO_SAY_RATE" 0; then
						_remember_spoken_comment "$playing_file"
						[ "$_cw_context_label" = "improve_progress" ] && _comment_mark_improve_progress_played
					fi
					echo "[_play_comment_queue $(date '+%H:%M:%S') PID=$_cp_my_pid] 再生完了: $playing_file" >> tmp/.say_queue/debug.log
					_broadcast_clear_expected_mode "$playing_file" 2>/dev/null || true
					_comment_clear_generation_meta "$playing_file" 2>/dev/null || true
					_comment_clear_speaker_sidecars "$playing_file" "$qf" 2>/dev/null || true
					rm -f "$playing_file"
				fi
			fi
	done

	# コメントが空のタイミングで deferred ラジオを1本だけ流す
	_play_deferred_radio_queue_once
}

# Legacy PID/token files (no longer used — workers are managed externally)
COMMENT_PLAYER_PID_FILE="tmp/.comment_queue/player.pid"

# Legacy stubs — workers are now managed externally via workers/*.sh
start_comment_player() { :; }
stop_comment_player() { :; }
start_comment_watcher() { :; }
stop_comment_watcher() { :; }

#=== プロセス管理 ===

_CLEANUP_ALL_RUNNING=0

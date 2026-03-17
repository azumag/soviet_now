# infra/cleanup.sh - PID停止, 子プロセス収集, cleanup_all, cleanup_tmp


#=== tmp/ クリーンアップ ===

cleanup_tmp_files() {
	local cleaned=0

	# --- マーカーファイル: 古いものを削除 ---

	# .radio_done_* : 最新200個を残して削除
	local radio_done_count
	radio_done_count=$(ls -1 $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | wc -l)
	if [ "$radio_done_count" -gt 200 ]; then
		ls -1t $TMP_MARKERS_DIR/.radio_done_* 2>/dev/null | tail -n +201 | xargs rm -f 2>/dev/null
		cleaned=$((cleaned + radio_done_count - 200))
	fi

	# .timed_corner_done_* : 7日より古いものを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.timed_corner_done_*' -mtime +7 -delete 2>/dev/null
	# .radio_inflight_* : 1時間以上古い孤児ディレクトリを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.radio_inflight_*' -type d -mmin +60 -exec rm -rf {} + 2>/dev/null
	# .twitch_clip_game_* : 7日より古いものを削除
	find "$TMP_MARKERS_DIR" -maxdepth 1 -name '.twitch_clip_game_*' -type d -mtime +7 -exec rm -rf {} + 2>/dev/null

	# --- デバッグダンプ: 1日以上古いものを削除 ---
	find "$TMP_DEBUG_DIR" -maxdepth 1 -name 'radio_short_*.txt' -mtime +1 -delete 2>/dev/null
	find "$TMP_DEBUG_DIR" -maxdepth 1 -name 'radio_factcheck_failed_*.txt' -mtime +1 -delete 2>/dev/null

	# --- サンドボックス孤児: 1時間以上古いものを削除 ---
	find tmp -maxdepth 1 -name '.sandbox_harvest_*' -type d -mmin +60 -exec rm -rf {} + 2>/dev/null

	# --- 履歴ファイル: キャップ適用 ---
	# .past_news_titles.txt / .past_news_links.txt にもキャップ適用
	local hist_file
	for hist_file in $TMP_HISTORY_DIR/.past_news_titles.txt $TMP_HISTORY_DIR/.past_news_links.txt $PAST_NEWS_URL_HASHES; do
		if [ -f "$hist_file" ]; then
			local lc
			lc=$(wc -l < "$hist_file" | tr -d ' ')
			if [ "${lc:-0}" -gt 300 ]; then
				tail -200 "$hist_file" > "${hist_file}.tmp" && mv "${hist_file}.tmp" "$hist_file"
			fi
		fi
	done

	# --- レガシー/テスト用ファイル削除 ---
	rm -f tmp/test_*.txt tmp/v158_*.txt tmp/v159_*.txt tmp/monitor_v159.sh 2>/dev/null
	rm -f tmp/batch_test.sh tmp/accumulated_games.test.json 2>/dev/null

	# --- 古い .past_soviet_themes.txt を統合済みなので削除可 ---
	# (テーマが radio_themes.txt に移動済み。ただし _pick_radio_theme の重複防止用は残す)

	if [ "$cleaned" -gt 0 ]; then
		log "[CLEANUP] tmp/ クリーンアップ完了: ${cleaned}ファイル削除"
	fi
}

_stop_pid_with_fallback() {
	local pid="$1" label="${2:-process}"
	case "$pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	kill "$pid" 2>/dev/null || true
	local i
	for i in $(seq 1 20); do
		if ! kill -0 "$pid" 2>/dev/null; then
			return 0
		fi
		sleep 0.1
	done
	if kill -0 "$pid" 2>/dev/null; then
		log "[CLEANUP] ${label} がTERMで停止しないためKILL (PID=$pid)"
		kill -9 "$pid" 2>/dev/null || true
	fi
}

_collect_descendant_pids() {
	local root_pid="$1"
	case "$root_pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	local queue=("$root_pid")
	local seen=" ${root_pid} "
	local descendants=()
	while [ "${#queue[@]}" -gt 0 ]; do
		local parent_pid="${queue[0]}"
		queue=("${queue[@]:1}")
		local child_pid
		while read -r child_pid; do
			case "$child_pid" in
			''|*[!0-9]*) continue ;;
			esac
			if [[ "$seen" == *" ${child_pid} "* ]]; then
				continue
			fi
			seen="${seen}${child_pid} "
			descendants+=("$child_pid")
			queue+=("$child_pid")
		done < <(ps -Ao pid=,ppid= 2>/dev/null | awk -v p="$parent_pid" '$2==p {print $1}')
	done
	printf '%s\n' "${descendants[@]}"
}

_is_audio_playback_process() {
	local pid="$1"
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	# Ctrl-C停止時でも再生中読み上げは途切れさせない
	if echo "$cmd" | grep -Eq '(^|[[:space:]])say([[:space:]]|$)|say_enqueue\.sh'; then
		return 0
	fi
	return 1
}

_stop_loop_descendants() {
	local root_pid="$1"
	case "$root_pid" in
	''|*[!0-9]*) return 0 ;;
	esac
	local descendants=()
	local pid
	while read -r pid; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		descendants+=("$pid")
	done < <(_collect_descendant_pids "$root_pid")
	if [ "${#descendants[@]}" -eq 0 ]; then
		return 0
	fi
	local idx
	for ((idx=${#descendants[@]} - 1; idx>=0; idx--)); do
		pid="${descendants[$idx]}"
		[ "$pid" = "$$" ] && continue
		if _is_audio_playback_process "$pid"; then
			log "[CLEANUP] 再生プロセスは維持 (PID=$pid)"
			continue
		fi
		_stop_pid_with_fallback "$pid" "child"
	done
}

# IMPROVE_PID はグローバル変数として soren_loop.sh で管理
cleanup_all() {
	local reason="${1:-manual}"
	if [ "${_CLEANUP_ALL_RUNNING:-0}" -eq 1 ]; then
		return 0
	fi
	_CLEANUP_ALL_RUNNING=1

	log "クリーンアップ中... (reason=${reason})"

	local loop_pid
	loop_pid=$(_my_pid)
	if [ -f "tmp/soren_loop.lock" ]; then
		local lock_pid
		local lock_cmd
		lock_pid=$(cat "tmp/soren_loop.lock" 2>/dev/null || echo "")
		case "$lock_pid" in
		''|*[!0-9]*) lock_pid="" ;;
		esac
		if [ -n "$lock_pid" ] && kill -0 "$lock_pid" 2>/dev/null; then
			lock_cmd=$(ps -p "$lock_pid" -o command= 2>/dev/null || echo "")
			if echo "$lock_cmd" | grep -q "soren_loop.sh"; then
				loop_pid="$lock_pid"
			fi
		fi
	fi

	# 状態ファイルからPIDを復元 (IMPROVE_PIDが0でも状態ファイルにPIDがある場合)
	if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ -f "$IMPROVE_STATE_FILE" ]; then
		local _cleanup_pid
		_cleanup_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		case "$_cleanup_pid" in
		''|*[!0-9]*) _cleanup_pid=0 ;;
		esac
		[ "${_cleanup_pid:-0}" -ne 0 ] && IMPROVE_PID=$_cleanup_pid
	fi

	# 改善プロセス停止
	if [ "${IMPROVE_PID:-0}" -ne 0 ] && kill -0 "$IMPROVE_PID" 2>/dev/null; then
		pkill -P "$IMPROVE_PID" 2>/dev/null || true
		_stop_pid_with_fallback "$IMPROVE_PID" "improve"
		wait "$IMPROVE_PID" 2>/dev/null || true
	fi
	_write_improve_state "idle" "0" ""

	local rollback_postmortem_pid=0
	if [ -f "$ROLLBACK_POSTMORTEM_PID_FILE" ]; then
		rollback_postmortem_pid=$(cat "$ROLLBACK_POSTMORTEM_PID_FILE" 2>/dev/null || echo 0)
		case "$rollback_postmortem_pid" in
		''|*[!0-9]*) rollback_postmortem_pid=0 ;;
		esac
	fi
	if [ "${rollback_postmortem_pid:-0}" -ne 0 ] && kill -0 "$rollback_postmortem_pid" 2>/dev/null; then
		pkill -P "$rollback_postmortem_pid" 2>/dev/null || true
		_stop_pid_with_fallback "$rollback_postmortem_pid" "rollback_postmortem"
		wait "$rollback_postmortem_pid" 2>/dev/null || true
	fi
	rm -f "$ROLLBACK_POSTMORTEM_PID_FILE"

	# コメント関連停止
	stop_comment_watcher
	_kill_comment_gen
	stop_comment_player

	# Twitchチャット停止
	./twitch_chat.sh stop 2>/dev/null || true

	# 最後に子孫プロセスを強制的に掃除
	_stop_loop_descendants "$loop_pid"

	# /tmp/eloop_* 一時ファイル一括削除
	rm -f /tmp/eloop_prompt.* /tmp/eloop_runner.* /tmp/eloop_radio_* /tmp/eloop_comment_* /tmp/eloop_fix_* /tmp/eloop_celebration_* /tmp/eloop_news_*
	# ロックファイル削除
	rm -f tmp/soren_loop.lock
	log "クリーンアップ完了"
}

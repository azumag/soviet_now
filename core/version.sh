# core/version.sh - save_strategy_version, update_best, archive_history


#=== バージョン管理 ===

save_strategy_version() {
	local score="$1"
	GAME_NUM=$((GAME_NUM + 1))
	echo "$GAME_NUM" >"$GAME_COUNT_FILE"
	local version_file
	version_file=$(printf "%s/v%04d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	# スナップショットがあれば試合時の戦略を保存 (裏の改善で書き換わっていても正確)
	local src="${STRATEGY_FILE}.game_snapshot"
	[ ! -f "$src" ] && src="$STRATEGY_FILE"
	cp "$src" "$version_file"
	log "[VERSION] saved: $version_file"

	local total
	total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	local delete_count=$((total - 10))
	if [ "$delete_count" -gt 0 ]; then
		ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py | sort -V | head -n "$delete_count" | while read -r f; do
			rm -f "$f"
			log "[VERSION] pruned: $(basename "$f")"
		done
	fi
}

update_best() {
	local current_score="$1"
	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)

	if [ "${current_score:-0}" -gt "${best_score:-0}" ]; then
		log "NEW HIGH SCORE: $current_score (prev: $best_score)"
		echo "$current_score" >best_score.txt

		local hall_file
		hall_file=$(printf "%s/best_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$current_score")
		# スナップショットがあれば試合時の戦略を保存 (裏の改善で書き換わっていても正確)
		local src="${STRATEGY_FILE}.game_snapshot"
		[ ! -f "$src" ] && src="$STRATEGY_FILE"
		cp "$src" "$hall_file"
		log "[HALL OF FAME] saved: $hall_file"

		python3 tag_best_changelog.py "$STRATEGY_FILE" "$current_score" 2>/dev/null
		python3 tag_best_changelog.py "$hall_file" "$current_score" 2>/dev/null

		local best_total
		best_total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
		local best_delete=$((best_total - 10))
		if [ "$best_delete" -gt 0 ]; then
			ls -1 "$STRATEGY_VERSIONS_DIR"/best_score*_strategy.py | sort | head -n "$best_delete" | while read -r f; do
				rm -f "$f"
				log "[HALL OF FAME] pruned: $(basename "$f")"
			done
		fi

		return 0
	else
		log "Score: $current_score (best: $best_score)"
		return 1
	fi
}

# Twitch クリップ作成（バックグラウンド・ノンブロッキング）
# 同一ゲームで複数イベント発火時は最初の1回のみ
_TWITCH_CLIP_GAME=""
_create_twitch_clip() {
	local event_msg="$1" game_id="${2:-}" delay="${3:-0}"
	[ "${TWITCH_CLIP_ENABLED:-0}" = "1" ] || return 0
	[ -n "${TWITCH_CLIENT_ID:-}" ] && [ -n "${TWITCH_BROADCASTER_ID:-}" ] || return 0
	if [ -n "$game_id" ]; then
		local clip_marker="$TMP_MARKERS_DIR/.twitch_clip_game_${game_id}"
		if ! mkdir "$clip_marker" 2>/dev/null; then
			log "[CLIP] skip: already claimed for game $game_id"
			return 0
		fi
	fi
	# 同一ゲーム内デデュプ（建国+ハイスコア同時発生時に2本作らない）
	if [ -n "$game_id" ] && [ "$game_id" = "$_TWITCH_CLIP_GAME" ]; then
		log "[CLIP] skip: already clipped for game $game_id"
		return 0
	fi
	[ -n "$game_id" ] && _TWITCH_CLIP_GAME="$game_id"
	( [ "$delay" -gt 0 ] 2>/dev/null && sleep "$delay"; ./twitch_clip.sh "$event_msg" 2>>"$TMP_DEBUG_DIR/twitch_clip.log" || true ) &
}

archive_history() {
	local score="$1"
	local ts
	ts=$(date '+%Y%m%d_%H%M%S')
	if [ -f "$HISTORY_FILE" ]; then
		local archive
		archive=$(printf "%s/%s_score%04d.jsonl" "$HISTORY_DIR" "$ts" "$score")
		cp "$HISTORY_FILE" "$archive"
			log "[ARCHIVE] $archive"
	fi
}

_history_gameover_asset_path() {
	local history_file="$1" kind="$2"
	local stem
	[ -n "$history_file" ] || return 1
	case "$history_file" in
	*.jsonl) ;;
	*) return 1 ;;
	esac
	stem=$(basename "${history_file%.jsonl}")
	case "$kind" in
	board) printf '%s/gameover_screens/%s.gameover_board.png\n' "$TMP_HISTORY_DIR" "$stem" ;;
	next) printf '%s/gameover_screens/%s.gameover_next.png\n' "$TMP_HISTORY_DIR" "$stem" ;;
	*) return 1 ;;
	esac
}

archive_gameover_screenshots() {
	local history_file="$1"
	local copied=0 kind src dst
	[ -n "$history_file" ] || return 0
	[ -f "$history_file" ] || return 0

	for kind in board next; do
		case "$kind" in
		board) src="board.png" ;;
		next) src="next_block.png" ;;
		esac
		[ -s "$src" ] || continue
		dst=$(_history_gameover_asset_path "$history_file" "$kind" 2>/dev/null || true)
		[ -n "$dst" ] || continue
		mkdir -p "$(dirname "$dst")" 2>/dev/null || true
		if cp "$src" "$dst" 2>/dev/null; then
			log "[ARCHIVE] $dst"
			copied=$((copied + 1))
		fi
	done

	if [ "$copied" -eq 0 ]; then
		log "[ARCHIVE] gameover screenshots unavailable for $(basename "$history_file")"
	fi
}

recover_strategy_backup() {
	if [ ! -f "$STRATEGY_FILE" ] && [ -f "${STRATEGY_FILE}.bak" ]; then
		log "[RECOVER] .bak から復元"
		cp "${STRATEGY_FILE}.bak" "$STRATEGY_FILE"
	fi
}

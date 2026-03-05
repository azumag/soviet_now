#!/bin/bash
# lib/eloop_version.sh - バージョン管理

#=== バージョン管理 ===

save_strategy_version() {
	local score="$1"
	GAME_NUM=$((GAME_NUM + 1))
	echo "$GAME_NUM" >"$GAME_COUNT_FILE"
	local version_file
	version_file=$(printf "%s/v%03d_score%04d_strategy.py" "$STRATEGY_VERSIONS_DIR" "$GAME_NUM" "$score")
	# スナップショットがあれば試合時の戦略を保存 (裏の改善で書き換わっていても正確)
	local src="${STRATEGY_FILE}.game_snapshot"
	[ ! -f "$src" ] && src="$STRATEGY_FILE"
	cp "$src" "$version_file"
	log "[VERSION] saved: $version_file"

	local total
	total=$(ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null | wc -l | tr -d ' ')
	local delete_count=$((total - 10))
	if [ "$delete_count" -gt 0 ]; then
		ls -1 "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py | sort | head -n "$delete_count" | while read -r f; do
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

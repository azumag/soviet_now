#!/bin/bash
# lib/eloop_regression.sh - ローリングスコア・リグレッション検知

#=== ローリングスコア & リグレッション検知 ===

_archive_strategy_snapshot_by_hash() {
	local source_file="$1" hash_value="$2"
	[ -f "$source_file" ] || return 0
	if [ -z "$hash_value" ] || [ "$hash_value" = "unknown" ]; then
		hash_value=$(python3 extract_decide_hash.py "$source_file" 2>/dev/null || echo "")
	fi
	[ -z "$hash_value" ] && return 0
	mkdir -p "$STRATEGY_HASH_ARCHIVE_DIR"
	local dst="$STRATEGY_HASH_ARCHIVE_DIR/${hash_value}.py"
	if [ ! -f "$dst" ]; then
		cp "$source_file" "$dst" 2>/dev/null || true
	fi
}

_find_strategy_file_by_hash() {
	local target_hash="$1"
	[ -z "$target_hash" ] && return 1
	if [ -f "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py" ]; then
		echo "$STRATEGY_HASH_ARCHIVE_DIR/${target_hash}.py"
		return 0
	fi

	local candidates=()
	[ -f "$STRATEGY_FILE" ] && candidates+=("$STRATEGY_FILE")
	[ -f "tmp/revert_strategy.py" ] && candidates+=("tmp/revert_strategy.py")
	while IFS= read -r vf; do
		[ -n "$vf" ] && candidates+=("$vf")
	done < <(ls -1t "$STRATEGY_VERSIONS_DIR"/*.py 2>/dev/null || true)

	local f h
	for f in "${candidates[@]}"; do
		[ -f "$f" ] || continue
		h=$(python3 extract_decide_hash.py "$f" 2>/dev/null || echo "")
		if [ "$h" = "$target_hash" ]; then
			echo "$f"
			return 0
		fi
	done
	return 1
}

_pick_best_rollback_candidate() {
	local current_hash="$1"
	[ -f "$ROLLING_SCORES_FILE" ] || return 1

	local ranked
	ranked=$(python3 "$ELOOP_LIB_DIR/lib/regression_calc.py" rank "$ROLLING_SCORES_FILE" "$current_hash" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB")
	[ -z "$ranked" ] && return 1

	local line h comp p50 p25 lcb n candidate_file
	while IFS= read -r line; do
		[ -z "$line" ] && continue
		IFS='|' read -r h comp p50 p25 lcb n <<<"$line"
		candidate_file=$(_find_strategy_file_by_hash "$h")
		if [ -n "$candidate_file" ]; then
			echo "${h}|${comp}|${p50}|${p25}|${lcb}|${n}|${candidate_file}"
			return 0
		fi
	done <<EOF
$ranked
EOF
	return 1
}

_prune_hash_archive_by_ranking() {
	[ -d "$STRATEGY_HASH_ARCHIVE_DIR" ] || return 0
	[ -f "$ROLLING_SCORES_FILE" ] || return 0

	local ranked_hashes
	ranked_hashes=$(python3 "$ELOOP_LIB_DIR/lib/regression_calc.py" prune "$ROLLING_SCORES_FILE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$HASH_ARCHIVE_KEEP_TOP" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB")

	local current_hash=""
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
	local revert_hash=""
	if [ -f "tmp/revert_strategy.py" ]; then
		revert_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
	fi

	local keep_hashes
	keep_hashes=$(printf '%s\n%s\n%s\n' "$ranked_hashes" "$current_hash" "$revert_hash" | sed '/^$/d' | sort -u)

	local removed=0
	local f base h
	while IFS= read -r f; do
		[ -f "$f" ] || continue
		base=$(basename "$f")
		h="${base%.py}"
		if ! printf '%s\n' "$keep_hashes" | grep -qxF "$h"; then
			rm -f "$f"
			removed=$((removed + 1))
		fi
	done < <(ls -1 "$STRATEGY_HASH_ARCHIVE_DIR"/*.py 2>/dev/null || true)

	if [ "$removed" -gt 0 ]; then
		log "[HASH-ARCHIVE] pruned ${removed} file(s): keep top ${HASH_ARCHIVE_KEEP_TOP} (+current/revert)"
	fi
}

update_rolling_scores() {
	local score="$1"
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$strategy_hash"

	python3 "$ELOOP_LIB_DIR/lib/regression_calc.py" update_scores "$ROLLING_SCORES_FILE" "$strategy_hash" "$score" 20 2>/dev/null
	_prune_hash_archive_by_ranking
}

check_regression() {
	# 新戦略が十分試行数で、LCB+中央値+分位点ベースの比較で劣化していればリグレッション
	# 戻り値: 0=リグレッション検知(リバート実行済み), 1=問題なし
	REGRESSION_ROLLBACK_DONE=0
	REGRESSION_ROLLBACK_HASH=""
	local strategy_hash
	strategy_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")

	local result
	result=$(python3 "$ELOOP_LIB_DIR/lib/regression_calc.py" check "$ROLLING_SCORES_FILE" "$strategy_hash" "$MIN_GAMES_BEFORE_IMPROVE" "$MIN_GAMES_FOR_BEST_ROLLBACK" "$RANK_LCB_Z" "$RANK_WEIGHT_P50" "$RANK_WEIGHT_P25" "$RANK_WEIGHT_LCB" "$REGRESSION_COMPOSITE_RATIO" "$REGRESSION_P25_RATIO" 2>/dev/null)

	if echo "$result" | grep -q '^REGRESSION:'; then
		log "[REGRESSION] リグレッション検知: $result"
		# 進行中の改善プロセスがあれば停止して、リバート後の再上書きを防ぐ
		local running_pid=0
		if [ -f "$IMPROVE_STATE_FILE" ]; then
			running_pid=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('pid',0))" 2>/dev/null || echo 0)
		fi
		if [ "${running_pid:-0}" -eq 0 ] && [ "${IMPROVE_PID:-0}" -ne 0 ]; then
			running_pid="$IMPROVE_PID"
		fi
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				log "[REGRESSION] 改善プロセス停止 (PID=$running_pid)"
				kill "$running_pid" 2>/dev/null || true
				wait "$running_pid" 2>/dev/null || true
			else
				log "[REGRESSION] PID=$running_pid は改善プロセスではないため停止スキップ: $pid_cmd"
			fi
		fi
		IMPROVE_PID=0
		_write_improve_state "idle" "0" ""
		log "[REGRESSION] 自動ロールバック開始"

		# リジェクトハッシュに記録
		echo "$strategy_hash" >> "$REJECTED_HASHES_FILE"
		# 最新N件のみ保持
		if [ -f "$REJECTED_HASHES_FILE" ]; then
			tail -"${REJECTED_HASHES_KEEP:-20}" "$REJECTED_HASHES_FILE" > "$REJECTED_HASHES_FILE.tmp"
			mv "$REJECTED_HASHES_FILE.tmp" "$REJECTED_HASHES_FILE"
		fi

		# リバート先選定:
		# 1) LCB+中央値+分位点の合成スコアで最良(十分試行数)かつ実ファイルが見つかる戦略
		# 2) 見つからなければ従来どおり直前戦略(tmp/revert_strategy.py)
		local rollback_file="" rollback_note="" rollback_hash=""
		local best_candidate
		best_candidate=$(_pick_best_rollback_candidate "$strategy_hash")
		if [ -n "$best_candidate" ]; then
			local best_comp best_p50 best_p25 best_lcb best_n
			IFS='|' read -r rollback_hash best_comp best_p50 best_p25 best_lcb best_n rollback_file <<<"$best_candidate"
			rollback_note="best_comp hash=${rollback_hash} comp=${best_comp} p50=${best_p50} p25=${best_p25} lcb=${best_lcb} n=${best_n}"
		elif [ -f "tmp/revert_strategy.py" ]; then
			rollback_file="tmp/revert_strategy.py"
			rollback_note="previous_strategy"
		fi

		if [ -z "$rollback_file" ]; then
			log "[REGRESSION] ロールバック候補なし → 現在戦略を維持"
			return 0
		fi

		# リバート実行
		cp "$rollback_file" "$STRATEGY_FILE"
		# 次回比較の基準も現戦略に合わせる（再帰的な誤判定防止）
		cp "$STRATEGY_FILE" "tmp/revert_strategy.py" 2>/dev/null || true
		local rolled_hash
		rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
		_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
		REGRESSION_ROLLBACK_DONE=1
		REGRESSION_ROLLBACK_HASH="$rolled_hash"
		log "[REGRESSION] リバート完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"

		git add -A
		git commit -m "eloop Auto-revert: regression detected ($result, target=${rollback_note})" 2>/dev/null || true

		return 0  # リグレッション検知
		fi

	return 1  # 問題なし
}

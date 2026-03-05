#!/bin/bash
# lib/eloop_improve.sh - 改善ステート管理

#=== 改善ステート管理 ===

_read_improve_state() {
	if [ -f "$IMPROVE_STATE_FILE" ]; then
		cat "$IMPROVE_STATE_FILE"
	else
		echo '{"status":"idle","pid":0,"strategy_hash_before":"","phase":"","progress":0,"detail":"","started_at":0,"updated_at":0}'
	fi
}

_write_improve_state() {
	local status="$1" pid="$2" hash="$3"
	local phase="${4:-}" progress="${5:-0}" detail="${6:-}" started_at="${7:-0}"
	local now
	now=$(date +%s)
	python3 - "$IMPROVE_STATE_FILE" "$status" "${pid:-0}" "${hash:-}" "$phase" "$progress" "$detail" "$started_at" "$now" <<'PY'
import json
import sys

out_file, status, pid_raw, hash_before, phase, progress_raw, detail, started_raw, now_raw = sys.argv[1:10]

try:
    pid = int(pid_raw)
except Exception:
    pid = 0
try:
    progress = int(float(progress_raw))
except Exception:
    progress = 0
progress = max(0, min(100, progress))
try:
    started_at = int(started_raw)
except Exception:
    started_at = 0
try:
    now = int(now_raw)
except Exception:
    now = 0

if started_at <= 0 and status == "running":
    started_at = now

data = {
    "status": status,
    "pid": pid,
    "strategy_hash_before": hash_before,
    "phase": phase,
    "progress": progress,
    "detail": detail,
    "started_at": started_at,
    "updated_at": now,
}

with open(out_file, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)
PY
}

check_and_harvest_improvement() {
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		local pid
		pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)

		# IMPROVE_PID を状態ファイルから同期 (再起動時の復元)
		if [ "${IMPROVE_PID:-0}" -eq 0 ] && [ "${pid:-0}" -ne 0 ]; then
			IMPROVE_PID=$pid
		fi

		# PID再利用チェック: eloop_improve.sh のプロセスかどうか確認
		local pid_alive=false
		if [ "${pid:-0}" -ne 0 ] && kill -0 "$pid" 2>/dev/null; then
			# プロセスが存在する場合、eloop_improve.sh のプロセスか確認
			local pid_cmd
			pid_cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				pid_alive=true
			else
				log "[IMPROVE] PID=$pid は別プロセス ($pid_cmd) → stale状態クリア"
			fi
		fi

		if [ "$pid_alive" = false ]; then
			# プロセス完了 or stale → harvest
			local hash_before
			hash_before=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('strategy_hash_before',''))" 2>/dev/null)
			local hash_now
			hash_now=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)

			if [ "$hash_before" != "$hash_now" ]; then
				log "[IMPROVE] 戦略更新検出: $hash_before -> $hash_now"

				# リバート用候補はeloop_improve.shが tmp/revert_strategy.py に保存済み
				# ローリングスコアで新戦略のprev_hashを記録
				local new_decide_hash
				new_decide_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
				if [ -n "$new_decide_hash" ] && [ -f "$ROLLING_SCORES_FILE" ]; then
					local prev_decide_hash=""
					if [ -f "tmp/revert_strategy.py" ]; then
						prev_decide_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
					fi
					python3 -c "
import json, os
rs_file = '$ROLLING_SCORES_FILE'
if os.path.exists(rs_file):
    with open(rs_file) as f:
        rs = json.load(f)
else:
    rs = {}
h = '$new_decide_hash'
if h not in rs:
    rs[h] = {'scores': [], 'prev_hash': '$prev_decide_hash', 'games_total': 0}
elif 'games_total' not in rs[h]:
    rs[h]['games_total'] = len(rs[h].get('scores', []))
with open(rs_file, 'w') as f:
    json.dump(rs, f)
" 2>/dev/null
				fi

				# 戦略が変わった → 蓄積データは旧戦略のものなので破棄
				local acc_count_discarded=0
				if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
					acc_count_discarded=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
				fi
				_clear_accumulated_data
				if [ "${acc_count_discarded:-0}" -gt 0 ]; then
					log "[IMPROVE] 蓄積${acc_count_discarded}試合を破棄 (旧戦略のデータ)"
				fi
			else
				log "[IMPROVE] 戦略変更なし (改善失敗 or 差分なし)"
				# 戦略が変わっていない → 蓄積データはそのまま有効
			fi

			_write_improve_state "idle" "0" "" "" "0" ""
			IMPROVE_PID=0
			log "[IMPROVE] 改善完了 → idle"
		fi
	fi
}

accumulate_game_data() {
	local archive_file="$1" score="$2" soviet="$3"

	python3 -c "
import json, os
acc_file = '$ACCUMULATED_GAMES_FILE'
if os.path.exists(acc_file):
    with open(acc_file) as f:
        acc = json.load(f)
else:
    acc = {'files': [], 'scores': '', 'soviet': False, 'count': 0}

acc['files'].append('$archive_file')
acc['scores'] = (acc['scores'] + ' $score').strip()
if '$soviet' == 'true':
    acc['soviet'] = True
acc['count'] += 1

with open(acc_file, 'w') as f:
    json.dump(acc, f)
print(f'[ACCUMULATE] 蓄積: {acc[\"count\"]}試合')
" 2>/dev/null
}

_read_accumulated_data() {
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		cat "$ACCUMULATED_GAMES_FILE"
	else
		echo '{"files":[],"scores":"","soviet":false,"count":0}'
	fi
}

_clear_accumulated_data() {
	rm -f "$ACCUMULATED_GAMES_FILE"
}

_start_improvement_job() {
	local all_history_files="$1" all_scores="$2" any_soviet="$3" acc_count="$4" reason="$5"

	# 既存の eloop_improve プロセスが残っていないか確認
	local stale_pids
	stale_pids=$(pgrep -f "eloop_improve" 2>/dev/null || true)
	if [ -n "$stale_pids" ]; then
		log "[IMPROVE] WARNING: 既存の eloop_improve プロセス検出 (PIDs: $stale_pids) → kill"
		echo "$stale_pids" | xargs kill 2>/dev/null || true
		sleep 1
	fi

	if [ "$reason" = "post_regression" ]; then
		log "[IMPROVE] 回帰ロールバック直後の即時改善を開始"
	else
		log "[IMPROVE] ${acc_count}試合分のデータで改善開始"
	fi

	# Twitchコメント処理は comment watcher 側に一本化
	log "[NEWS] ニュース取得..."
	./fetch_news.sh

	# 戦略ハッシュ記録
	local strategy_hash
	strategy_hash=$(md5 -q "$STRATEGY_FILE" 2>/dev/null | cut -c1-8)
	local improve_ai_log="$IMPROVE_AI_LOG_FILE"
	mkdir -p "$(dirname "$improve_ai_log")" 2>/dev/null || true
	: >"$improve_ai_log"
	printf '[%s] [IMPROVE] job start reason=%s game=%s scores=%s\n' \
		"$(date '+%H:%M:%S')" "$reason" "${GAME_NUM:-?}" "${all_scores:-}" >>"$improve_ai_log" 2>/dev/null || true

	# バックグラウンド改善開始
	RUN_CMD_LOG_FILE="$improve_ai_log" ./eloop_improve.sh "$all_history_files" "$all_scores" "$any_soviet" "$GAME_NUM" "$LAST_TURNS" &
	IMPROVE_PID=$!

	# 起動成功を確認してから状態更新
	if kill -0 "$IMPROVE_PID" 2>/dev/null; then
		_write_improve_state "running" "$IMPROVE_PID" "$strategy_hash" "boot" "1" "job_started" "$(date +%s)"
		if [ "$reason" = "post_regression" ]; then
			log "[IMPROVE] 回帰ロールバック後の改善開始 (PID=$IMPROVE_PID, base=${REGRESSION_ROLLBACK_HASH:-unknown})"
		else
			log "[IMPROVE] バックグラウンド開始 (PID=$IMPROVE_PID, ${acc_count} 試合)"
		fi
		return 0
	else
		log "[IMPROVE] 起動失敗 (PID=$IMPROVE_PID 即死)"
		IMPROVE_PID=0
		return 1
	fi
}

trigger_adaptive_improvement() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] trigger_adaptive_improvementをスキップ（建国後停止中）"
		return
	fi

	# Step 1: 常にデータを蓄積 & ローリングスコア更新
	accumulate_game_data "$LAST_ARCHIVE_FILE" "$LAST_SCORE" "$LAST_SOVIET"
	update_rolling_scores "$LAST_SCORE"

	# Step 2: リグレッション検知 (新戦略が旧戦略の85%未満なら自動リバート)
	if check_regression; then
		# リグレッション検知 → リバート済み、蓄積データクリア
		_clear_accumulated_data
		return
	fi

	# Step 3: 改善プロセス実行中?
	local state
	state=$(_read_improve_state)
	local status
	status=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('status','idle'))" 2>/dev/null)

	if [ "$status" = "running" ]; then
		# PIDが本当に生きているか確認 (stale検出)
		local running_pid
		running_pid=$(echo "$state" | python3 -c "import json,sys; print(json.load(sys.stdin).get('pid',0))" 2>/dev/null)
		local still_alive=false
		if [ "${running_pid:-0}" -ne 0 ] && kill -0 "$running_pid" 2>/dev/null; then
			local pid_cmd
			pid_cmd=$(ps -p "$running_pid" -o command= 2>/dev/null || echo "")
			if echo "$pid_cmd" | grep -q "eloop_improve"; then
				still_alive=true
			fi
		fi
		if [ "$still_alive" = true ]; then
			log "[IMPROVE] 改善中 (PID=$running_pid), データ蓄積済み"
			return
		else
			log "[IMPROVE] stale検出: PID=$running_pid は既に終了 → harvest & 続行"
			check_and_harvest_improvement
		fi
	fi

	# Step 4: 最低10試合ゲート
	local acc_data
	acc_data=$(_read_accumulated_data)
	local acc_count
	acc_count=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('count',0))" 2>/dev/null)

	if [ "${acc_count:-0}" -lt "$MIN_GAMES_BEFORE_IMPROVE" ]; then
		log "[IMPROVE] 蓄積 ${acc_count:-0}/${MIN_GAMES_BEFORE_IMPROVE} 試合 → 待機"
		return
	fi

	# Step 5: idle → 改善開始
	# 蓄積データから履歴ファイル・スコアを統合
	local all_history_files all_scores any_soviet
	all_history_files=$(echo "$acc_data" | python3 -c "import json,sys; print(' '.join(json.load(sys.stdin).get('files',[])))" 2>/dev/null)
	all_scores=$(echo "$acc_data" | python3 -c "import json,sys; print(json.load(sys.stdin).get('scores',''))" 2>/dev/null)
	any_soviet=$(echo "$acc_data" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet',False) else 'false')" 2>/dev/null)
	if _start_improvement_job "$all_history_files" "$all_scores" "$any_soviet" "$acc_count" "normal"; then
		# 通常改善のみ、起動成功後に蓄積をクリア (即死時は保持)
		_clear_accumulated_data
	fi
}

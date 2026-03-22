#!/bin/bash
# eloop.sh - 1試合のゲームプレイと後処理
#
# soren_loop.sh から毎試合 source される。
# AI による改善対象 — このファイルが書き換わっても、次の source で反映される。
#
# 提供する関数:
#   play_one_game()          — 1試合プレイ、結果をグローバル変数に格納
#   post_game_bookkeeping()  — スコア記録、バージョン保存、git commit 等
#   handle_soviet_celebration() — ソ連建国祝賀
#   prepare_next_game()      — retry送信 or MOVE待ち
#   _handle_decide_exception_recovery() — decide例外時のロールバック/再試行
#
# 前提: eloop_lib.sh が source 済みであること

PLAY_RECOVERED_RETRY_RC=75

_stop_improvement_for_runtime_recovery() {
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
			log "[RECOVERY] 改善プロセス停止 (PID=$running_pid)"
			pkill -P "$running_pid" 2>/dev/null || true
			kill "$running_pid" 2>/dev/null || true
			wait "$running_pid" 2>/dev/null || true
		fi
	fi
	IMPROVE_PID=0
	_write_improve_state "idle" "0" "" "runtime_recovery" "0" "decide_exception"
}

_pick_runtime_rollback_candidate() {
	local current_hash="$1"

	if [ -f "tmp/revert_strategy.py" ]; then
		local revert_hash
		revert_hash=$(python3 extract_decide_hash.py "tmp/revert_strategy.py" 2>/dev/null || echo "")
		if [ -n "$revert_hash" ] && [ "$revert_hash" != "$current_hash" ]; then
			echo "tmp/revert_strategy.py|previous_strategy|$revert_hash"
			return 0
		fi
	fi

	local best_candidate
	best_candidate=$(_pick_best_rollback_candidate "$current_hash")
	if [ -n "$best_candidate" ]; then
		local h comp p50 p25 lcb n file
		IFS='|' read -r h comp p50 p25 lcb n file <<<"$best_candidate"
		echo "${file}|best_comp hash=${h} comp=${comp} p50=${p50} p25=${p25} lcb=${lcb} n=${n}|${h}"
		return 0
	fi

	local vf vh
	while IFS= read -r vf; do
		[ -f "$vf" ] || continue
		vh=$(python3 extract_decide_hash.py "$vf" 2>/dev/null || echo "")
		[ -z "$vh" ] && continue
		if [ "$vh" != "$current_hash" ]; then
			echo "${vf}|latest_version|${vh}"
			return 0
		fi
	done < <(ls -1t "$STRATEGY_VERSIONS_DIR"/v[0-9]*_strategy.py 2>/dev/null || true)

	return 1
}

_handle_decide_exception_recovery() {
	local err_msg="$1"
	local err_turn="${2:-0}"
	local err_score="${3:-0}"
	local rollback_applied=false
	log "[RECOVERY] decide例外を検出: ${err_msg} (turn=${err_turn}, score=${err_score})"

	_stop_improvement_for_runtime_recovery

	local current_hash rollback_line rollback_file rollback_note rollback_hash
	current_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "unknown")
	rollback_line=$(_pick_runtime_rollback_candidate "$current_hash" || true)
	if [ -n "$rollback_line" ]; then
		IFS='|' read -r rollback_file rollback_note rollback_hash <<<"$rollback_line"
		if [ -f "$rollback_file" ]; then
			cp "$rollback_file" "$STRATEGY_FILE"
			cp "$STRATEGY_FILE" "tmp/revert_strategy.py" 2>/dev/null || true
			local rolled_hash
			local phylo_push_ok=false
			rolled_hash=$(python3 extract_decide_hash.py "$STRATEGY_FILE" 2>/dev/null || echo "")
			_archive_strategy_snapshot_by_hash "$STRATEGY_FILE" "$rolled_hash"
			rollback_applied=true
			log "[RECOVERY] ロールバック完了: ${rollback_note} (file=${rollback_file}, hash=${rolled_hash:-unknown})"
			git add "$STRATEGY_FILE" tmp/revert_strategy.py 2>/dev/null || true
			if git commit -m "eloop Auto-revert: decide runtime exception (turn=${err_turn}, score=${err_score}, target=${rollback_note})" 2>/dev/null; then
				if git push 2>/dev/null; then
					phylo_push_ok=true
				fi
			fi
			if [ "$phylo_push_ok" = true ]; then
				_post_phyrogenetic_tree_link_to_chat "rollback" "$current_hash" "$rolled_hash"
			fi
		else
			log "[RECOVERY] ロールバック候補ファイルなし: ${rollback_file}"
		fi
	else
		log "[RECOVERY] ロールバック候補なし（現戦略のまま再試行）"
	fi

	# 戦略が切り替わる可能性が高いため蓄積データは破棄
	_clear_accumulated_data
	_clear_active_branch

	# 進行中ゲームは捨てて、即リトライ
	send_retry

	# ロールバック直後は即改善せず、ロールバック戦略の実績を一定数ためてから改善する
	if [ "$rollback_applied" = true ]; then
		echo "${MIN_GAMES_BEFORE_IMPROVE:-12}" > "$RUNTIME_RECOVERY_GATE_FILE"
		log "[RECOVERY] 改善ゲート設定: ロールバック戦略で ${MIN_GAMES_BEFORE_IMPROVE:-12} 試合蓄積後に改善開始"
	else
		rm -f "$RUNTIME_RECOVERY_GATE_FILE" 2>/dev/null || true
	fi
}

#=== 1試合プレイ ===
play_one_game() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] play_one_gameをスキップ（strategy停止中）"
		LAST_SOVIET="false"
		return 0
	fi

	# 前試合のダッシュボードを非表示
	./generate_dashboard.sh MOVE || true

	local game_num_display=$((GAME_NUM + 1))
	log ""
	log "── Game #${game_num_display} ──"
	_clear_stale_commands_if_any "before play_one_game"

	# 試合開始時の strategy.py をスナップショット保存
	# (裏で改善が strategy.py を書き換えても、この試合で使った戦略を正確に保存できる)
	cp "$STRATEGY_FILE" "${STRATEGY_FILE}.game_snapshot"

	# strategy_runner.py で1試合プレイ
	# パイプラインを使わない: bash はパイプライン中 INT trap を遅延するため Ctrl-C が効かない。
	# 代わりに python3 をバックグラウンド実行 + tail -f でリアルタイム表示。
	local runner_tmpfile
	runner_tmpfile=$(mktemp /tmp/eloop_runner.XXXXXX)
	python3 -u strategy_runner.py > "$runner_tmpfile" 2>&1 &
	local py_pid=$!
	tail -n +1 -f "$runner_tmpfile" &
	local tail_pid=$!
	wait "$py_pid"
	local py_rc=$?
	kill "$tail_pid" 2>/dev/null; wait "$tail_pid" 2>/dev/null || true
	if [ "$py_rc" -eq 130 ] || [ "$py_rc" -eq 143 ]; then
		log "[SIGNAL] strategy_runner が割り込み終了 (py_rc=$py_rc)"
		rm -f "$runner_tmpfile"
		return 130
	fi
	if [ "$py_rc" -ne 0 ] && grep -q "KeyboardInterrupt" "$runner_tmpfile" 2>/dev/null; then
		log "[SIGNAL] strategy_runner KeyboardInterrupt を検出 (py_rc=$py_rc)"
		rm -f "$runner_tmpfile"
		return 130
	fi
	if [ "$py_rc" -ne 0 ]; then
		log "[RUNNER] WARNING: strategy_runner が異常終了 (py_rc=$py_rc)"
	fi

	# 結果抽出
	RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$runner_tmpfile" | tail -n 1)
	rm -f "$runner_tmpfile"

	if [ -z "$RESULT_JSON" ]; then
		RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
	fi

	LAST_SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
	LAST_TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)
	LAST_RUSSIA=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('russia_created',False) else 'false')" 2>/dev/null || echo "false")
	LAST_RUSSIA_ANNOUNCED=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('russia_announced',False) else 'false')" 2>/dev/null || echo "false")
	LAST_SOVIET=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet_created',False) else 'false')" 2>/dev/null || echo "false")
	local runner_error runner_error_msg
	runner_error=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('error',''))" 2>/dev/null || echo "")
	runner_error_msg=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('error_message',''))" 2>/dev/null || echo "")

	if [ "$runner_error" = "decide_exception" ]; then
		_handle_decide_exception_recovery "$runner_error_msg" "$LAST_TURNS" "$LAST_SCORE"
		LAST_SCORE=0
		LAST_TURNS=0
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
		LAST_SOVIET="false"
		return "$PLAY_RECOVERED_RETRY_RC"
	fi

	log "[RESULT] score=$LAST_SCORE turns=$LAST_TURNS"
}

#=== ロシア建国祝賀 ===
handle_russia_celebration() {
	local score="$1" turns="$2" game_num="$3"

	# クリップは祝賀有効/無効に関係なく作成
	_create_twitch_clip "🇷🇺 ロシア建国! score=${score} (Game #${game_num})" "$game_num" "${RUSSIA_CELEBRATION_CLIP_DELAY_SEC:-5}"
	_append_celebration_history "russia" "$score" "$turns" "$game_num"

	if [ "${RUSSIA_CELEBRATION_ENABLED:-0}" = "0" ]; then
		log "[RUSSIA] 祝賀読み上げは無効化中"
		rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE"
		rm -f "$TMP_MARKERS_DIR/.russia_created"
		return 0
	fi

	log "!!! RUSSIA CREATED !!!"

	# 既存の読み上げを停止して祝賀を優先 (afplayのみ、say_enqueueはkill_flagでリトライ抑止)
	echo "1" > tmp/.say_queue/kill_flag
	pgrep -x 'afplay' 2>/dev/null | xargs kill -9 2>/dev/null || true
	rm -f tmp/.say_queue/.lock/owner_pid tmp/.say_queue/.lock/heartbeat 2>/dev/null
	rmdir tmp/.say_queue/.lock 2>/dev/null || true

	generate_russia_celebration "$score" "$turns" "$game_num"
	if [ -f "$TMP_DEBUG_DIR/radio_russia_celebration.txt" ] && [ -s "$TMP_DEBUG_DIR/radio_russia_celebration.txt" ]; then
		_refresh_radio_intro_for_playback_file "$TMP_DEBUG_DIR/radio_russia_celebration.txt" "russia_celebration"
		./say_enqueue.sh --no-preempt "$TMP_DEBUG_DIR/radio_russia_celebration.txt" "$RADIO_SAY_RATE" 0
	fi
	_radio_clear_state "russia_celebration"
	rm -f "$RUSSIA_CELEBRATION_WORKER_PID_FILE"
	rm -f "$TMP_MARKERS_DIR/.russia_created"
}

#=== ソ連建国祝賀 ===
handle_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"

	log "!!! SOVIET CREATED !!!"
	_create_twitch_clip "☭ ソ連建国! score=${score} (Game #${game_num})" "$game_num" "${SOVIET_CELEBRATION_CLIP_DELAY_SEC:-20}"
	_append_celebration_history "soviet" "$score" "$turns" "$game_num"

	# ロシア祝賀が走っていたら中止してソ連祝賀を優先
	_cancel_russia_celebration_worker

	# 祝賀トーク生成
	generate_soviet_celebration "$score" "$turns" "$game_num"

	# コメント監視・生成停止
	stop_comment_watcher
	_kill_comment_gen

	# 既存の読み上げを停止して祝賀を優先 (say_enqueueごとkill)
	log "[CELEBRATION] 既存読み上げを停止"
	echo "1" > tmp/.say_queue/kill_flag
	pgrep -x 'afplay' 2>/dev/null | xargs kill -9 2>/dev/null || true
	rm -f tmp/.say_queue/.lock/owner_pid tmp/.say_queue/.lock/heartbeat 2>/dev/null
	rmdir tmp/.say_queue/.lock 2>/dev/null || true

	sleep 30

	# 祝賀トーク再生
	if [ -f "tmp/radio_celebration.txt" ] && [ -s "tmp/radio_celebration.txt" ]; then
		_play_priority_audio_file "tmp/radio_celebration.txt" "celebration"
	fi
	_radio_clear_state "celebration"
	rm -f "$TMP_MARKERS_DIR/.soviet_created"

	# コメントプレイヤー・ウォッチャー再開
	start_comment_player
	start_comment_watcher
}

#=== 試合後の後処理 ===
post_game_bookkeeping() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ] && [ "${LAST_SOVIET:-false}" != "true" ]; then
		log "[HALT] post_game_bookkeepingをスキップ（建国後停止中）"
		return 0
	fi

	local game_num_display=$((GAME_NUM + 1))

	# ハイスコア判定を先行実行（クリップは早いほど良い）
	local _best_for_clip
	_best_for_clip=$(cat best_score.txt 2>/dev/null || echo 0)
	if [ "${LAST_SCORE:-0}" -gt "${_best_for_clip:-0}" ]; then
		_create_twitch_clip "🏆 NEW HIGH SCORE: ${LAST_SCORE}! (Game #${game_num_display})" "$game_num_display"
	fi

	# チャネルポイント予想: 新サイクル初戦なら賭けを作成（改善中は待機）
	./twitch_predictions.sh cleanup "$game_num_display" >>tmp/prediction.log 2>&1 || true
	if [ ! -f "$TMP_STATE_DIR/current_prediction.json" ]; then
		local acc_count_for_pred=0
		if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
			acc_count_for_pred=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
		fi
		local improve_status=""
		improve_status=$(python3 -c "import json; print(json.load(open('$IMPROVE_STATE_FILE')).get('status',''))" 2>/dev/null || echo "")
		if [ "${acc_count_for_pred:-0}" -eq 0 ] && [ "$improve_status" != "running" ]; then
			./twitch_predictions.sh create "$game_num_display" >>tmp/prediction.log 2>&1 || true
		fi
	fi

	# チャネルポイント予想: 今回の結果を best_outcome に蓄積（リセット前に判定）
	if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
		local cur_outcome=0
		if [ "${LAST_SOVIET:-false}" = "true" ]; then
			cur_outcome=2
		elif [ "${LAST_RUSSIA:-false}" = "true" ]; then
			cur_outcome=1
		fi
		if [ "$cur_outcome" -gt 0 ]; then
			local prev_best=0
			prev_best=$(python3 -c "import json; print(json.load(open('$TMP_STATE_DIR/current_prediction.json')).get('best_outcome',0))" 2>/dev/null || echo 0)
			if [ "$cur_outcome" -gt "$prev_best" ]; then
				python3 -c "
import json
f='$TMP_STATE_DIR/current_prediction.json'
d=json.load(open(f))
d['best_outcome']=$cur_outcome
json.dump(d,open(f,'w'))
" 2>/dev/null
			fi
		fi
	fi

	# 蓄積用にロシア建国フラグを保存（後でリセットされるため）
	local _russia_for_acc="$LAST_RUSSIA"

	# ソ連建国チェック
	if [ "$LAST_SOVIET" = "true" ]; then
		handle_soviet_celebration "$LAST_SCORE" "$LAST_TURNS" "$game_num_display"
		# チャネルポイント予想: ソ連建国で即 resolve（HALT 後は trigger_adaptive_improvement が呼ばれないため）
		if [ -f "$TMP_STATE_DIR/current_prediction.json" ]; then
			./twitch_predictions.sh resolve 2 >>tmp/prediction.log 2>&1 || true
		fi
		HALT_STRATEGY_AFTER_SOVIET=1
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
		LAST_SOVIET="false"
		log "[HALT] ソ連建国達成: strategy実行を停止し、retry/次ゲーム操作を無効化"
	elif [ "$LAST_RUSSIA" = "true" ] && [ "${LAST_RUSSIA_ANNOUNCED:-false}" != "true" ]; then
		handle_russia_celebration "$LAST_SCORE" "$LAST_TURNS" "$game_num_display"
		LAST_RUSSIA="false"
		LAST_RUSSIA_ANNOUNCED="false"
	fi

	# スコア履歴
	printf '%s\t%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/')" "$LAST_SCORE" >> score_history.txt

	# ダッシュボード更新（GAMEOVER状態で生成→表示される）
	log "[DASHBOARD] Generating GAMEOVER dashboard..."
	./generate_dashboard.sh GAMEOVER || log "[DASHBOARD] ERROR: generate_dashboard.sh GAMEOVER failed"

	# バージョン保存・ベスト判定・履歴アーカイブ
	save_strategy_version "$LAST_SCORE"
	update_best "$LAST_SCORE"
	archive_history "$LAST_SCORE"

	# アーカイブファイル名を記録
	LAST_ARCHIVE_FILE=$(ls -1t "$HISTORY_DIR"/[0-9]*_score*.jsonl 2>/dev/null | head -1)
	archive_gameover_screenshots "$LAST_ARCHIVE_FILE"

	# 建国ボーナス: 最終盤面のtype別ボーナスを加算した評価スコア
	local EVAL_SCORE
	EVAL_SCORE=$(echo "$RESULT_JSON" | python3 -c "
import json, sys
d = json.load(sys.stdin)
types = d.get('final_types', [])
soviet = d.get('soviet_created', False)
TB = {1:0,2:0,3:1,4:1,5:2,6:3,7:4,8:6,9:10,10:16,11:26,12:40,13:70,14:120,15:240}
bonus = sum(TB.get(t, 0) for t in types)
if soviet: bonus += 800
print(d.get('score', 0) + bonus)
" 2>/dev/null || echo "$LAST_SCORE")

	local _bonus=$(( EVAL_SCORE - LAST_SCORE ))
	if [ "$_bonus" -gt 0 ]; then
		local _top_types
		_top_types=$(echo "$RESULT_JSON" | python3 -c "import json,sys;ts=json.load(sys.stdin).get('final_types',[]);print(sorted(ts,reverse=True)[:5])" 2>/dev/null || echo "[]")
		log "[BONUS] types=${_top_types} bonus=+${_bonus} eval=${EVAL_SCORE} (raw=${LAST_SCORE})"
	fi

	# 改善用の rolling/queued 記録はここで一度だけ行う
	record_completed_game_for_adaptive_improvement "$LAST_ARCHIVE_FILE" "$EVAL_SCORE" "$LAST_SOVIET" "$_russia_for_acc"

	# サイクル1試合目: 前サイクルの改善結果 or 粛清ラジオをここで発火
	if [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		local _acc_count
		_acc_count=$(python3 -c "import json; print(json.load(open('$ACCUMULATED_GAMES_FILE')).get('count',0))" 2>/dev/null || echo 0)
		if [ "${_acc_count:-0}" -eq 1 ]; then
			fire_pending_cycle_radio
		fi
	fi

	# 予想サイクル進捗をチャットに投稿
	if [ -f "$TMP_STATE_DIR/current_prediction.json" ] && [ -f "$ACCUMULATED_GAMES_FILE" ]; then
		local pred_progress
		pred_progress=$(python3 - "$ACCUMULATED_GAMES_FILE" "$LAST_SCORE" <<'PY'
import json, sys
acc = json.load(open(sys.argv[1]))
count = acc.get("count", 0)
scores = acc.get("scores", "").split()
avg = sum(int(s) for s in scores) // len(scores) if scores else 0
remain = 12 - count
russia = acc.get("russia_count", 0)
russia_str = f" 🇷🇺×{russia}" if russia > 0 else ""
print(f"サイクル進捗 [{count}/12] score={sys.argv[2]} | avg={avg}{russia_str} (次の戦略改善まであと{remain}試合)")
PY
		)
		./twitch_chat.sh send "${pred_progress}" 2>/dev/null &
	fi

	# コメントプレイヤー・ウォッチャーが死んでいたら再起動
	start_comment_player
	start_comment_watcher

	# git commit
	git add -A
	git commit -m "eloop Game #${game_num_display}: score=${LAST_SCORE}" 2>/dev/null && \
		git push 2>/dev/null || true
	_post_pending_phyrogenetic_tree_link_to_chat_if_any
}

#=== 次の試合準備 ===
prepare_next_game() {
	if [ "${HALT_STRATEGY_AFTER_SOVIET:-0}" -eq 1 ]; then
		log "[HALT] prepare_next_gameをスキップ（retryなし）"
		return 0
	fi

	# 試合時スナップショットのクリーンアップ
	rm -f "${STRATEGY_FILE}.game_snapshot"

	if is_game_over; then
		send_retry
	else
		wait_for_move || {
			send_retry
		}
	fi
}

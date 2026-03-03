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
#
# 前提: eloop_lib.sh が source 済みであること

#=== 1試合プレイ ===
play_one_game() {
	local game_num_display=$((GAME_NUM + 1))
	log ""
	log "── Game #${game_num_display} ──"

	# 試合開始時の strategy.py をスナップショット保存
	# (裏で改善が strategy.py を書き換えても、この試合で使った戦略を正確に保存できる)
	cp "$STRATEGY_FILE" "${STRATEGY_FILE}.game_snapshot"

	# strategy_runner.py で1試合プレイ
	local runner_tmpfile
	runner_tmpfile=$(mktemp /tmp/eloop_runner.XXXXXX)
	python3 -u strategy_runner.py 2>&1 | tee "$runner_tmpfile"

	# 結果抽出
	RESULT_JSON=$(sed -n '/^---RESULT---$/,$ p' "$runner_tmpfile" | tail -n 1)
	rm -f "$runner_tmpfile"

	if [ -z "$RESULT_JSON" ]; then
		RESULT_JSON='{"score":0,"turns":0,"state":"UNKNOWN"}'
	fi

	LAST_SCORE=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('score',0))" 2>/dev/null || echo 0)
	LAST_TURNS=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print(json.load(sys.stdin).get('turns',0))" 2>/dev/null || echo 0)
	LAST_SOVIET=$(echo "$RESULT_JSON" | python3 -c "import json,sys; print('true' if json.load(sys.stdin).get('soviet_created',False) else 'false')" 2>/dev/null || echo "false")

	log "[RESULT] score=$LAST_SCORE turns=$LAST_TURNS"
}

#=== ソ連建国祝賀 ===
handle_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"

	log "!!! SOVIET CREATED !!!"

	# 祝賀トーク生成
	generate_soviet_celebration "$score" "$turns" "$game_num"

	# コメント監視・生成停止
	stop_comment_watcher
	_kill_comment_gen

	# 既存の読み上げを全停止
	stop_comment_player
	pkill -x say 2>/dev/null || true
	pkill -f say_enqueue 2>/dev/null || true
	rm -f tmp/.say_queue/content_*.txt tmp/.say_queue/pid tmp/.say_queue/token
	rm -f "$COMMENT_QUEUE_DIR"/comment_*.txt
	log "[CELEBRATION] 既存読み上げを全停止"

	sleep 30

	# 祝賀トーク再生
	if [ -f "tmp/radio_celebration.txt" ] && [ -s "tmp/radio_celebration.txt" ]; then
		./say_enqueue.sh --no-preempt tmp/radio_celebration.txt "$RADIO_SAY_RATE" 0
	fi
	rm -f tmp/.radio_state
	rm -f tmp/.soviet_created

	# コメントプレイヤー・ウォッチャー再開
	start_comment_player
	start_comment_watcher
}

#=== 試合後の後処理 ===
post_game_bookkeeping() {
	local game_num_display=$((GAME_NUM + 1))

	# ソ連建国チェック
	if [ "$LAST_SOVIET" = "true" ]; then
		handle_soviet_celebration "$LAST_SCORE" "$LAST_TURNS" "$game_num_display"
	fi

	# スコア履歴
	echo "$LAST_SCORE" >> score_history.txt

	# ダッシュボード更新（GAMEOVER状態で生成→表示される）
	log "[DASHBOARD] Generating GAMEOVER dashboard..."
	./generate_dashboard.sh GAMEOVER || log "[DASHBOARD] ERROR: generate_dashboard.sh GAMEOVER failed"

	# バージョン保存・ベスト判定・履歴アーカイブ
	save_strategy_version "$LAST_SCORE"
	update_best "$LAST_SCORE"
	archive_history "$LAST_SCORE"

	# アーカイブファイル名を記録
	LAST_ARCHIVE_FILE=$(ls -1t "$HISTORY_DIR"/[0-9]*_score*.jsonl 2>/dev/null | head -1)

	# コメントプレイヤー・ウォッチャーが死んでいたら再起動
	start_comment_player
	start_comment_watcher

	# git commit
	git add -A
	git commit -m "eloop Game #${game_num_display}: score=${LAST_SCORE}" 2>/dev/null && \
		git push 2>/dev/null || true

	_maybe_show_joke
}

#=== 次の試合準備 ===
prepare_next_game() {
	# 試合時スナップショットのクリーンアップ
	rm -f "${STRATEGY_FILE}.game_snapshot"

	# ダッシュボード表示の猶予（OBSで見せる時間）
	sleep 10

	# ダッシュボード非表示（次のゲーム開始前）
	log "[DASHBOARD] Generating MOVE dashboard (hiding)..."
	./generate_dashboard.sh MOVE || log "[DASHBOARD] ERROR: generate_dashboard.sh MOVE failed"

	if is_game_over; then
		send_retry
	else
		wait_for_move || {
			send_retry
		}
	fi
}

#!/bin/bash
# lib/eloop_comment.sh - コメントプレイヤー・ウォッチャー・生成

#=== コメント関連 ===

_kill_comment_gen() {
	local pidfile="tmp/.twitch_chat/comment_gen.pid"
	local statefile="$COMMENT_GEN_STATE_FILE"
	if [ -f "$pidfile" ]; then
		local raw old_pid old_ppid live_ppid
		raw=$(cat "$pidfile" 2>/dev/null || true)
		old_pid="${raw%%|*}"
		case "$old_pid" in
		''|*[!0-9]*) old_pid="" ;;
		esac
		if [ "$raw" != "$old_pid" ]; then
			old_ppid=$(printf '%s' "$raw" | awk -F'|' '{print $2}')
			case "$old_ppid" in
			''|*[!0-9]*) old_ppid="" ;;
			esac
		else
			old_ppid=""
		fi
		if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
			live_ppid=$(ps -o ppid= -p "$old_pid" 2>/dev/null | tr -d ' ')
			if [ -f "$statefile" ] && { [ -z "$old_ppid" ] || [ "$old_ppid" = "$live_ppid" ]; }; then
				pkill -P "$old_pid" 2>/dev/null
				kill "$old_pid" 2>/dev/null
				log "[COMMENT] 前回のコメント生成プロセス停止 (PID=$old_pid)"
			else
				log "[COMMENT] stale comment_gen pid検出 → killスキップ (PID=$old_pid, ppid_file=${old_ppid:-?}, ppid_live=${live_ppid:-?})"
			fi
		fi
		rm -f "$pidfile"
	fi
	rm -f "$statefile"
}

COMMENT_PLAYED_HASHES_FILE="tmp/.comment_queue/played_hashes.txt"

get_comment_backlog_counts() {
	local queued playing
	queued=$(ls -1 "$COMMENT_QUEUE_DIR"/comment_*.txt 2>/dev/null | wc -l | tr -d ' ')
	playing=$(ls -1 "$COMMENT_QUEUE_DIR"/comment_*.playing 2>/dev/null | wc -l | tr -d ' ')
	queued=${queued:-0}
	playing=${playing:-0}
	echo "${queued} ${playing}"
}

is_comment_backlog_high() {
	local threshold="${1:-4}"
	local queued playing total
	read -r queued playing <<<"$(get_comment_backlog_counts)"
	queued=${queued:-0}
	playing=${playing:-0}
	total=$((queued + playing))
	[ "$total" -ge "$threshold" ]
}

_is_recent_comment_batch_processed() {
	local batch_hash="$1"
	[ -n "$batch_hash" ] || return 1
	[ -f "$COMMENT_BATCH_HISTORY_FILE" ] || return 1
	local now
	now=$(date +%s)
	awk -F'|' -v h="$batch_hash" -v now="$now" -v ttl="$COMMENT_BATCH_DEDUP_TTL" '
		$2 == h && (now - $1) <= ttl { found=1 }
		END { exit(found ? 0 : 1) }
	' "$COMMENT_BATCH_HISTORY_FILE" 2>/dev/null
}

_mark_comment_batch_processed() {
	local batch_hash="$1"
	[ -n "$batch_hash" ] || return 0
	local now tmpf
	now=$(date +%s)
	tmpf=$(mktemp /tmp/eloop_comment_batch_history_XXXXXXXX)
	{
		if [ -f "$COMMENT_BATCH_HISTORY_FILE" ]; then
			awk -F'|' -v now="$now" -v ttl="$COMMENT_BATCH_DEDUP_TTL" '
				NF >= 2 && $1 ~ /^[0-9]+$/ && (now - $1) <= (ttl * 3) { print }
			' "$COMMENT_BATCH_HISTORY_FILE" 2>/dev/null
		fi
		echo "${now}|${batch_hash}"
	} >"$tmpf"
	mv "$tmpf" "$COMMENT_BATCH_HISTORY_FILE"
}

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
		echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] リカバリ: $orphan → $recovered" >> tmp/.say_queue/debug.log
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
			# 重複チェック: 同じ内容を再度再生しない
			local file_hash
			file_hash=$(md5 -q "$qf" 2>/dev/null)
			if [ -n "$file_hash" ] && grep -qF "$file_hash" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 重複スキップ: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				rm -f "$qf"
				continue
			fi

			# 再生前にリネームして他プレイヤーとの二重再生を防ぐ
			local playing_file="${qf%.txt}.playing"
			if mv "$qf" "$playing_file" 2>/dev/null; then
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 再生開始: $qf (hash=$file_hash)" >> tmp/.say_queue/debug.log
				# ハッシュを記録（再生開始前に記録して、kill時にも重複防止）
				echo "$file_hash" >> "$COMMENT_PLAYED_HASHES_FILE"
				# ハッシュファイルを最新50件に制限
				tail -50 "$COMMENT_PLAYED_HASHES_FILE" > "${COMMENT_PLAYED_HASHES_FILE}.tmp" 2>/dev/null && \
					mv "${COMMENT_PLAYED_HASHES_FILE}.tmp" "$COMMENT_PLAYED_HASHES_FILE" 2>/dev/null
				./say_enqueue.sh --no-preempt "$playing_file" "$RADIO_SAY_RATE" 0
				echo "[_play_comment_queue $(date '+%H:%M:%S') PID=${BASHPID:-$$}] 再生完了: $playing_file" >> tmp/.say_queue/debug.log
				rm -f "$playing_file"
			fi
		fi
	done
}

COMMENT_PLAYER_PID_FILE="tmp/.comment_queue/player.pid"

_is_comment_worker_healthy() {
	local pid_file="$1" heartbeat_file="$2" ttl="${3:-30}"
	[ -f "$pid_file" ] || return 1

	local pid
	pid=$(cat "$pid_file" 2>/dev/null)
	[ -n "$pid" ] || return 1
	kill -0 "$pid" 2>/dev/null || return 1
	# ttl<=0 の場合は PID 生存のみでヘルシー判定
	if [ "$ttl" -le 0 ]; then
		return 0
	fi

	[ -f "$heartbeat_file" ] || return 1
	local hb now age
	hb=$(cat "$heartbeat_file" 2>/dev/null)
	case "$hb" in
	''|*[!0-9]*) return 1 ;;
	esac
	now=$(date +%s)
	age=$((now - hb))
	[ "$age" -le "$ttl" ] || return 1
	return 0
}

start_comment_player() {
	# 既存プレイヤーが生存中なら重複起動しない（再生中はheartbeatが止まり得るためPID優先）
	if _is_comment_worker_healthy "$COMMENT_PLAYER_PID_FILE" "$COMMENT_PLAYER_HEARTBEAT_FILE" 0; then
		return
	fi
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] 再生プロセスPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
	mkdir -p "$(dirname "$COMMENT_PLAYER_PID_FILE")"

	(
		# サブシェル内でPIDファイルを自分のPIDで上書き
		# NOTE: local はサブシェル直下では使えない (関数内でのみ有効)
		_cp_my_pid=${BASHPID:-$$}
		echo "$_cp_my_pid" > "$COMMENT_PLAYER_PID_FILE" 2>/dev/null
		_recover_orphan_comment_playing_files
		while true; do
			# PIDファイルが自分のPIDでなくなったら終了（別プレイヤーに交代された）
			_cp_file_pid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
			if [ "$_cp_file_pid" != "$_cp_my_pid" ]; then
				exit 0
			fi
			date +%s >"$COMMENT_PLAYER_HEARTBEAT_FILE" 2>/dev/null || true
			_play_comment_queue
			sleep 5
		done
	) &
	local cpid=$!
	echo "$cpid" > "$COMMENT_PLAYER_PID_FILE"
	log "[COMMENT] 再生プロセス開始 (PID=$cpid)"
}

stop_comment_player() {
	if [ -f "$COMMENT_PLAYER_PID_FILE" ]; then
		local cpid
		cpid=$(cat "$COMMENT_PLAYER_PID_FILE" 2>/dev/null)
		if [ -n "$cpid" ] && [ "$cpid" != "$$" ] && kill -0 "$cpid" 2>/dev/null; then
			kill "$cpid" 2>/dev/null
			wait "$cpid" 2>/dev/null
		fi
		rm -f "$COMMENT_PLAYER_PID_FILE"
	fi
	rm -f "$COMMENT_PLAYER_HEARTBEAT_FILE"
}

_format_comment_batch_context() {
	python3 -c '
import sys

lines = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
items = []
for ln in lines:
    if ": " in ln:
        user, msg = ln.split(": ", 1)
    else:
        user, msg = "不明", ln
    items.append((user.strip(), msg.strip(), ln))

for i, (user, msg, raw) in enumerate(items, start=1):
    prev_raw = items[i - 2][2] if i > 1 else "（なし）"
    next_raw = items[i][2] if i < len(items) else "（なし）"
    same_user_prev = "あり" if i > 1 and items[i - 2][0] == user else "なし"
    print(f"[{i}] {user}: {msg}")
    print(f"  直前: {prev_raw}")
    print(f"  直後: {next_raw}")
    print(f"  直前が同一ユーザー: {same_user_prev}")
    print("")
'
}

_build_comment_game_context() {
	local gs_file="${1:-$GAME_STATE}"
	python3 - "$gs_file" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        gs = json.load(f)
except Exception:
    print("（game_state.json を読めませんでした）")
    raise SystemExit(0)

state = gs.get("state", "?")
score = gs.get("score", 0)
record = gs.get("record", 0)
print("この値はコメント生成時点の参考メモ。盤面の厳密照合には使わないこと。")
print(f"state={state}, score={score}, record={record}")
PY
}

generate_comment_response() {
	_kill_comment_gen
	mkdir -p "tmp/.twitch_chat"

	# fetch+ack を原子的に実行して、同一コメントの二重取り込みを防ぐ
	./twitch_chat.sh claim

	local twitch_comments=""
	if [ -f "tmp/twitch_comments.txt" ] && [ -s "tmp/twitch_comments.txt" ]; then
		twitch_comments=$(cat "tmp/twitch_comments.txt")
		# コメント返し担当が取得したので、ラジオトークと重複しないようクリア
		rm -f "tmp/twitch_comments.txt"
	fi
	[ -z "$twitch_comments" ] && return

	local comment_batch_hash=""
	comment_batch_hash=$(printf '%s' "$twitch_comments" | md5 -q 2>/dev/null || echo "")
	if _is_recent_comment_batch_processed "$comment_batch_hash"; then
		log "[COMMENT] 同一コメントバッチを直近で処理済みのためスキップ (batch=$comment_batch_hash)"
		return
	fi

	local past_topics=""
	[ -f "$PAST_RADIO_TOPICS" ] && past_topics=$(cat "$PAST_RADIO_TOPICS")
	local game_state_context=""
	game_state_context=$(_build_comment_game_context "$GAME_STATE")

	local comment_context_history_file="tmp/.twitch_chat/comment_context_history.log"
	local previous_comments_context=""
	[ -f "$comment_context_history_file" ] && previous_comments_context=$(tail -30 "$comment_context_history_file" 2>/dev/null)
	printf '%s\n' "$twitch_comments" >> "$comment_context_history_file"
	if [ -f "$comment_context_history_file" ] && [ "$(wc -l < "$comment_context_history_file")" -gt 300 ]; then
		tail -300 "$comment_context_history_file" > "${comment_context_history_file}.tmp"
		mv "${comment_context_history_file}.tmp" "$comment_context_history_file"
	fi

	local comment_batch_context=""
	comment_batch_context=$(printf '%s\n' "$twitch_comments" | _format_comment_batch_context)

	local current_time current_hour time_period
	current_time=$(date '+%H:%M')
	current_hour=$(date '+%H')
	if [ "$current_hour" -ge 5 ] && [ "$current_hour" -lt 9 ]; then
		time_period="朝"
	elif [ "$current_hour" -ge 9 ] && [ "$current_hour" -lt 12 ]; then
		time_period="午前"
	elif [ "$current_hour" -ge 12 ] && [ "$current_hour" -lt 17 ]; then
		time_period="午後"
	elif [ "$current_hour" -ge 17 ] && [ "$current_hour" -lt 21 ]; then
		time_period="夕方"
	elif [ "$current_hour" -ge 21 ] || [ "$current_hour" -lt 2 ]; then
		time_period="夜"
	else
		time_period="未明"
	fi

	local comment_parent_pid comment_started_at
	comment_parent_pid="${BASHPID:-$$}"
	comment_started_at=$(date +%s)
	echo "generating:comment:${comment_started_at}" > $COMMENT_GEN_STATE_FILE

	(
		_cleanup_comment_gen_worker() {
			local raw file_pid
			raw=$(cat tmp/.twitch_chat/comment_gen.pid 2>/dev/null || true)
			file_pid="${raw%%|*}"
			if [ "$file_pid" = "${BASHPID:-$$}" ]; then
				rm -f tmp/.twitch_chat/comment_gen.pid
			fi
			rm -f $COMMENT_GEN_STATE_FILE
		}
		trap '_cleanup_comment_gen_worker' EXIT

		local comment_prompt_file
		comment_prompt_file=$(mktemp /tmp/eloop_comment_prompt_XXXXXXXX)
		export current_time time_period twitch_comments
		export comment_batch_context="${comment_batch_context:-（なし）}"
		export previous_comments_context="${previous_comments_context:-（なし）}"
		export past_topics
		export game_state_context="${game_state_context:-（取得失敗）}"
		envsubst < "$ELOOP_LIB_DIR/prompts/comment_response.md" >"$comment_prompt_file"
		unset current_time time_period twitch_comments comment_batch_context previous_comments_context past_topics game_state_context

			echo "generating:comment:$(date +%s)" > $COMMENT_GEN_STATE_FILE
			log "[COMMENT] コメント返し生成中..."
			local comments_talk comment_model_used
			comment_model_used=""
			comments_talk=$(_run_opencode_radio "$RADIO_AGENT" "$comment_prompt_file")
			comment_model_used="$RADIO_AGENT"
			comments_talk=$(_clean_comment_talk "$comments_talk")
			comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
			if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
				log "[COMMENT] ${RADIO_AGENT} 出力が不正/短文のため破棄 → fallback"
				comments_talk=""
				comment_model_used=""
			fi
			if [ -z "$comments_talk" ]; then
				comments_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$comment_prompt_file")
				comment_model_used="$RADIO_FALLBACK"
				comments_talk=$(_clean_comment_talk "$comments_talk")
				comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
				if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
					log "[COMMENT] ${RADIO_FALLBACK} 出力が不正/短文のため破棄 → claude fallback"
					comments_talk=""
					comment_model_used=""
				fi
			fi
			if [ -z "$comments_talk" ]; then
				comments_talk=$(_run_claude_radio "$comment_prompt_file")
				comment_model_used="claude:${RADIO_CLAUDE_MODEL}"
				comments_talk=$(_clean_comment_talk "$comments_talk")
				comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
				if [ -n "$comments_talk" ] && ! _is_valid_comment_talk "$comments_talk"; then
					log "[COMMENT] claude 出力が不正/短文のため破棄"
					comments_talk=""
					comment_model_used=""
				fi
			fi
			rm -f "$comment_prompt_file"

			if [ -n "$comments_talk" ]; then
				# 戦略アドバイスを抽出して advice.md に追記
				local advice_part
				advice_part=$(echo "$comments_talk" | sed -n '/^===ADVICE===/,$ p' | tail -n +2)
				if [ -n "$advice_part" ]; then
					local advice_item
					advice_item=$(printf '%s' "$advice_part" | tr '\n' ' ' | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
					if [ -n "$advice_item" ] && [ "$advice_item" != "（アドバイスなし）" ] && [ "$advice_item" != "なし" ] && [[ "$advice_item" != なし* ]] && [[ "$advice_item" != （アドバイスなし）* ]]; then
						echo "- $advice_item" >> advice.md
					fi
					# 最新エントリ程度に制限
					if [ -f advice.md ] && [ "$(wc -l < advice.md)" -gt 150 ]; then
						tail -150 advice.md > advice.md.tmp
						mv advice.md.tmp advice.md
					fi
					log "[COMMENT] 戦略アドバイス検出 → advice.md に追記"
					# トーク本文からアドバイス部分を除去
					comments_talk=$(echo "$comments_talk" | sed '/^===ADVICE===/,$ d')
				fi

			comments_talk=$(_clean_comment_talk "$comments_talk")
			comments_talk=$(printf '%s' "$comments_talk" | _sanitize_onair_text)
			if ! _is_valid_comment_talk "$comments_talk"; then
				log "[COMMENT] 最終本文が不正/短文のため破棄"
			else
				local queue_file="$COMMENT_QUEUE_DIR/comment_$(date +%s)_${RANDOM}.txt"
				echo "$comments_talk" >"$queue_file"
				# 生成直後に重複チェック（同じ内容のキューファイルがないか）
					local new_hash
					new_hash=$(md5 -q "$queue_file" 2>/dev/null)
					if [ -n "$new_hash" ] && grep -qF "$new_hash" "$COMMENT_QUEUE_DIR/played_hashes.txt" 2>/dev/null; then
						log "[COMMENT] 重複コメント返し検出 → 破棄 (hash=$new_hash)"
						_mark_comment_batch_processed "$comment_batch_hash"
						rm -f "$queue_file"
					else
						_mark_comment_batch_processed "$comment_batch_hash"
						log "[COMMENT] コメント返し ${#comments_talk}字 → キュー追加: $queue_file (model=${comment_model_used:-unknown}, batch=${comment_batch_hash:-none})"
					fi
				fi
			else
			log "[COMMENT] コメント返し生成失敗（次回再取得）"
		fi
	) &
	local comment_pid=$!
	echo "${comment_pid}|${comment_parent_pid}|${comment_started_at}" >tmp/.twitch_chat/comment_gen.pid
	disown "$comment_pid"
}

#=== コメント監視デーモン ===
# 10秒ごとにTwitchコメントをポーリングし、新コメントがあれば即座に生成→キュー追加

start_comment_watcher() {
	# 既存ウォッチャーが生存中なら重複起動しない（PID + heartbeat で判定）
	if _is_comment_worker_healthy "$COMMENT_WATCHER_PID_FILE" "$COMMENT_WATCHER_HEARTBEAT_FILE" "$COMMENT_WORKER_HEALTH_TTL"; then
		return
	fi
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local stale_pid
		stale_pid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$stale_pid" ]; then
			log "[COMMENT] ウォッチャーPIDが不整合/停止を検出 → 再起動 (PID=$stale_pid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
	mkdir -p "$(dirname "$COMMENT_WATCHER_PID_FILE")"

	(
		_cw_my_pid=${BASHPID:-$$}
		echo "$_cw_my_pid" > "$COMMENT_WATCHER_PID_FILE" 2>/dev/null
		while true; do
			# PIDファイルが自分でなくなったら終了
			_cw_file_pid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
			if [ "$_cw_file_pid" != "$_cw_my_pid" ]; then
				exit 0
			fi
			date +%s >"$COMMENT_WATCHER_HEARTBEAT_FILE" 2>/dev/null || true

			# コメント生成が進行中なら今回はスキップ
			local gen_pidfile="tmp/.twitch_chat/comment_gen.pid"
			local gen_running=false
			if [ -f "$gen_pidfile" ]; then
				local gen_pid
				gen_pid=$(cat "$gen_pidfile" 2>/dev/null)
				gen_pid="${gen_pid%%|*}"
				case "$gen_pid" in
				''|*[!0-9]*) gen_pid="" ;;
				esac
				if [ -n "$gen_pid" ] && kill -0 "$gen_pid" 2>/dev/null; then
					gen_running=true
				fi
			fi

			if [ "$gen_running" = "true" ]; then
				# 生成中は未読を溜めるだけにして、取りこぼしを防ぐ
				./twitch_chat.sh fetch 2>/dev/null
			else
				# idle時は claim で原子的に取得して生成
				generate_comment_response
			fi

			sleep "$COMMENT_WATCHER_INTERVAL"
		done
	) &
	local wpid=$!
	echo "$wpid" > "$COMMENT_WATCHER_PID_FILE"
	disown "$wpid"
	log "[COMMENT] ウォッチャー開始 (PID=$wpid, interval=${COMMENT_WATCHER_INTERVAL}s)"
}

stop_comment_watcher() {
	if [ -f "$COMMENT_WATCHER_PID_FILE" ]; then
		local wpid
		wpid=$(cat "$COMMENT_WATCHER_PID_FILE" 2>/dev/null)
		if [ -n "$wpid" ] && [ "$wpid" != "$$" ] && kill -0 "$wpid" 2>/dev/null; then
			kill "$wpid" 2>/dev/null
			wait "$wpid" 2>/dev/null
			log "[COMMENT] ウォッチャー停止 (PID=$wpid)"
		fi
		rm -f "$COMMENT_WATCHER_PID_FILE"
	fi
	rm -f "$COMMENT_WATCHER_HEARTBEAT_FILE"
}

# soren91_control.sh - soren91 (メリケンAI) の起動・停止・改善キック管理
#
# eloop_lib.sh から source される。
# SOREN91_ENABLED=1 (.env) でなければ全関数は即 return 0。

# --- 定数 ---
_soren91_env_get() {
	local key="$1"
	local env_file="$ELOOP_LIB_DIR/.env"
	[ -f "$env_file" ] || return 1
	local value=""
	value=$(grep -E "^${key}=" "$env_file" 2>/dev/null | tail -n 1 | cut -d= -f2-)
	[ -n "$value" ] || return 1
	value="${value#\"}"
	value="${value%\"}"
	printf '%s' "$value"
}

SOREN91_ENABLED="$(_soren91_env_get SOREN91_ENABLED 2>/dev/null || printf '%s' "${SOREN91_ENABLED:-0}")"
SOREN91_STOP_TIMEOUT="${SOREN91_STOP_TIMEOUT:-120}"
SOREN91_DIR="$ELOOP_LIB_DIR/soren91"
SOREN91_PID_FILE="$SOREN91_DIR/tmp/soren91.pid"
SOREN91_IMPROVE_PID_FILE="$SOREN91_DIR/tmp/soren91_improve.pid"
SOREN91_IMPROVE_LOCK="$SOREN91_DIR/tmp/soren91_improve.lock"
SOREN91_SESSION_FILE="$SOREN91_DIR/tmp/session_games.json"
SOREN91_STOP_FILE="$SOREN91_DIR/tmp/stop"
SOREN91_RUNNER_SCRIPT="$SOREN91_DIR/run_player_loop.sh"
SOREN91_VOICEVOX_SPEAKER="$(_soren91_env_get SOREN91_VOICEVOX_SPEAKER 2>/dev/null || printf '%s' "${SOREN91_VOICEVOX_SPEAKER:-46}")"
SOREN91_OBS_CONTROL="$ELOOP_LIB_DIR/obs_control.sh"
SOREN91_OBS_INPUT_NAME="$(_soren91_env_get SOREN91_OBS_INPUT_NAME 2>/dev/null || printf '%s' "${SOREN91_OBS_INPUT_NAME:-91}")"
SOREN91_AUDIO_GAIN_MULTIPLIER="$(_soren91_env_get SOREN91_AUDIO_GAIN_MULTIPLIER 2>/dev/null || printf '%s' "${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}")"
MANUAL_MERIKEN_MODE_FILE="${MANUAL_MERIKEN_MODE_FILE:-$TMP_STATE_DIR/manual_meriken_mode.json}"
SOREN91_MERIKEN_IMPROVE_INTERVAL="${SOREN91_MERIKEN_IMPROVE_INTERVAL:-3}"

_soren91_switch_obs_layout() {
	local mode="${1:-}"
	[ -x "$SOREN91_OBS_CONTROL" ] || return 0
	case "$mode" in
	meriken)
		"$SOREN91_OBS_CONTROL" batch soren hide:console1,console2,console3 >/dev/null 2>&1 &
		;;
	china)
		"$SOREN91_OBS_CONTROL" batch soren show:console1,console2,console3 >/dev/null 2>&1 &
		;;
	*)
		return 1
		;;
	esac
}

_soren91_enabled() {
	[ "${SOREN91_ENABLED:-0}" = "1" ]
}

_write_manual_meriken_mode_state() {
	local enabled="$1" note="${2:-}"
	mkdir -p "$(dirname "$MANUAL_MERIKEN_MODE_FILE")" 2>/dev/null || true
	python3 - "$MANUAL_MERIKEN_MODE_FILE" "$enabled" "$note" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, enabled_raw, note = sys.argv[1:4]
enabled = enabled_raw == "1"
payload = {
    "enabled": enabled,
    "note": note,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

manual_meriken_mode_is_enabled() {
	[ -f "$MANUAL_MERIKEN_MODE_FILE" ] || return 1
	python3 - "$MANUAL_MERIKEN_MODE_FILE" <<'PY' >/dev/null 2>&1
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    raise SystemExit(1)
raise SystemExit(0 if data.get("enabled") else 1)
PY
}

manual_meriken_mode_enable() {
	_soren91_enabled || return 0
	_write_manual_meriken_mode_state "1" "manual_override"
	log "[SOREN91] manual_meriken_mode=on"
	soren91_start
}

manual_meriken_mode_disable() {
	_soren91_enabled || return 0
	if [ -f "$MANUAL_MERIKEN_MODE_FILE" ]; then
		rm -f "$MANUAL_MERIKEN_MODE_FILE"
	fi
	log "[SOREN91] manual_meriken_mode=off"
	if command -v _is_improve_running >/dev/null 2>&1 && _is_improve_running; then
		log "[SOREN91] 改善中のため、メリケンAIは継続"
		return 0
	fi
	soren91_stop
}

manual_meriken_mode_status() {
	if manual_meriken_mode_is_enabled; then
		printf 'on'
	else
		printf 'off'
	fi
}

soren91_is_running() {
	_soren91_enabled || return 1
	[ -f "$SOREN91_PID_FILE" ] || return 1
	local pid
	pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
	case "$pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	if ! kill -0 "$pid" 2>/dev/null; then
		return 1
	fi
	# pid file は soren91 起動用の bash subshell を指すことがあり、
	# 実行環境によっては ps でそのコマンドラインを安定取得できない。
	# start/stop で専用 PID ファイルを管理しているため、生存中なら稼働中とみなす。
	return 0
}

_soren91_is_improve_process() {
	# PIDが soren91 improve プロセスかどうか確認
	local pid="$1"
	case "$pid" in ''|*[!0-9]*) return 1 ;; esac
	kill -0 "$pid" 2>/dev/null || return 1
	local cmd
	cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
	echo "$cmd" | grep -q "improve.mjs" && return 0
	return 1
}

_soren91_text_has_japanese() {
	printf '%s' "$1" | grep -q '[ぁ-んァ-ヶ一-龠々ー]'
}

_soren91_generate_strategy_explanation() {
	local strategy_header="$1"
	[ -n "$strategy_header" ] || return 1
	claude -p "あなたはメリケンAI（アメリカ製AI）。以下は、元のソ連ゲーム用 strategy.py ではなく、ソ連ゲーム91（対戦版）専用の soren91/strategy.mjs のヘッダーコメントです。
この「91用戦略」の特徴だけを、視聴者向けに2〜3文で簡潔に、陽気なアメリカンな口調で解説してください。専門用語は噛み砕いてください。
元のソ連ゲームの戦略や strategy.py には触れないこと。T1、ULTRA、HOLD、balance などの語は、91の盤面制御・置き方のクセとして説明すること。
【最重要】出力は自然な日本語のみ。英語の文、英語だけの箇条書き、ローマ字だけの文は禁止。アメリカンなキャラクターでも話す言語は日本語です。
出力はトーク本文のみ（カッコや注釈なし）。

${strategy_header}" --model haiku 2>/dev/null
}

_soren91_rewrite_strategy_explanation_to_japanese() {
	local raw_text="$1"
	[ -n "$raw_text" ] || return 1
	claude -p "以下の戦略解説を、意味を変えずに自然な日本語の話し言葉2〜3文へ言い換えてください。
メリケンAIの陽気さは残してよいですが、英語の文は禁止です。
出力は日本語のトーク本文のみ（カッコや注釈なし）。

${raw_text}" --model haiku 2>/dev/null
}

soren91_start() {
	_soren91_enabled || return 0
	if soren91_is_running; then
		log "[SOREN91] Already running, skip start"
		return 0
	fi

	log "[SOREN91] Starting soren91 (メリケンAI)..."
	rm -f "$SOREN91_STOP_FILE"
	mkdir -p "$SOREN91_DIR/tmp" 2>/dev/null || true

	# 前回の soren91 improve がまだ実行中なら session_games.json を上書きしない
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local prev_imp_pid
		prev_imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		if _soren91_is_improve_process "$prev_imp_pid"; then
			log "[SOREN91] Previous improve still running (PID=$prev_imp_pid), keeping session_games.json"
		fi
	fi

	# セッション開始時のゲーム番号を記録
	local start_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		start_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	printf '{"start_game":%d,"start_time":"%s"}\n' "$start_game" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
		> "$SOREN91_SESSION_FILE"

	# メリケンAIモード判定 (手動発火 or 22-23時)
	# メリケンモードでは内部改善を有効化 (3ゲームごと)
	local _meriken_mode=0
	if manual_meriken_mode_is_enabled; then
		_meriken_mode=1
	elif [ "$(date +%H)" = "22" ] || [ "$(date +%H)" = "23" ]; then
		_meriken_mode=1
	fi

	local _ext_improve=1
	local _improve_interval=""
	if [ "$_meriken_mode" -eq 1 ]; then
		_ext_improve=0
		_improve_interval="$SOREN91_MERIKEN_IMPROVE_INTERVAL"
		log "[SOREN91] メリケンAIモード: 内部改善有効 (${_improve_interval}ゲームごと)"
	fi

	# 再試行付きランナーを完全 detach 起動
	# manual_meriken_mode を起動したターミナルを閉じても継続するよう、
	# HUP を無視して stdin/stdout/stderr を端末から切り離す。
	local pid=""
	pid=$(
		cd "$SOREN91_DIR" || exit 1
		SOREN91_SHARED_BROWSER=1 \
		SOREN91_AUDIO_GAIN_MULTIPLIER="${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}" \
		SOREN91_EXTERNAL_IMPROVE="$_ext_improve" \
		IMPROVEMENT_INTERVAL_GAMES="${_improve_interval:-}" \
			/usr/bin/nohup /bin/bash "$SOREN91_RUNNER_SCRIPT" </dev/null >/dev/null 2>&1 &
		echo $!
	)
	case "$pid" in
	''|*[!0-9]*)
		log "[SOREN91] Failed to launch detached runner"
		return 1
		;;
	esac
	echo "$pid" > "$SOREN91_PID_FILE"

	# 5秒後に生存チェック
	sleep 5
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Started successfully (PID=$pid, start_game=$start_game)"
		# 中華AI側のBGMをミュート（改善中は不要）
		touch "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
		log "[SOREN91] Muted local game BGM (flag file)"
		log "[SOREN91] soren91 browser audio gain=${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}"
		# 読み上げアナウンス + 戦略解説 (バックグラウンド)
		{
			local announce_file
			announce_file=$(mktemp /tmp/eloop_soren91_announce.XXXXXX)
			printf '%s\n' "中華AIが戦略を改善中。その間、メリケンAIがソ連ゲーム91で同志を迎え撃ちます。挑戦お待ちしています" > "$announce_file"
			SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:announce" ./say_enqueue.sh "$announce_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
			rm -f "$announce_file"

			# soren91の現在の戦略を解説
				local strategy_header=""
				strategy_header=$(sed -n '1,/\*\//p' "$SOREN91_DIR/strategy.mjs" 2>/dev/null)
				if [ -n "$strategy_header" ]; then
					local strategy_explain=""
					strategy_explain=$(_soren91_generate_strategy_explanation "$strategy_header")
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						log "[SOREN91] 戦略解説が英語寄りのため日本語へ再生成"
						strategy_explain=$(_soren91_rewrite_strategy_explanation_to_japanese "$strategy_explain")
					fi
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						log "[SOREN91] 戦略解説の日本語化に失敗したため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ]; then
						local explain_file
						explain_file=$(mktemp /tmp/eloop_soren91_strategy.XXXXXX)
						printf '%s\n' "$strategy_explain" > "$explain_file"
					SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:strategy" ./say_enqueue.sh "$explain_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
					rm -f "$explain_file"
					log "[SOREN91] 戦略解説を読み上げ"
				fi
			fi
		} &
		_soren91_switch_obs_layout meriken || true
	else
		log "[SOREN91] WARNING: Process died immediately (PID=$pid)"
		rm -f "$SOREN91_PID_FILE"
		return 1
	fi
	return 0
}

_soren91_record_end_game() {
	# セッション終了時のゲーム番号を記録 (stop/早期終了の両方から呼ばれる)
	local end_game=0
	if [ -d "$SOREN91_DIR/game_history" ]; then
		end_game=$(ls -1 "$SOREN91_DIR/game_history"/game_*.jsonl 2>/dev/null | wc -l | tr -d ' ')
	fi
	if [ -f "$SOREN91_SESSION_FILE" ]; then
		python3 -c "
import json, sys
with open('$SOREN91_SESSION_FILE') as f:
    sess = json.load(f)
sess['end_game'] = $end_game
sess['end_time'] = '$(date -u +%Y-%m-%dT%H:%M:%SZ)'
with open('$SOREN91_SESSION_FILE', 'w') as f:
    json.dump(sess, f)
" 2>/dev/null || true
	fi
	echo "$end_game"
}

soren91_stop() {
	_soren91_enabled || return 0

	local pid=""
	if [ -f "$SOREN91_PID_FILE" ]; then
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in ''|*[!0-9]*) pid="" ;; esac
	fi

	if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
		# プロセスが既に終了 → end_game だけ記録して終了
		log "[SOREN91] Not running, recording end_game"
		local eg
		eg=$(_soren91_record_end_game)
		rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
		log "[SOREN91] Stopped (already exited, end_game=$eg)"
		return 0
	fi

	log "[SOREN91] Stopping soren91 (PID=$pid)..."

	# graceful stop: stop ファイルを作成して現在のゲーム終了を待つ
	touch "$SOREN91_STOP_FILE"

	local waited=0
	while [ "$waited" -lt "$SOREN91_STOP_TIMEOUT" ]; do
		if ! kill -0 "$pid" 2>/dev/null; then
			log "[SOREN91] Stopped gracefully after ${waited}s"
			break
		fi
		sleep 2
		waited=$((waited + 2))
	done

	# タイムアウト: 子プロセス含めて強制停止
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Timeout after ${SOREN91_STOP_TIMEOUT}s, force stopping..."
		_stop_loop_descendants "$pid"
		_stop_pid_with_fallback "$pid" "soren91"
	fi

	local eg
	eg=$(_soren91_record_end_game)

	rm -f "$SOREN91_PID_FILE" "$SOREN91_STOP_FILE"
	# 中華AI側のBGMをアンミュート（改善終了・復帰）
	rm -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
	_soren91_switch_obs_layout china || true
	log "[SOREN91] Unmuted local game BGM (flag file removed)"

	# メリケンAI終了あいさつ (TTS + Twitch)
	{
		local _bye_file
		_bye_file=$(mktemp /tmp/eloop_soren91_bye.XXXXXX)
		printf '%s\n' "対戦ありがとうございました。メリケンAIはここで退場するぜ、またな！" > "$_bye_file"
		SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:bye" ./say_enqueue.sh "$_bye_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
		rm -f "$_bye_file"
	} &
	./twitch_chat.sh send "対戦ありがとうございました。メリケンAIはここで退場するぜ、またな！" 2>/dev/null &

	log "[SOREN91] Stopped (end_game=$eg)"
	return 0
}

soren91_improve() {
	_soren91_enabled || return 0

	# ロック + PID生存チェック
	if [ -f "$SOREN91_IMPROVE_LOCK" ] && [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				log "[SOREN91] Improvement already running (PID=$imp_pid), skip"
				return 0
			fi
			;;
		esac
		# stale lock cleanup
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	fi

	# セッションデータからゲーム範囲を取得
	if [ ! -f "$SOREN91_SESSION_FILE" ]; then
		log "[SOREN91] No session file, skip improve"
		return 0
	fi

	local start_game end_game
	start_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('start_game',0))" 2>/dev/null || echo 0)
	end_game=$(python3 -c "import json; print(json.load(open('$SOREN91_SESSION_FILE')).get('end_game',0))" 2>/dev/null || echo 0)

	local games_played=$((end_game - start_game))
	if [ "$games_played" -le 0 ]; then
		log "[SOREN91] No games played in session (start=$start_game, end=$end_game), skip improve"
		return 0
	fi

	log "[SOREN91] Starting improvement for games $start_game-$end_game ($games_played games)..."
	touch "$SOREN91_IMPROVE_LOCK"

	(
		cd "$SOREN91_DIR" && \
		node improve.mjs --standalone "$start_game" "$end_game" \
			>> "$SOREN91_DIR/tmp/soren91_improve.log" 2>&1
		rm -f "$SOREN91_IMPROVE_LOCK" "$SOREN91_IMPROVE_PID_FILE"
	) &
	local pid=$!
	echo "$pid" > "$SOREN91_IMPROVE_PID_FILE"
	log "[SOREN91] Improvement started (PID=$pid, games=$start_game-$end_game)"
	return 0
}

soren91_cleanup() {
	_soren91_enabled || return 0

	# プレイヤープロセス停止 (コマンド名を検証して誤kill防止)
	if [ -f "$SOREN91_PID_FILE" ]; then
		local pid
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$pid" 2>/dev/null; then
				local cmd
				cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
				if echo "$cmd" | grep -Eq 'main\.mjs|run_player_loop\.sh|soren_loop\.sh'; then
					log "[SOREN91] Cleanup: stopping player (PID=$pid)"
					_stop_loop_descendants "$pid"
					_stop_pid_with_fallback "$pid" "soren91_player"
				else
					log "[SOREN91] Cleanup: PID=$pid is not soren91 player ($cmd), skipping"
				fi
			fi
			;;
		esac
	fi

	# 改善プロセス停止 (コマンド名を検証)
	if [ -f "$SOREN91_IMPROVE_PID_FILE" ]; then
		local imp_pid
		imp_pid=$(cat "$SOREN91_IMPROVE_PID_FILE" 2>/dev/null)
		case "$imp_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$imp_pid" 2>/dev/null; then
				local cmd
				cmd=$(ps -p "$imp_pid" -o command= 2>/dev/null || echo "")
				if echo "$cmd" | grep -q "improve.mjs"; then
					log "[SOREN91] Cleanup: stopping improve (PID=$imp_pid)"
					_stop_loop_descendants "$imp_pid"
					_stop_pid_with_fallback "$imp_pid" "soren91_improve"
				else
					log "[SOREN91] Cleanup: PID=$imp_pid is not soren91 improve ($cmd), skipping"
				fi
			fi
			;;
		esac
	fi

	# ファイルクリーンアップ
	rm -f "$SOREN91_PID_FILE" "$SOREN91_IMPROVE_PID_FILE" \
		"$SOREN91_IMPROVE_LOCK" "$SOREN91_STOP_FILE" \
		"$ELOOP_LIB_DIR/tmp/mute_local_bgm"
}

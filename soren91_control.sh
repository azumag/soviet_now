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
SOREN91_STOP_TIMEOUT="${SOREN91_STOP_TIMEOUT:-300}"
SOREN91_DIR="$ELOOP_LIB_DIR/soren91"
SOREN91_PID_FILE="$SOREN91_DIR/tmp/soren91.pid"
SOREN91_MAIN_PID_FILE="$SOREN91_DIR/tmp/main.pid"
SOREN91_IMPROVE_PID_FILE="$SOREN91_DIR/tmp/soren91_improve.pid"
SOREN91_IMPROVE_LOCK="$SOREN91_DIR/tmp/soren91_improve.lock"
SOREN91_SESSION_FILE="$SOREN91_DIR/tmp/session_games.json"
SOREN91_STOP_FILE="$SOREN91_DIR/tmp/stop"
SOREN91_STOPPING_FILE="$SOREN91_DIR/tmp/stopping"
SOREN91_RUNNER_SCRIPT="$SOREN91_DIR/run_player_loop.sh"
SOREN91_VOICEVOX_SPEAKER="$(_soren91_env_get SOREN91_VOICEVOX_SPEAKER 2>/dev/null || printf '%s' "${SOREN91_VOICEVOX_SPEAKER:-46}")"
SOREN91_OBS_CONTROL="$ELOOP_LIB_DIR/obs_control.sh"
SOREN91_OBS_INPUT_NAME="$(_soren91_env_get SOREN91_OBS_INPUT_NAME 2>/dev/null || printf '%s' "${SOREN91_OBS_INPUT_NAME:-91}")"
SOREN91_AUDIO_GAIN_MULTIPLIER="$(_soren91_env_get SOREN91_AUDIO_GAIN_MULTIPLIER 2>/dev/null || printf '%s' "${SOREN91_AUDIO_GAIN_MULTIPLIER:-0.70}")"
SOREN91_TEXT_FALLBACKS="$(_soren91_env_get SOREN91_TEXT_FALLBACKS 2>/dev/null || printf '%s' "${SOREN91_TEXT_FALLBACKS:-claude}")"
MANUAL_MERIKEN_MODE_FILE="${MANUAL_MERIKEN_MODE_FILE:-$TMP_STATE_DIR/manual_meriken_mode.json}"
SOREN91_MERIKEN_IMPROVE_INTERVAL="${SOREN91_MERIKEN_IMPROVE_INTERVAL:-12}"
SOREN91_CAPITALISM_CORNER_ENABLED="${SOREN91_CAPITALISM_CORNER_ENABLED:-1}"
MERIKEN_TIME_START_HOUR="${MERIKEN_TIME_START_HOUR:-20}"
MERIKEN_TIME_END_HOUR="${MERIKEN_TIME_END_HOUR:-21}"
MERIKEN_TIME_STATE_FILE="${MERIKEN_TIME_STATE_FILE:-$TMP_STATE_DIR/meriken_time_state.json}"
SOREN91_MODE_FLAG_FILE="${SOREN91_MODE_FLAG_FILE:-$ELOOP_LIB_DIR/tmp/.soren91_mode_active}"

_soren91_switch_obs_layout() {
	local mode="${1:-}"
	[ -x "$SOREN91_OBS_CONTROL" ] || return 0
	case "$mode" in
	meriken)
		"$SOREN91_OBS_CONTROL" batch soren show:"$SOREN91_OBS_INPUT_NAME" hide:console1,console2,console3,dashboard >/dev/null 2>&1 &
		;;
	china)
		"$SOREN91_OBS_CONTROL" batch soren show:console1,console2,console3,dashboard hide:"$SOREN91_OBS_INPUT_NAME" >/dev/null 2>&1 &
		;;
	*)
		return 1
		;;
	esac
}

_soren91_enabled() {
	[ "${SOREN91_ENABLED:-0}" = "1" ]
}

_scheduled_meriken_time_enabled() {
	[ "${MERIKEN_SCHEDULED_TIME_ENABLED:-1}" = "1" ]
}

_clear_soren91_mode_flag() {
	rm -f "$SOREN91_MODE_FLAG_FILE" 2>/dev/null || true
}

_soren91_scan_alive_runner_pids() {
	local line="" pid="" cmd=""
	while IFS= read -r line; do
		pid=$(printf '%s\n' "$line" | awk '{print $1}')
		cmd=$(printf '%s\n' "$line" | cut -d' ' -f2-)
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
		[ "$pid" = "$$" ] && continue
		case "$cmd" in
		*"$SOREN91_RUNNER_SCRIPT"*|*"$SOREN91_DIR/run_player_loop.sh"*|*"soren91/run_player_loop.sh"*)
			kill -0 "$pid" 2>/dev/null || continue
			printf '%s\n' "$pid"
			;;
		esac
	done <<EOF
$(ps -Ao pid=,command= 2>/dev/null || true)
EOF
}

_soren91_read_alive_player_pid() {
	local pid="" f="" cmd=""
	for f in "$SOREN91_MAIN_PID_FILE" "$SOREN91_PID_FILE"; do
		[ -f "$f" ] || continue
		pid=$(cat "$f" 2>/dev/null)
		case "$pid" in ''|*[!0-9]*) pid="" ;; esac
		[ -n "$pid" ] || continue
		kill -0 "$pid" 2>/dev/null || continue
		cmd=$(ps -p "$pid" -o command= 2>/dev/null || echo "")
		if [ "$f" = "$SOREN91_MAIN_PID_FILE" ]; then
			echo "$cmd" | grep -q "main\.mjs" || continue
		else
			echo "$cmd" | grep -q "run_player_loop\.sh" || continue
		fi
		printf '%s' "$pid"
		return 0
	done

	# PIDファイルは停止処理の途中で消えることがある。実プロセスが残っていると
	# "Not running" と誤判定して stop file を出せないため、runnerをプロセス表から復旧する。
	pid=$(_soren91_scan_alive_runner_pids | head -n 1)
	if [ -n "$pid" ]; then
		printf '%s' "$pid"
		return 0
	fi
	return 1
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

_meriken_time_slot_end_epoch() {
	python3 - "$MERIKEN_TIME_END_HOUR" <<'PY' 2>/dev/null
import sys
from datetime import datetime

try:
    end_hour = int(sys.argv[1])
except Exception:
    raise SystemExit(1)

now = datetime.now().astimezone()
end_dt = now.replace(hour=end_hour, minute=0, second=0, microsecond=0)
if now >= end_dt:
    print(0)
else:
    print(int(end_dt.timestamp()))
PY
}

_write_meriken_time_state() {
	local end_epoch="$1" reason="${2:-scheduled}"
	mkdir -p "$(dirname "$MERIKEN_TIME_STATE_FILE")" 2>/dev/null || true
	python3 - "$MERIKEN_TIME_STATE_FILE" "$end_epoch" "$reason" <<'PY' >/dev/null 2>&1
import json
import sys
from datetime import datetime, timezone

out_file, end_epoch_raw, reason = sys.argv[1:4]
try:
    end_epoch = int(end_epoch_raw)
except Exception:
    raise SystemExit(1)
payload = {
    "active": True,
    "reason": reason,
    "end_epoch": end_epoch,
    "updated_at": datetime.now(timezone.utc).isoformat(),
}
with open(out_file, "w", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
PY
}

_clear_meriken_time_state() {
	rm -f "$MERIKEN_TIME_STATE_FILE" 2>/dev/null || true
}

_meriken_time_state_end_epoch() {
	[ -f "$MERIKEN_TIME_STATE_FILE" ] || return 1
	python3 - "$MERIKEN_TIME_STATE_FILE" <<'PY' 2>/dev/null
import json
import sys

try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    print(int(data.get("end_epoch", 0) or 0))
except Exception:
    raise SystemExit(1)
PY
}

scheduled_meriken_time_is_active() {
	if ! _scheduled_meriken_time_enabled; then
		_clear_meriken_time_state
		return 1
	fi
	local end_epoch=0
	end_epoch=$(_meriken_time_state_end_epoch 2>/dev/null || echo 0)
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	if [ "${end_epoch:-0}" -le 0 ]; then
		_clear_meriken_time_state
		return 1
	fi
	if [ "$(date +%s)" -lt "$end_epoch" ]; then
		return 0
	fi
	_clear_meriken_time_state
	return 1
}

scheduled_meriken_time_begin() {
	_scheduled_meriken_time_enabled || return 1
	local reason="${1:-scheduled}"
	local end_epoch=0
	end_epoch=$(_meriken_time_slot_end_epoch 2>/dev/null || echo 0)
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	if [ "${end_epoch:-0}" -le "$(date +%s)" ]; then
		return 1
	fi
	_write_meriken_time_state "$end_epoch" "$reason" || return 1
	printf '%s' "$end_epoch"
}

scheduled_meriken_time_end_label() {
	local end_epoch="${1:-0}"
	case "$end_epoch" in
	''|*[!0-9]*) end_epoch=0 ;;
	esac
	[ "$end_epoch" -gt 0 ] || return 1
	python3 - "$end_epoch" <<'PY' 2>/dev/null
import sys
from datetime import datetime

try:
    end_epoch = int(sys.argv[1])
except Exception:
    raise SystemExit(1)
print(datetime.fromtimestamp(end_epoch).astimezone().strftime('%H:%M %Z'))
PY
}

soren91_is_running() {
	_soren91_enabled || return 1
	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)
	[ -n "$pid" ] || return 1
	# pid file は soren91 起動用の bash subshell を指すことがあり、
	# 実行環境によっては ps でそのコマンドラインを安定取得できない。
	# start/stop で専用 PID ファイルを管理しているため、生存中なら稼働中とみなす。
	return 0
}

_soren91_stop_in_progress() {
	[ -f "$SOREN91_STOPPING_FILE" ] || return 1

	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)

	if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
		return 0
	fi
	if [ -f "$SOREN91_STOP_FILE" ] || [ -f "$SOREN91_DIR/tmp/in_game" ]; then
		return 0
	fi

	rm -f "$SOREN91_STOPPING_FILE" 2>/dev/null || true
	return 1
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

_soren91_text_is_meta_failure() {
	local text
	text=$(printf '%s' "$1" | tr '\r\n' '  ' | sed 's/[[:space:]]\+/ /g')
	[ -n "$text" ] || return 0
	printf '%s' "$text" | grep -Eiq '申し訳(ありません|ございません|ない).*(エラーメッセージ|提供|ユーザーメッセージ|指示|タスク|情報|スクリーンショット)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラーメッセージ|ユーザーメッセージ|具体的な指示|明確な指示|具体的なタスク).*(提供されてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(入力|依頼|プロンプト|コンテキスト|戦略ヘッダー|本文).*(提供されてい|与えられてい|見当たりません|ありません|ない|不足)' && return 0
	printf '%s' "$text" | grep -Eiq '(エラー|エラーメッセージ).*(詳細|内容|原因|情報).*(提供されてい|見当たりません|ありません|ない|不足|不明)' && return 0
	printf '%s' "$text" | grep -Eiq '(ツール|権限|許可|WebFetch|検索|外部アクセス).*(確認|必要|できません|ありません|ない)' && return 0
	printf '%s' "$text" | grep -Eiq '(何も言えません|語ることはできません|控えておくべき|確認させてください|どうすればよい|何を.*すれば)' && return 0
	printf '%s' "$text" | grep -Eiq '(テキスト|文章|説明|解説).*(生成|作成).*(失敗|できません|できない|無理)' && return 0
	printf '%s' "$text" | grep -Eiq '(戦略|strategy).*(説明|解説).*(できません|できない|無理)' && return 0
	printf '%s' "$text" | grep -Eiq '(日本語|話し言葉).*(直す|言い換え|変換).*(できません|できない|無理|失敗)' && return 0
	return 1
}

_soren91_fallback_strategy_explanation() {
	local strategy_header="${1:-}"
	local details=""
	if printf '%s' "$strategy_header" | grep -Eiq '高さ|height|deadline|デッドライン|高積み'; then
		details="${details}高く積み上がる前に置き場所を絞り、"
	fi
	if printf '%s' "$strategy_header" | grep -Eiq '先読み|look.?ahead|next|次'; then
		details="${details}次のピースまで見て、"
	fi
	if printf '%s' "$strategy_header" | grep -Eiq 'merge|併合|chain|pipeline|パイプライン'; then
		details="${details}併合の流れを残しながら、"
	fi
	if [ -z "$details" ]; then
		details="置き場所を慎重に選び、"
	fi
	printf '%s\n' "今のメリケンAIは、${details}盤面を薄く保つ方針です。派手な勝負より生存率を買う、いかにも資本主義らしい臆病な投資判断ですね。まあ、その臆病さで最後まで残れば勝ちです。"
}

_soren91_provider_error_preview() {
	printf '%s' "$1" | tr '\r\n' '  ' | sed 's/[[:space:]]\+/ /g' | cut -c1-160
}

_soren91_text_generation_debug_file() {
	local tag="$1"
	local debug_dir="$ELOOP_LIB_DIR/tmp/debug/soren91_strategy_explanation"
	local safe_tag
	safe_tag=$(printf '%s' "$tag" | tr -c '[:alnum:]_-' '_')
	mkdir -p "$debug_dir" 2>/dev/null || return 0
	printf '%s/%s_%s_error.txt' "$debug_dir" "$(date +%Y%m%d_%H%M%S)" "$safe_tag"
}

_soren91_generate_text_with_shared_fallback() {
	local tag="$1" prompt="$2" fallback_mode="${3:-${SOREN91_TEXT_FALLBACKS:-claude}}"
	local prompt_file="" err_file="" debug_file="" result="" err_preview=""

	command -v node >/dev/null 2>&1 || return 1
	prompt_file=$(mktemp /tmp/soren91_ai_prompt.XXXXXX) || return 1
	err_file=$(mktemp /tmp/soren91_ai_err.XXXXXX) || {
		rm -f "$prompt_file"
		return 1
	}
	printf '%s' "$prompt" >"$prompt_file"
	debug_file=$(_soren91_text_generation_debug_file "$tag")

	if result=$(SOREN91_TEXT_CLAUDE_TIMEOUT="${SOREN91_TEXT_CLAUDE_TIMEOUT:-60}" node "$SOREN91_DIR/text_ai.mjs" --tag "$tag" --prompt-file "$prompt_file" --fallbacks "$fallback_mode" 2>"$err_file"); then
		rm -f "$prompt_file" "$err_file"
		rm -f "$debug_file" 2>/dev/null || true
		printf '%s' "$result"
		return 0
	fi

	err_preview=$(_soren91_provider_error_preview "$(cat "$err_file" 2>/dev/null || true)")
	if [ -n "$debug_file" ]; then
		{
			printf 'tag=%s\n' "$tag"
			printf 'fallbacks=%s\n' "$fallback_mode"
			printf 'time=%s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
			cat "$err_file" 2>/dev/null || true
		} >"$debug_file" 2>/dev/null || true
	fi
	rm -f "$prompt_file" "$err_file"
	[ -n "$err_preview" ] && log "[SOREN91] ${tag}: text generation failed (${err_preview})" >&2
	return 1
}

_soren91_dump_strategy_explanation_debug() {
	local phase="$1" text="$2"
	local debug_dir="$ELOOP_LIB_DIR/tmp/debug/soren91_strategy_explanation"
	local debug_file
	mkdir -p "$debug_dir" 2>/dev/null || return 0
	debug_file="$debug_dir/$(date +%Y%m%d_%H%M%S)_${phase}.txt"
	{
		printf 'phase=%s\n' "$phase"
		printf 'time=%s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
		printf '%s\n' "$text"
	} >"$debug_file" 2>/dev/null || true
}

_soren91_start_capitalism_corner() {
	[ "${SOREN91_CAPITALISM_CORNER_ENABLED:-1}" = "1" ] || return 0
	command -v start_radio_corner_capitalism >/dev/null 2>&1 || return 0

	local radio_game_num="${GAME_NUM:-}"
	case "$radio_game_num" in
	''|*[!0-9]*) radio_game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0) ;;
	esac
	case "$radio_game_num" in
	''|*[!0-9]*) radio_game_num=0 ;;
	esac

	local radio_score=""
	if command -v _last_score >/dev/null 2>&1; then
		radio_score=$(_last_score 2>/dev/null || true)
	fi
	case "$radio_score" in
	''|*[!0-9]*) radio_score=0 ;;
	esac

	log "[SOREN91] 資本主義ネタコーナー開始 (game=${radio_game_num}, score=${radio_score})"
	start_radio_corner_capitalism "$radio_game_num" "$radio_score"
}

_soren91_generate_strategy_explanation() {
	local strategy_header="$1"
	[ -n "$strategy_header" ] || return 1
	local prompt_file="$SOREN91_DIR/prompts/explain_strategy.md"
	if [ ! -f "$prompt_file" ]; then
		log "[SOREN91] Warning: prompt file not found: $prompt_file"
		return 1
	fi
	local prompt_text
	prompt_text=$(cat "$prompt_file")
	prompt_text="${prompt_text//\{\{STRATEGY_HEADER\}\}/$strategy_header}"
	_soren91_generate_text_with_shared_fallback "strategy_explanation" "$prompt_text" "${SOREN91_TEXT_FALLBACKS:-claude}"
}

_soren91_rewrite_strategy_explanation_to_japanese() {
	local raw_text="$1"
	[ -n "$raw_text" ] || return 1
	local prompt_file="$SOREN91_DIR/prompts/explain_strategy_japanese.md"
	if [ ! -f "$prompt_file" ]; then
		log "[SOREN91] Warning: prompt file not found: $prompt_file"
		return 1
	fi
	local prompt_text
	prompt_text=$(cat "$prompt_file")
	prompt_text="${prompt_text//\{\{STRATEGY_EXPLANATION\}\}/$raw_text}"
	_soren91_generate_text_with_shared_fallback "strategy_explanation_rewrite" "$prompt_text" "${SOREN91_TEXT_FALLBACKS:-claude}"
}

soren91_start() {
	_soren91_enabled || return 0
	if soren91_is_running; then
		log "[SOREN91] Already running, skip start"
		return 0
	fi
	if _soren91_stop_in_progress; then
		log "[SOREN91] Stop in progress, skip start"
		return 0
	fi

	log "[SOREN91] Starting soren91 (メリケンAI)..."
	rm -f "$SOREN91_STOP_FILE" "$SOREN91_MAIN_PID_FILE" "$TMP_STATE_DIR/.soren91_bye_sent"
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

	# メリケンAIモード判定 (手動発火 or 定時メリケン枠の継続中)
	# メリケンモードでは内部改善を有効化 (12ゲームごと、env override可)
	local _meriken_mode=0
	if manual_meriken_mode_is_enabled; then
		_meriken_mode=1
	elif scheduled_meriken_time_is_active; then
		_meriken_mode=1
	elif _scheduled_meriken_time_enabled && [ "$(date +%H)" = "$MERIKEN_TIME_START_HOUR" ]; then
		# 定時メリケン枠の開始時刻に起動されたセッションはメリケンモード扱いにする。
		# 継続判定は state file の end_epoch を優先する。
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
					if strategy_explain=$(_soren91_generate_strategy_explanation "$strategy_header"); then
						_soren91_dump_strategy_explanation_debug "raw" "$strategy_explain"
					else
						_soren91_dump_strategy_explanation_debug "raw_failed" "$strategy_explain"
						log "[SOREN91] 戦略解説の生成に失敗したため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説がメタ失敗文のため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						local rewritten_strategy_explain=""
						log "[SOREN91] 戦略解説が英語寄りのため日本語へ再生成"
						if rewritten_strategy_explain=$(_soren91_rewrite_strategy_explanation_to_japanese "$strategy_explain"); then
							strategy_explain="$rewritten_strategy_explain"
						else
							log "[SOREN91] 戦略解説の日本語化生成に失敗したため読み上げをスキップ"
							strategy_explain=""
						fi
					fi
					if [ -n "$strategy_explain" ] && _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説の日本語化結果がメタ失敗文のため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ] && ! _soren91_text_has_japanese "$strategy_explain"; then
						log "[SOREN91] 戦略解説の日本語化に失敗したため読み上げをスキップ"
						strategy_explain=""
					fi
					if _soren91_text_is_meta_failure "$strategy_explain"; then
						log "[SOREN91] 戦略解説の最終ガードでメタ失敗文を検出したため読み上げをスキップ"
						strategy_explain=""
					fi
					if [ -n "$strategy_explain" ]; then
						_soren91_dump_strategy_explanation_debug "final" "$strategy_explain"
						local explain_file
						explain_file=$(mktemp /tmp/eloop_soren91_strategy.XXXXXX)
						printf '%s\n' "$strategy_explain" > "$explain_file"
					SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:strategy" ./say_enqueue.sh "$explain_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
					rm -f "$explain_file"
					log "[SOREN91] 戦略解説を読み上げ"
				fi
			fi
		} &
		_soren91_start_capitalism_corner >/dev/null 2>&1 &
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
	touch "$SOREN91_STOPPING_FILE"

	local pid=""
	pid=$(_soren91_read_alive_player_pid 2>/dev/null || true)

	if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
		# プロセスが既に終了 → end_game だけ記録して終了
		log "[SOREN91] Not running, recording end_game"
		local eg
		eg=$(_soren91_record_end_game)
		_clear_meriken_time_state
		_clear_soren91_mode_flag
		rm -f "$SOREN91_PID_FILE" "$SOREN91_MAIN_PID_FILE" "$SOREN91_STOP_FILE" "$SOREN91_STOPPING_FILE" "$SOREN91_DIR/tmp/in_game"
		rm -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
		_soren91_switch_obs_layout china || true
		log "[SOREN91] Unmuted local game BGM (flag file removed)"
		log "[SOREN91] Stopped (already exited, end_game=$eg)"
		return 0
	fi

	log "[SOREN91] Stopping soren91 (PID=$pid)..."

	# graceful stop: stop ファイルを作成して現在のゲーム終了を待つ
	touch "$SOREN91_STOP_FILE"

	local in_game_file="$SOREN91_DIR/tmp/in_game"

	# Phase 1: 試合中なら試合終了を待つ。長すぎる居残りを避けるため、
	# 固定600秒ではなく SOREN91_STOP_TIMEOUT に従う。
	local game_waited=0
	local max_game_wait="${SOREN91_STOP_TIMEOUT:-300}"
	case "$max_game_wait" in
	''|*[!0-9]*) max_game_wait=300 ;;
	esac
	while [ -f "$in_game_file" ] && kill -0 "$pid" 2>/dev/null && [ "$game_waited" -lt "$max_game_wait" ]; do
		log "[SOREN91] Game in progress, waiting for round to end... (${game_waited}s/${max_game_wait}s)"
		sleep 5
		game_waited=$((game_waited + 5))
	done

	# Phase 2: 試合終了後、graceful exit を短時間待つ
	local waited=0
	local post_game_timeout=30
	while [ "$waited" -lt "$post_game_timeout" ]; do
		if ! kill -0 "$pid" 2>/dev/null; then
			log "[SOREN91] Stopped gracefully after game ended"
			break
		fi
		sleep 2
		waited=$((waited + 2))
	done

	# Phase 3: それでも生きていたら強制停止 (従来通り)
	if kill -0 "$pid" 2>/dev/null; then
		log "[SOREN91] Post-game timeout, force stopping..."
		_stop_loop_descendants "$pid"
		_stop_pid_with_fallback "$pid" "soren91"
	fi

	# Phase 4: run_player_loop.sh (runner) の確実な終了を待つ
	local runner_pid=""
	[ -f "$SOREN91_PID_FILE" ] && runner_pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
	case "$runner_pid" in ''|*[!0-9]*) runner_pid="" ;; esac
	if [ -n "$runner_pid" ] && kill -0 "$runner_pid" 2>/dev/null; then
		local runner_waited=0
		while kill -0 "$runner_pid" 2>/dev/null && [ "$runner_waited" -lt 10 ]; do
			sleep 1
			runner_waited=$((runner_waited + 1))
		done
		if kill -0 "$runner_pid" 2>/dev/null; then
			log "[SOREN91] Killing stray runner process (PID=$runner_pid)"
			kill "$runner_pid" 2>/dev/null || true
		fi
	fi

	local eg
	eg=$(_soren91_record_end_game)

	rm -f "$SOREN91_PID_FILE" "$SOREN91_MAIN_PID_FILE" "$SOREN91_STOP_FILE" "$SOREN91_STOPPING_FILE" "$SOREN91_DIR/tmp/in_game"
	_clear_meriken_time_state
	_clear_soren91_mode_flag
	# 中華AI側のBGMをアンミュート（改善終了・復帰）
	rm -f "$ELOOP_LIB_DIR/tmp/mute_local_bgm"
	_soren91_switch_obs_layout china || true
	log "[SOREN91] Unmuted local game BGM (flag file removed)"

	# メリケンAI終了あいさつ (TTS + Twitch) — 重複防止
	local _bye_guard="$TMP_STATE_DIR/.soren91_bye_sent"
	if [ ! -f "$_bye_guard" ]; then
		touch "$_bye_guard"
		{
			local _bye_file
			_bye_file=$(mktemp /tmp/eloop_soren91_bye.XXXXXX)
			printf '%s\n' "対戦ありがとうございました。メリケンAIはここで退場しますね、またね！" > "$_bye_file"
			SAY_VOICEVOX_SPEAKER_OVERRIDE="$SOREN91_VOICEVOX_SPEAKER" SAY_CONTEXT_LABEL="soren91:bye" ./say_enqueue.sh "$_bye_file" "$RADIO_SAY_RATE" 0 2>/dev/null || true
			rm -f "$_bye_file"
		} &
		enqueue_chat_message "対戦ありがとうございました。メリケンAIはここで退場しますね、またね！" "soren91"
	fi

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
	local player_pids="" pid=""
	if [ -f "$SOREN91_PID_FILE" ]; then
		pid=$(cat "$SOREN91_PID_FILE" 2>/dev/null)
		case "$pid" in
		''|*[!0-9]*) ;;
		*) player_pids="$player_pids $pid" ;;
		esac
	fi
	player_pids="$player_pids $(_soren91_scan_alive_runner_pids 2>/dev/null | tr '\n' ' ')"
	for pid in $player_pids; do
		case "$pid" in
		''|*[!0-9]*) continue ;;
		esac
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
	done

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
		"$SOREN91_MAIN_PID_FILE" \
		"$SOREN91_DIR/tmp/in_game" \
		"$ELOOP_LIB_DIR/tmp/mute_local_bgm"
	_clear_meriken_time_state
	_soren91_switch_obs_layout china || true
}

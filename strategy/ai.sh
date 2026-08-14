# strategy/ai.sh - spinner, build_prompt, run_cmd, run_ai

start_spinner() {
	local label="$1"
	if [ "${RUN_CMD_SPINNER_FORCE:-0}" != "1" ] && [ ! -t 2 ]; then
		_spinner_pid=0
		return 0
	fi
	(
		local frames=('⣾' '⣽' '⣻' '⢿' '⡿' '⣟' '⣯' '⣷')
		local i=0 start=$SECONDS
		while true; do
			local e=$((SECONDS - start))
			local m=$((e / 60)) s=$((e % 60))
			printf '\r  \033[1;35m%s\033[0m \033[1m%s\033[0m \033[2m%d:%02d\033[0m  ' \
				"${frames[i % ${#frames[@]}]}" "$label" "$m" "$s" >&2
			sleep 0.12
			((i++))
		done
	) &
	_spinner_pid=$!
}
stop_spinner() {
	if [ "${_spinner_pid:-0}" -ne 0 ]; then
		kill "$_spinner_pid" 2>/dev/null
		wait "$_spinner_pid" 2>/dev/null
		printf '\r\033[K' >&2
		_spinner_pid=0
	fi
}

#=== プロンプト構築 ===

build_prompt() {
	local pf="$1"
	shift
	local p
	p=$(cat "$pf" 2>/dev/null) || return 1
	local c=""
	for f in "$@"; do
		[ -f "$f" ] && c+=$'\n--- '"$f"$' ---\n'"$(cat "$f")"$'\n---\n'
	done
	if [ -n "$c" ]; then
		echo "${p}"$'\n\n'"参照データ:${c}"
	else
		echo "$p"
	fi
}

_run_cmd_timeout_bin() {
	if command -v timeout >/dev/null 2>&1; then
		command -v timeout
		return 0
	fi
	if command -v gtimeout >/dev/null 2>&1; then
		command -v gtimeout
		return 0
	fi
	return 1
}

_run_cmd_start_heartbeat() {
	local cmd_pid="$1" cmd_log_file="$2" cmd_log_tag="$3"
	local interval="${RUN_CMD_HEARTBEAT_INTERVAL_SEC:-30}"
	case "$cmd_pid" in
	''|*[!0-9]*) return 1 ;;
	esac
	case "$interval" in
	''|*[!0-9]*) interval=30 ;;
	esac
	[ "$interval" -gt 0 ] || return 1
	(
		local hb_start
		hb_start=$(date +%s)
		while kill -0 "$cmd_pid" 2>/dev/null; do
			sleep "$interval"
			kill -0 "$cmd_pid" 2>/dev/null || break
			local hb_elapsed
			hb_elapsed=$(( $(date +%s) - hb_start ))
			if [ -n "$cmd_log_file" ]; then
				printf '[%s] [AI:%s] HEARTBEAT waiting pid=%s elapsed=%ss\n' \
					"$(date '+%H:%M:%S')" "$cmd_log_tag" "$cmd_pid" "$hb_elapsed" \
					>>"$cmd_log_file" 2>/dev/null || true
			fi
			if [ "${RUN_CMD_TOUCH_IMPROVE_STATE:-0}" = "1" ] && command -v _write_improve_state >/dev/null 2>&1; then
				_write_improve_state "running" \
					"${RUN_CMD_IMPROVE_PID:-0}" \
					"${RUN_CMD_IMPROVE_HASH_BEFORE:-}" \
					"${RUN_CMD_IMPROVE_PHASE:-}" \
					"${RUN_CMD_IMPROVE_PROGRESS:-0}" \
					"${RUN_CMD_IMPROVE_DETAIL:-run_cmd_wait}" \
					"${RUN_CMD_IMPROVE_STARTED_AT:-0}" \
					"${RUN_CMD_IMPROVE_PID_BIRTH_EPOCH:-0}" \
					"${RUN_CMD_IMPROVE_REASON:-}"
			fi
		done
	) &
	RUN_CMD_HEARTBEAT_PID=$!
	return 0
}

_run_cmd_stop_heartbeat() {
	if [ "${RUN_CMD_HEARTBEAT_PID:-0}" -ne 0 ]; then
		kill "$RUN_CMD_HEARTBEAT_PID" 2>/dev/null || true
		wait "$RUN_CMD_HEARTBEAT_PID" 2>/dev/null || true
		RUN_CMD_HEARTBEAT_PID=0
	fi
}

_prepare_minimax_claude_command() {
	local prompt_body="$1"
	local model="${2:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local permission_mode="${3:-acceptEdits}"
	MINIMAX_CLAUDE_ENV=(
		ANTHROPIC_BASE_URL="${MINIMAX_BASE_URL:-https://api.minimax.io/anthropic}"
		ANTHROPIC_AUTH_TOKEN="${MINIMAX_API_KEY:-}"
		ANTHROPIC_API_KEY=""
	)
	MINIMAX_CLAUDE_CMD=(claude --print -p "$prompt_body" --model="$model" --permission-mode="$permission_mode" --no-session-persistence)
}

_run_minimax_claude_prompt_file() {
	local prompt_file="$1"
	local output_file="$2"
	local model="${3:-${MINIMAX_MODEL:-MiniMax-M2.7}}"
	local timeout_sec="${4:-}"
	local permission_mode="${5:-acceptEdits}"
	local prompt_body="" timeout_bin="" stderr_file="" output="" rc=1
	MINIMAX_CLAUDE_LAST_RC=1
	MINIMAX_CLAUDE_LAST_STDERR=""
	MINIMAX_CLAUDE_LAST_STDOUT_PREVIEW=""
	MINIMAX_CLAUDE_LAST_PROVIDER_ERROR=false
	MINIMAX_CLAUDE_LAST_LOGIN_ERROR=false
	[ -n "$output_file" ] && : >"$output_file"
	[ -s "$prompt_file" ] || return 1
	prompt_body=$(cat "$prompt_file" 2>/dev/null) || return 1
	_prepare_minimax_claude_command "$prompt_body" "$model" "$permission_mode"
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec="" ;;
	esac
	if [ -n "$timeout_sec" ] && [ "$timeout_sec" -gt 0 ]; then
		timeout_bin=$(_run_cmd_timeout_bin 2>/dev/null || true)
	fi
	stderr_file=$(mktemp /tmp/eloop_minimax_stderr_XXXXXXXX)
	if [ -n "$timeout_sec" ] && [ -n "$timeout_bin" ]; then
		output=$(env "${MINIMAX_CLAUDE_ENV[@]}" "$timeout_bin" "$timeout_sec" "${MINIMAX_CLAUDE_CMD[@]}" 2>"$stderr_file")
		rc=$?
	else
		output=$(env "${MINIMAX_CLAUDE_ENV[@]}" "${MINIMAX_CLAUDE_CMD[@]}" 2>"$stderr_file")
		rc=$?
	fi
	if [ -s "$stderr_file" ]; then
		MINIMAX_CLAUDE_LAST_STDERR=$(head -c 4000 "$stderr_file")
	fi
	rm -f "$stderr_file"
	if _contains_provider_error_text "$output" || { [ -n "$MINIMAX_CLAUDE_LAST_STDERR" ] && _contains_provider_error_text "$MINIMAX_CLAUDE_LAST_STDERR"; }; then
		MINIMAX_CLAUDE_LAST_PROVIDER_ERROR=true
		MINIMAX_CLAUDE_LAST_STDOUT_PREVIEW=$(printf '%s' "$output" | head -c 4000)
	fi
	if _contains_claude_login_error_text "$output" || { [ -n "$MINIMAX_CLAUDE_LAST_STDERR" ] && _contains_claude_login_error_text "$MINIMAX_CLAUDE_LAST_STDERR"; }; then
		MINIMAX_CLAUDE_LAST_LOGIN_ERROR=true
	fi
	MINIMAX_CLAUDE_LAST_RC=$rc
	if [ $rc -ne 0 ] || [ "$MINIMAX_CLAUDE_LAST_PROVIDER_ERROR" = "true" ]; then
		return 1
	fi
	printf '%s' "$output" >"$output_file"
	return 0
}

#=== コマンド実行 ===

_opencode_latest_session_id_for_dir() {
	local target_dir="$1"
	local opencode_db="${OPENCODE_SESSION_DB:-}"
	if [ -z "$opencode_db" ]; then
		opencode_db="$(_opencode_xdg_data_home 2>/dev/null)/opencode/opencode.db"
	fi
	[ -n "$target_dir" ] || return 1
	[ -f "$opencode_db" ] || return 1
	python3 - "$opencode_db" "$target_dir" <<'PY' 2>/dev/null
import os
import sqlite3
import sys

db_path = sys.argv[1]
target_dir = os.path.realpath(sys.argv[2])

try:
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "SELECT id FROM session WHERE directory = ? ORDER BY time_updated DESC LIMIT 1",
        (target_dir,),
    )
    row = cur.fetchone()
    if row and row[0]:
        print(row[0])
except Exception:
    pass
PY
}

_run_cmd_session_meta_file() {
	local session_dir="$1" spec="$2"
	[ -n "$session_dir" ] || return 1
	[ -n "$spec" ] || return 1
	local key
	key=$(printf '%s' "$spec" | tr -cs 'A-Za-z0-9._-' '_')
	printf '%s/%s.session\n' "$session_dir" "$key"
}

_run_cmd_load_resume_session() {
	local spec="$1"
	local session_dir="${RUN_CMD_SESSION_DIR:-}"
	[ -n "$session_dir" ] || return 1
	local meta_file
	meta_file=$(_run_cmd_session_meta_file "$session_dir" "$spec") || return 1
	[ -f "$meta_file" ] || return 1
	sed -n '1p' "$meta_file" 2>/dev/null | tr -d '[:space:]'
}

_run_cmd_store_resume_session() {
	local spec="$1" workdir="${2:-$PWD}"
	local session_dir="${RUN_CMD_SESSION_DIR:-}"
	[ -n "$session_dir" ] || return 0
	mkdir -p "$session_dir" 2>/dev/null || return 0
	local session_id meta_file
	session_id=$(_opencode_latest_session_id_for_dir "$workdir")
	[ -n "$session_id" ] || return 0
	meta_file=$(_run_cmd_session_meta_file "$session_dir" "$spec") || return 0
	printf '%s\n' "$session_id" >"$meta_file" 2>/dev/null || true
}

run_cmd() {
	local spec="$1" prompt="$2"
	local type="${spec%%:*}" agent="${spec#*:}"
	[ "$type" = "$agent" ] && agent=""
	local target="$type"
	[ -n "$agent" ] && target="${type}:${agent}"
	local cmd_log_file="${RUN_CMD_LOG_FILE:-}"
	local cmd_log_tag="${RUN_CMD_LOG_TAG:-$type}"
	local prompt_body="$prompt"
	local resume_session=""
	local RUN_CMD_CODEX_OUT_FILE=""
	local timeout_sec="${RUN_CMD_TIMEOUT_SEC:-}"
	local timeout_bin=""
	local timeout_label="none"
	case "$timeout_sec" in
	'' | *[!0-9]*) timeout_sec="" ;;
	esac
	if [ -n "$timeout_sec" ] && [ "$timeout_sec" -gt 0 ]; then
		timeout_bin=$(_run_cmd_timeout_bin 2>/dev/null || true)
		if [ -z "$timeout_bin" ]; then
			log "[CMD] timeout requested (${timeout_sec}s) but no timeout binary found"
			timeout_sec=""
		fi
	fi
	[ -n "$timeout_sec" ] && timeout_label="${timeout_sec}s"
	# litellm プロキシ経由運用時は、codex 起動前にプロキシ生存を確認する。
	# 死亡時に無駄な codex 起動を避け、run_ai のフォールバック判定 (rc=79) へ乗せる。
	local litellm_health_url="${LITELLM_HEALTH_URL:-http://127.0.0.1:4100/health}"
	if [ -n "$litellm_health_url" ]; then
		if ! curl -fsS --max-time 5 "$litellm_health_url" >/dev/null 2>&1; then
			log "[CMD] litellm proxy unreachable ($litellm_health_url) → rc=79 fallback"
			if [ -n "$cmd_log_file" ]; then
				printf '[%s] [AI:%s] LITELLM_DOWN url=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$litellm_health_url" >>"$cmd_log_file" 2>/dev/null || true
			fi
			return 79
		fi
	fi
	# ハーネスは codex CLI に統一 (モデル: deepseek-v4-flash)。
	# 旧 zai/minimax/glm/opencode/gemini/claude/ollama 指定はすべて codex へ正規化する。
	local original_type="$type"
	if [ "$type" != "codex" ]; then
		log "[CMD] ${type} → codex (ハーネス統一)"
		type="codex"
		agent=""
		target="codex"
	fi

	local prompt_file
	if [ -n "${RUN_CMD_TMP_DIR:-}" ]; then
		mkdir -p "$RUN_CMD_TMP_DIR" 2>/dev/null || true
		prompt_file=$(mktemp "$RUN_CMD_TMP_DIR/eloop_prompt.XXXXXX" 2>/dev/null)
	else
		prompt_file=$(mktemp /tmp/eloop_prompt.XXXXXX)
	fi
	printf '%s' "$prompt" >"$prompt_file"
	log "[CMD] $(wc -c <"$prompt_file" | tr -d ' ')B → $type"
	if [ -n "$cmd_log_file" ]; then
		mkdir -p "$(dirname "$cmd_log_file")" 2>/dev/null || true
		_trim_log_file "$cmd_log_file" "$IMPROVE_AI_LOG_KEEP_LINES" "$IMPROVE_AI_LOG_TRIM_LINES"
		if [ -n "$resume_session" ]; then
			printf '[%s] [AI:%s] START spec=%s target=%s timeout=%s continue_session=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$spec" "$target" "$timeout_label" "$resume_session" >>"$cmd_log_file" 2>/dev/null || true
		else
			printf '[%s] [AI:%s] START spec=%s target=%s timeout=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$spec" "$target" "$timeout_label" >>"$cmd_log_file" 2>/dev/null || true
		fi
	fi

	if [ "$type" = "opencode" ] && [ -z "$agent" ]; then
		log "[CMD] opencode spec requires explicit agent (e.g. opencode:glmflash)"
		rm -f "$prompt_file"
		return 2
	fi

	local opencode_lock_token=""
	local opencode_prev_xdg_state_home="${XDG_STATE_HOME-}"
	local opencode_prev_xdg_data_home="${XDG_DATA_HOME-}"
	local opencode_had_xdg_state_home=0
	local opencode_had_xdg_data_home=0
	[ "${XDG_STATE_HOME+x}" = "x" ] && opencode_had_xdg_state_home=1
	[ "${XDG_DATA_HOME+x}" = "x" ] && opencode_had_xdg_data_home=1
	if [ "$type" = "glm" ] || [ "$type" = "opencode" ]; then
		_opencode_run_lock_enter "$cmd_log_tag:$target" || {
			rm -f "$prompt_file"
			return 1
		}
		opencode_lock_token="$OPENCODE_RUN_LOCK_LAST_TOKEN"
		mkdir -p "$(_opencode_xdg_state_home)/opencode/locks" 2>/dev/null || true
		mkdir -p "$(_opencode_xdg_data_home)/opencode" 2>/dev/null || true
		_opencode_sync_auth_to_xdg
		_opencode_cleanup_internal_locks
		export XDG_STATE_HOME="$(_opencode_xdg_state_home)"
		export XDG_DATA_HOME="$(_opencode_xdg_data_home)"
	fi

	# codex exec で最終メッセージを出力ファイルへ書き、stdout/stderr はログへ。
	local codex_model="${agent:-${CODEX_MODEL:-deepseek-v4-flash}}"
	local codex_out_file
	codex_out_file=$(mktemp /tmp/eloop_codex_out_XXXXXXXX)
	local -a codex_args=(
		exec --skip-git-repo-check -m "$codex_model"
		--dangerously-bypass-approvals-and-sandbox
		-o "$codex_out_file" "$prompt_body"
	)
	if [ -n "$cmd_log_file" ]; then
		if [ -n "$timeout_sec" ]; then
			"$timeout_bin" "$timeout_sec" codex "${codex_args[@]}" >>"$cmd_log_file" 2>&1 &
		else
			codex "${codex_args[@]}" >>"$cmd_log_file" 2>&1 &
		fi
	else
		if [ -n "$timeout_sec" ]; then
			"$timeout_bin" "$timeout_sec" codex "${codex_args[@]}" &
		else
			codex "${codex_args[@]}" &
		fi
	fi
	# 出力ファイルを後処理でログへ追記するため、一時ファイルパスを保存
	RUN_CMD_CODEX_OUT_FILE="$codex_out_file"
	local cmd_pid=$!
	if [ "$type" = "glm" ] || [ "$type" = "opencode" ]; then
		if [ "$opencode_had_xdg_state_home" -eq 1 ]; then
			export XDG_STATE_HOME="$opencode_prev_xdg_state_home"
		else
			unset XDG_STATE_HOME
		fi
		if [ "$opencode_had_xdg_data_home" -eq 1 ]; then
			export XDG_DATA_HOME="$opencode_prev_xdg_data_home"
		else
			unset XDG_DATA_HOME
		fi
	fi
	RUN_CMD_ACTIVE_PID=$cmd_pid
	local _cmd_start_epoch
	_cmd_start_epoch=$(date +%s)

	start_spinner "$type thinking..."
	_run_cmd_start_heartbeat "$cmd_pid" "$cmd_log_file" "$cmd_log_tag" >/dev/null 2>&1 || true

	local prev_int_trap interrupted
	prev_int_trap=$(trap -p INT || true)
	interrupted=0
	trap 'interrupted=1; _run_cmd_stop_heartbeat; stop_spinner; _stop_loop_descendants "$cmd_pid"; kill "$cmd_pid" 2>/dev/null; wait "$cmd_pid" 2>/dev/null; _opencode_run_lock_leave "$opencode_lock_token" "$cmd_log_tag"; opencode_lock_token=""; RUN_CMD_ACTIVE_PID=0; log "Interrupted"' INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?
	# codex exec の最終メッセージをログへ追記（後段の判定抽出が読めるように）
	if [ -n "${RUN_CMD_CODEX_OUT_FILE:-}" ] && [ -s "$RUN_CMD_CODEX_OUT_FILE" ]; then
		if [ -n "$cmd_log_file" ]; then
			printf '\n[%s] [AI:%s] FINAL_MESSAGE\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" >>"$cmd_log_file" 2>/dev/null || true
			cat "$RUN_CMD_CODEX_OUT_FILE" >>"$cmd_log_file" 2>/dev/null || true
		fi
		rm -f "$RUN_CMD_CODEX_OUT_FILE"
	fi
	RUN_CMD_CODEX_OUT_FILE=""
	_run_cmd_stop_heartbeat
	_opencode_run_lock_leave "$opencode_lock_token" "$cmd_log_tag"
	opencode_lock_token=""
	RUN_CMD_ACTIVE_PID=0
	local _cmd_elapsed=$(( $(date +%s) - _cmd_start_epoch ))
	# デバッグ: wait直後の状態をログに記録 (リトライ未到達問題の調査用)
	if [ -n "$cmd_log_file" ]; then
		printf '[%s] [AI:%s] WAIT_DONE rc=%s elapsed=%ss interrupted=%s\n' \
			"$(date '+%H:%M:%S')" "$cmd_log_tag" "$ret" "$_cmd_elapsed" "$interrupted" \
			>>"$cmd_log_file" 2>/dev/null || true
	fi
	if [ "$interrupted" -eq 1 ]; then
		ret=130
	fi
	if [ "$ret" -eq 124 ] && [ -n "$timeout_sec" ]; then
		log "[CMD] timeout after ${timeout_sec}s → $type"
		if [ -n "$cmd_log_file" ]; then
			printf '[%s] [AI:%s] TIMEOUT after %ss\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$timeout_sec" >>"$cmd_log_file" 2>/dev/null || true
		fi
	fi
	# コンテキスト上限/トークン超過エラー検出 → セッション継続しても無駄なので rc=77 で通知
	if [ -n "$cmd_log_file" ] && tail -20 "$cmd_log_file" 2>/dev/null | grep -qiE "exceeds.*context length|exceeds.*maximum.*token|context window limit|context window exceeds|maximum context length|prompt is too long|too many tokens|invalid params, context window" 2>/dev/null; then
		log "[CMD] コンテキスト上限検出 → セッションクリア"
		ret=77
	fi
	# レートリミット/残高不足エラー検出 → 即フォールバック (rc=79)
	if [ "$ret" -ne 77 ] && [ -n "$cmd_log_file" ] && tail -20 "$cmd_log_file" 2>/dev/null | grep -qiE '"code":"1113"|Insufficient balance|no resource package|HTTP 429|status: 429|rate.?limit' 2>/dev/null; then
		log "[CMD] レートリミット/残高不足検出 → 即フォールバック (rc=79)"
		ret=79
	fi
	# 空応答検出: 極短時間かつログが空 → モデルが実質的な応答を返していない
	# (ファイル変更の有無ではなくログ内容で判定 — バリデーション等の短時間タスクを誤検出しない)
	if [ "$ret" -eq 0 ] && [ "${_cmd_elapsed:-999}" -le 3 ]; then
		_log_has_content=false
		if [ -n "$cmd_log_file" ] && [ -s "$cmd_log_file" ]; then
			_log_bytes=$(wc -c < "$cmd_log_file" 2>/dev/null || echo 0)
			if [ "$_log_bytes" -gt 200 ]; then
				_log_has_content=true
			fi
		fi
		if [ "$_log_has_content" = "false" ]; then
			log "[CMD] 空応答検出 (${_cmd_elapsed}s, $(wc -c < "$cmd_log_file" 2>/dev/null || echo 0)B) → セッションクリア"
			ret=78
		fi
	fi
	if [ "$type" = "glm" ] || [ "$type" = "opencode" ] || [ "$type" = "zai" ]; then
		if [ "$ret" -eq 77 ] || [ "$ret" -eq 78 ]; then
			# トークン超過 or 空応答時はセッションを保存しない（次回は新規セッション）
			local meta_file
			meta_file=$(_run_cmd_session_meta_file "${RUN_CMD_SESSION_DIR:-}" "$spec" 2>/dev/null || true)
			[ -n "$meta_file" ] && rm -f "$meta_file" 2>/dev/null || true
		else
			_run_cmd_store_resume_session "$spec" "$PWD"
		fi
	fi
	if [ -n "$cmd_log_file" ]; then
		printf '[%s] [AI:%s] END rc=%s\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$ret" >>"$cmd_log_file" 2>/dev/null || true
		_trim_log_file "$cmd_log_file" "$IMPROVE_AI_LOG_KEEP_LINES" "$IMPROVE_AI_LOG_TRIM_LINES"
	fi

	stop_spinner
	if [ -n "$prev_int_trap" ]; then
		eval "$prev_int_trap"
	else
		trap - INT
	fi

	rm -f "$prompt_file"

	return $ret
}

#=== AIステップ ===

_build_no_edit_retry_prompt() {
	local original_prompt="$1" expect="$2" attempt="$3" primary_attempts="$4" resume_session="$5"
	local short_followup
	short_followup=$(
		cat <<EOF
前回の応答は \`$expect\` を実際には変更していないため失敗扱いです。
同じタスクを続けて、今回は必ず実ファイル編集まで完了してください。
- いま必要なのは説明ではなく \`$expect\` の実編集
- 再分析・要約・長文説明は禁止
- 必要なら \`$expect\` を1回だけ Read し、その直後に Edit
- patch の根拠は必ず現在の \`$expect\` に合わせること。別ファイルや古い読み取り結果を oldString 根拠にしない
- \`Edit\` が2回連続で失敗したら、\`$expect\` の該当箇所だけを狭く再読込して、より小さい patch に分割して再実行する
- 新規トップレベル .py を作らない
- 終了前に、\`$expect\` に差分が入った状態にすること
- これは no-edit 後の再試行 ${attempt}/${primary_attempts}
EOF
	)
	if [ -n "$resume_session" ]; then
		printf '%s\n' "$short_followup"
	else
		printf '%s\n\n%s\n' "$original_prompt" "$short_followup"
	fi
}

run_ai() {
	local label="$1" primary="$2" fallback="$3" pf="$4" expect="$5"
	shift 5
	local prompt
	prompt=$(build_prompt "$pf" "$@")
	if [ -z "$prompt" ]; then
		log "[$label] prompt missing"
		return 1
	fi

	local expect_snapshot=""
	if [ -n "$expect" ] && [ -f "$expect" ]; then
		if [ -n "${RUN_CMD_TMP_DIR:-}" ]; then
			mkdir -p "$RUN_CMD_TMP_DIR" 2>/dev/null || true
			expect_snapshot=$(mktemp "$RUN_CMD_TMP_DIR/eloop_expect_before.XXXXXX" 2>/dev/null || echo "")
		else
			expect_snapshot=$(mktemp /tmp/eloop_expect_before.XXXXXX 2>/dev/null || echo "")
		fi
		if [ -n "$expect_snapshot" ]; then
			cp "$expect" "$expect_snapshot" 2>/dev/null || {
				rm -f "$expect_snapshot" 2>/dev/null || true
				expect_snapshot=""
			}
		fi
	fi

	local primary_attempts="${RUN_AI_PRIMARY_RETRIES:-1}"
	case "$primary_attempts" in
	'' | *[!0-9]*) primary_attempts=1 ;;
	esac
	[ "$primary_attempts" -lt 1 ] && primary_attempts=1

	log "[$label] primary=$primary (attempts=$primary_attempts)"
	local prev_cmd_log_tag="${RUN_CMD_LOG_TAG:-}"
	local primary_ret=1
	local attempt=1
	local attempt_prompt="$prompt"
	while [ "$attempt" -le "$primary_attempts" ]; do
		if [ "$primary_attempts" -gt 1 ]; then
			RUN_CMD_LOG_TAG="${label}:primary#${attempt}"
		else
			RUN_CMD_LOG_TAG="${label}:primary"
		fi
		run_cmd "$primary" "$attempt_prompt"
		primary_ret=$?
		log "[$label] run_cmd returned rc=$primary_ret (attempt ${attempt}/${primary_attempts})"
		# トークン超過 or 空応答: セッションが汚染されている → primary ループ打ち切り
		# rc=79: レートリミット/残高不足 → 即フォールバック
		if [ "$primary_ret" -eq 77 ] || [ "$primary_ret" -eq 78 ] || [ "$primary_ret" -eq 79 ]; then
			log "[$label] session poisoned or rate-limited (rc=$primary_ret) → skip remaining primary attempts"
			break
		fi
		if [ -n "$expect" ]; then
			local expect_written=false
			if [ -s "$expect" ]; then
				if [ -n "$expect_snapshot" ] && [ -f "$expect_snapshot" ]; then
					if ! cmp -s "$expect_snapshot" "$expect" 2>/dev/null; then
						expect_written=true
					fi
				else
					expect_written=true
				fi
			fi
			log "[$label] expect_check: written=$expect_written file=$expect"
			if [ "$expect_written" = true ]; then
				rm -f "$expect_snapshot" 2>/dev/null || true
				if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
				log "[$label] primary OK ($expect written, attempt ${attempt}/${primary_attempts})"
				return 0
			fi
			if [ "$attempt" -lt "$primary_attempts" ]; then
				local retry_resume_session=""
				retry_resume_session=$(_run_cmd_load_resume_session "$primary" 2>/dev/null || true)
				attempt_prompt=$(_build_no_edit_retry_prompt "$prompt" "$expect" "$attempt" "$primary_attempts" "$retry_resume_session")
			fi
		else
			[ "$primary_ret" -eq 0 ] && {
				rm -f "$expect_snapshot" 2>/dev/null || true
				if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
				return 0
			}
		fi
		if [ "$attempt" -lt "$primary_attempts" ]; then
			log "[$label] primary attempt ${attempt}/${primary_attempts} failed"
		fi
		attempt=$((attempt + 1))
	done

	log "[$label] primary failed → fallback=$fallback"
	RUN_CMD_LOG_TAG="${label}:fallback"
	run_cmd "$fallback" "$prompt"
	local fallback_ret=$?
	if [ "$fallback_ret" -eq 77 ] || [ "$fallback_ret" -eq 78 ] || [ "$fallback_ret" -eq 79 ]; then
		log "[$label] fallback session poisoned or rate-limited (rc=$fallback_ret) → skip last_resort"
		rm -f "$expect_snapshot" 2>/dev/null || true
		if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
		return 1
	fi
	if [ -n "$expect" ]; then
		local expect_written_fb=false
		if [ -s "$expect" ]; then
			if [ -n "$expect_snapshot" ] && [ -f "$expect_snapshot" ]; then
				if ! cmp -s "$expect_snapshot" "$expect" 2>/dev/null; then
					expect_written_fb=true
				fi
			else
				expect_written_fb=true
			fi
		fi
		if [ "$expect_written_fb" != true ]; then
			# --- last resort ---
			local last_resort="${MODEL_LAST_RESORT:-}"
			if [ -n "$last_resort" ]; then
				log "[$label] fallback failed → last_resort=$last_resort"
				RUN_CMD_LOG_TAG="${label}:last_resort"
				run_cmd "$last_resort" "$prompt"
				local last_resort_ret=$?
				if [ "$last_resort_ret" -eq 77 ] || [ "$last_resort_ret" -eq 78 ] || [ "$last_resort_ret" -eq 79 ]; then
					log "[$label] last_resort session poisoned or rate-limited (rc=$last_resort_ret) → abort"
					rm -f "$expect_snapshot" 2>/dev/null || true
					if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
					return 1
				fi
				local expect_written_lr=false
				if [ -s "$expect" ]; then
					if [ -n "$expect_snapshot" ] && [ -f "$expect_snapshot" ]; then
						if ! cmp -s "$expect_snapshot" "$expect" 2>/dev/null; then
							expect_written_lr=true
						fi
					else
						expect_written_lr=true
					fi
				fi
				if [ "$expect_written_lr" = true ]; then
					rm -f "$expect_snapshot" 2>/dev/null || true
					if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
					log "[$label] last_resort OK ($expect written)"
					return 0
				fi
			fi
			rm -f "$expect_snapshot" 2>/dev/null || true
			if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
			log "[$label] all attempts failed ($expect not written)"
			return 1
		fi
	fi
	rm -f "$expect_snapshot" 2>/dev/null || true
	if [ -n "$prev_cmd_log_tag" ]; then RUN_CMD_LOG_TAG="$prev_cmd_log_tag"; else unset RUN_CMD_LOG_TAG; fi
}

#=== strategy.py バリデーション ===

VALIDATE_ERROR=""

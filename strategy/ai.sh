# strategy/ai.sh - spinner, build_prompt, run_cmd, run_ai

start_spinner() {
	local label="$1"
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

#=== コマンド実行 ===

_opencode_latest_session_id_for_dir() {
	local target_dir="$1"
	local opencode_db="${OPENCODE_SESSION_DB:-$HOME/.local/share/opencode/opencode.db}"
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
	if [ "$type" = "glm" ] || [ "$type" = "opencode" ] || [ "$type" = "zai" ]; then
		resume_session=$(_run_cmd_load_resume_session "$spec" 2>/dev/null || true)
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

	case "$type" in
	zai)
		local -a zai_claude_args
		zai_claude_args=(-p "$prompt_body" --model=haiku --permission-mode=acceptEdits)
		[ -n "$resume_session" ] && zai_claude_args+=(-c)
		local -a zai_env=(
			ANTHROPIC_BASE_URL="https://api.z.ai/api/anthropic"
			ANTHROPIC_AUTH_TOKEN="${ZAI_API_KEY:-}"
			ANTHROPIC_DEFAULT_HAIKU_MODEL="glm-5-turbo"
		)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				env "${zai_env[@]}" "$timeout_bin" "$timeout_sec" claude "${zai_claude_args[@]}" >>"$cmd_log_file" 2>&1 &
			else
				env "${zai_env[@]}" claude "${zai_claude_args[@]}" >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				env "${zai_env[@]}" "$timeout_bin" "$timeout_sec" claude "${zai_claude_args[@]}" &
			else
				env "${zai_env[@]}" claude "${zai_claude_args[@]}" &
			fi
		fi
		;;
	glm)
		local -a glm_args
		glm_args=(run "$prompt_body" --agent="zai")
		[ -n "$resume_session" ] && glm_args+=(--continue --session "$resume_session")
		if [ -n "$cmd_log_file" ]; then
			if [ -n "${RUN_CMD_OPENCODE_PERMISSION:-}" ]; then
				if [ -n "$timeout_sec" ]; then
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" "$timeout_bin" "$timeout_sec" opencode "${glm_args[@]}" >>"$cmd_log_file" 2>&1 &
				else
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" opencode "${glm_args[@]}" >>"$cmd_log_file" 2>&1 &
				fi
			else
				if [ -n "$timeout_sec" ]; then
					"$timeout_bin" "$timeout_sec" opencode "${glm_args[@]}" >>"$cmd_log_file" 2>&1 &
				else
					opencode "${glm_args[@]}" >>"$cmd_log_file" 2>&1 &
				fi
			fi
		else
			if [ -n "${RUN_CMD_OPENCODE_PERMISSION:-}" ]; then
				if [ -n "$timeout_sec" ]; then
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" "$timeout_bin" "$timeout_sec" opencode "${glm_args[@]}" &
				else
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" opencode "${glm_args[@]}" &
				fi
			else
				if [ -n "$timeout_sec" ]; then
					"$timeout_bin" "$timeout_sec" opencode "${glm_args[@]}" &
				else
					opencode "${glm_args[@]}" &
				fi
			fi
		fi
		;;
	gemini)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" gemini -p "$prompt_body" -y -s >>"$cmd_log_file" 2>&1 &
			else
				gemini -p "$prompt_body" -y -s >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" gemini -p "$prompt_body" -y -s &
			else
				gemini -p "$prompt_body" -y -s &
			fi
		fi
		;;
	gemini-flash)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" gemini -p "$prompt_body" -y -s --model=gemini-2.5-flash >>"$cmd_log_file" 2>&1 &
			else
				gemini -p "$prompt_body" -y -s --model=gemini-2.5-flash >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" gemini -p "$prompt_body" -y -s --model=gemini-2.5-flash &
			else
				gemini -p "$prompt_body" -y -s --model=gemini-2.5-flash &
			fi
		fi
		;;
	sonnet)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			fi
		fi
		;;
	opus)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			fi
		fi
		;;
	haiku|claude)
		if [ -n "$cmd_log_file" ]; then
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits >>"$cmd_log_file" 2>&1 &
			fi
		else
			if [ -n "$timeout_sec" ]; then
				"$timeout_bin" "$timeout_sec" claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			else
				claude -p "$prompt_body" --model=haiku --permission-mode=acceptEdits &
			fi
		fi
		;;
	opencode)
		local -a opencode_args
		opencode_args=(run "$prompt_body" --agent="${agent:-glmflash}")
		[ -n "$resume_session" ] && opencode_args+=(--continue --session "$resume_session")
		if [ -n "$cmd_log_file" ]; then
			if [ -n "${RUN_CMD_OPENCODE_PERMISSION:-}" ]; then
				if [ -n "$timeout_sec" ]; then
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" "$timeout_bin" "$timeout_sec" opencode "${opencode_args[@]}" >>"$cmd_log_file" 2>&1 &
				else
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" opencode "${opencode_args[@]}" >>"$cmd_log_file" 2>&1 &
				fi
			else
				if [ -n "$timeout_sec" ]; then
					"$timeout_bin" "$timeout_sec" opencode "${opencode_args[@]}" >>"$cmd_log_file" 2>&1 &
				else
					opencode "${opencode_args[@]}" >>"$cmd_log_file" 2>&1 &
				fi
			fi
		else
			if [ -n "${RUN_CMD_OPENCODE_PERMISSION:-}" ]; then
				if [ -n "$timeout_sec" ]; then
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" "$timeout_bin" "$timeout_sec" opencode "${opencode_args[@]}" &
				else
					OPENCODE_PERMISSION="$RUN_CMD_OPENCODE_PERMISSION" opencode "${opencode_args[@]}" &
				fi
			else
				if [ -n "$timeout_sec" ]; then
					"$timeout_bin" "$timeout_sec" opencode "${opencode_args[@]}" &
				else
					opencode "${opencode_args[@]}" &
				fi
			fi
		fi
		;;
	esac
	local cmd_pid=$!
	RUN_CMD_ACTIVE_PID=$cmd_pid
	local _cmd_start_epoch
	_cmd_start_epoch=$(date +%s)

	start_spinner "$type thinking..."

	local prev_int_trap interrupted
	prev_int_trap=$(trap -p INT || true)
	interrupted=0
	trap 'interrupted=1; stop_spinner; _stop_loop_descendants "$cmd_pid"; kill "$cmd_pid" 2>/dev/null; wait "$cmd_pid" 2>/dev/null; RUN_CMD_ACTIVE_PID=0; log "Interrupted"' INT

	wait "$cmd_pid" 2>/dev/null
	local ret=$?
	RUN_CMD_ACTIVE_PID=0
	local _cmd_elapsed=$(( $(date +%s) - _cmd_start_epoch ))
	if [ "$interrupted" -eq 1 ]; then
		ret=130
	fi
	if [ "$ret" -eq 124 ] && [ -n "$timeout_sec" ]; then
		log "[CMD] timeout after ${timeout_sec}s → $type"
		if [ -n "$cmd_log_file" ]; then
			printf '[%s] [AI:%s] TIMEOUT after %ss\n' "$(date '+%H:%M:%S')" "$cmd_log_tag" "$timeout_sec" >>"$cmd_log_file" 2>/dev/null || true
		fi
	fi
	# トークン超過エラー検出 → セッション継続しても無駄なので rc=77 で通知
	if [ -n "$cmd_log_file" ] && tail -5 "$cmd_log_file" 2>/dev/null | grep -qi "exceeds.*context length\|exceeds.*maximum.*token" 2>/dev/null; then
		log "[CMD] トークン超過検出 → セッションクリア"
		ret=77
	fi
	# 空応答検出: 10秒以内に rc=0 で完了 → モデルが実質的な応答を返していない
	# (実際の改善作業は最低30秒以上かかる)
	if [ "$ret" -eq 0 ] && [ "${_cmd_elapsed:-999}" -le 10 ]; then
		log "[CMD] 空応答検出 (${_cmd_elapsed}s) → セッションクリア"
		ret=78
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
		# トークン超過 or 空応答: セッションが汚染されている → primary ループ打ち切り
		if [ "$primary_ret" -eq 77 ] || [ "$primary_ret" -eq 78 ]; then
			log "[$label] session poisoned (rc=$primary_ret) → skip remaining primary attempts"
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
	if [ "$fallback_ret" -eq 77 ] || [ "$fallback_ret" -eq 78 ]; then
		log "[$label] fallback session poisoned (rc=$fallback_ret) → skip last_resort"
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
				if [ "$last_resort_ret" -eq 77 ] || [ "$last_resort_ret" -eq 78 ]; then
					log "[$label] last_resort session poisoned (rc=$last_resort_ret) → abort"
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

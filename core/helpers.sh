# core/helpers.sh - log, commands_empty, _trim_log_file 等

#=== コアヘルパー ===

# score_history.txt からスコアのみ抽出（新旧両形式対応）
_last_score() {
	local line
	line=$(tail -1 score_history.txt 2>/dev/null) || { echo 0; return; }
	printf '%s\n' "${line##*	}"
}
_recent_scores() {
	local n="${1:-10}"
	tail -"$n" score_history.txt 2>/dev/null | awk -F'\t' '{print $NF}'
}

_append_celebration_history() {
	local kind="$1" score="${2:-0}" turns="${3:-0}" game_num="${4:-0}"
	local history_file=""
	case "$kind" in
	russia) history_file="$RUSSIA_CREATION_HISTORY_FILE" ;;
	soviet) history_file="$SOVIET_CREATION_HISTORY_FILE" ;;
	*) return 1 ;;
	esac
	mkdir -p "$(dirname "$history_file")" 2>/dev/null || true
	if [ -f "$history_file" ]; then
		local last_line last_key new_key
		last_line=$(tail -1 "$history_file" 2>/dev/null || true)
		last_key=$(printf '%s' "$last_line" | awk -F'\t' 'NR==1{print $3 "\t" $4 "\t" $5}')
		new_key=$(printf '%s\t%s\t%s' "$game_num" "$score" "$turns")
		if [ "$last_key" = "$new_key" ]; then
			return 0
		fi
	fi
	local iso_ts local_ts
	iso_ts=$(date '+%Y-%m-%dT%H:%M:%S%z' | sed 's/\([+-][0-9][0-9]\)\([0-9][0-9]\)$/\1:\2/')
	local_ts=$(date '+%Y-%m-%d %H:%M %Z')
	printf '%s\t%s\t%s\t%s\t%s\n' "$iso_ts" "$local_ts" "$game_num" "$score" "$turns" >>"$history_file"
	if [ -f "$history_file" ] && [ "$(wc -l < "$history_file")" -gt "$CELEBRATION_HISTORY_KEEP_LINES" ]; then
		tail -"$CELEBRATION_HISTORY_KEEP_LINES" "$history_file" >"${history_file}.tmp" 2>/dev/null && \
			mv "${history_file}.tmp" "$history_file" 2>/dev/null
	fi
}

commands_empty() { [ -z "$(tr -d '[:space:]' <"$COMMANDS" 2>/dev/null)" ]; }
log() { echo "[$(date '+%H:%M:%S')] $*"; }
# bash 3.2 (macOS /bin/bash) には BASHPID がないため、サブシェルPID取得のポータブル関数
_my_pid() { sh -c 'echo $PPID'; }
clear_commands_file() { : >"$COMMANDS"; }
_clear_stale_commands_if_any() {
	local reason="${1:-unknown}"
	[ -f "$COMMANDS" ] || return 0
	local cmd_preview
	cmd_preview=$(tr '\n' ' ' <"$COMMANDS" 2>/dev/null | sed -E 's/[[:space:]]+/ /g; s/^ //; s/ $//')
	[ -n "$cmd_preview" ] || return 0
	if [ "${#cmd_preview}" -gt 120 ]; then
		cmd_preview="${cmd_preview:0:117}..."
	fi
	log "[COMMANDS] stale commandsをクリア (${reason}): ${cmd_preview}"
	clear_commands_file
}
_trim_log_file() {
	local f="$1" keep="${2:-2000}" trim="${3:-4000}"
	[ -n "$f" ] || return 0
	[ -f "$f" ] || return 0
	local n
	n=$(wc -l <"$f" 2>/dev/null | tr -d ' ')
	[ "${n:-0}" -le "$trim" ] && return 0
	local tmpf="${f}.tmp"
	tail -n "$keep" "$f" >"$tmpf" 2>/dev/null && mv "$tmpf" "$f" 2>/dev/null || true
}

#=== ピーク時間帯判定 & エージェント優先順位入替え ===

# "HH" / "HHMM" / "HH:MM" を 0-1440 の「分」に変換して stdout に出す。
# 解釈できない場合は何も出力せず rc=1 を返す。
_peak_hours_to_minutes() {
	local token="${1:-}" hh="" mm=""
	token="${token#"${token%%[![:space:]]*}"}"
	token="${token%"${token##*[![:space:]]}"}"
	[ -n "$token" ] || return 1
	case "$token" in
	*:*)
		hh="${token%%:*}"
		mm="${token#*:}"
		;;
	????)
		hh="${token%??}"
		mm="${token#??}"
		;;
	???)
		hh="${token%??}"
		mm="${token#?}"
		;;
	*)
		hh="$token"
		mm="0"
		;;
	esac
	case "$hh" in '' | *[!0-9]*) return 1 ;; esac
	case "$mm" in '' | *[!0-9]*) return 1 ;; esac
	hh=$((10#$hh))
	mm=$((10#$mm))
	[ "$mm" -le 59 ] || return 1
	if [ "$hh" -eq 24 ] && [ "$mm" -eq 0 ]; then
		printf '%s' 1440
		return 0
	fi
	[ "$hh" -le 23 ] || return 1
	printf '%s' $((hh * 60 + mm))
}

# ピーク時間帯かどうかを判定する。ピークなら rc=0。
# 使い方: _is_peak_hours [HHMM]
#   now の決定順: 第1引数 > $PEAK_HOURS_TEST_NOW（テスト専用フック、本番envに置かない） > 実時刻 (TZ=$PEAK_HOURS_TZ)
#   ウィンドウ: $PEAK_HOURS_WINDOWS = "開始-終了[,開始-終了...]" (開始含む・終了含まない)
#   未設定なら既定 "10-13,15-19"。空文字を明示指定した場合はウィンドウなし＝常に非ピーク
#   （ピーク切替そのものを無効にしたいときは PEAK_HOURS_AGENT_SWAP_ENABLED=0 を使う）
#   開始==終了のウィンドウは幅ゼロとして無視する（「常時ピーク」にはならない）
_is_peak_hours() {
	local now="${1:-}" rest win start_tok end_tok now_min start_min end_min
	[ -n "$now" ] || now="${PEAK_HOURS_TEST_NOW:-}"
	[ -n "$now" ] || now=$(TZ="${PEAK_HOURS_TZ:-Asia/Tokyo}" date '+%H%M' 2>/dev/null)
	now_min=$(_peak_hours_to_minutes "$now") || return 1
	rest="${PEAK_HOURS_WINDOWS-10-13,15-19}"
	while [ -n "$rest" ]; do
		case "$rest" in
		*,*)
			win="${rest%%,*}"
			rest="${rest#*,}"
			;;
		*)
			win="$rest"
			rest=""
			;;
		esac
		case "$win" in
		*-*) ;;
		*) continue ;;
		esac
		start_tok="${win%%-*}"
		end_tok="${win#*-}"
		start_min=$(_peak_hours_to_minutes "$start_tok") || continue
		end_min=$(_peak_hours_to_minutes "$end_tok") || continue
		[ "$start_min" -eq "$end_min" ] && continue
		if [ "$start_min" -lt "$end_min" ]; then
			if [ "$now_min" -ge "$start_min" ] && [ "$now_min" -lt "$end_min" ]; then
				return 0
			fi
		else
			if [ "$now_min" -ge "$start_min" ] || [ "$now_min" -lt "$end_min" ]; then
				return 0
			fi
		fi
	done
	return 1
}

# ピーク時のみ、カンマ区切りエージェントリスト内の優先エージェントを先頭へ寄せる。
# 他候補は元の相対順序を保つ。候補の削除・追加は一切しない（フォールバックは温存）。
# 使い方: NEW_LIST=$(_peak_priority_agent_list "$LIST" [PREFERRED])
# 注意: 呼び出し側が $( ) で受けるため、この関数は stdout に結果以外を出力しない。
_peak_priority_agent_list() {
	local list_raw="${1:-}" preferred="${2:-}"
	[ -n "$preferred" ] || preferred="${PEAK_HOURS_PRIORITY_AGENT:-codex:minimax-m3}"
	if [ -z "$list_raw" ] || [ -z "$preferred" ] || [ "${PEAK_HOURS_AGENT_SWAP_ENABLED:-1}" != "1" ]; then
		printf '%s' "$list_raw"
		return 0
	fi
	if ! _is_peak_hours; then
		printf '%s' "$list_raw"
		return 0
	fi
	local rest="$list_raw" item matched="" others=""
	while [ -n "$rest" ]; do
		case "$rest" in
		*,*)
			item="${rest%%,*}"
			rest="${rest#*,}"
			;;
		*)
			item="$rest"
			rest=""
			;;
		esac
		item="${item#"${item%%[![:space:]]*}"}"
		item="${item%"${item##*[![:space:]]}"}"
		[ -n "$item" ] || continue
		if [ "$item" = "$preferred" ]; then
			if [ -n "$matched" ]; then matched="${matched},${item}"; else matched="$item"; fi
		else
			if [ -n "$others" ]; then others="${others},${item}"; else others="$item"; fi
		fi
	done
	if [ -z "$matched" ]; then
		printf '%s' "$list_raw"
		return 0
	fi
	if [ -n "$others" ]; then
		printf '%s' "${matched},${others}"
	else
		printf '%s' "$matched"
	fi
}

#=== ANSIエスケープ除去 ===

_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

_contains_provider_error_text() {
	printf '%s' "$1" | grep -Eiq 'invalid bearer token|authentication_error|failed to authenticat(e|ed)|api error[: ]|bad request|request_id|invalid error token|invalid token|not logged in|please run /login|unexpected error, check log file|failed to run the query|pragma wal_checkpoint|insufficient balance|no resource package|rate limit exceeded|freeusagelimiterror|degraded function cannot be invoked|function id .*degraded|providermodelnotfounderror|model not found|no such model|modelid|providerid|potentially unsafe or sensitive content|avoid using prompts that may generate sensitive content|unsafe or sensitive content in input or generation|content policy|safety policy|(^|[^[:alnum:]])error:[[:space:]]*gone|status["[:space:]]*:[[:space:]]*410|reached its end of life|is no longer available'
}

_contains_webfetch_failure_text() {
	printf '%s' "$1" | grep -Eiq '((WebFetch|WebSearch).*(取得できなかった|取得できません|確認が入りました|許可|permission|denied|rejected)|((取得できなかった|取得できません|確認が入りました|許可|permission|denied|rejected).*(WebFetch|WebSearch)))'
}

_notify_webfetch_failure() {
	local label="${1:-AI}" agent="${2:-unknown}" text="${3:-}" context="${4:-}"
	_contains_webfetch_failure_text "$text" || return 1

	local state_dir="${TMP_STATE_DIR:-tmp/state}"
	local throttle="${WEBFETCH_FAILURE_NOTIFY_THROTTLE_SEC:-180}"
	local key marker now mt age
	key=$(printf '%s_%s_%s' "$label" "$agent" "$context" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9._-]+/_/g; s/^_+//; s/_+$//')
	[ -n "$key" ] || key="webfetch"
	marker="${state_dir}/webfetch_failure_notify_${key}"
	now=$(date +%s)
	case "$throttle" in
	'' | *[!0-9]*) throttle=180 ;;
	esac
	if [ -f "$marker" ]; then
		mt=$(stat -f %m "$marker" 2>/dev/null) \
			|| mt=$(stat -c %Y "$marker" 2>/dev/null) \
			|| mt=0
		age=$((now - mt))
		[ "$age" -lt "$throttle" ] && return 0
	fi

	mkdir -p "$state_dir" 2>/dev/null || true
	: >"$marker" 2>/dev/null || true
	log "[${label}] Web取得失敗を検出; on-air本文から除去済み (agent=${agent}${context:+ context=${context}})" >&2
	if [ -x ./overlay_notify.sh ]; then
		./overlay_notify.sh radio "Web取得失敗" "label=${label} agent=${agent}${context:+ context=${context}} | 音声本文からは除去" "warn" >/dev/null 2>&1 || true
	fi
	return 0
}

_contains_claude_login_error_text() {
	printf '%s' "$1" | grep -Eiq 'not logged in|please run /login'
}

# soren_loop.sh の同名関数をバックグラウンド実行版で上書き。
# eloop_lib.sh は毎ループ source されるためこちらが優先される。
# フォアグラウンド実行だと monitor が詰まった際にメインループ全体がブロックされる。
_run_improve_runtime_monitor() {
	[ -x ./monitor_improve_runtime.sh ] || return 0
	local now interval
	now=$(date +%s)
	interval="${SOREN_IMPROVE_MONITOR_INTERVAL_SEC:-15}"
	case "$interval" in
	'' | *[!0-9]*) interval=15 ;;
	esac
	if [ "${_SOREN_IMPROVE_MONITOR_TS:-0}" -gt 0 ] && [ $((now - _SOREN_IMPROVE_MONITOR_TS)) -lt "$interval" ]; then
		return 0
	fi
	_SOREN_IMPROVE_MONITOR_TS=$now
	./monitor_improve_runtime.sh >/dev/null 2>&1 &
}

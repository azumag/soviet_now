# lib/ai_generate_policy.sh - ai_generate_list の障害分離ポリシー
#
# lib/ai_generate.sh の直後に source する。
# 明示的な rate-limit backoff は従来どおり agent 単位で全用途へ共有する一方、
# generic provider/CLI failure の短期 backoff と failure streak は用途スコープを
# 分ける。特に optional RADIO:*:prepass の一過性障害が同一 tick の本文生成まで
# 全候補を backoff skip することを防ぐ。

if ! declare -F _ai_priority_prepend >/dev/null; then
	source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ai_priority_window.sh"
fi

_ai_failure_backoff_scope() {
	local label="${1:-AI}" lower
	lower="${label,,}"
	case "$lower" in
	radio:*:prepass*) printf '%s\n' "radio_prepass" ;;
	radio*) printf '%s\n' "radio_main" ;;
	*) printf '%s\n' "default" ;;
	esac
}

_ai_failure_backoff_dir() {
	if [ -n "${AI_FAILURE_BACKOFF_DIR:-}" ]; then
		printf '%s\n' "$AI_FAILURE_BACKOFF_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_failure_backoff\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_failure_backoff\n'
	fi
}

_ai_failure_backoff_file() {
	local label="$1" agent="$2" scope key
	scope=$(_ai_failure_backoff_scope "$label")
	key=$(_ai_lock_sanitize_key "$agent")
	printf '%s/%s/%s\n' "$(_ai_failure_backoff_dir)" "$scope" "$key"
}

_ai_failure_backoff_check() {
	local label="$1" agent="$2" bf_file now bf_until
	bf_file=$(_ai_failure_backoff_file "$label" "$agent")
	[ -f "$bf_file" ] || return 0
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	if [ "$now" -lt "$bf_until" ]; then
		return 1
	fi
	rm -f "$bf_file" 2>/dev/null || true
	return 0
}

_ai_failure_backoff_set() {
	local label="$1" agent="$2" backoff_sec="$3" bf_file bf_until
	bf_file=$(_ai_failure_backoff_file "$label" "$agent")
	mkdir -p "$(dirname "$bf_file")" 2>/dev/null || true
	bf_until=$(( $(date +%s) + backoff_sec ))
	printf '%s\n' "$bf_until" >"$bf_file" 2>/dev/null || true
}

_ai_failure_backoff_remaining() {
	local label="$1" agent="$2" bf_file now bf_until rem
	bf_file=$(_ai_failure_backoff_file "$label" "$agent")
	[ -f "$bf_file" ] || { printf '0\n'; return; }
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	rem=$((bf_until - now))
	[ "$rem" -lt 0 ] && rem=0
	printf '%s\n' "$rem"
}

_ai_family_backoff_dir() {
	if [ -n "${AI_FAMILY_BACKOFF_DIR:-}" ]; then
		printf '%s\n' "$AI_FAMILY_BACKOFF_DIR"
	elif [ -n "${ELOOP_LIB_DIR:-}" ]; then
		printf '%s/tmp/state/ai_family_backoff\n' "$ELOOP_LIB_DIR"
	else
		printf 'tmp/state/ai_family_backoff\n'
	fi
}

_ai_family_backoff_file() {
	local family="$1"
	printf '%s/%s\n' "$(_ai_family_backoff_dir)" "$(_ai_lock_sanitize_key "$family")"
}

_ai_family_backoff_check() {
	local family="$1" bf_file now bf_until
	bf_file=$(_ai_family_backoff_file "$family")
	[ -f "$bf_file" ] || return 0
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	if [ "$now" -lt "$bf_until" ]; then
		return 1
	fi
	rm -f "$bf_file" 2>/dev/null || true
	return 0
}

_ai_family_backoff_set() {
	local family="$1" backoff_sec="$2" bf_file bf_until
	bf_file=$(_ai_family_backoff_file "$family")
	mkdir -p "$(dirname "$bf_file")" 2>/dev/null || true
	bf_until=$(( $(date +%s) + backoff_sec ))
	printf '%s\n' "$bf_until" >"$bf_file" 2>/dev/null || true
}

_ai_family_backoff_remaining() {
	local family="$1" bf_file now bf_until rem
	bf_file=$(_ai_family_backoff_file "$family")
	[ -f "$bf_file" ] || { printf '0\n'; return; }
	now=$(date +%s)
	bf_until=$(cat "$bf_file" 2>/dev/null || echo 0)
	case "$bf_until" in ''|*[!0-9]*) bf_until=0 ;; esac
	rem=$((bf_until - now))
	[ "$rem" -lt 0 ] && rem=0
	printf '%s\n' "$rem"
}

_ai_family_backoff_sec() {
	local family="$1" value=60
	case "$family" in
	vercel) value="${AI_VERCEL_FAMILY_BACKOFF_SEC:-60}" ;;
	esac
	case "$value" in ''|*[!0-9]*) value=60 ;; esac
	[ "$value" -lt 1 ] && value=60
	# Family suppression is deliberately short; agent-level quota backoff keeps
	# its existing longer duration. Do not let a config typo turn this into a
	# multi-hour provider outage.
	[ "$value" -gt 300 ] && value=300
	printf '%s\n' "$value"
}

_ai_failure_streak_file() {
	local label="$1" agent="$2" scope key
	scope=$(_ai_failure_backoff_scope "$label")
	key=$(_ai_lock_sanitize_key "$agent")
	printf '%s/generic__%s__%s\n' "$(_ai_fail_streak_dir)" "$scope" "$key"
}

_ai_failure_streak_record() {
	local label="$1" agent="$2" f n
	f=$(_ai_failure_streak_file "$label" "$agent")
	mkdir -p "$(_ai_fail_streak_dir)" 2>/dev/null || true
	n=$(cat "$f" 2>/dev/null || echo 0)
	case "$n" in ''|*[!0-9]*) n=0 ;; esac
	n=$((n + 1))
	printf '%s\n' "$n" >"$f" 2>/dev/null || true
	printf '%s\n' "$n"
}

_ai_failure_streak_clear() {
	local label="$1" agent="$2" f n
	f=$(_ai_failure_streak_file "$label" "$agent")
	[ -f "$f" ] || return 0
	n=$(cat "$f" 2>/dev/null || echo 0)
	case "$n" in ''|*[!0-9]*) n=0 ;; esac
	rm -f "$f" 2>/dev/null || true
	if [ "$n" -ge 3 ]; then
		log "[AI] ${agent} recovered after ${n} scoped provider failures (scope=$(_ai_failure_backoff_scope "$label"))" >&2
	fi
	return 0
}

# 1回の ai_generate_list 内で Vercel 429 が複数agentへ連鎖しているかを
# 秘密情報なしで観測する。agent/model名は chain_summary へ保存せず、固定キーの
# 件数と terminal enum だけを ai_stats JSONL に残す。挙動は一切変更しない。
_ai_chain_summary_record() {
	local label="$1" vercel_rate_limits="${2:-0}" vercel_distinct_agents="${3:-0}"
	local non_vercel_success="${4:-0}" terminal="${5:-all_failed}"
	case "$vercel_rate_limits" in ''|*[!0-9]*) vercel_rate_limits=0 ;; esac
	case "$vercel_distinct_agents" in ''|*[!0-9]*) vercel_distinct_agents=0 ;; esac
	case "$non_vercel_success" in 1) ;; *) non_vercel_success=0 ;; esac
	case "$terminal" in
	winner|all_failed|queue_giveup|gate_giveup) ;;
	*) terminal="all_failed" ;;
	esac
	_ai_stats_record "chain_summary" "$label" "" "$vercel_rate_limits" "" \
		"vrl=${vercel_rate_limits};vda=${vercel_distinct_agents};nfs=${non_vercel_success};term=${terminal}"
}

# lib/ai_generate.sh の ai_generate_list を同じ public contract のまま上書きする。
# 差分は generic failure backoff/streak のスコープ分離だけ。明示的 429 は
# _ai_backoff_* を使い続けるため全用途で共有されれる。
ai_generate_list() {
	local label="$1" prompt_file="$2" agent_list_raw="$3"
	local timeout_override="${4:-}"
	local validator="${5:-}"
	local last_agent_file="${6:-}"
	local failure_kind_file="${7:-}"
	local _AI_PRIORITY_CHAIN=1 _AI_PRIORITY_ORIGINAL_LIST="$agent_list_raw"
	agent_list_raw=$(_ai_priority_prepend "$agent_list_raw")
	local _bd agent output rc _rem attempted_count=0 saw_rate_limit=0
	local vercel_rate_limit_count=0
	local vercel_rate_limit_agents=()
	local saved_validator="${AI_DISPATCH_VALIDATOR:-}"

	AI_DISPATCH_VALIDATOR="$validator"
	AI_GENERATE_LAST_AGENT=""
	AI_GENERATE_LIST_LAST_AGENT=""
	[ -n "$last_agent_file" ] && : >"$last_agent_file"
	[ -n "$failure_kind_file" ] && : >"$failure_kind_file"

	# RADIO の長文生成は、eloop_lib.sh 読込後に環境値が再注入されても
	# 20秒等の旧値へ戻らないよう dispatch 直前にも safety floor を再適用する。
	# 明示 per-call timeout は呼び出し契約として優先し、ここでは触らない。
	if [[ "$label" == RADIO* ]] && [ -z "$timeout_override" ] \
		&& declare -F _normalize_radio_codex_timeout >/dev/null; then
		_normalize_radio_codex_timeout
	fi

	_bd=$(_ai_backoff_dir)
	mkdir -p "$_bd" 2>/dev/null || true
	mkdir -p "$(_ai_failure_backoff_dir)" 2>/dev/null || true

	local agents=()
	local _IFS_save="$IFS"
	IFS=',' read -ra agents <<< "$agent_list_raw"
	IFS="$_IFS_save"

	local skipped_rate_backoff=()
	local skipped_family_backoff=()
	local skipped_failure_backoff=()

	for agent in "${agents[@]}"; do
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -z "$agent" ] && continue
		if ! _ai_agent_spec_valid "$agent"; then
			log "[${label}] invalid agent spec skipped: ${agent}" >&2
			continue
		fi

		# A single Vercel 429 remains agent-scoped. Only a previous chain that
		# proved >=2 distinct Vercel 429s, or this chain after its second distinct
		# Vercel 429, activates the short provider-family breaker. Non-Vercel
		# candidates are never suppressed by this check.
		case "$agent" in
		vercel:*)
			if ! _ai_family_backoff_check "vercel"; then
				_rem=$(_ai_family_backoff_remaining "vercel")
				log "[${label}] Vercel family rate-limit backoff skip: ${agent} (${_rem}s remaining)" >&2
				skipped_family_backoff+=("$agent")
				continue
			fi
			;;
		esac

		# 明示的 429/quota backoff は用途を跨いで共有する。
		if ! _ai_backoff_check "$agent"; then
			_rem=$(_ai_backoff_remaining "$agent")
			log "[${label}] rate-limit backoff skip: ${agent} (${_rem}s remaining)" >&2
			skipped_rate_backoff+=("$agent")
			continue
		fi
		# generic provider failure は prepass/main を分離する。
		if ! _ai_failure_backoff_check "$label" "$agent"; then
			_rem=$(_ai_failure_backoff_remaining "$label" "$agent")
			log "[${label}] provider-failure backoff skip: ${agent} (${_rem}s remaining, scope=$(_ai_failure_backoff_scope "$label"))" >&2
			skipped_failure_backoff+=("$agent")
			continue
		fi

		_ai_priority_dispatch_allowed "$agent" || continue
		attempted_count=$((attempted_count + 1))
		output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")
		rc=$?
		if [ "$rc" -eq 93 ]; then
			attempted_count=$((attempted_count - 1))
			continue
		fi
		if [ "$rc" -eq "$AI_GATE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'gate_giveup\n' >"$failure_kind_file"
			_ai_chain_summary_record "$label" "$vercel_rate_limit_count" "${#vercel_rate_limit_agents[@]}" 0 "gate_giveup"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return "$AI_GATE_GIVEUP_RC"
		fi
		if [ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'queue_giveup\n' >"$failure_kind_file"
			_ai_chain_summary_record "$label" "$vercel_rate_limit_count" "${#vercel_rate_limit_agents[@]}" 0 "queue_giveup"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return "$AI_QUEUE_GIVEUP_RC"
		fi
		AI_DISPATCH_VALIDATOR=""
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && { [ -z "$validator" ] || "$validator" "$output"; }; then
			AI_GENERATE_LAST_AGENT="$agent"
			AI_GENERATE_LIST_LAST_AGENT="$agent"
			[ -n "$last_agent_file" ] && printf '%s\n' "$agent" >"$last_agent_file"
			_ai_failure_streak_clear "$label" "$agent"
			_ai_stats_record "winner" "$label" "$agent" "0" "$(_ai_resolved_model_from_agent "$agent")"
			local non_vercel_success=0
			if [ "$vercel_rate_limit_count" -gt 0 ]; then
				case "$agent" in vercel:*) ;; *) non_vercel_success=1 ;; esac
			fi
			_ai_chain_summary_record "$label" "$vercel_rate_limit_count" "${#vercel_rate_limit_agents[@]}" "$non_vercel_success" "winner"
			printf '%s' "$output"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return 0
		fi
		if [ "$rc" -eq 0 ] && [ -n "$output" ] && [ -n "$validator" ]; then
			log "[${label}] ${agent} returned invalid output → fallback" >&2
		else
			log "[${label}] ${agent} failed → fallback" >&2
		fi
		if [ "$rc" -eq "$AI_RATE_LIMIT_RC" ]; then
			saw_rate_limit=1
			case "$agent" in
			vercel:*)
				vercel_rate_limit_count=$((vercel_rate_limit_count + 1))
				local _vercel_seen=0 _vercel_agent
				for _vercel_agent in "${vercel_rate_limit_agents[@]}"; do
					[ "$_vercel_agent" = "$agent" ] && _vercel_seen=1 && break
				done
				[ "$_vercel_seen" -eq 1 ] || vercel_rate_limit_agents+=("$agent")
				if [ "${#vercel_rate_limit_agents[@]}" -ge 2 ]; then
					local family_backoff_sec
					family_backoff_sec=$(_ai_family_backoff_sec "vercel")
					_ai_family_backoff_set "vercel" "$family_backoff_sec"
					log "[${label}] multiple distinct Vercel rate limits in one chain → family backoff ${family_backoff_sec}s" >&2
				fi
				;;
			esac
			local agent_backoff_sec
			agent_backoff_sec=$(_ai_backoff_sec_for_agent "$agent" "$label")
			log "[${label}] ${agent} explicit rate limit → backoff ${agent_backoff_sec}s" >&2
			_ai_backoff_set "$agent" "$agent_backoff_sec"
		else
			if [ "$rc" -ne 0 ] && [ -n "$agent" ]; then
				local failure_backoff_sec="${AI_BACKOFF_FAILURE_SEC:-300}"
				case "$failure_backoff_sec" in ''|*[!0-9]*) failure_backoff_sec=300 ;; esac
				[ "$failure_backoff_sec" -lt 1 ] && failure_backoff_sec=300
				local _streak _shift _streak_max="${AI_FAILURE_STREAK_MAX_BACKOFF_SEC:-3600}"
				case "$_streak_max" in ''|*[!0-9]*) _streak_max=3600 ;; esac
				_streak=$(_ai_failure_streak_record "$label" "$agent")
				if [ "$_streak" -gt 1 ]; then
					_shift=$((_streak - 1))
					[ "$_shift" -gt 3 ] && _shift=3
					failure_backoff_sec=$((failure_backoff_sec * (1 << _shift)))
					[ "$failure_backoff_sec" -gt "$_streak_max" ] && failure_backoff_sec="$_streak_max"
				fi
				log "[${label}] ${agent} provider failure → scoped short backoff ${failure_backoff_sec}s (outcome=${rc}, streak=${_streak}, scope=$(_ai_failure_backoff_scope "$label"))" >&2
				_ai_failure_backoff_set "$label" "$agent" "$failure_backoff_sec"
			else
				log "[${label}] ${agent} no model backoff (outcome=${rc})" >&2
			fi
		fi
	done

	if [ "$attempted_count" -eq 0 ] && { [ ${#skipped_rate_backoff[@]} -gt 0 ] || [ ${#skipped_family_backoff[@]} -gt 0 ]; }; then
		log "[${label}] all available agents include explicit rate-limit or family backoff; retry later" >&2
		saw_rate_limit=1
	elif [ "$attempted_count" -eq 0 ] && [ ${#skipped_failure_backoff[@]} -gt 0 ]; then
		log "[${label}] all agents are in scoped provider-failure backoff; retry later" >&2
	fi

	log "[${label}] all agents failed (list=${agent_list_raw})" >&2
	local resolved_models=""
	for agent in "${agents[@]}"; do
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -n "$agent" ] || continue
		if [ -n "$resolved_models" ]; then
			resolved_models="${resolved_models},$(_ai_resolved_model_from_agent "$agent")"
		else
			resolved_models="$(_ai_resolved_model_from_agent "$agent")"
		fi
	done
	_ai_stats_record "all_failed" "$label" "" "" "$resolved_models"
	_ai_chain_summary_record "$label" "$vercel_rate_limit_count" "${#vercel_rate_limit_agents[@]}" 0 "all_failed"
	if [ -n "$failure_kind_file" ]; then
		if [ "$saw_rate_limit" -eq 1 ]; then
			printf 'rate_limit\n' >"$failure_kind_file"
		else
			printf 'failed\n' >"$failure_kind_file"
		fi
	fi
	AI_DISPATCH_VALIDATOR="$saved_validator"
	return 1
}

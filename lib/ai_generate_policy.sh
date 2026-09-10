# lib/ai_generate_policy.sh - ai_generate_list の障害分離ポリシー
#
# lib/ai_generate.sh の直後に source する。
# 明示的な rate-limit backoff は従来どおり agent 単位で全用途へ共有する一方、
# generic provider/CLI failure の短期 backoff と failure streak は用途スコープを
# 分ける。特に optional RADIO:*:prepass の一過性障害が同一 tick の本文生成まで
# 全候補を backoff skip することを防ぐ。

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

# lib/ai_generate.sh の ai_generate_list を同じ public contract のまま上書きする。
# 差分は generic failure backoff/streak のスコープ分離だけ。明示的 429 は
# _ai_backoff_* を使い続けるため全用途で共有される。
ai_generate_list() {
	local label="$1" prompt_file="$2" agent_list_raw="$3"
	local timeout_override="${4:-}"
	local validator="${5:-}"
	local last_agent_file="${6:-}"
	local failure_kind_file="${7:-}"
	local _bd agent output rc _rem attempted_count=0 saw_rate_limit=0
	local saved_validator="${AI_DISPATCH_VALIDATOR:-}"

	AI_DISPATCH_VALIDATOR="$validator"
	AI_GENERATE_LAST_AGENT=""
	AI_GENERATE_LIST_LAST_AGENT=""
	[ -n "$last_agent_file" ] && : >"$last_agent_file"
	[ -n "$failure_kind_file" ] && : >"$failure_kind_file"

	_bd=$(_ai_backoff_dir)
	mkdir -p "$_bd" 2>/dev/null || true
	mkdir -p "$(_ai_failure_backoff_dir)" 2>/dev/null || true

	local agents=()
	local _IFS_save="$IFS"
	IFS=',' read -ra agents <<< "$agent_list_raw"
	IFS="$_IFS_save"

	local skipped_rate_backoff=()
	local skipped_failure_backoff=()

	for agent in "${agents[@]}"; do
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -z "$agent" ] && continue
		if ! _ai_agent_spec_valid "$agent"; then
			log "[${label}] invalid agent spec skipped: ${agent}" >&2
			continue
		fi

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

		attempted_count=$((attempted_count + 1))
		output=$(_ai_dispatch "$label" "$agent" "$prompt_file" "$timeout_override")
		rc=$?
		if [ "$rc" -eq "$AI_GATE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'gate_giveup\n' >"$failure_kind_file"
			AI_DISPATCH_VALIDATOR="$saved_validator"
			return "$AI_GATE_GIVEUP_RC"
		fi
		if [ "$rc" -eq "$AI_QUEUE_GIVEUP_RC" ]; then
			[ -n "$failure_kind_file" ] && printf 'queue_giveup\n' >"$failure_kind_file"
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

	if [ "$attempted_count" -eq 0 ] && [ ${#skipped_rate_backoff[@]} -gt 0 ]; then
		log "[${label}] all available agents include explicit rate-limit backoff; retry later" >&2
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

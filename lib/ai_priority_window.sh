# One fixed, reviewed promotion window; never derived from process start/reload.
# Shared manifest is also consumed by soren91/text_ai.mjs. No credentials here.
_ai_priority_manifest="$(cd "$(dirname "${BASH_SOURCE[0]}")/../config" && pwd)/ai_priority_window.json"
read -r _AI_PRIORITY_START _AI_PRIORITY_END _AI_PRIORITY_AGENTS < <(
	python3 - "$_ai_priority_manifest" <<'PY'
import datetime, json, sys
with open(sys.argv[1]) as f:
    window = json.load(f)
def epoch(value):
    return int(datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
start, end = epoch(window['start_utc']), epoch(window['end_utc'])
assert end - start == 7 * 86400
assert window['agents'] == ['opencode-go:union-alpha', 'openrouter:stealth/union-alpha']
print(start, end, ','.join(window['agents']))
PY
)
unset _ai_priority_manifest

# Deterministic offline tests may supply an epoch; production uses the clock on
# every chain assembly and every individual dispatch (including post-queue).
_ai_priority_now() { printf '%s\n' "${AI_PRIORITY_NOW_EPOCH:-$(date +%s)}"; }
_ai_priority_active() {
	local now
	now=$(_ai_priority_now)
	case "$now:${_AI_PRIORITY_START:-}:${_AI_PRIORITY_END:-}" in *[!0-9:]*|::*|*::*) return 1 ;; esac
	[ -n "${_AI_PRIORITY_START:-}" ] && [ -n "${_AI_PRIORITY_END:-}" ] || return 1
	[ "$now" -ge "$_AI_PRIORITY_START" ] && [ "$now" -lt "$_AI_PRIORITY_END" ]
}
_ai_priority_agent() {
	case "${1:-}" in opencode-go:union-alpha|openrouter:stealth/union-alpha) return 0 ;; esac
	return 1
}

# Apply only AFTER caller-specific peak/last-winner ordering. Preserve all
# original candidates and their order; deduplicate the two promoted providers.
# An empty chain stays empty, so this cannot activate a disabled feature.
_ai_priority_prepend() {
	local original="${1:-}" agent result="" entries=()
	if ! _ai_priority_active || [[ "$original" != *[![:space:],]* ]]; then
		printf '%s' "$original"
		return 0
	fi
	result="$_AI_PRIORITY_AGENTS"
	IFS=',' read -ra entries <<<"$original"
	for agent in "${entries[@]}"; do
		agent="${agent#"${agent%%[![:space:]]*}"}"
		agent="${agent%"${agent##*[![:space:]]}"}"
		[ -n "$agent" ] || continue
		_ai_priority_agent "$agent" && continue
		result="$result,$agent"
	done
	printf '%s' "$result"
}

# Dynamic scope is deliberately per-chain: outside the campaign, explicitly
# configured Union Alpha models remain valid. Injected candidates alone expire.
_ai_priority_dispatch_allowed() {
	_ai_priority_agent "${1:-}" || return 0
	[ "${_AI_PRIORITY_CHAIN:-0}" = 1 ] || return 0
	_ai_priority_active && return 0
	local candidate entries=()
	IFS=',' read -ra entries <<<"${_AI_PRIORITY_ORIGINAL_LIST:-}"
	for candidate in "${entries[@]}"; do
		candidate="${candidate#"${candidate%%[![:space:]]*}"}"
		candidate="${candidate%"${candidate##*[![:space:]]}"}"
		[ "$candidate" = "$1" ] && return 0
	done
	return 1
}

# 93 is a local scheduling skip, NOT a provider failure/backoff (79), gate
# give-up (91), or queue give-up (92). Commands already running may finish.
# Direct-chain adapters share quota suppression for the promoted providers only.
# Preserve the existing non-promotion failure policies of those call sites.
_ai_priority_record_failure() {
	local agent="$1" rc="$2" label="${3:-AI}"
	_ai_priority_agent "$agent" || return 0
	[ "$rc" -eq 79 ] || return 0
	declare -F _ai_backoff_set >/dev/null || return 0
	_ai_backoff_set "$agent" "$(_ai_backoff_sec_for_agent "$agent" "$label")"
}

_ai_priority_run() {
	local agent="$1"
	shift
	_ai_priority_dispatch_allowed "$agent" || return 93
	"$@"
}

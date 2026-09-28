# Thin, opt-in bridge to docich's screen-aware common generation (#1233).
# Loaded after the base dispatcher and comment policy. No classifier, capture,
# provider, delivery or ack implementation lives here. Remove this adapter when
# #829 moves the caller into docich's native comment pipeline.

_docich_screen_text_fallback() (
	# This extra subshell owns only the temporary prompt, including on signals.
	local temporary="" rc=1
	temporary=$(mktemp "${TMPDIR:-/tmp}/eloop_comment_screen_text_XXXXXXXX") || return 1
	trap 'rm -f -- "$temporary"' EXIT
	cat -- "$2" >"$temporary" || return 1
	printf '\n\n【画面参照状態】画面を確認できていません。見たと主張しないでください。\n' >>"$temporary"
	_docich_screen_base_ai_generate_list "$1" "$temporary" "${@:3}"
	rc=$?
	return "$rc"
)

_docich_screen_route() {
	# Bash's dynamic scope supplies the exact already-classified reply batch
	# and attempt from generate_comment_response. Other call sites are unchanged.
	if [ "${COMMENT_SCREEN_CONTEXT_ENABLED:-0}" != "1" ] || [ "$#" -ne 7 ] \
		|| [ "${1:-}" != "COMMENT" ] || [ "${5:-}" != "_comment_is_valid_generation_candidate" ] \
		|| [ "${classification_json+x}" != "x" ] || [ "${attempt+x}" != "x" ]; then
		_docich_screen_base_ai_generate_list "$@"
		return $?
	fi
	local screen_cli="${DOCICH_COMMENT_SCREEN_CLI:-/home/ubuntu/docich/bin/docich-comment-screen}"
	if [ -x "$screen_cli" ] && [ "${DOCICH_ALLOW_REAL_AI:-0}" = "1" ]; then
		printf '%s' "${classification_json:-[]}" | \
			COMMENT_SCREEN_CONTEXT_ENABLED=1 DOCICH_ALLOW_REAL_AI=1 \
			COMMENT_SCREEN_CAPTURE_ATTEMPT="$attempt" "$screen_cli" \
			--prompt-file "$2" --agents "$3" --timeout "$4" \
			--last-agent-file "$6" --failure-kind-file "$7"
		# Preserve native rc (including 79/91/92) and sidecars; the caller keeps
		# its existing validation/retry/translation/delivery/ack contract.
		return $?
	fi
	_docich_screen_text_fallback "$@"
}

_docich_install_screen_bridge() {
	local definition=""
	definition=$(declare -f ai_generate_list) || return 0
	case "$definition" in *'_docich_screen_route "$@"'*) return 0 ;; esac
	# Only a definition from the already sourced, trusted dispatcher is renamed.
	# Viewer text, configuration and prompts never enter this eval.
	eval "${definition/#ai_generate_list /_docich_screen_base_ai_generate_list }"
	ai_generate_list() { _docich_screen_route "$@"; }
}
_docich_install_screen_bridge

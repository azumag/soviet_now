# Optional classification backend, layered after comment.sh (like runtime policy).
# Capture only trusted, already-loaded shell code; never eval a comment or output.
# Re-source alone and full eloop_lib.sh reload both preserve the real legacy base.
if declare -F _classify_comments >/dev/null 2>&1; then
	_comment_classifier_jev_definition=$(declare -f _classify_comments)
	case "$_comment_classifier_jev_definition" in
	*'_comment_classifier_jev_base'*) ;;
	*) eval "${_comment_classifier_jev_definition/_classify_comments/_comment_classifier_jev_base}" ;;
	esac
	unset _comment_classifier_jev_definition
fi

_classify_comments() {
	if [ "${COMMENT_CLASSIFIER_BACKEND:-}" != "jev" ]; then
		_comment_classifier_jev_base "$@"
		return $?
	fi
	local comments_file="${1:-}" classification
	[ -f "$comments_file" ] || return 1
	# The adapter times the canonical heuristic, then runs a single killable HTTP
	# child. A normalized baseline is retained before doing any network I/O.
	if classification=$(python3 "${ELOOP_LIB_DIR:-.}/lib/comment_classifier_jev.py" "$comments_file" 2>/dev/null) && [ -n "$classification" ]; then
		printf '%s' "$classification"
		return 0
	fi
	# Missing/broken adapter is NOT permission to enter the legacy 45/90s AI chain.
	classification=$(_classify_comments_heuristic "$comments_file" 2>/dev/null) || return 1
	_comment_normalize_classification_for_comments "$classification" "$comments_file"
}

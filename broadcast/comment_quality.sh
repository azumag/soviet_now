# Shared comment speech quality bridge.
#
# The canonical quality policy lives in docich.  This file only adapts the
# existing soviet_now comment validator to that policy.  When an older docich
# is temporarily deployed, keep a small compatibility fallback so staggered
# rollouts never reject every comment.

if declare -F _is_valid_comment_talk >/dev/null 2>&1 && ! declare -F _is_valid_comment_talk_base >/dev/null 2>&1; then
	# Preserve the radio_engine implementation (content/error guards) and wrap it
	# instead of copying those checks into a second source of truth.
	eval "$(declare -f _is_valid_comment_talk | sed '1s/_is_valid_comment_talk/_is_valid_comment_talk_base/')"
fi

_comment_quality_docich_bin() {
	local bin="${DOCICH_BIN:-}"
	if [ -z "$bin" ]; then
		bin="$(command -v docich 2>/dev/null || true)"
	fi
	[ -n "$bin" ] && [ -x "$bin" ] || return 1
	printf '%s' "$bin"
}

_comment_quality_fallback() {
	# Compatibility only. docich `speech-quality --profile comment` is the
	# canonical implementation. Mirror just the critical max-30-char pause rule
	# for CI/old deployments where the new command is unavailable.
	python3 -c '
import re
import sys
text = sys.stdin.read()
japanese = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
for sentence in re.split(r"(?<=[。！？!?])", text):
    if not japanese.search(sentence):
        continue
    body = sentence.rstrip("。！？!? \\t\\r\\n")
    for run in re.split(r"[、，,]", body):
        compact = re.sub(r"\\s+", "", run)
        if japanese.search(compact) and len(compact) > 30:
            raise SystemExit(1)
raise SystemExit(0)
'
}

_is_valid_comment_talk() {
	local talk="$1" docich_bin rc
	if declare -F _is_valid_comment_talk_base >/dev/null 2>&1; then
		_is_valid_comment_talk_base "$talk" || return 1
	fi

	docich_bin=$(_comment_quality_docich_bin 2>/dev/null || true)
	if [ -n "$docich_bin" ]; then
		printf '%s' "$talk" | "$docich_bin" speech-quality --profile comment >/dev/null 2>&1
		rc=$?
		case "$rc" in
		0) return 0 ;;
		1) return 1 ;;
		# rc=2 (old docich: unknown command) and execution failures fall back
		# instead of turning a rolling deployment into a total reply outage.
		*) ;;
		esac
	fi

	printf '%s' "$talk" | _comment_quality_fallback
}

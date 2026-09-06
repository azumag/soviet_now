# strategy/retry_policy.sh - retry classification for improvement validation failures

_validation_error_is_nonretryable_infrastructure() {
	local error_text="${1:-}"
	case "$error_text" in
	"OS隔離runner未導入のため自動適用をfail-closedで停止"*) return 0 ;;
	esac
	return 1
}

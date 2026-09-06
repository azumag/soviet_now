# strategy/retry_policy.sh - retry classification for improvement validation failures

_validation_error_is_nonretryable_infrastructure() {
	local error_text="${1:-}"
	# Reset on every call so a prior shadow result cannot contaminate a later
	# retryable candidate failure. This code is diagnostic, not an apply bypass.
	VALIDATION_RETRY_BLOCK_CODE=""
	case "$error_text" in
	"OS隔離runner未導入のため自動適用をfail-closedで停止"*)
		VALIDATION_RETRY_BLOCK_CODE="isolated_runner_unavailable"
		;;
	"OS隔離runner評価がpassにならなかったため適用を見送り、既存のknown-good strategyを維持する (mode=shadow。"*)
		# The rollout policy refuses application even after a passing receipt.
		# Editing the candidate cannot change that policy; do not ask AI to fix it.
		VALIDATION_RETRY_BLOCK_CODE="isolated_runner_shadow"
		;;
	*) return 1 ;;
	esac
	return 0
}

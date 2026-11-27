# lib/reclaim_improve_temps.sh - soviet_now#869
#
# 改善ループが失敗・中断したときの一時ファイル（起動時スナップショット、
# host_integrity スナップショット等）は、正常終了の削除だけでは残る。
# 起動時に 24 時間より古いものだけを回収する。パターンと置き場を厳密に絞り、
# 想定外のファイルは絶対に消さない（fail-closed）。

reclaim_improve_temps() {
	local dir="${1:-}"
	if [ -z "$dir" ] || [ ! -d "$dir" ]; then
		printf '0\n'
		return 0
	fi
	local removed
	removed=$(find "$dir" -maxdepth 1 -type f -mmin +1440 \
		\( -name 'eloop_improve_runtime.*.sh' \
		   -o -name 'host_integrity_before.*' \
		   -o -name 'eloop_fix_prompt.*' \
		   -o -name 'pre_review_staging.*' \
		   -o -name 'eloop_sandbox_py.*' \) \
		-print -delete 2>/dev/null | wc -l | tr -d ' ')
	case "$removed" in
	'' | *[!0-9]*) removed=0 ;;
	esac
	printf '%s\n' "$removed"
}

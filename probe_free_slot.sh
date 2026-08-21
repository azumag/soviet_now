#!/usr/bin/env bash
# free枠モデルの上流復旧を1回だけ確認するプローブ。
# cron 例: */30 * * * * /home/ubuntu/soren/probe_free_slot.sh >> logs/ai_stderr.log 2>&1
#
# - FREE_PROBE_MODELS (カンマ区切り。空なら既定の2モデル)
# - 各モデルへ短文を投げ、成功/失敗を tmp/state/free_slot_probe/<key> に記録
# - 前回失敗→今回成功の遷移で overlay_notify し、ai_stderr.log へ復旧を記録
# - 常に exit 0 (cron で安全)
set -u

cd "$(dirname "$0")" || exit 0
log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

MODELS="${FREE_PROBE_MODELS:-opencode:deepseek-v4-flash-free,opencode:x-preview-f-free}"
STATE_DIR="${FREE_PROBE_STATE_DIR:-tmp/state/free_slot_probe}"
TIMEOUT_SEC="${FREE_PROBE_TIMEOUT:-45}"
mkdir -p "$STATE_DIR" 2>/dev/null || true

opencode_bin="${OPENCODE_BIN:-/snap/bin/opencode}"
[ -x "$opencode_bin" ] || opencode_bin="opencode"

IFS=',' read -ra _models <<<"$MODELS"
for agent in "${_models[@]}"; do
	agent="${agent#"${agent%%[![:space:]]*}"}"
	agent="${agent%"${agent##*[![:space:]]}"}"
	[ -z "$agent" ] && continue
	model="$agent"
	case "$agent" in
	opencode:*) model="opencode/${agent#opencode:}" ;;
	esac
	key=$(printf '%s' "$agent" | tr ':/ ' '___')
	state_file="$STATE_DIR/$key"
	prev=""
	[ -f "$state_file" ] && prev=$(cat "$state_file" 2>/dev/null)

	out=$(timeout --kill-after=5s "$TIMEOUT_SEC" "$opencode_bin" run --model "$model" \
		'「はい」とだけ返してください。他の文字は出力しないでください。' </dev/null 2>&1)
	rc=$?
	if [ "$rc" -eq 0 ] && [ -n "$out" ] && ! printf '%s' "$out" | grep -Eiq 'error|unexpected server'; then
		printf 'ok %s\n' "$(date +%s)" >"$state_file"
		if [ "$prev" != "ok" ] && [ -n "$prev" ]; then
			log "[FreeProbe] ${agent} RECOVERED (prev=${prev})"
			if [ -x ./overlay_notify.sh ]; then
				./overlay_notify.sh radio "free枠復旧" "${agent} が応答を返しました。チェーン再追加を検討してください。" "info" >/dev/null 2>&1 || true
			fi
		fi
	else
		reason="rc=$rc"
		[ "$rc" -eq 124 ] && reason="timeout ${TIMEOUT_SEC}s"
		printf '%s %s\n' "$(date +%s)" "$reason" >"$state_file"
		log "[FreeProbe] ${agent} down (${reason})"
	fi
done
exit 0

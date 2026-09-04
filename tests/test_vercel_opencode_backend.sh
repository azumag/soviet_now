#!/usr/bin/env bash
set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

export ELOOP_LIB_DIR="$ROOT"
export TMP_STATE_DIR="$TMP/state"
export OPENCODE_BIN="$TMP/opencode"
export OPENCODE_RUN_LOCK_ENABLED=0
export OPENCODE_ABORT_RETRY=1
export AI_STATS_ENABLED=0
export AI_GENERATION_QUEUE_ENABLED=0
mkdir -p "$TMP/state"
printf 'test prompt' >"$TMP/prompt"

cat >"$OPENCODE_BIN" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >>"$OPENCODE_CALLS"
case "${OPENCODE_RESULT:-ok}" in
  ok) printf 'VERCEL_OK\n' ;;
  rate) printf '429 rate limit exceeded\n' >&2; exit 1 ;;
  forbidden) printf '403 forbidden\n' >&2; exit 1 ;;
esac
SH
chmod +x "$OPENCODE_BIN"
export OPENCODE_CALLS="$TMP/calls"

# shellcheck disable=SC1091
. "$ROOT/core/helpers.sh"
# shellcheck disable=SC1091
. "$ROOT/core/config.sh"
# shellcheck disable=SC1091
. "$ROOT/lib/ai_generate.sh"

result=$(_ai_call_opencode_unqueued TEST vercel:minimax/minimax-m3-free "$TMP/prompt" 10)
[ "$result" = "VERCEL_OK" ] || { echo "not ok - M3 result"; exit 1; }
grep -qx 'run --agent soren-lite --model vercel/minimax/minimax-m3-free test prompt' "$OPENCODE_CALLS" || { echo "not ok - M3 mapping"; exit 1; }
[ "$(wc -l <"$OPENCODE_CALLS" | tr -d ' ')" = 1 ] || { echo "not ok - Vercel must not retry"; exit 1; }

: >"$OPENCODE_CALLS"
result=$(_ai_call_opencode_unqueued TEST vercel:poolside/laguna-s-2.1-free "$TMP/prompt" 10)
[ "$result" = "VERCEL_OK" ] || { echo "not ok - Laguna result"; exit 1; }
grep -qx 'run --agent soren-lite --model vercel/poolside/laguna-s-2.1-free test prompt' "$OPENCODE_CALLS" || { echo "not ok - Laguna mapping"; exit 1; }

[ "$(_ai_resolved_model_from_agent vercel:minimax/minimax-m3-free)" = 'vercel/minimax/minimax-m3-free' ] || { echo "not ok - resolved model"; exit 1; }
[ "$(_ai_backoff_sec_for_agent vercel:minimax/minimax-m3-free RADIO)" = 300 ] || { echo "not ok - backoff"; exit 1; }
[ "$(_ai_agent_spec_valid vercel:minimax/minimax-m3-free; echo $?)" = 0 ] || { echo "not ok - enabled spec"; exit 1; }
VERCEL_FREE_AGENTS=""
_ai_agent_spec_valid vercel:minimax/minimax-m3-free
[ "$?" = 1 ] || { echo "not ok - emergency disable"; exit 1; }
VERCEL_FREE_AGENTS="vercel:minimax/minimax-m3-free"

: >"$OPENCODE_CALLS"
OPENCODE_RESULT=rate _ai_call_opencode_unqueued TEST vercel:minimax/minimax-m3-free "$TMP/prompt" 10 >/dev/null
[ "$?" = "$AI_RATE_LIMIT_RC" ] || { echo "not ok - 429 classification"; exit 1; }
[ "$(wc -l <"$OPENCODE_CALLS" | tr -d ' ')" = 1 ] || { echo "not ok - 429 retried"; exit 1; }

echo "ok - Vercel models map to OpenCode exactly and disable internal retry"

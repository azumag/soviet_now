#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# shellcheck source=/dev/null
. "$ROOT/broadcast/radio_persona.sh"
# Production sources this immediately after radio_persona.sh.
# shellcheck source=/dev/null
. "$ROOT/broadcast/docich_soren91_host_mode.sh"

assert_eq() {
	local expected="$1" actual="$2" label="$3"
	if [ "$actual" != "$expected" ]; then
		printf 'FAIL: %s (expected=%s actual=%s)\n' "$label" "$expected" "$actual" >&2
		exit 1
	fi
	printf 'ok: %s\n' "$label"
}

unset RADIO_CAPITALISM_VOICEVOX_SPEAKER
SOREN91_VOICEVOX_SPEAKER=14
assert_eq "14" "$(_radio_voicevox_speaker_override capitalism)" "capitalism uses the Meriken AI speaker"
assert_eq "" "$(_radio_voicevox_speaker_override news)" "other radio corners keep the main speaker"

RADIO_CAPITALISM_VOICEVOX_SPEAKER=123
assert_eq "123" "$(_radio_voicevox_speaker_override capitalism)" "capitalism speaker can be configured independently"

unset RADIO_CAPITALISM_VOICEVOX_SPEAKER SOREN91_VOICEVOX_SPEAKER
assert_eq "46" "$(_radio_voicevox_speaker_override capitalism)" "capitalism retains the repository fallback speaker"

# The docich game-switch canonical keeps candidate runtimes before commit.
# Viewer-facing persona/voice must remain on the main host until ready commits
# Soren91 into active.
soren91_is_running() { return 1; }
export SOREN_ACTIVE_GAME_CONTEXT_FILE="$TMP/game_switch.json"

cat >"$SOREN_ACTIVE_GAME_CONTEXT_FILE" <<'JSON'
{"phase":"starting","active":{"game":"robots"},"candidate":{"game":"soren91"}}
JSON
assert_eq "main" "$(_broadcast_host_mode)" "starting candidate does not switch persona early"

cat >"$SOREN_ACTIVE_GAME_CONTEXT_FILE" <<'JSON'
{"phase":"probing","active":{"game":"robots"},"candidate":{"game":"soren91"}}
JSON
assert_eq "main" "$(_broadcast_host_mode)" "probing candidate does not switch persona early"

cat >"$SOREN_ACTIVE_GAME_CONTEXT_FILE" <<'JSON'
{"phase":"ready","active":{"game":"soren91"},"candidate":null}
JSON
assert_eq "soren91" "$(_broadcast_host_mode)" "committed Soren91 active switches persona"

cat >"$SOREN_ACTIVE_GAME_CONTEXT_FILE" <<'JSON'
{"phase":"ready","active":{"game":"robots"},"candidate":null}
JSON
assert_eq "main" "$(_broadcast_host_mode)" "other committed game keeps main persona"

printf '%s\n' '{not-json' >"$SOREN_ACTIVE_GAME_CONTEXT_FILE"
assert_eq "main" "$(_broadcast_host_mode)" "malformed canonical fails closed to main persona"

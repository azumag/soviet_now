#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)

# shellcheck source=/dev/null
. "$ROOT/broadcast/radio_persona.sh"

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

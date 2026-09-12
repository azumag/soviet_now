#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

unset RADIO_CODEX_TIMEOUT RADIO_CODEX_TIMEOUT_MIN_SEC
source "$ROOT/core/radio_timeout_guard.sh"

# Unset keeps ai_generate.sh's own 240s default path intact.
[ -z "${RADIO_CODEX_TIMEOUT:-}" ]

RADIO_CODEX_TIMEOUT=20
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

RADIO_CODEX_TIMEOUT=59
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

RADIO_CODEX_TIMEOUT=60
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "60" ]

RADIO_CODEX_TIMEOUT=180
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "180" ]

RADIO_CODEX_TIMEOUT=not-a-number
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

# The minimum itself fails safe if malformed; an operator cannot accidentally
# disable the guard with a typo.
RADIO_CODEX_TIMEOUT_MIN_SEC=invalid
RADIO_CODEX_TIMEOUT=20
_normalize_radio_codex_timeout
[ "$RADIO_CODEX_TIMEOUT" = "240" ]

echo "radio timeout guard test: PASS"

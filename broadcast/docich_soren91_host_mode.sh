#!/usr/bin/env bash
# docich_soren91_host_mode.sh - docich game-switch canonical bridge for Soren91 host mode.
#
# This file is sourced after radio_persona.sh and intentionally replaces only
# _docich_soren91_corner_active(). The docich canonical `candidate` is a
# pre-commit runtime (starting/probing), so it must never make viewer-facing
# persona/voice switch early. Only a committed `ready` + active=soren91 state
# represents the corner being on air.

_docich_soren91_corner_active() {
	local ctx="${SOREN_ACTIVE_GAME_CONTEXT_FILE:-/home/ubuntu/docich/run-soren-live/game_switch.json}"
	[ -f "$ctx" ] || return 1
	# Cheap pre-check: avoid starting Python unless Soren91 appears at all.
	grep -q '"soren91"' "$ctx" 2>/dev/null || return 1
	python3 - "$ctx" <<'PY' 2>/dev/null
import json
import sys

try:
    with open(sys.argv[1], encoding="utf-8") as stream:
        data = json.load(stream)
except Exception:
    raise SystemExit(1)

phase = data.get("phase")
active = (data.get("active") or {}).get("game")
raise SystemExit(0 if phase == "ready" and active == "soren91" else 1)
PY
}

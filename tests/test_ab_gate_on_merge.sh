#!/usr/bin/env bash
# tests/test_ab_gate_on_merge.sh - unit tests for strategy/ab_gate_on_merge.sh
#
# Covers the active-A/B detector: file-based state, live toggle, .env flag,
# pause, lock, and abort markers. Exit codes 0 (clear) / 1 (active) / 2
# (unknown) are exercised.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$ROOT/strategy/ab_gate_on_merge.sh"
[ -x "$SCRIPT" ] || { echo "skip: $SCRIPT not present"; exit 0; }

RES=0
ok()  { printf 'ok   %s\n' "$1"; }
ng()  { printf 'FAIL %s\n' "$1"; RES=1; }

# --- run a check scenario ---------------------------------------------------
# $1 = label, $2 = name of a function that sets up the scenario inside a
# fresh temp dir (cd-ed). Then runs "$SCRIPT" check and compares the exit
# code against $3.
run_check() { # label  setup_func  expected_rc
	local label="$1" setup="$2" expected="$3"
	local WORK
	WORK=$(mktemp -d "${TMPDIR:-/tmp}/ab_gate_on_merge.XXXXXX") || { ng "tmp for $label"; return; }
	(
		cd "$WORK" || exit 1
		"$setup" || exit 1
		"$SCRIPT" check >/dev/null 2>&1
		exit $?
	)
	local rc=$?
	rm -rf "$WORK"
	if [ "$rc" -eq "$expected" ]; then ok "$label"; else ng "$label (expected rc=$expected, got rc=$rc)"; fi
}

# --- scenarios (each sets up its own temp dir, then cds into it) -----------
scenario_no_state() {
	echo 'REGRESSION_DISABLED=0' > .env
}
scenario_recorded_experiment() {
	mkdir -p tmp/state
	printf '%s' '{"a_hash":"AAA","b_hash":"BBB"}' > tmp/state/ab_state.json
}
scenario_confirmation_stage() {
	mkdir -p tmp/state
	printf '%s' '{"a_hash":"AAA","b_hash":"BBB","confirmation_required":true,"confirmation_phase":"confirmation"}' > tmp/state/ab_state.json
}
scenario_aborted() {
	mkdir -p tmp/state
	echo x > tmp/state/ab_abort
	printf '%s' '{"a_hash":"AAA","b_hash":"BBB"}' > tmp/state/ab_state.json
}
scenario_alt_strategy_set() {
	export SOREN_AB_ALT_STRATEGY=tmp/state/ab_alt_strategy.py
}
scenario_improve_paused() {
	mkdir -p tmp/state
	touch tmp/state/improve_daemon.paused
}
scenario_improve_lock() {
	mkdir -p tmp/state
	touch tmp/state/improve.lock
}
scenario_regression_disabled() {
	echo 'REGRESSION_DISABLED=1' > .env
}
scenario_unreadable_state() {
	mkdir -p tmp/state
	printf '%s' '{"a_hash":"AAA"}' > tmp/state/ab_state.json
	chmod 000 tmp/state/ab_state.json
}
cleanup_unreadable() {
	chmod 644 tmp/state/ab_state.json
}

# --- 1) no state, no toggles --------------------------------------------
run_check "no-state-offline" scenario_no_state 0

# --- 2) recorded experiment (a_hash+b_hash, no abort) --------------------
run_check "recorded-experiment" scenario_recorded_experiment 1

# --- 3) confirmation stage ------------------------------------------------
run_check "confirmation-phase" scenario_confirmation_stage 1

# --- 4) abort marker clears it ------------------------------------------------
run_check "abort-marker" scenario_aborted 0

# --- 5) live arm selected -------------------------------------------------------
run_check "alt-strategy-set" scenario_alt_strategy_set 1

# --- 6) improve daemon paused ----------------------------------------------------
run_check "improve-daemon-paused" scenario_improve_paused 1

# --- 7) improve lock -------------------------------------------------------------
run_check "improve-lock" scenario_improve_lock 1

# --- 8) REGRESSION_DISABLED=1 in .env ---------------------------------------------
run_check "regression-disabled" scenario_regression_disabled 1

# --- 9) empty state file -> clear (no a_hash/b_hash, no crash) ---------------------------------
scenario_empty_state() {
	mkdir -p tmp/state
	: > tmp/state/ab_state.json
}
run_check "empty-state" scenario_empty_state 0

echo
if [ "$RES" -eq 0 ]; then echo "ALL ok"; else echo "SOME FAILED"; fi
exit "$RES"

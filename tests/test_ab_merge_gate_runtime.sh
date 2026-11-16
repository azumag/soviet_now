#!/usr/bin/env bash
# tests/test_ab_merge_gate_runtime.sh - soviet_now#925 regression tests
#
# Verifies the merge/reflect boundary wiring: _strategy_runtime_ab_merge_guard
# (invoked at the top of strategy_runtime_projection_apply_gate, and by the
# direct-writer paths in strategy/regression.sh + core/version.sh) runs
# strategy/ab_gate_on_merge.sh check and branches on its exit code:
#
#   0 (clear)          -> the live-bytes apply proceeds
#   1 (active A/B)     -> refused fail-closed (rc=3 = _STRATEGY_RUNTIME_GUARD_REFUSED)
#   2 (unknown/unread) -> refused fail-closed
#
# Also covers the experiment's own conclusion path: _ab_finish's B adopt sets
# AB_GATE_ON_MERGE_INTERNAL_ADOPT=1 and must not deadlock on the gate.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GATE_SCRIPT="$ROOT/strategy/ab_gate_on_merge.sh"
[ -f "$GATE_SCRIPT" ] || { echo "skip: $GATE_SCRIPT not present"; exit 0; }

RES=0
ok()  { printf 'ok   %s\n' "$1"; }
ng()  { printf 'FAIL %s\n' "$1"; RES=1; }

# --- scenarios (run inside the fresh temp runtime root) ----------------------
scn_clear()         { : > .env; }
scn_active_state()  { printf '%s' '{"a_hash":"AAA","b_hash":"BBB"}' > tmp/state/ab_state.json; }
scn_aborted()       { echo x > tmp/state/ab_abort; printf '%s' '{"a_hash":"AAA","b_hash":"BBB"}' > tmp/state/ab_state.json; }
scn_alt_strategy()  { export SOREN_AB_ALT_STRATEGY="tmp/state/ab_alt_strategy.py"; }
scn_regr_disabled() { echo 'REGRESSION_DISABLED=1' > .env; }
scn_improve_paused(){ touch tmp/state/improve_daemon.paused; }
scn_broken_state()  { printf 'not-json{{{' > tmp/state/ab_state.json; }
scn_gate_missing()  { rm -f strategy/ab_gate_on_merge.sh; }
scn_internal_adopt(){ scn_active_state; export AB_GATE_ON_MERGE_INTERNAL_ADOPT=1; }

# --- run a guard scenario ----------------------------------------------------
# $1 label, $2 scenario func, $3 expected rc. Sets up an isolated runtime root
# (projection-like: no .git), sources helpers + strategy_runtime, and calls
# _strategy_runtime_ab_merge_guard. The marker files are checked by callers
# via the kept work dir only for dedicated cases; otherwise the dir is freed.
run_guard() { # label scenario expected_rc
	local label="$1" setup="$2" expected="$3"
	local WORK
	WORK=$(mktemp -d "${TMPDIR:-/tmp}/ab_merge_gate.XXXXXX") || { ng "tmp for $label"; return; }
	(
		cd "$WORK" || exit 1
		mkdir -p tmp/state strategy
		cp "$GATE_SCRIPT" strategy/ab_gate_on_merge.sh
		export SOREN_PROJECTION_ROOT="$WORK"
		export ELOOP_LIB_DIR="$WORK"
		export TMP_STATE_DIR="$WORK/tmp/state"
		export AB_ENV_FILE="$WORK/.env"
		export STRATEGY_FILE="strategy.py"
		export SOREN_AB_ALT_STRATEGY=""
		unset AB_GATE_ON_MERGE_INTERNAL_ADOPT
		"$setup" || exit 1
		source "$ROOT/core/helpers.sh" >/dev/null 2>&1 || exit 2
		source "$ROOT/core/strategy_runtime.sh" >/dev/null 2>&1 || exit 2
		_strategy_runtime_ab_merge_guard "test" >/dev/null 2>&1
		exit $?
	)
	local rc=$?
	rm -rf "$WORK"
	if [ "$rc" -eq "$expected" ]; then ok "$label"; else ng "$label (expected rc=$expected, got rc=$rc)"; fi
}

# --- run an atomic apply through the full gate -------------------------------
# $1 label, $2 scenario func, $3 expected_result ("applied"|"refused"),
# $4 impl ("base"|"strict"). Copies strategy.py live bytes only when the gate
# chain allows; asserts final live content + apply return code.
run_apply() {
	local label="$1" setup="$2" expected="$3" impl="${4:-base}"
	local WORK out
	WORK=$(mktemp -d "${TMPDIR:-/tmp}/ab_merge_apply.XXXXXX") || { ng "tmp for $label"; return; }
	out=$(
		cd "$WORK" || exit 1
		mkdir -p tmp/state strategy
		cp "$GATE_SCRIPT" strategy/ab_gate_on_merge.sh
		echo 'LIVE' > strategy.py
		echo 'CANDIDATE' > candidate.py
		export SOREN_PROJECTION_ROOT="$WORK"
		export ELOOP_LIB_DIR="$WORK"
		export TMP_STATE_DIR="$WORK/tmp/state"
		export AB_ENV_FILE="$WORK/.env"
		export STRATEGY_FILE="strategy.py"
		export SOREN_AB_ALT_STRATEGY=""
		unset AB_GATE_ON_MERGE_INTERNAL_ADOPT
		# Isolate the A/B gate from the projection-lineage verdict so this test
		# only exercises the #925 branch. Base honors off directly; strict needs
		# the production unsafe-ack on a git-less root.
		export IMPROVE_LIVE_APPLY_POLICY=off
		export SOREN_UNSAFE_PROJECTION_APPLY_ACK=1
		"$setup" || exit 1
		source "$ROOT/core/helpers.sh" >/dev/null 2>&1 || exit 2
		source "$ROOT/core/strategy_runtime.sh" >/dev/null 2>&1 || exit 2
		[ "$impl" = "strict" ] && { source "$ROOT/core/strategy_runtime_strict.sh" >/dev/null 2>&1 || exit 2; }
		apply_rc=0
		strategy_runtime_atomic_apply "candidate.py" "strategy.py" >/dev/null 2>&1 || apply_rc=$?
		if grep -q '^CANDIDATE$' strategy.py; then echo "applied"; else echo "blocked:$apply_rc"; fi
	)
	rm -rf "$WORK"
	if [ "$expected" = "applied" ] && [ "$out" = "applied" ]; then
		ok "$label"
	elif [ "$expected" = "refused" ] && [ "${out%%:*}" = "blocked" ]; then
		ok "$label (rc=${out#blocked:})"
	else
		ng "$label (expected $expected, got $out)"
	fi
}

# --- marker verification -----------------------------------------------------
# After a refusal the durable markers must record the fail-closed evidence:
# tmp/state/strategy_apply_last_failure.json carries failure_code
# "ab_merge_gate_refused" and tmp/state/projection_guard_last_event.json
# carries the refused event.
check_markers() {
	local WORK rc=0 ev="" code=""
	WORK=$(mktemp -d "${TMPDIR:-/tmp}/ab_merge_mark.XXXXXX") || { ng "tmp for markers"; return; }
	(
		cd "$WORK" || exit 1
		mkdir -p tmp/state strategy
		cp "$GATE_SCRIPT" strategy/ab_gate_on_merge.sh
		printf '%s' '{"a_hash":"AAA","b_hash":"BBB"}' > tmp/state/ab_state.json
		export SOREN_PROJECTION_ROOT="$WORK"
		export ELOOP_LIB_DIR="$WORK"
		export TMP_STATE_DIR="$WORK/tmp/state"
		export AB_ENV_FILE="$WORK/.env"
		export STRATEGY_FILE="strategy.py"
		export SOREN_AB_ALT_STRATEGY=""
		unset AB_GATE_ON_MERGE_INTERNAL_ADOPT
		source "$ROOT/core/helpers.sh" >/dev/null 2>&1 || exit 2
		source "$ROOT/core/strategy_runtime.sh" >/dev/null 2>&1 || exit 2
		_strategy_runtime_ab_merge_guard "test" >/dev/null 2>&1
		[ $? -eq 3 ] || exit 1
	)
	rc=$?
	if [ "$rc" -eq 0 ]; then
		code=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("failure_code",""))' "$WORK/tmp/state/strategy_apply_last_failure.json" 2>/dev/null)
		ev=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("event",""))' "$WORK/tmp/state/projection_guard_last_event.json" 2>/dev/null)
	fi
	rm -rf "$WORK"
	if [ "$rc" -eq 0 ] && [ "$code" = "ab_merge_gate_refused" ] && [ "$ev" = "refused" ]; then
		ok "refusal-markers (failure_code=$code event=$ev)"
	else
		ng "refusal-markers (guard_rc=$rc failure_code=$code event=$ev)"
	fi
}

# --- guard-level checks ------------------------------------------------------
run_guard "guard-clear"            scn_clear          0
run_guard "guard-active-state"     scn_active_state   3
run_guard "guard-aborted"          scn_aborted        0
run_guard "guard-alt-strategy"     scn_alt_strategy   3
run_guard "guard-regr-disabled"    scn_regr_disabled  3
run_guard "guard-improve-paused"   scn_improve_paused 3
run_guard "guard-broken-state"     scn_broken_state   3
run_guard "guard-gate-missing"     scn_gate_missing   3
run_guard "guard-internal-adopt"   scn_internal_adopt 0

# --- apply-level regression (the actual merge/reflect boundary) --------------
run_apply "apply-base-clear"      scn_clear        "applied" base
run_apply "apply-base-active"     scn_active_state "refused" base
run_apply "apply-strict-clear"    scn_clear        "applied" strict
run_apply "apply-strict-active"   scn_active_state "refused" strict

# --- durable evidence on refusal ---------------------------------------------
check_markers

echo
if [ "$RES" -eq 0 ]; then echo "ALL ok"; else echo "SOME FAILED"; fi
exit "$RES"

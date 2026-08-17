import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ImprovePeakHourDeferTests(unittest.TestCase):
    def run_bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_is_peak_hour_utc_boundaries(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"

! _is_peak_hour_utc 0059
_is_peak_hour_utc 0100
_is_peak_hour_utc 0359
! _is_peak_hour_utc 0400
_is_peak_hour_utc 0600
_is_peak_hour_utc 0959
! _is_peak_hour_utc 1000
! _is_peak_hour_utc 0000
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_is_peak_hour_utc_avoids_octal_trap_for_08_09(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"

_is_peak_hour_utc 0800
_is_peak_hour_utc 0900
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_is_peak_hour_utc_handles_overnight_range(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"
ranges="23:00-02:00"

_is_peak_hour_utc 2330 "$ranges"
_is_peak_hour_utc 0130 "$ranges"
! _is_peak_hour_utc 0215 "$ranges"
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_is_peak_hour_utc_skips_invalid_entries_without_crashing(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"
ranges="abc,01:00-04:00"

_is_peak_hour_utc 0200 "$ranges"
! _is_peak_hour_utc 0500 "$ranges"
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_defers_during_peak_hour_and_creates_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
ACCUMULATED_GAMES_FILE="$2/acc.json"
MIN_GAMES_BEFORE_IMPROVE=12
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h"}' >"$IMPROVE_LOCK_FILE"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "0200"
    else
        command date "$@"
    fi
}

_is_peak_hour_utc
_improve_peak_gate_should_defer
[ -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_disabled_passes_through_and_clears_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h"}' >"$IMPROVE_LOCK_FILE"
printf '%s\n' "$(($(date +%s) - 10))" >"$TMP_STATE_DIR/peak_hour_defer"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "0200"
    else
        command date "$@"
    fi
}

IMPROVE_PEAK_HOUR_DEFER_ENABLED=0
! _improve_peak_gate_should_defer
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_max_wait_safety_valve_passes_through_and_clears_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
ACCUMULATED_GAMES_FILE="$2/acc.json"
MIN_GAMES_BEFORE_IMPROVE=12
IMPROVE_PEAK_DEFER_MAX_WAIT_SEC=100
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h"}' >"$IMPROVE_LOCK_FILE"
# started well beyond the max wait window
printf '%s\n' "$(($(date +%s) - 200))" >"$TMP_STATE_DIR/peak_hour_defer"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "0200"
    else
        command date "$@"
    fi
}

! _improve_peak_gate_should_defer
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_urgent_lock_bypasses_defer(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
ACCUMULATED_GAMES_FILE="$2/acc.json"
MIN_GAMES_BEFORE_IMPROVE=12
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h","improve_reason":"wildcard"}' >"$IMPROVE_LOCK_FILE"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "0200"
    else
        command date "$@"
    fi
}

! _improve_peak_gate_should_defer
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_accumulated_backlog_bypasses_defer(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
ACCUMULATED_GAMES_FILE="$2/acc.json"
MIN_GAMES_BEFORE_IMPROVE=12
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":12,"hash":"h"}' >"$IMPROVE_LOCK_FILE"
printf '%s\n' '{"count":12,"hash":"h"}' >"$ACCUMULATED_GAMES_FILE"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "0200"
    else
        command date "$@"
    fi
}

# combined=24 >= threshold(12*200/100=24) → force bypass
! _improve_peak_gate_should_defer
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_gate_clears_marker_when_peak_window_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "1200"
    else
        command date "$@"
    fi
}

! _improve_peak_gate_should_defer
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_expire_peak_hour_defer_if_stale_clears_without_going_through_trigger(self):
        # Simulates the "daemon down" scenario: nothing calls
        # trigger_adaptive_improvement (and thus never reaches
        # _improve_peak_gate_should_defer) yet the marker must still clear
        # once the peak window ends, via the independent loop-side expiry.
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

date() {
    if [ "$1" = "-u" ] && [ "$2" = "+%H%M" ]; then
        printf '%s\n' "1200"
    else
        command date "$@"
    fi
}

_expire_peak_hour_defer_if_stale
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]

# no-op when no marker present
_expire_peak_hour_defer_if_stale
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_soren_loop_calls_expiry_unconditionally_every_iteration(self):
        # Regression guard for the deadlock fix: soren_loop.sh:853 only
        # calls trigger_adaptive_improvement (which owns marker creation
        # and clearing) when the marker is absent, so an unconditional
        # loop-side expiry call is required to avoid a permanent defer
        # when improve_daemon.sh is not running.
        loop = (REPO_ROOT / "soren_loop.sh").read_text(encoding="utf-8")
        self.assertIn("_expire_rate_limit_backoff_if_elapsed", loop)
        self.assertIn("_expire_peak_hour_defer_if_stale", loop)
        # rate-limit helper is both defined and called in this file; take the
        # call site (last occurrence), not the function definition.
        rl_idx = loop.rindex("_expire_rate_limit_backoff_if_elapsed")
        phd_idx = loop.index("_expire_peak_hour_defer_if_stale")
        # must be adjacent (both unconditional, top-of-loop calls)
        between = loop[rl_idx:phd_idx]
        self.assertNotIn("if ", between)
        self.assertNotIn("continue", between)

    def test_orphan_lock_gc_protected_by_peak_hour_defer_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
soren91_harvest_hung_improve() { :; }
_sync_improve_state_with_live_process() { :; }
_read_improve_state() { printf '%s\n' '{"status":"idle"}'; }

TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '{}\n' >"$IMPROVE_LOCK_FILE"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"
touch -t 202001010000 "$IMPROVE_LOCK_FILE"
check_and_harvest_improvement
[ -f "$IMPROVE_LOCK_FILE" ]

rm -f "$TMP_STATE_DIR/peak_hour_defer"
check_and_harvest_improvement
[ ! -f "$IMPROVE_LOCK_FILE" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

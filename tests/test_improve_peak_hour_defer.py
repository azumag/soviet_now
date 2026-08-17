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

    def test_expire_peak_hour_defer_if_stale_delegates_to_trigger_when_lock_present(self):
        # The loop-side expiry must NOT decide disabled/window-ended/bypass
        # itself and clear the marker in isolation: doing so would let the
        # very next gate re-evaluation lose the elapsed-wait evidence (the
        # marker) and silently restart the defer clock, defeating max_wait
        # as a hard backstop. It must hand off atomically to
        # trigger_adaptive_improvement, which owns the gate and, on a
        # "proceed" decision, immediately continues into lock consumption
        # (refreshing the lock's mtime via enrich_accumulated_game_metadata)
        # in the very same call.
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
test_root="$2"
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h"}' >"$IMPROVE_LOCK_FILE"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

trigger_adaptive_improvement() { printf 'called\n' >>"$test_root/trigger_calls"; }

_expire_peak_hour_defer_if_stale
[ -f "$test_root/trigger_calls" ]
[ "$(cat "$test_root/trigger_calls")" = "called" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_expire_peak_hour_defer_if_stale_uses_background_daemon_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
test_root="$2"
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":3,"hash":"h"}' >"$IMPROVE_LOCK_FILE"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

trigger_adaptive_improvement() { printf '%s\n' "${IMPROVE_DAEMON_MODE:-unset}" >"$test_root/mode"; }

_expire_peak_hour_defer_if_stale
[ "$(cat "$test_root/mode")" = "0" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_expire_peak_hour_defer_if_stale_clears_orphan_marker_without_lock(self):
        # Anomalous state: marker present but lock already gone. Must clean
        # up the marker directly rather than calling trigger (which would
        # itself just no-op on a missing lock, leaving the marker stuck).
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
test_root="$2"
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

trigger_adaptive_improvement() { printf 'should-not-be-called\n' >>"$test_root/trigger_calls"; }

_expire_peak_hour_defer_if_stale
[ ! -f "$TMP_STATE_DIR/peak_hour_defer" ]
[ ! -f "$test_root/trigger_calls" ]

# no-op when no marker present at all
_expire_peak_hour_defer_if_stale
[ ! -f "$test_root/trigger_calls" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_peak_defer_clear_touches_lock_mtime_to_survive_immediate_orphan_gc(self):
        # Regression guard: soren_loop.sh runs check_and_harvest_improvement
        # (whose orphan-lock GC now also respects the peak_hour_defer
        # marker) immediately after the loop-side expiry clears the marker.
        # If the lock's mtime were left stale from hours of deferral, that
        # very next GC pass would delete the still-pending accumulated
        # batch. _peak_defer_clear must refresh the mtime so the lock
        # survives long enough to actually be consumed.
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
mkdir -p "$TMP_STATE_DIR"
printf '{}\n' >"$IMPROVE_LOCK_FILE"
# simulate a lock that has been sitting for hours (well past the orphan
# GC's default 600s staleness threshold), as would happen after a long
# peak-hour defer.
touch -t 202001010000 "$IMPROVE_LOCK_FILE"
printf '%s\n' "$(date +%s)" >"$TMP_STATE_DIR/peak_hour_defer"

_peak_defer_clear "peak_window_ended"

old_epoch=$(date -j -f "%Y%m%d%H%M" "202001010000" +%s 2>/dev/null || echo 0)
new_mtime=$(stat -f %m "$IMPROVE_LOCK_FILE" 2>/dev/null || stat -c %Y "$IMPROVE_LOCK_FILE" 2>/dev/null)
[ "$new_mtime" -gt "$old_epoch" ]
[ $(( $(date +%s) - new_mtime )) -lt 5 ]
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

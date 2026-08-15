import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class ImproveRetryReliabilityTests(unittest.TestCase):
    def run_bash(self, script: str, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", script, "bash", *args],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_active_backoff_preserves_retry_lock_but_orphan_cleanup_still_works(self):
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
printf '2\n0\n' >"$TMP_STATE_DIR/rate_limit_backoff"
touch -t 202001010000 "$IMPROVE_LOCK_FILE"
check_and_harvest_improvement
[ -f "$IMPROVE_LOCK_FILE" ]

rm -f "$TMP_STATE_DIR/rate_limit_backoff"
check_and_harvest_improvement
[ ! -f "$IMPROVE_LOCK_FILE" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_daemon_always_delegates_backoff_expiry_to_trigger(self):
        daemon = (REPO_ROOT / "improve_daemon.sh").read_text(encoding="utf-8")
        loop = daemon[daemon.index("while true; do") :]
        self.assertIn('trigger_adaptive_improvement', loop)
        self.assertNotIn('daemon trigger をスキップ', loop)
        self.assertNotIn('if [ -f "$TMP_STATE_DIR/rate_limit_backoff" ]', loop)

    def test_spawn_mutex_rechecks_live_state_before_starting_second_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
TMP_STATE_DIR="$2/state"
test_root="$2"
mkdir -p "$TMP_STATE_DIR"
_acquire_spawn_lock() { printf 'acquired\n' >>"$test_root/events"; }
_release_spawn_lock() { printf 'released\n' >>"$test_root/events"; }
_read_improve_state() { printf '%s\n' '{"status":"running","pid":123}'; }
_is_live_improve_pid() { [ "$1" = 123 ]; }
log() { printf '%s\n' "$*" >>"$test_root/events"; }

set +e
_start_improvement_job "history" "1 2" false 100 normal
rc=$?
set -e
[ "$rc" -eq 1 ]
grep -qx 'acquired' "$2/events"
grep -qx 'released' "$2/events"
grep -q 'state再確認で既存running/manualを検出' "$2/events"
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        improve = (REPO_ROOT / "strategy/improve.sh").read_text(encoding="utf-8")
        start = improve[improve.index("_start_improvement_job()") :]
        acquire = start.index("_acquire_spawn_lock")
        recheck = start.index("_improve_spawn_state_blocks_start", acquire)
        stale_scan = start.index("stale_pids=", recheck)
        self.assertLess(acquire, recheck)
        self.assertLess(recheck, stale_scan)
        self.assertIn('eloop_improve(_runtime\\.[^ ]+)?\\.sh', start)

    def test_spawn_state_guard_allows_only_idle_or_dead_running_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
fake_status=idle
fake_pid=0
fake_live=0
_read_improve_state() { printf '{"status":"%s","pid":%s}\n' "$fake_status" "$fake_pid"; }
_is_live_improve_pid() { [ "$fake_live" -eq 1 ] && [ "$1" = "$fake_pid" ]; }

! _improve_spawn_state_blocks_start
fake_status=manual
_improve_spawn_state_blocks_start
fake_status=running
fake_pid=321
fake_live=1
_improve_spawn_state_blocks_start
fake_live=0
! _improve_spawn_state_blocks_start
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_live_pid_detection_accepts_runtime_snapshot_name(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/improve.sh"
kill() { return 0; }
ps() { printf '%s\n' 'bash /srv/soren/tmp/state/eloop_improve_runtime.A1b2.sh history'; }
_is_live_improve_pid 123
ps() { printf '%s\n' 'bash /srv/soren/eloop_improve.sh history'; }
_is_live_improve_pid 123
ps() { printf '%s\n' 'bash /srv/soren/unrelated.sh'; }
! _is_live_improve_pid 123
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_no_apply_restores_missing_full_retry_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
test_root="$2"
log() { printf '%s\n' "$*" >>"$test_root/events"; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
IMPROVE_RETRY_BATCH_FILE="$TMP_STATE_DIR/improve_retry_batch.json"
MIN_GAMES_BEFORE_IMPROVE=100
mkdir -p "$TMP_STATE_DIR"
printf '%s\n' '{"count":100,"hash":"same-hash","scores":"1 2 3"}' >"$IMPROVE_LOCK_FILE"
_snapshot_improve_retry_batch
rm -f "$IMPROVE_LOCK_FILE"
_restore_improve_retry_batch_if_valid same-hash
python3 - "$IMPROVE_LOCK_FILE" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["count"] == 100
assert data["hash"] == "same-hash"
assert data["retry_restore_count"] == 1
PY
grep -q 'retry batch restored (100 games' "$2/events"
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_retry_batch_rejects_stale_or_partial_normal_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/improve.sh"
log() { :; }
TMP_STATE_DIR="$2/state"
IMPROVE_LOCK_FILE="$2/improve.lock"
IMPROVE_RETRY_BATCH_FILE="$TMP_STATE_DIR/improve_retry_batch.json"
MIN_GAMES_BEFORE_IMPROVE=100
mkdir -p "$TMP_STATE_DIR"

printf '%s\n' '{"count":100,"hash":"old-hash"}' >"$IMPROVE_LOCK_FILE"
_snapshot_improve_retry_batch
rm -f "$IMPROVE_LOCK_FILE"
! _restore_improve_retry_batch_if_valid new-hash
[ ! -e "$IMPROVE_LOCK_FILE" ]

printf '%s\n' '{"count":99,"hash":"new-hash"}' >"$IMPROVE_LOCK_FILE"
_snapshot_improve_retry_batch
rm -f "$IMPROVE_LOCK_FILE"
! _restore_improve_retry_batch_if_valid new-hash
[ ! -e "$IMPROVE_LOCK_FILE" ]

printf '%s\n' '{"count":4,"hash":"new-hash","early_escape_lock":true}' >"$IMPROVE_LOCK_FILE"
_snapshot_improve_retry_batch
rm -f "$IMPROVE_LOCK_FILE"
_restore_improve_retry_batch_if_valid new-hash
[ -s "$IMPROVE_LOCK_FILE" ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_fallback_retries_no_edit_and_accepts_second_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/ai.sh"
test_root="$2"
log() { printf '%s\n' "$*" >>"$test_root/log"; }
RUN_AI_PRIMARY_RETRIES=1
RUN_AI_FALLBACK_RETRIES=3
RUN_CMD_TMP_DIR="$test_root"
MODEL_LAST_RESORT=last
fallback_calls=0
run_cmd() {
    printf '%s\n' "$1" >>"$test_root/calls"
    case "$1" in
        primary) return 79 ;;
        fallback)
            fallback_calls=$((fallback_calls + 1))
            if [ "$fallback_calls" -eq 1 ]; then
                return 1
            fi
            printf '# result\n' >"$3"
            return 0
            ;;
        last) return 9 ;;
    esac
}
printf 'prompt\n' >"$test_root/prompt.md"
set +e
run_ai TEST primary fallback "$test_root/prompt.md" "$test_root/result.md"
run_rc=$?
set -e
[ "$run_rc" -eq 0 ]
[ "$(cat "$test_root/result.md")" = '# result' ]
[ "$(grep -c '^fallback$' "$test_root/calls")" -eq 2 ]
! grep -q '^last$' "$test_root/calls"
grep -q 'fallback OK' "$test_root/log"
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_rate_limited_fallback_continues_to_last_resort(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/ai.sh"
test_root="$2"
log() { :; }
RUN_AI_PRIMARY_RETRIES=1
RUN_AI_FALLBACK_RETRIES=3
RUN_CMD_TMP_DIR="$test_root"
MODEL_LAST_RESORT=last
run_cmd() {
    printf '%s\n' "$1" >>"$test_root/calls"
    case "$1" in
        primary|fallback) return 79 ;;
        last) printf '# recovered\n' >"$3"; return 0 ;;
    esac
}
printf 'prompt\n' >"$test_root/prompt.md"
set +e
run_ai TEST primary fallback "$test_root/prompt.md" "$test_root/result.md"
run_rc=$?
set -e
[ "$run_rc" -eq 0 ]
[ "$(cat "$test_root/result.md")" = '# recovered' ]
[ "$(tr '\n' ',' <"$test_root/calls")" = 'primary,fallback,last,' ]
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_new_expected_file_stops_looping_provider_after_stable_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.run_bash(
                r'''
set -e
source "$1/strategy/ai.sh"
_stop_loop_descendants() { :; }
RUN_CMD_EXPECT_STABLE_SEC=1
sleep 30 &
cmd_pid=$!
_run_cmd_start_expected_file_watchdog "$cmd_pid" "$2/result.md" false "$2/watch.log" TEST
( sleep 0.2; printf '# done\n' >"$2/result.md" ) &
set +e
wait "$cmd_pid" 2>/dev/null
wait_rc=$?
set -e
_run_cmd_stop_expected_file_watchdog
[ "$wait_rc" -ne 0 ]
[ "$(cat "$2/result.md")" = '# done' ]
grep -q 'EXPECT_READY' "$2/watch.log"
''',
                str(REPO_ROOT),
                tmp,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_minimax_timeout_is_capped_without_affecting_deepseek(self):
        result = self.run_bash(
            r'''
set -e
source "$1/strategy/ai.sh"
[ "$(_run_cmd_limit_timeout_for_model 1800 minimax-m3)" = '300' ]
[ "$(_run_cmd_limit_timeout_for_model 1800 deepseek-v4-pro)" = '1800' ]
[ "$(_run_cmd_limit_timeout_for_model 120 minimax-m3)" = '120' ]
CODEX_MINIMAX_RUN_TIMEOUT_SEC=600
[ "$(_run_cmd_limit_timeout_for_model 1800 minimax-m3)" = '600' ]
''',
            str(REPO_ROOT),
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

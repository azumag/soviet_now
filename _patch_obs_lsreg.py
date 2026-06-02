#!/usr/bin/env python3
"""Apply two patches:

Patch 1: Add file-based observability logging to prelaunch_candidate_chrome_with_retry
         (option 1 from the user's "1,3" selection). Writes events to
         tmp/wildcard_parallel/prelaunch_retry.log so the operator can verify
         the wrapper actually fired even when Python's stderr is captured
         into a shell variable and discarded on successful trials.

Patch 3: Add WILDCARD_PARALLEL_LSREGISTER_PRE_KILL gate + helper in
         eloop_improve.sh that runs `lsregister -kill -r -domain local -domain user`
         right before each wildcard_parallel trial launches. Off by default
         (gated by WILDCARD_PARALLEL_LSREGISTER_PRE_KILL=1) for safety --
         the command has whole-machine side effects (Finder/Dock freeze ~5-30s).
"""

import sys
from pathlib import Path

REPO = Path("/Users/azumag/azumag/work/soren")
PY_TARGET = REPO / "wildcard_parallel.py"
SH_TARGET = REPO / "eloop_improve.sh"

# ===== Patch 1: add log lock + helper + wire up calls =====

# 1a. Add the log lock right after _OBS_SOURCE_LOCK
OLD_LOCK_BLOCK = """# Serialise all OBS SetInputSettings calls from parallel slot threads.
# mac-capture (SCK) crashes with a double-free / heap corruption when two
# threads call obs_source_update concurrently — even on different sources —
# because OBS's internal timer threads race with the WebSocket handler thread
# during SCStream teardown/recreate.  A single mutex + 3s settle delay
# eliminates the race window.  (Root cause confirmed from OBS crash report
# OBS-2026-06-01-132749.ips: Thread 84 abort in obs_source_update.)
_OBS_SOURCE_LOCK = Lock()


def _spawn_with_launch_stagger(spawn_fn):"""

NEW_LOCK_BLOCK = """# Serialise all OBS SetInputSettings calls from parallel slot threads.
# mac-capture (SCK) crashes with a double-free / heap corruption when two
# threads call obs_source_update concurrently — even on different sources —
# because OBS's internal timer threads race with the WebSocket handler thread
# during SCStream teardown/recreate.  A single mutex + 3s settle delay
# eliminates the race window.  (Root cause confirmed from OBS crash report
# OBS-2026-06-01-132749.ips: Thread 84 abort in obs_source_update.)
_OBS_SOURCE_LOCK = Lock()
# Serialise writes to the prelaunch event log file. The wrapper is called from
# multiple worker threads concurrently, and a single open(...).write() in
# Python can interleave lines if two threads hit the same file at the same
# instant. Holding this lock keeps each log line atomic.
_PRELAUNCH_LOG_LOCK = Lock()


def _spawn_with_launch_stagger(spawn_fn):"""

# 1b. Add the helper function before prelaunch_candidate_chrome_with_retry
OLD_WRAPPER = '''def prelaunch_candidate_chrome_with_retry(app_path: str, executable_path: str, profile_dir: str, cdp_port: int) -> bool:
    """Thin retry wrapper around prelaunch_candidate_chrome (2026-06-01).'''

NEW_WRAPPER = '''def _log_prelaunch_event(cdp_port: int, attempt: int, max_attempts: int, outcome: str, extra: str = "") -> None:
    """Write a prelaunch event to a dedicated log file (and stderr) (2026-06-01).

    Why this exists: eloop_improve.sh captures the Python process's stderr
    into a shell variable (`wildcard_parallel_result=$(python3 ... 2>&1)`)
    and only logs the first 500 chars on failure (rc != 0). On a successful
    trial the stderr is discarded, so we cannot tell from the daemon log
    whether the EXC_CRASH recovery wrapper actually fired. This helper writes
    each event to a dedicated file that the operator can `tail -f` regardless
    of the trial outcome.

    Env vars:
      WILDCARD_PARALLEL_PRELAUNCH_LOG_FILE  -- path to the log file
        (default: tmp/wildcard_parallel/prelaunch_retry.log;
         relative paths are resolved against the current working directory,
         which is the project root when invoked from eloop_improve.sh)
      WILDCARD_PARALLEL_PRELAUNCH_LOG_DISABLE=1 -- skip the file write
        (stderr output still happens so this stays useful for one-off runs)
    """
    if os.getenv("WILDCARD_PARALLEL_PRELAUNCH_LOG_DISABLE") == "1":
        return
    log_path = os.getenv("WILDCARD_PARALLEL_PRELAUNCH_LOG_FILE", "tmp/wildcard_parallel/prelaunch_retry.log")
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    line = timestamp + " [prelaunch] cdp_port=" + str(cdp_port) + " attempt=" + str(attempt) + "/" + str(max_attempts) + " outcome=" + outcome
    if extra:
        line += " " + extra
    try:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
    except Exception:
        pass
    try:
        with _PRELAUNCH_LOG_LOCK:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, file=sys.stderr)
    except Exception:
        pass


def prelaunch_candidate_chrome_with_retry(app_path: str, executable_path: str, profile_dir: str, cdp_port: int) -> bool:
    """Thin retry wrapper around prelaunch_candidate_chrome (2026-06-01).'''

# 1c. Replace the body of the for-loop to log launch start, success, failure, and final outcome
OLD_FOR_BODY = """    for prelaunch_attempt in range(1, max_prelaunch_attempts + 1):
        if prelaunch_candidate_chrome(app_path, executable_path, profile_dir, cdp_port):
            return True
        if prelaunch_attempt >= max_prelaunch_attempts:
            return False
        # CDP never opened: clean up any dead/half-started Chrome for this slot's
        # profile and free the CDP port so the next open() can bind it. Both
        # helpers are best-effort; if they fail we still proceed to the retry --
        # the new open() will fail loudly if the port is held, which is fine.
        try:
            cleanup_chrome_profile_processes(profile_dir, cdp_port)
        except Exception:
            pass
        try:
            cleanup_wildcard_server_ports([cdp_port])
        except Exception:
            pass
        try:
            print(
                "[wildcard_parallel] prelaunch retry: cdp_port=" + str(cdp_port)
                + " attempt=" + str(prelaunch_attempt) + "/" + str(max_prelaunch_attempts)
                + " CDP did not open; cleaning up and backing off " + str(prelaunch_backoff) + "s",
                file=sys.stderr,
            )
        except Exception:
            pass
        if prelaunch_backoff > 0:
            time.sleep(prelaunch_backoff)
    return False
"""

NEW_FOR_BODY = """    for prelaunch_attempt in range(1, max_prelaunch_attempts + 1):
        _log_prelaunch_event(cdp_port, prelaunch_attempt, max_prelaunch_attempts, "launching", "app_path=" + ("set" if app_path else "unset"))
        if prelaunch_candidate_chrome(app_path, executable_path, profile_dir, cdp_port):
            _log_prelaunch_event(cdp_port, prelaunch_attempt, max_prelaunch_attempts, "success", "CDP opened within 8s")
            return True
        _log_prelaunch_event(cdp_port, prelaunch_attempt, max_prelaunch_attempts, "failed_cdp_not_up", "CDP did not open within 8s window")
        if prelaunch_attempt >= max_prelaunch_attempts:
            _log_prelaunch_event(cdp_port, prelaunch_attempt, max_prelaunch_attempts, "giving_up", "all attempts exhausted; slot-level retry will take over")
            return False
        # CDP never opened: clean up any dead/half-started Chrome for this slot's
        # profile and free the CDP port so the next open() can bind it. Both
        # helpers are best-effort; if they fail we still proceed to the retry --
        # the new open() will fail loudly if the port is held, which is fine.
        try:
            cleanup_chrome_profile_processes(profile_dir, cdp_port)
        except Exception:
            pass
        try:
            cleanup_wildcard_server_ports([cdp_port])
        except Exception:
            pass
        _log_prelaunch_event(cdp_port, prelaunch_attempt, max_prelaunch_attempts, "retrying", "cleanup done; backoff=" + str(prelaunch_backoff) + "s")
        if prelaunch_backoff > 0:
            time.sleep(prelaunch_backoff)
    return False
"""

# ===== Patch 3: add lsregister pre-kill helper + call sites =====

# 3a. Add the helper function after _wildcard_parallel_cleanup_stale
# The cleanup_stale function spans roughly lines 317-335. We need to find
# the end of the function (its closing brace) and insert after it.
# Look for the function definition and its end pattern.

OLD_CLEANUP_END = """_wildcard_parallel_cleanup_stale() {
	local jobs="${1:-${WILDCARD_PARALLEL_JOBS:-6}}"
	local serve_base="${2:-${WILDCARD_PARALLEL_SERVE_BASE_PORT:-18080}}"
	local cdp_base="${3:-${WILDCARD_PARALLEL_CDP_BASE_PORT:-19320}}"
	case "$jobs" in ''|*[!0-9]*) jobs=6 ;; esac
	case "$serve_base" in ''|*[!0-9]*) serve_base=18080 ;; esac
	case "$cdp_base" in ''|*[!0-9]*) cdp_base=19320 ;; esac
	python3 wildcard_parallel.py --cleanup-stale --cleanup-sessions \\
		--jobs "$jobs" \\
		--session-root "${WILDCARD_PARALLEL_WORK_DIR:-tmp/wildcard_parallel}" \\
		--status-file "${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}" \\
		--html-file "${WILDCARD_PARALLEL_HTML_FILE:-tmp/state/wildcard_parallel_overlay.html}" \\
		--serve-base-port "$serve_base" \\
		--cdp-base-port "$cdp_base"
}

_wildcard_parallel_prewrite_status() {"""

NEW_CLEANUP_END = """_wildcard_parallel_cleanup_stale() {
	local jobs="${1:-${WILDCARD_PARALLEL_JOBS:-6}}"
	local serve_base="${2:-${WILDCARD_PARALLEL_SERVE_BASE_PORT:-18080}}"
	local cdp_base="${3:-${WILDCARD_PARALLEL_CDP_BASE_PORT:-19320}}"
	case "$jobs" in ''|*[!0-9]*) jobs=6 ;; esac
	case "$serve_base" in ''|*[!0-9]*) serve_base=18080 ;; esac
	case "$cdp_base" in ''|*[!0-9]*) cdp_base=19320 ;; esac
	python3 wildcard_parallel.py --cleanup-stale --cleanup-sessions \\
		--jobs "$jobs" \\
		--session-root "${WILDCARD_PARALLEL_WORK_DIR:-tmp/wildcard_parallel}" \\
		--status-file "${WILDCARD_PARALLEL_STATUS_FILE:-tmp/state/wildcard_parallel_status.json}" \\
		--html-file "${WILDCARD_PARALLEL_HTML_FILE:-tmp/state/wildcard_parallel_overlay.html}" \\
		--serve-base-port "$serve_base" \\
		--cdp-base-port "$cdp_base"
}

# Reset the macOS LaunchServices database to clear Chrome registration race
# conditions that manifest as EXC_CRASH in +[NSApplication sharedApplication]
# -> ___RegisterApplication_block_invoke (crashes E30E23F6 16:45:14 and
# 92E9181B 18:01:49 -- both Chrome for Testing 145.0.7632.6 on macOS 26.5).
# Off by default; enable by exporting WILDCARD_PARALLEL_LSREGISTER_PRE_KILL=1
# before the trial begins. The command has whole-machine side effects (Finder
# and Dock briefly freeze while the DB is rebuilt, ~5-30s) and affects every
# LaunchServices-aware app on the machine, not just this trial -- only enable
# when the recurring crash is the bigger problem than the brief UI freeze.
_wildcard_parallel_lsregister_pre_kill() {
	[ "${WILDCARD_PARALLEL_LSREGISTER_PRE_KILL:-0}" = "1" ] || return 0
	local lsreg="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
	if [ ! -x "$lsreg" ]; then
		log "[WILDCARD-LSREGISTER] lsregister not found at $lsreg -- skip"
		return 0
	fi
	log "[WILDCARD-LSREGISTER] rebuilding LaunchServices DB (-kill -r local+user) -- may freeze Finder/Dock for 5-30s"
	local started wall
	started=$(date +%s)
	if "$lsreg" -kill -r -domain local -domain user >/dev/null 2>&1; then
		wall=$(( $(date +%s) - started ))
		log "[WILDCARD-LSREGISTER] OK (took ${wall}s)"
	else
		local rc=$?
		wall=$(( $(date +%s) - started ))
		log "[WILDCARD-LSREGISTER] FAILED rc=$rc after ${wall}s -- trial will proceed anyway"
	fi
}

_wildcard_parallel_prewrite_status() {"""

# 3b. Add the call before the post-improve param parallel trial
OLD_POST_TRIAL_START = """	_post_improve_param_parallel_heartbeat_start
	set +e
	result=$(WILDCARD_PARALLEL_OVERLAY_TITLE="POST-IMPROVE PARAM TUNING" python3 wildcard_parallel.py \\"""

NEW_POST_TRIAL_START = """	_post_improve_param_parallel_heartbeat_start
	_wildcard_parallel_lsregister_pre_kill
	set +e
	result=$(WILDCARD_PARALLEL_OVERLAY_TITLE="POST-IMPROVE PARAM TUNING" python3 wildcard_parallel.py \\"""

# 3c. Add the call before the main trial
OLD_MAIN_TRIAL_START = """		wildcard_random_count_arg="--random-count"
		[ "${WILDCARD_PERTURB_RANDOM_COUNT:-1}" = "1" ] || wildcard_random_count_arg="--no-random-count"
		set +e
		wildcard_parallel_result=$(python3 wildcard_parallel.py \\"""

NEW_MAIN_TRIAL_START = """		wildcard_random_count_arg="--random-count"
		[ "${WILDCARD_PERTURB_RANDOM_COUNT:-1}" = "1" ] || wildcard_random_count_arg="--no-random-count"
		_wildcard_parallel_lsregister_pre_kill
		set +e
		wildcard_parallel_result=$(python3 wildcard_parallel.py \\"""

# ===== Apply =====

text = PY_TARGET.read_text(encoding="utf-8")
for label, old, new in (
    ("py-lock-block", OLD_LOCK_BLOCK, NEW_LOCK_BLOCK),
    ("py-wrapper", OLD_WRAPPER, NEW_WRAPPER),
    ("py-for-body", OLD_FOR_BODY, NEW_FOR_BODY),
):
    if old not in text:
        print(f"ERROR: Python {label} block not found verbatim. Aborting.")
        sys.exit(1)
    if text.count(old) != 1:
        print(
            f"ERROR: Python {label} block found {text.count(old)} times; expected 1. Aborting."
        )
        sys.exit(1)
text = text.replace(OLD_LOCK_BLOCK, NEW_LOCK_BLOCK)
text = text.replace(OLD_WRAPPER, NEW_WRAPPER)
text = text.replace(OLD_FOR_BODY, NEW_FOR_BODY)
PY_TARGET.write_text(text, encoding="utf-8")
print(f"OK: patched {PY_TARGET.name} (added log lock + helper + log calls).")

text = SH_TARGET.read_text(encoding="utf-8")
for label, old, new in (
    ("sh-helper", OLD_CLEANUP_END, NEW_CLEANUP_END),
    ("sh-post-trial", OLD_POST_TRIAL_START, NEW_POST_TRIAL_START),
    ("sh-main-trial", OLD_MAIN_TRIAL_START, NEW_MAIN_TRIAL_START),
):
    if old not in text:
        print(f"ERROR: Shell {label} block not found verbatim. Aborting.")
        sys.exit(1)
    if text.count(old) != 1:
        print(
            f"ERROR: Shell {label} block found {text.count(old)} times; expected 1. Aborting."
        )
        sys.exit(1)
text = text.replace(OLD_CLEANUP_END, NEW_CLEANUP_END)
text = text.replace(OLD_POST_TRIAL_START, NEW_POST_TRIAL_START)
text = text.replace(OLD_MAIN_TRIAL_START, NEW_MAIN_TRIAL_START)
SH_TARGET.write_text(text, encoding="utf-8")
print(f"OK: patched {SH_TARGET.name} (added lsregister helper + 2 call sites).")

#!/usr/bin/env python3
"""Safely remove dead allowlisted pidfiles before runtime start.

Two reviewed allowlists converge at the legitimate service-start boundary
(systemd ExecStartPre -> wait_soren_runtime_prereqs.sh):

* the three overlay watcher pidfiles, whose worker names can leave the
  supervisor's managed set after a unified/legacy config switch (soviet_now
  #435), and
* the legacy ``soren_loop.manual`` auxiliary pidfile, which has no producer in
  reviewed code and is only ever observed as dead stale metadata in read-only
  diagnostics (docich #416).

Both use the identical fail-closed envelope: the helper never signals
processes and refuses unsafe state-directory indirection, symlinks,
hardlinks, foreign-owned files, malformed PIDs, or a PID that is still alive
(including a PID reused by another live process).  An operator pause marker
for the auxiliary name suspends only its own convergence: that pidfile is then
operator-owned state and is left untouched.
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import stat
from pathlib import Path
from typing import Final


PIDFILE_NAMES: Final[tuple[str, ...]] = (
    "status_overlay_watch.pid",
    "show_status_overlay_watch.pid",
    "soren_overlay_watch.pid",
)
# Legacy auxiliary pidfile with no producer in any reviewed code or history
# (verified: no committed soviet_now/docich path writes this name).  Its
# recorded PID has been dead in every observed read-only diagnostic, so it is
# pure stale metadata (docich #416).
MANUAL_LOOP_PIDFILE: Final[str] = "soren_loop.manual.pid"
# An operator-owned pause marker for the same auxiliary name suspends
# convergence; the pidfile pair is then deliberate operator state.
MANUAL_LOOP_PAUSE_MARKER: Final[str] = "soren_loop.manual.paused"
MAX_PIDFILE_BYTES: Final[int] = 64


def _pid_is_alive(pid: int) -> bool:
    if pid <= 1:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process we cannot signal still exists. Fail closed.
        return True
    except OSError:
        # Unknown kernel/process state: never unlink on uncertainty.
        return True
    return True


def _safe_open_pidfile(dir_fd: int, name: str, expected_uid: int) -> tuple[int, os.stat_result] | None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EACCES, errno.EPERM}:
            return None
        raise

    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(fd)
            return None
        if info.st_uid != expected_uid or info.st_nlink != 1:
            os.close(fd)
            return None
        # A pidfile should never be group/world writable. Treat such a file as
        # untrusted rather than normalizing its permissions in an ops helper.
        if stat.S_IMODE(info.st_mode) & 0o022:
            os.close(fd)
            return None
        return fd, info
    except Exception:
        try:
            os.close(fd)
        finally:
            raise


def _read_pid(fd: int) -> int | None:
    data = os.read(fd, MAX_PIDFILE_BYTES + 1)
    if len(data) > MAX_PIDFILE_BYTES:
        return None
    try:
        text = data.decode("ascii").strip()
    except UnicodeDecodeError:
        return None
    if not text.isdigit():
        return None
    try:
        pid = int(text, 10)
    except ValueError:
        return None
    return pid if 1 < pid <= 2_147_483_647 else None


def _same_inode(current: os.stat_result, opened: os.stat_result, expected_uid: int) -> bool:
    return (
        stat.S_ISREG(current.st_mode)
        and current.st_dev == opened.st_dev
        and current.st_ino == opened.st_ino
        and current.st_uid == expected_uid
        and current.st_nlink == 1
        and not (stat.S_IMODE(current.st_mode) & 0o022)
    )


def _safe_open_state_dir(state_dir: Path) -> int | None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(state_dir, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR, errno.EACCES, errno.EPERM}:
            return -1
        raise

    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        return -1
    return fd


def cleanup_overlay_pidfiles(root: Path, *, expected_uid: int | None = None) -> dict[str, int]:
    """Remove only dead, owned, exact overlay pidfiles below *root*.

    Returns fixed counters only; paths, PIDs and file contents are deliberately
    excluded so callers can log the result without disclosing runtime details.
    """

    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    state_dir = root / "tmp" / "state"
    result = {"removed": 0, "skipped_alive": 0, "skipped_unsafe": 0, "missing": 0}

    dir_fd = _safe_open_state_dir(state_dir)
    if dir_fd is None:
        result["missing"] = len(PIDFILE_NAMES)
        return result
    if dir_fd < 0:
        result["skipped_unsafe"] = len(PIDFILE_NAMES)
        return result

    try:
        for name in PIDFILE_NAMES:
            opened = _safe_open_pidfile(dir_fd, name, expected_uid)
            if opened is None:
                try:
                    os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
                except FileNotFoundError:
                    result["missing"] += 1
                else:
                    result["skipped_unsafe"] += 1
                continue

            fd, opened_info = opened
            try:
                pid = _read_pid(fd)
            finally:
                os.close(fd)
            if pid is None:
                result["skipped_unsafe"] += 1
                continue
            if _pid_is_alive(pid):
                result["skipped_alive"] += 1
                continue

            # Re-stat the directory entry after checking liveness. If the file
            # was replaced while we inspected it, leave the replacement alone.
            try:
                current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                result["missing"] += 1
                continue
            if not _same_inode(current, opened_info, expected_uid):
                result["skipped_unsafe"] += 1
                continue

            try:
                os.unlink(name, dir_fd=dir_fd)
            except FileNotFoundError:
                result["missing"] += 1
            else:
                result["removed"] += 1
    finally:
        os.close(dir_fd)

    return result


def cleanup_manual_loop_pidfile(root: Path, *, expected_uid: int | None = None) -> dict[str, int]:
    """Remove only the dead, owned, legacy manual-loop pidfile below *root*.

    The auxiliary ``soren_loop.manual`` pid file has no producer in reviewed
    code or history; its recorded PID has been dead in every observed
    read-only diagnostic.  It converges at the same legitimate service-start
    boundary as the overlay pidfiles, under the identical fail-closed checks.
    An operator pause marker for the auxiliary name suspends convergence
    entirely: the pidfile is then operator-owned state and neither file is
    modified.

    Returns fixed counters only; paths, PIDs and file contents are deliberately
    excluded so callers can log the result without disclosing runtime details.
    """

    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    state_dir = root / "tmp" / "state"
    result = {
        "removed": 0,
        "skipped_alive": 0,
        "skipped_unsafe": 0,
        "skipped_paused": 0,
        "missing": 0,
    }

    dir_fd = _safe_open_state_dir(state_dir)
    if dir_fd is None:
        result["missing"] = 1
        return result
    if dir_fd < 0:
        result["skipped_unsafe"] = 1
        return result

    try:
        # Operator-owned state wins before any liveness or ownership check:
        # a pause marker (of any file type) means the name slot is deliberate
        # operator state and the pidfile must survive convergence.
        try:
            os.stat(MANUAL_LOOP_PAUSE_MARKER, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError:
            result["skipped_unsafe"] = 1
            return result
        else:
            result["skipped_paused"] = 1
            return result

        opened = _safe_open_pidfile(dir_fd, MANUAL_LOOP_PIDFILE, expected_uid)
        if opened is None:
            try:
                os.stat(MANUAL_LOOP_PIDFILE, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                result["missing"] += 1
            else:
                result["skipped_unsafe"] += 1
            return result

        fd, opened_info = opened
        try:
            pid = _read_pid(fd)
        finally:
            os.close(fd)
        if pid is None:
            result["skipped_unsafe"] += 1
            return result
        if _pid_is_alive(pid):
            result["skipped_alive"] += 1
            return result

        # Re-stat the directory entry after checking liveness. If the file
        # was replaced while we inspected it, leave the replacement alone.
        try:
            current = os.stat(MANUAL_LOOP_PIDFILE, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            result["missing"] += 1
            return result
        if not _same_inode(current, opened_info, expected_uid):
            result["skipped_unsafe"] += 1
            return result

        try:
            os.unlink(MANUAL_LOOP_PIDFILE, dir_fd=dir_fd)
        except FileNotFoundError:
            result["missing"] += 1
        else:
            result["removed"] += 1
    finally:
        os.close(dir_fd)

    return result


def _merged_counts(*results: dict[str, int]) -> dict[str, int]:
    merged: dict[str, int] = {}
    for result in results:
        for key, value in result.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Soren repository root (defaults to the parent of lib/)",
    )
    args = parser.parse_args()
    root = args.root.resolve()
    result = _merged_counts(
        cleanup_overlay_pidfiles(root),
        cleanup_manual_loop_pidfile(root),
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

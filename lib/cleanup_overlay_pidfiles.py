#!/usr/bin/env python3
"""Safely remove dead overlay watcher pidfiles before runtime start.

This helper is intentionally narrow: it only knows the three reviewed overlay
watcher pidfiles. It never signals processes and refuses unsafe state-directory
indirection, symlinks, hardlinks, foreign-owned files, malformed PIDs, or a PID
that is still alive. It is called from the existing systemd ExecStartPre path,
so old overlay-mode metadata can converge without reinstalling the unit.
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Soren repository root (defaults to the parent of lib/)",
    )
    args = parser.parse_args()
    result = cleanup_overlay_pidfiles(args.root.resolve())
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

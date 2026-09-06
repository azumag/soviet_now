#!/usr/bin/env python3
"""Serialize spawn-mutex operations with a policy transaction's kernel lease.

The adjacent .lease file is permanent: never unlink/replace it while workers
are running. The short-lived directory keeps the normal spawner's PID/TTL
contract; flock protects policy updates independently of that TTL.
"""
from __future__ import annotations
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import stat
import sys
import time

PROTOCOL = 'kernel-lease-v1'


def checked(path: Path) -> Path:
    path = path.absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink in spawn guard path')
    return path


@contextmanager
def lease(guard: Path):
    path = checked(guard.with_name(guard.name + '.lease'))
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ValueError('spawn lease must be an unlinked-to regular file')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        now = path.stat()
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            raise ValueError('spawn lease inode changed')
        yield
    finally:
        # Python descriptors are non-inheritable. Close also releases on crash.
        os.close(fd)


def owner_of(guard: Path) -> str:
    path = checked(guard / 'owner')
    if not path.exists():
        return ''
    if not path.is_file() or path.stat().st_size > 64:
        raise ValueError('invalid spawn owner file')
    return path.read_text(encoding='ascii').strip()


def alive(owner: str) -> bool:
    if not owner.isdecimal() or int(owner) <= 0:
        return False
    try:
        os.kill(int(owner), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Uninspectable is not permission to steal a live lock.


def identity(guard: Path):
    st = checked(guard).stat()
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError('spawn guard must be a directory')
    return st.st_dev, st.st_ino


def remove_owned(guard: Path, expected_identity, owner: str) -> None:
    if identity(guard) != expected_identity or owner_of(guard) != owner:
        raise ValueError('spawn owner changed')
    # Do not recursively remove unrelated state inside an unexpected guard.
    if any(p.name != 'owner' for p in guard.iterdir()):
        raise ValueError('unexpected spawn guard contents')
    (guard / 'owner').unlink(missing_ok=True)
    guard.rmdir()


def acquire(guard: Path, owner: str, ttl: float) -> bool:
    guard = checked(guard)
    with lease(guard):
        try:
            guard.mkdir(mode=0o700)
        except FileExistsError:
            old_identity = identity(guard)
            old_owner = owner_of(guard)
            age = time.time() - guard.stat().st_mtime
            if not ((old_owner and not alive(old_owner)) or age >= ttl):
                return False
            remove_owned(guard, old_identity, old_owner)
            guard.mkdir(mode=0o700)
        (guard / 'owner').write_text(owner + '\n', encoding='ascii')
        return True


def release(guard: Path, owner: str) -> bool:
    guard = checked(guard)
    with lease(guard):
        if not guard.exists():
            return True
        old_identity = identity(guard)
        if owner_of(guard) == owner:
            remove_owned(guard, old_identity, owner)
        return True


def main() -> int:
    if len(sys.argv) not in (4, 5):
        return 1
    action, directory, owner = sys.argv[1:4]
    if action not in ('acquire', 'release') or not owner.isdecimal() or int(owner) <= 0:
        return 1
    try:
        if action == 'acquire':
            ttl = int(sys.argv[4]) if len(sys.argv) == 5 else 90
            if ttl < 0:
                return 1
            ok = acquire(Path(directory), owner, ttl)
        else:
            ok = release(Path(directory), owner)
        return 0 if ok else 1
    except (OSError, ValueError) as exc:
        print(f'[IMPROVE] spawn guard unavailable: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

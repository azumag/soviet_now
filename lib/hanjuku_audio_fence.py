"""Fail-closed playback fence for Hanjuku commentary; never accepts runtime paths."""
from __future__ import annotations

import contextlib
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

KEYS = ('game', 'runtime_id', 'generation', 'lease_id')


def validate(value):
    if not isinstance(value, dict) or set(value) != {*KEYS, 'expires_at'}:
        raise ValueError('invalid fence keys')
    if value['game'] != 'hanjuku-hero':
        raise ValueError('unsupported game')
    if not isinstance(value['runtime_id'], str) or not re.fullmatch(r'g[1-9][0-9]*-[a-f0-9]{6,32}', value['runtime_id']):
        raise ValueError('invalid runtime id')
    if type(value['generation']) is not int or value['generation'] < 1:
        raise ValueError('invalid generation')
    if not isinstance(value['lease_id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value['lease_id']):
        raise ValueError('invalid lease')
    expiry = value['expires_at']
    if type(expiry) not in (float, int) or not math.isfinite(expiry) or not time.time() < expiry <= time.time() + 60:
        raise ValueError('expired or excessive lifetime')
    return value


def read(path):
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError('unsafe record')
    value = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('invalid record')
    return value


def sidecar(target):
    path = Path(target)
    if path.suffix in ('.txt', '.playing'):
        path = path.with_suffix('')
    return Path(str(path) + '.runtime_fence.json')


def required(target):
    return '_hanjuku_commentary.' in Path(target).name or sidecar(target).exists() or sidecar(target).is_symlink()


def active(canonical, value):
    validate(value)
    state = read(canonical)
    runtime = state.get('active')
    if state.get('phase') != 'ready' or not isinstance(runtime, dict):
        raise ValueError('not active')
    if any(type(runtime.get(key)) is not type(value[key]) or runtime.get(key) != value[key] for key in KEYS):
        raise ValueError('fence lost')
    root = canonical.parent / 'runtimes'
    directory = root / value['runtime_id']
    if root.is_symlink() or directory.is_symlink():
        raise ValueError('unsafe runtime')
    run = read(directory / 'hanjuku_run.json')
    if any(type(run.get(key)) is not type(value[key]) or run.get(key) != value[key] for key in KEYS):
        raise ValueError('run identity lost')
    if run.get('terminal_reason') or run.get('terminal_candidate') or run.get('playing') is not True:
        raise ValueError('terminal or inactive run')


@contextlib.contextmanager
def locked(canonical):
    path = canonical.parent / 'locks' / 'game-switch.lock'
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('unsafe lock')
    # Read-only existing lock: an absent canonical control plane fails closed.
    with path.open('rb') as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
        yield


def check(canonical, target):
    if not required(target):
        return
    with locked(canonical):
        active(canonical, read(sidecar(target)))


def play(canonical, target, command):
    """Launch atomically with the fence; stop only this owned child on fence loss."""
    value = read(sidecar(target))
    # Shell playback wrappers may ignore TERM; our child must not inherit that.
    def interrupted(_signum, _frame):
        raise InterruptedError('playback interrupted')
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    child = None
    try:
        with locked(canonical):
            active(canonical, value)
            # Defer signals across Popen assignment so finally always knows
            # the child it owns, including interruption during process spawn.
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
            try:
                child = subprocess.Popen(command, start_new_session=True,
                                         preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask))
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        while child.poll() is None:
            time.sleep(.1)
            # Do not hold the switch lock while playback is in progress. A
            # transition/terminal/expiry drops only this narration's player.
            with locked(canonical):
                active(canonical, value)
        return child.returncode
    finally:
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()


def main():
    try:
        operation = sys.argv[1]
        if operation == 'encode':
            value = validate(json.loads(sys.argv[2]))
            print(json.dumps(value, separators=(',', ':'), sort_keys=True))
            return 0
        canonical, target = Path(sys.argv[2]), sys.argv[3]
        if operation == 'check':
            check(canonical, target)
            return 0
        if operation == 'play' and sys.argv[4] == '--':
            return play(canonical, target, sys.argv[5:])
    except (OSError, ValueError, TypeError, KeyError, IndexError):
        # Never echo narration, runtime identifiers, or arbitrary input.
        return 75
    return 75


if __name__ == '__main__':
    raise SystemExit(main())

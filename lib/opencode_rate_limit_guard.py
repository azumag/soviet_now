#!/usr/bin/env python3
"""Expose OpenCode's hidden retry to the caller without forwarding debug logs.

The CLI can sleep until tomorrow after a 429 while emitting no normal stderr.
Run it with --print-logs, detect only error records, and stop its owned process
 group. Model stdout is never inspected as a control signal.
"""
import os
import re
import selectors
import signal
import subprocess
import sys
import time

RATE_LIMIT = re.compile(r'\b429\b|rate[ _-]*limit|too many requests|FreeUsageLimitError|free usage exceeded', re.I)


def run(timeout, command):
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=None,
                               stderr=subprocess.PIPE, start_new_session=True)
    selector = selectors.DefaultSelector()
    selector.register(process.stderr, selectors.EVENT_READ)
    pending = b''
    deadline = time.monotonic() + timeout
    interrupted = [False]
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT):
        previous[sig] = signal.signal(sig, lambda *_: interrupted.__setitem__(0, True))

    def line_received(raw):
        line = raw.decode('utf-8', errors='replace')
        if ('level=ERROR' in line or 'level=error' in line or line.lstrip().startswith('Error:')) and RATE_LIMIT.search(line):
            print('OpenCode upstream rate limit (429); stopped internal retry', file=sys.stderr)
            return True
        # Structured debug records may include prompts or credentials: never forward.
        if not re.search(r'\b(?:level|service|timestamp)=', line):
            sys.stderr.write(line)
        return False

    try:
        while selector.get_map():
            if interrupted[0]:
                return 143
            if time.monotonic() >= deadline:
                return 124
            for key, _ in selector.select(min(0.1, max(0, deadline-time.monotonic()))):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    if pending and line_received(pending):
                        return 79
                    selector.unregister(key.fileobj)
                    break
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    if line_received(line + b'\n'):
                        return 79
                # Bound malformed log lines. Discard oversized records, not stdout.
                if len(pending) > 262144:
                    pending = b''
        while process.poll() is None:
            if interrupted[0]:
                return 143
            if time.monotonic() >= deadline:
                return 124
            time.sleep(0.05)
        return process.returncode if process.returncode >= 0 else 128-process.returncode
    finally:
        selector.close()
        # Also stop grandchildren after the CLI exits or closes its stderr.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stderr.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == '__main__':
    sys.exit(run(float(sys.argv[1]), sys.argv[2:]))

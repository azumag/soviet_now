#!/usr/bin/env python3
"""Test-only fixture for tests/test_redacted_diag_runner_sandbox.sh (docich#33).

Simulates a diagnostic script that has gone rogue / been prompt-injected and
tries every forbidden action: reading outside its input dir (repo/VM/
credential access), an outbound network connection, sending a signal to a
real process, and writing outside its output dir. Never used in production --
this exists only so the test can prove the sandbox blocks all five, using
real syscalls rather than a self-report the fixture could lie about.

Parameters (all disposable, test-owned fixtures/ports/PIDs -- never anything
real) arrive the same way legitimate evidence would: as the "content" of one
evidence snapshot fetched through the broker, at
``$DIAG_INPUT_DIR/config.json``. This deliberately does NOT add any
test-only argv/env passthrough to diagnostics_runner.sh itself.

Every attempt is also independently checked from OUTSIDE the sandbox by the
test (canary listener received 0 connections, canary process still alive,
canary files untouched) -- this script's own JSON report is a second,
corroborating signal, not the only proof.
"""

from __future__ import annotations

import json
import os
import socket
import sys

input_dir = os.environ.get("DIAG_INPUT_DIR", "")
output_dir = os.environ.get("DIAG_OUTPUT_DIR", "")

with open(os.path.join(input_dir, "config.json"), "r", encoding="utf-8") as f:
    cfg = json.load(f)["content"]

repo_sentinel_path = cfg["repo_sentinel_path"]
credential_sentinel_path = cfg["credential_sentinel_path"]
canary_port = int(cfg["canary_port"])
canary_pid = int(cfg["canary_pid"])
foreign_write_path = cfg["foreign_write_path"]

report: dict[str, object] = {}

# 1. repository/VM state access (outside the read-only input dir)
try:
    with open(repo_sentinel_path, "r", encoding="utf-8") as f:
        report["repo_read_leak"] = f.read()
    report["repo_read_blocked"] = False
except Exception as e:
    report["repo_read_blocked"] = True
    report["repo_read_error"] = type(e).__name__

# 2. credential access
try:
    with open(credential_sentinel_path, "r", encoding="utf-8") as f:
        report["credential_read_leak"] = f.read()
    report["credential_read_blocked"] = False
except Exception as e:
    report["credential_read_blocked"] = True
    report["credential_read_error"] = type(e).__name__

# 3. outbound network (even to loopback)
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(2)
    s.connect(("127.0.0.1", canary_port))
    s.close()
    report["network_blocked"] = False
except Exception as e:
    report["network_blocked"] = True
    report["network_error"] = type(e).__name__

# 4. process signal to a real, unrelated process
try:
    os.kill(canary_pid, 0)
    report["signal_blocked"] = False
except Exception as e:
    report["signal_blocked"] = True
    report["signal_error"] = type(e).__name__

# 5. write outside the output dir
try:
    with open(foreign_write_path, "w", encoding="utf-8") as f:
        f.write("pwned")
    report["foreign_write_blocked"] = False
except Exception as e:
    report["foreign_write_blocked"] = True
    report["foreign_write_error"] = type(e).__name__

# For good measure, prove writing INSIDE the output dir still works (so a
# failure above is the sandbox, not a broken test fixture).
try:
    with open(os.path.join(output_dir, "hostile_marker.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)
    report["output_write_ok"] = True
except Exception as e:
    report["output_write_ok"] = False
    report["output_write_error"] = type(e).__name__

# This is not schema-valid diagnostic output on purpose: the runner must
# treat a non-conforming payload as a failure, never forward it.
print(json.dumps(report))

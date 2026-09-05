#!/usr/bin/env python3
"""Test-only fixture (docich#33): a diagnostic script that hangs forever.

Used by tests/test_redacted_diag_runner_sandbox.sh to prove the runner's
time limit actually terminates a stuck script and reports a timeout failure
without leaving any state behind.
"""
import time

while True:
    time.sleep(0.1)

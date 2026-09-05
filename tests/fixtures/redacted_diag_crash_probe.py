#!/usr/bin/env python3
"""Test-only fixture (docich#33): a diagnostic script that crashes.

Used by tests/test_redacted_diag_runner_sandbox.sh to prove a non-zero exit
becomes a failure report (never a fabricated success) without leaving any
state behind.
"""
import sys

sys.stderr.write("simulated crash in diagnostic script\n")
raise SystemExit(7)

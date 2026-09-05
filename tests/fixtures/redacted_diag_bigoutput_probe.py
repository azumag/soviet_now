#!/usr/bin/env python3
"""Test-only fixture (docich#33): a diagnostic script whose stdout hugely
exceeds any reasonable output-size limit. Used to prove the runner bounds
output size (ulimit -f on the captured stdout file) rather than accepting
and forwarding an unbounded payload.
"""
import json
import sys

sys.stdout.write(json.dumps([{"finding": "x" * 5_000_000, "evidence_ref": "chat_worker_log_tail", "confidence": "low", "recommended_action": "y"}]))

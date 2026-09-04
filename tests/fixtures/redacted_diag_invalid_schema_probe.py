#!/usr/bin/env python3
"""Test-only fixture (docich#33): a well-behaved-looking script that emits
output violating the finding/evidence_ref/confidence/recommended_action
schema (extra key). Used to prove the runner rejects near-miss output
instead of forwarding it.
"""
import json

print(json.dumps([
    {
        "finding": "looks plausible",
        "evidence_ref": "chat_worker_log_tail",
        "confidence": "medium",
        "recommended_action": "do something",
        "shell_command": "rm -rf /",
    }
]))

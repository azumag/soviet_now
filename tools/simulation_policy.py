#!/usr/bin/env python3
"""Persistent, local JSON-lines policy process for simulation_lab.mjs."""
import contextlib
import importlib.util
import json
import math
import random
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.policy_bundle import bundle


def load_policy(path):
    spec = importlib.util.spec_from_file_location("simulation_candidate", path)
    policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(policy)
    return policy


def decide(policy, state, runner):
    t0 = time.perf_counter()
    analysis = runner.build_analysis(state)
    if not analysis or not analysis.get("results"):
        raise ValueError("no analysis candidates")
    runner.enrich_game_state_deadline_fields(state, analysis)
    t1 = time.perf_counter()
    decision = policy.decide(state, analysis)
    if not isinstance(decision, dict) or isinstance(decision.get("x"), bool):
        raise ValueError("invalid policy decision")
    x = float(decision["x"])
    if not math.isfinite(x):
        raise ValueError("non-finite policy decision")
    decision["x"] = max(runner.GAME_X_MIN, min(runner.GAME_X_MAX, x))
    decision = runner.enforce_deadline_safety(decision, analysis, state, policy)
    decision = runner.apply_strategy_final_decision(policy, decision, analysis, state)
    decision["x"] = max(runner.GAME_X_MIN, min(runner.GAME_X_MAX, float(decision["x"])))
    t2 = time.perf_counter()
    return {"decision": decision, "analysis_ms": (t1 - t0) * 1000,
            "decision_ms": (t2 - t1) * 1000}


def main():
    # No .env loader, runtime loop, notifications, command files or remote services.
    random.seed(int(sys.argv[2]))
    with contextlib.redirect_stdout(sys.stderr):
        import strategy_runner as runner
        runner.log = lambda *a, **k: None
        policy = load_policy(sys.argv[1])
    for line in sys.stdin:
        try:
            request = json.loads(line)
            with contextlib.redirect_stdout(sys.stderr):
                if request["op"] == "config":
                    policy_hash, parts = bundle(sys.argv[1], str(ROOT))
                    result = {"policy_bundle": policy_hash, "modes": parts["modes"],
                              "fast_drop": runner.strategy_fast_drop_deadline_contact_enabled(policy)}
                elif request["op"] == "observe":
                    state = request["state"]
                    result = {"settled": runner.is_board_settled(state),
                              "contact": runner.has_deadline_contact(state)}
                elif request["op"] == "decide":
                    result = decide(policy, request["state"], runner)
                else:
                    raise ValueError("unknown request")
            print(json.dumps(result, ensure_ascii=False, allow_nan=False), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__ + ": " + str(exc)}), flush=True)


if __name__ == "__main__":
    main()

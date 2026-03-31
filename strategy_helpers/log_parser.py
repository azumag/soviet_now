#!/usr/bin/env python3
"""Helper to parse game log JSONL for analysis."""
import json
import sys

def parse_log(path, last_n=8):
    with open(path) as f:
        lines = f.readlines()
    for line in lines[-last_n:]:
        d = json.loads(line)
        t = d["turn"]
        pc = d["piece_count"]
        my = d["max_y"]
        sd = d["score_delta"]
        rp = d["reactor_reactive_pairs"]
        mg = d["best_merge_grade"]
        ma = d.get("merge_available", False)
        dc = d.get("deadline_crossed", False)
        dpc = d.get("danger_piece_count", 0)
        lm = d.get("deadline_margin", 0)
        reason = d["decision_reason"]
        print(f"T{t:3d} pc={pc:2d} max_y={my:+.2f} delta={sd:4d} rp={rp} mg={mg} ma={ma} dc={dc} dpc={dpc} lm={lm:.1f} {reason}")

def parse_danger_turns(path, min_y=2.0):
    with open(path) as f:
        lines = f.readlines()
    count = 0
    for line in lines:
        d = json.loads(line)
        if d["max_y"] >= min_y:
            t = d["turn"]
            pc = d["piece_count"]
            my = d["max_y"]
            sd = d["score_delta"]
            rp = d["reactor_reactive_pairs"]
            mg = d["best_merge_grade"]
            ma = d.get("merge_available", False)
            dc = d.get("deadline_crossed", False)
            dpc = d.get("danger_piece_count", 0)
            lm = d.get("deadline_margin", 0)
            reason = d["decision_reason"]
            nt = d.get("next_type", 0)
            # Check for type 15 in pieces
            pieces = d.get("state_snapshot", {}).get("pieces", [])
            has_russia = any(p.get("type") == 15 for p in pieces)
            russia_tag = " R15!" if has_russia else ""
            print(f"T{t:3d} pc={pc:2d} max_y={my:+.2f} delta={sd:4d} rp={rp} mg={mg} ma={ma} dc={dc} dpc={dpc} lm={lm:.1f} nt={nt}{russia_tag} {reason}")
            count += 1
    print(f"\nTotal max_y>={min_y} turns: {count}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: log_parser.py <file.jsonl> [last_n] [min_y]")
        sys.exit(1)
    path = sys.argv[1]
    if len(sys.argv) >= 4:
        parse_danger_turns(path, float(sys.argv[3]))
    else:
        n = int(sys.argv[2]) if len(sys.argv) >= 2 else 8
        parse_log(path, n)

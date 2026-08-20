"""Read-modify-write layer for tmp/state/instadeath_monitor.json.

Phase 1 of soren-stat-gate-design.md (section B). This is the *only* I/O
layer for the instadeath monitor -- lib/eval_stats.py stays a pure-function
module with no file access. Written to be called from a single writer
(strategy/regression.sh's update_rolling_scores(), live-game path only; see
the design doc's C-1(a)/6-2 for why) so there is exactly one place that
appends to the global window and no double-counting risk.

Everything here is inert unless a caller actually invokes observe()/state()
-- importing this module does nothing on its own, and no caller does that
yet unless INSTADEATH_SPLIT_ENABLED=1 (wired in strategy/regression.sh).

CLI usage (what strategy/regression.sh actually shells out to):
    python3 lib/instadeath_monitor.py observe --file PATH --hash H --score S
        [--raw R] [--turns T] [--archive A] --cfg-json '{"dead_eval_threshold":3000,...}'
    python3 lib/instadeath_monitor.py state --file PATH
Both print exactly one line of JSON to stdout and exit 0 on success. Any
exception is caught and reported as {"error": "..."} with exit 0 (never a
non-zero exit / traceback) -- a broken monitor must never be allowed to make
the caller (update_rolling_scores, a hot path called every single game)
fail. Callers are expected to treat a missing/unparseable stdout line as
"skip the observation, keep going".
"""

import json
import os
import sys
import time

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import eval_stats  # noqa: E402


VERSION = 1

_CFG_DEFAULTS = {
    "dead_eval_threshold": 3000,
    "dead_monitor_window": 400,
    "dead_alert_window": 50,
    "dead_alert_rate": 0.10,
    "dead_quarantine_enabled": True,
    "dead_quarantine_window": 20,
    "dead_quarantine_rate": 0.30,
    "dead_quarantine_clear_window": 20,
    "dead_quarantine_clear_rate": 0.05,
    "dead_burst_ratio": 3.0,
    "dead_hard_ratio": 0.5,
    "dead_near_total_rate": 0.90,
    "dead_max_turns": 3,
    "dead_alpha": 0.01,
}


def _merged_cfg(cfg):
    merged = dict(_CFG_DEFAULTS)
    if cfg:
        merged.update(cfg)
    return merged


def _empty():
    return {
        "version": VERSION,
        "updated_at": 0,
        "window": [],
        "by_hash": {},
        "alert": {
            "active": False, "evaluated": False, "window": 0, "n": 0,
            "dead": 0, "rate": 0.0, "since": None,
        },
        "quarantine": {
            "active": False, "evaluated": False, "verdict": "INSUFFICIENT_WINDOW",
            "detail": {}, "started_at": None, "started_hash": "", "started_verdict": "",
            "cleared_at": None, "clear_rate": None, "diverted_total": 0, "history": [],
        },
        "runs": {
            "current_len": 0, "current_started_ts": None, "current_hashes": [],
            "spans_hash_change": False, "max_len": 0,
        },
        "counters": {"games_seen": 0, "dead_seen": 0, "skipped_dedup": 0},
    }


def load(path):
    """Load the monitor file. Never raises -- missing or corrupt files both
    yield a fresh empty monitor (self-healing; a broken monitor must not
    block the game loop)."""
    if not path or not os.path.exists(path):
        return _empty()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "window" not in data:
            return _empty()
        base = _empty()
        base.update(data)
        # Defensive: ensure nested dicts have all expected sub-keys even if
        # an older/partial file is loaded (forward-compatible reads).
        for key in ("alert", "quarantine", "runs", "counters"):
            merged = dict(_empty()[key])
            merged.update(data.get(key) or {})
            base[key] = merged
        if not isinstance(base.get("window"), list):
            base["window"] = []
        if not isinstance(base.get("by_hash"), dict):
            base["by_hash"] = {}
        return base
    except Exception:
        return _empty()


def _atomic_write(path, obj):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _recompute_by_hash(window):
    by_hash = {}
    for r in window:
        h = r.get("h") or ""
        entry = by_hash.setdefault(h, {"n": 0, "dead": 0, "rate": 0.0, "last_ts": 0})
        entry["n"] += 1
        if r.get("d"):
            entry["dead"] += 1
        entry["last_ts"] = max(entry["last_ts"], int(r.get("ts") or 0))
    for entry in by_hash.values():
        entry["rate"] = entry["dead"] / entry["n"] if entry["n"] else 0.0
    return by_hash


def _recompute_runs(window):
    max_len = 0
    for length in eval_stats.run_lengths([bool(r.get("d")) for r in window]):
        max_len = max(max_len, length)
    current_len = 0
    current_started_ts = None
    current_hashes = []
    seen = set()
    for r in reversed(window):
        if not r.get("d"):
            break
        current_len += 1
        current_started_ts = r.get("ts")
        h = r.get("h") or ""
        if h not in seen:
            seen.add(h)
            current_hashes.append(h)
    return {
        "current_len": current_len,
        "current_started_ts": current_started_ts,
        "current_hashes": current_hashes,
        "spans_hash_change": len(current_hashes) > 1,
        "max_len": max_len,
    }


def _recompute_alert(window, cfg):
    n = cfg["dead_alert_window"]
    tail = window[-n:] if n > 0 else []
    evaluated = len(tail) >= n and n > 0
    dead = sum(1 for r in tail if r.get("d"))
    rate = dead / len(tail) if tail else 0.0
    return {
        "window": n, "n": len(tail), "dead": dead, "rate": rate,
        "evaluated": evaluated, "active": evaluated and rate >= cfg["dead_alert_rate"],
    }


def _classify(window, cfg):
    """Returns (verdict, detail, evaluated). evaluated=False (with
    verdict="INSUFFICIENT_WINDOW") until the quarantine window has enough
    history -- this is the cold-start guard (2026-08-20 Phase 1 review,
    risk R6): without it, the very first dead game observed after this
    monitor starts existing would see n=1, rate=1.0, and could rack up 3
    votes (near_total_rate + hard_ratio + median_turns) on a single data
    point, quarantining on n=1."""
    n = cfg["dead_quarantine_window"]
    tail = window[-n:] if n > 0 else []
    if len(tail) < n:
        return ("INSUFFICIENT_WINDOW", {"rate": None}, False)

    rate = sum(1 for r in tail if r.get("d")) / len(tail)
    if rate <= cfg["dead_quarantine_rate"]:
        # At or below the design's own quarantine floor
        # (soren-stat-gate-design.md B-3: "直近20件の即死率 > 0.30", strictly
        # greater-than -- rate==0.30 itself must NOT proceed to
        # classification; 2026-08-20 Phase 1 review round 3, next-best #2
        # caught this as an off-by-one against `<` instead of `<=`).
        # Without this gate at all,
        # eval_stats.classify_instadeath()'s internal dead_alert_rate check
        # (0.10 by default) is the only floor actually enforced -- 3x more
        # sensitive than intended, and DEAD_QUARANTINE_RATE ends up read but
        # never used for anything (2026-08-20 Phase 1 review, blocking issue
        # B3; measured: 2/20 dead = rate 0.10 triggered HARNESS/quarantine
        # under the shipped defaults before this fix).
        return ("NORMAL", {"rate": rate}, True)

    cur_flags = [bool(r.get("d")) for r in tail]
    deads_with_raw = [r for r in tail if r.get("d") and r.get("raw") is not None]
    hard_ratio = None
    if deads_with_raw:
        hard_ratio = sum(1 for r in deads_with_raw if int(r["raw"]) == 0) / len(deads_with_raw)

    death_turns = sorted(
        int(r["turns"]) for r in tail if r.get("d") and r.get("turns") is not None
    )
    median_death_turns = None
    if death_turns:
        mid = len(death_turns) // 2
        if len(death_turns) % 2:
            median_death_turns = float(death_turns[mid])
        else:
            median_death_turns = (death_turns[mid - 1] + death_turns[mid]) / 2.0

    run_hashes = set()
    for r in reversed(tail):
        if not r.get("d"):
            break
        run_hashes.add(r.get("h") or "")
    spans_hash_change = len(run_hashes) > 1

    verdict, detail = eval_stats.classify_instadeath(cur_flags, None, {
        "dead_alert_rate": cfg["dead_alert_rate"],
        "dead_burst_ratio": cfg["dead_burst_ratio"],
        "dead_hard_ratio": cfg["dead_hard_ratio"],
        "dead_max_turns": cfg["dead_max_turns"],
        "dead_near_total_rate": cfg["dead_near_total_rate"],
        "dead_alpha": cfg["dead_alpha"],
        "cur_dead_hard_ratio": hard_ratio,
        "spans_hash_change": spans_hash_change,
        "median_death_turns": median_death_turns,
    })
    return (verdict, detail, True)


def _recompute(monitor, cfg):
    window = monitor["window"]
    monitor["by_hash"] = _recompute_by_hash(window)
    monitor["runs"] = _recompute_runs(window)
    monitor["alert"] = _recompute_alert(window, cfg)
    monitor["counters"]["games_seen"] = len(window)
    monitor["counters"]["dead_seen"] = sum(1 for r in window if r.get("d"))
    return monitor


def _push_history(quarantine, event, now, verdict, rate, detail):
    entry = {"event": event, "ts": now, "verdict": verdict, "rate": rate, "detail": detail}
    history = quarantine.get("history") or []
    history.append(entry)
    quarantine["history"] = history[-10:]


def _apply_quarantine_transition(monitor, cfg, now, cur_hash):
    """Clear is checked before set (in that order) so a single tick can
    never both start and immediately clear a quarantine -- see Phase 1
    review section 5-3."""
    q = monitor["quarantine"]
    window = monitor["window"]

    if q["active"]:
        clear_n = cfg["dead_quarantine_clear_window"]
        ctail = window[-clear_n:] if clear_n > 0 else []
        if len(ctail) >= clear_n and clear_n > 0:
            crate = sum(1 for r in ctail if r.get("d")) / len(ctail)
            if crate < cfg["dead_quarantine_clear_rate"]:
                q["active"] = False
                q["cleared_at"] = now
                q["clear_rate"] = crate
                _push_history(q, "clear", now, "CLEARED", crate, {})
                # Re-classify immediately so verdict/detail reflect the
                # just-cleared reality instead of the stale HARNESS reading
                # that triggered quarantine in the first place (2026-08-20
                # Phase 1 review, next-best item #5).
                verdict, detail, evaluated = _classify(window, cfg)
                q["evaluated"] = evaluated
                q["verdict"] = verdict
                q["detail"] = detail
                return "clear"
        # Still active (didn't clear this tick); fall through without
        # re-evaluating "set" logic below in the same call.
        verdict, detail, evaluated = _classify(window, cfg)
        q["evaluated"] = evaluated
        q["verdict"] = verdict
        q["detail"] = detail
        return ""

    verdict, detail, evaluated = _classify(window, cfg)
    q["evaluated"] = evaluated
    q["verdict"] = verdict
    q["detail"] = detail
    if not evaluated:
        return ""
    if cfg["dead_quarantine_enabled"] and verdict == "HARNESS":
        q["active"] = True
        q["started_at"] = now
        q["started_hash"] = cur_hash or ""
        q["started_verdict"] = verdict
        q["cleared_at"] = None
        q["clear_rate"] = None
        rate = sum(1 for r in window[-cfg["dead_quarantine_window"]:] if r.get("d")) / max(
            1, len(window[-cfg["dead_quarantine_window"]:]))
        _push_history(q, "start", now, verdict, rate, detail)
        return "start"
    return ""


def observe(path, record, cfg=None):
    """Append one game's observation to the monitor and persist it.

    `record`: {"h": strategy_hash, "s": eval_score, "raw": int|None,
    "turns": int|None, "d": 0|1, "archive": str|""}. `d` is computed by the
    caller (against DEAD_EVAL_THRESHOLD) since the caller already has the
    score in hand; this module doesn't second-guess it.

    Returns a dict: {"status": "updated"|"dedup", "dead": 0|1,
    "quarantine_active": 0|1, "verdict": str, "evaluated": bool,
    "transition": ""|"start"|"clear", "rate": float|None,
    "alert_transition": ""|"start"|"clear"}. `alert_transition` mirrors
    `transition` but for the (softer) WARN-level alert threshold
    (DEAD_ALERT_WINDOW/RATE) rather than the quarantine threshold --
    logged on the bash side so a rising instadeath rate that hasn't yet
    crossed the quarantine bar is still visible (2026-08-20 Phase 1 review,
    next-best item #2: alert state was computed and persisted but never
    surfaced anywhere). Never raises -- catches everything and returns
    {"status": "error", "error": "..."} so a monitor failure can never take
    down the caller's own bookkeeping.
    """
    try:
        cfg = _merged_cfg(cfg)
        monitor = load(path)
        window = monitor["window"]
        archive = record.get("archive") or ""

        if archive and any((r.get("a") or "") == archive for r in window):
            monitor["counters"]["skipped_dedup"] = int(monitor["counters"].get("skipped_dedup", 0)) + 1
            _atomic_write(path, monitor)
            return {
                "status": "dedup", "dead": 0,
                "quarantine_active": 1 if monitor["quarantine"]["active"] else 0,
                "verdict": monitor["quarantine"]["verdict"],
                "evaluated": monitor["quarantine"]["evaluated"],
                "transition": "", "alert_transition": "", "rate": None,
            }

        was_alert_active = bool(monitor["alert"].get("active"))
        now = int(time.time())
        dead = 1 if record.get("d") else 0
        entry = {
            "ts": now, "h": record.get("h") or "", "s": record.get("s"),
            "raw": record.get("raw"), "turns": record.get("turns"),
            "d": dead, "a": archive,
        }
        window.append(entry)
        keep = cfg["dead_monitor_window"]
        monitor["window"] = window[-keep:] if keep > 0 else window
        _recompute(monitor, cfg)
        transition = _apply_quarantine_transition(monitor, cfg, now, record.get("h") or "")
        # A dead game observed while quarantine is (now, post-transition)
        # active is exactly the set of games the bash caller will divert
        # (its own divert decision is `_dead and quarantine_active` from
        # this function's return value) -- count it here so diverted_total
        # is self-consistent with the caller's actual behavior, instead of
        # relying on a separate note-diverted call that nothing ever made
        # (2026-08-20 Phase 1 review, next-best item #1: the previous code
        # here was a no-op self-assignment).
        if dead and monitor["quarantine"]["active"]:
            monitor["quarantine"]["diverted_total"] = int(
                monitor["quarantine"].get("diverted_total", 0)) + 1
        is_alert_active = bool(monitor["alert"].get("active"))
        alert_transition = ""
        if is_alert_active and not was_alert_active:
            alert_transition = "start"
        elif was_alert_active and not is_alert_active:
            alert_transition = "clear"

        monitor["updated_at"] = now
        _atomic_write(path, monitor)

        q = monitor["quarantine"]
        return {
            "status": "updated", "dead": dead,
            "quarantine_active": 1 if q["active"] else 0,
            "verdict": q["verdict"], "evaluated": q["evaluated"],
            "transition": transition, "alert_transition": alert_transition,
            "rate": q["detail"].get("rate") if isinstance(q.get("detail"), dict) else None,
        }
    except Exception as e:
        return {"status": "error", "error": str(e)}


def state(path):
    """Read-only snapshot of the current quarantine state. Never raises."""
    try:
        monitor = load(path)
        q = monitor["quarantine"]
        return {
            "quarantine_active": 1 if q["active"] else 0,
            "verdict": q["verdict"], "evaluated": q["evaluated"],
        }
    except Exception as e:
        return {"quarantine_active": 0, "verdict": "UNKNOWN", "evaluated": False, "error": str(e)}


def _cfg_from_env():
    """Build a cfg dict from the DEAD_* environment variables core/config.sh
    exports -- the CLI's callers (bash) already have these exported, so
    there's no need for bash to also serialize a JSON blob."""

    def _f(name, default):
        try:
            return float(os.environ.get(name, default))
        except Exception:
            return default

    def _i(name, default):
        try:
            return int(os.environ.get(name, default))
        except Exception:
            return default

    return {
        "dead_eval_threshold": _i("DEAD_EVAL_THRESHOLD", _CFG_DEFAULTS["dead_eval_threshold"]),
        "dead_monitor_window": _i("DEAD_MONITOR_WINDOW", _CFG_DEFAULTS["dead_monitor_window"]),
        "dead_alert_window": _i("DEAD_ALERT_WINDOW", _CFG_DEFAULTS["dead_alert_window"]),
        "dead_alert_rate": _f("DEAD_ALERT_RATE", _CFG_DEFAULTS["dead_alert_rate"]),
        "dead_quarantine_enabled": os.environ.get("DEAD_QUARANTINE_ENABLED", "1") == "1",
        "dead_quarantine_window": _i("DEAD_QUARANTINE_WINDOW", _CFG_DEFAULTS["dead_quarantine_window"]),
        "dead_quarantine_rate": _f("DEAD_QUARANTINE_RATE", _CFG_DEFAULTS["dead_quarantine_rate"]),
        "dead_quarantine_clear_window": _i(
            "DEAD_QUARANTINE_CLEAR_WINDOW", _CFG_DEFAULTS["dead_quarantine_clear_window"]),
        "dead_quarantine_clear_rate": _f(
            "DEAD_QUARANTINE_CLEAR_RATE", _CFG_DEFAULTS["dead_quarantine_clear_rate"]),
        "dead_burst_ratio": _f("DEAD_BURST_RATIO", _CFG_DEFAULTS["dead_burst_ratio"]),
        "dead_hard_ratio": _f("DEAD_HARD_RATIO", _CFG_DEFAULTS["dead_hard_ratio"]),
        "dead_near_total_rate": _f("DEAD_NEAR_TOTAL_RATE", _CFG_DEFAULTS["dead_near_total_rate"]),
        "dead_max_turns": _i("DEAD_MAX_TURNS", _CFG_DEFAULTS["dead_max_turns"]),
        "dead_alpha": _f("DEAD_ALPHA", _CFG_DEFAULTS["dead_alpha"]),
    }


def _cli():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_observe = sub.add_parser("observe")
    p_observe.add_argument("--file", required=True)
    p_observe.add_argument("--hash", default="")
    p_observe.add_argument("--score", type=int, required=True)
    p_observe.add_argument("--raw", default="")
    p_observe.add_argument("--turns", default="")
    p_observe.add_argument("--archive", default="")
    p_observe.add_argument("--dead", required=True, choices=["0", "1"])

    p_state = sub.add_parser("state")
    p_state.add_argument("--file", required=True)

    args = parser.parse_args()

    if args.cmd == "observe":
        cfg = _cfg_from_env()

        def _opt_int(s):
            s = (s or "").strip()
            if not s:
                return None
            try:
                return int(s)
            except Exception:
                return None

        record = {
            "h": args.hash, "s": args.score, "raw": _opt_int(args.raw),
            "turns": _opt_int(args.turns), "d": args.dead == "1", "archive": args.archive,
        }
        print(json.dumps(observe(args.file, record, cfg)))
    elif args.cmd == "state":
        print(json.dumps(state(args.file)))


if __name__ == "__main__":
    _cli()

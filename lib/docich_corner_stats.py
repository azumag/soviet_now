"""Read-only bridge from docich corner state to the Soren status dashboard.

The regular Soren dashboard reads ``score_history.txt``.  That file belongs to
the Soren game and must not be reused while docich has switched the stream to
another corner: doing so would make a previous game's score look current.

This module only reads the small, allowlisted public state files written by
docich.  It deliberately returns a typed-ish snapshot for the renderer rather
than exposing arbitrary JSON fields or file contents.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import re
import uuid
import time
import stat
from pathlib import Path
from typing import Mapping


DEFAULT_DOCICH_STATE_DIR = Path("/home/ubuntu/docich/run-soren-live")
ACTIVE_STATUSES = frozenset(
    {"preparing", "starting", "active", "restoring", "recovery_required"}
)

_CORNER_SPECS = (
    ("retro", "RETRO", ("retro_corner.json", "retro_corner_manual.json")),
    ("paper", "PAPER", ("paper_corner.json", "paper_corner_manual.json")),
    ("nethack", "NETHACK", ("nethack_corner.json", "nethack_corner_manual.json")),
    ("soren91", "SOREN91", ("soren91_corner.json", "soren91_corner_manual.json")),
    ("jev", "JEV", ("jev_corner.json",)),
)
_GAME_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def resolve_state_dir(value: str | os.PathLike[str] | None = None) -> Path:
    """Resolve the docich state directory, with an environment override for tests."""
    if value is not None:
        return Path(value).expanduser()
    configured = os.getenv("DOCICH_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    # The production checkouts conventionally sit beside each other.  Prefer
    # that relationship when it exists, while retaining the VM default for
    # the normal /home/ubuntu/soren checkout.
    sibling = Path.cwd().parent / "docich" / "run-soren-live"
    if sibling.is_dir():
        return sibling
    return DEFAULT_DOCICH_STATE_DIR


def _read_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError):
        return None, "unreadable"
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None, "invalid-json"
    if not isinstance(value, dict):
        return None, "invalid-shape"
    return value, None


def _parse_timestamp(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None and math.isfinite(number):
        return number
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def _safe_game(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    game = value.strip()
    return game if _GAME_NAME_RE.fullmatch(game) else None


def _active_states(root: Path) -> list[dict[str, object]]:
    active: list[dict[str, object]] = []
    for kind, label, names in _CORNER_SPECS:
        for name in names:
            path = root / name
            if not path.is_file():
                continue
            state, _error = _read_json(path)
            if state is None:
                # A malformed terminal/idle file cannot prove that a corner
                # is running.  Do not let it replace the normal dashboard.
                continue
            status = state.get("status")
            if status not in ACTIVE_STATUSES:
                continue
            if state.get("schema_version") != 1:
                active.append(
                    {
                        "kind": "invalid",
                        "label": "CORNER",
                        "state_path": str(path),
                        "error": "schema",
                    }
                )
                continue
            active.append(
                {
                    "kind": kind,
                    "label": label,
                    "state": state,
                    "state_path": str(path),
                    "status": str(status),
                    "source": name,
                }
            )
    return active


def _score_history(root: Path, state: Mapping[str, object]) -> list[dict[str, object]]:
    game = _safe_game(state.get("game"))
    if game is None:
        return []
    path = root / "scores" / f"{game}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    history: list[dict[str, object]] = []
    for order, line in enumerate(lines):
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(item, dict) or item.get("game") != game:
            continue
        score = _int_value(item.get("score"))
        if score is None:
            continue
        timestamp = _parse_timestamp(item.get("ts"))
        history.append({"score": score, "ts": timestamp, "order": order})
    history.sort(key=lambda item: (item.get("ts") is not None, item.get("ts") or 0, item["order"]))
    return history


def _session_count(history: list[dict], state: Mapping[str, object]) -> int | None:
    started = _parse_timestamp(state.get("started_at"))
    if started is None:
        return None
    return sum(row.get("ts") is not None and row["ts"] >= started for row in history)


def _strategy_ranking(root: Path, game: str) -> list[dict[str, object]]:
    """Read the bounded per-game strategy ranking from docich's improve log."""
    path = root / "resolver" / "improve_log.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    aggregate: dict[str, dict[str, object]] = {}
    for line in lines:
        try:
            entry = json.loads(line)
        except (TypeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("game") != game:
            continue
        trials = entry.get("trials")
        if not isinstance(trials, list):
            continue
        for trial in trials:
            if not isinstance(trial, Mapping):
                continue
            strategy = trial.get("strategy")
            mean = trial.get("mean_score")
            if not isinstance(strategy, Mapping) or isinstance(mean, bool) or not isinstance(mean, (int, float)):
                continue
            if not math.isfinite(float(mean)):
                continue
            try:
                payload = json.dumps(
                    dict(strategy),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError):
                continue
            key = hashlib.sha256(payload).hexdigest()
            row = aggregate.setdefault(key, {"key": key, "best": float(mean), "runs": 0, "wins": 0})
            row["runs"] = int(row["runs"]) + 1
            row["best"] = max(float(row["best"]), float(mean))
        best_key = entry.get("best_strategy_key")
        if isinstance(best_key, str) and best_key in aggregate and entry.get("promoted"):
            aggregate[best_key]["wins"] = int(aggregate[best_key]["wins"]) + 1
    return sorted(
        aggregate.values(),
        key=lambda row: (float(row["best"]), int(row["wins"]), int(row["runs"])),
        reverse=True,
    )[:5]


def _paper_snapshot(root: Path) -> dict[str, object]:
    value, _error = _read_json(root / "trading" / "status.json")
    if value is None:
        return {"status": "unavailable", "fills": [], "positions": {}}
    raw_fills = value.get("recent_fills")
    fills = [dict(fill) for fill in raw_fills if isinstance(fill, Mapping)] if isinstance(raw_fills, list) else []
    fills.sort(key=lambda item: _parse_timestamp(item.get("filled_at")) or 0)
    positions = value.get("open_positions")
    if not isinstance(positions, Mapping):
        positions = {}
    return {
        "status": str(value.get("worker_state") or "unknown"),
        "capital": value.get("capital_reference"),
        "deployed": value.get("deployed_reference"),
        "fills": fills[-20:],
        "positions": dict(positions),
        "snapshot_at": _parse_timestamp(value.get("snapshot_generated_at")),
        "market_count": _int_value(value.get("market_count")),
    }


def _nethack_snapshot(root: Path, state: Mapping[str, object]) -> dict[str, object]:
    current, _error = _read_json(root / "nethack" / "current.json")
    # current.json is a run-id pointer, not a copy of the run body.
    run = {}
    run_id = current.get("run_id") if current else None
    try:
        valid_id = isinstance(run_id, str) and str(uuid.UUID(run_id)) == run_id
    except ValueError:
        valid_id = False
    if valid_id and state.get("run_id") in (None, run_id):
        body, _error = _read_json(root / "nethack" / "runs" / f"{run_id}.json")
        if body and body.get("run_id") == run_id:
            run = dict(body)
    for source, target in (
        ("run_id", "run_id"),
        ("expedition", "expedition"),
        ("run_status", "status"),
        ("run_score", "score"),
        ("run_turns", "turns"),
        ("run_max_depth", "max_depth"),
    ):
        if target not in run and source in state:
            run[target] = state[source]

    history: list[dict[str, object]] = []
    runs_dir = root / "nethack" / "runs"
    try:
        paths = list(runs_dir.glob("*.json"))
    except OSError:
        paths = []
    for path in paths:
        item, _error = _read_json(path)
        if (item is None or _int_value(item.get("score")) is None
                or item.get("status") not in {"dead", "ascended", "ended", "ended_unknown"}):
            continue
        try:
            item["_mtime"] = path.stat().st_mtime
        except OSError:
            item["_mtime"] = 0
        history.append(item)
    history.sort(key=lambda item: _parse_timestamp(item.get("last_finished_at")) or float(item.get("_mtime") or 0))
    scores = [_int_value(item.get("score")) for item in history]
    # The current run can already be in runs/. Never count it twice or mix
    # an active/suspended expedition into completed-run statistics.
    return {"run": run, "history": history[-20:], "scores": [s for s in scores if s is not None]}


def _soren91_history(soren_root: Path) -> list[dict[str, object]]:
    history = []
    for path in (soren_root / "soren91" / "tmp" / "summaries").glob("game_*.json"):
        item, _error = _read_json(path)
        if item is None:
            continue
        rank = _int_value(item.get("rank"))
        timestamp = _parse_timestamp(item.get("timestamp"))
        if rank is None or not 1 <= rank <= 91 or timestamp is None:
            continue
        history.append({"score": rank, "ts": timestamp})
    return sorted(history, key=lambda row: row["ts"])


def _jev_history(soren_root: Path) -> list[dict[str, object]]:
    history = []
    for path in (soren_root / "tmp" / "jev_player" / "runs").glob("*/report.json"):
        item, _error = _read_json(path)
        if item is None or item.get("status") != "completed":
            continue
        summary = item.get("summary")
        if not isinstance(summary, dict):
            continue
        score = _int_value(summary.get("score"))
        timestamp = _parse_timestamp(item.get("finished_at"))
        if score is not None and timestamp is not None:
            history.append({"score": score, "ts": timestamp})
    return sorted(history, key=lambda row: row["ts"])


def _bounded_state(path: Path, limit=262144):
    """Fixed-file read; never follow a link or read an unbounded runtime log."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("invalid state file")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError("oversized state")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("invalid state shape")
        return data, info.st_mtime


def _hanjuku_snapshot(root: Path, state: Mapping[str, object]):
    """Display only cached observations from the canonical active generation."""
    unavailable = {"availability": "unavailable"}
    if state.get("status") != "active":
        return unavailable
    try:
        canonical, _ = _bounded_state(root / "game_switch.json")
        active = canonical.get("active")
        if canonical.get("phase") != "ready" or not isinstance(active, dict):
            return unavailable
        keys = ("game", "runtime_id", "generation", "lease_id")
        identity = {key: active.get(key) for key in keys}
        runtime_id = identity["runtime_id"]
        match = re.fullmatch(r"g([1-9][0-9]*)-([a-f0-9]{6,32})", runtime_id or "")
        if (identity["game"] != "hanjuku-hero" or not match
                or type(identity["generation"]) is not int
                or int(match[1]) != identity["generation"]
                or not isinstance(identity["lease_id"], str) or not identity["lease_id"]
                or state.get("bot_identity") != identity):
            return unavailable
        runtime = root / "runtimes" / runtime_id
        if (root / "runtimes").is_symlink() or runtime.is_symlink():
            return unavailable
        run, _ = _bounded_state(runtime / "hanjuku_run.json")
        bot, bot_at = _bounded_state(runtime / "hanjuku_bot.json")
        if (any(run.get(key) != value for key, value in identity.items())
                or not isinstance(bot.get("decision_trace"), dict)
                or any(bot["decision_trace"].get(key) != value for key, value in identity.items())
                or run.get("terminal_reason") or run.get("terminal_candidate")):
            return unavailable
        now = time.time()
        observed = run.get("observed_at")
        if (type(observed) not in (int, float) or not math.isfinite(observed)
                or not 0 <= now - observed <= 30 or not 0 <= now - bot_at <= 30):
            return {"availability": "stale"}
        policy = bot.get("policy") if isinstance(bot.get("policy"), dict) else {}
        stats = policy.get("stats") if isinstance(policy.get("stats"), dict) else {}
        number = lambda value: value if type(value) is int and 0 <= value <= 1000000 else None
        captured = policy.get("captured")
        chapter = number(policy.get("chapter"))
        # Chapter and currency are last observations, not predictions or orders.
        result = {"availability": "fresh", "age": int(now - observed),
                  "chapter": chapter if chapter is not None and 1 <= chapter <= 12 else None,
                  "gold": number(policy.get("gold")),
                  "captured": len(captured) if isinstance(captured, list) else None,
                  "wins": number(stats.get("wins")), "losses": number(stats.get("losses")),
                  "actions": number(run.get("actions_sent")),
                  "phase": run.get("phase") if run.get("phase") in {"field", "battle", "title", "name", "unknown"} else None,
                  "screen": bot.get("screen_kind") if isinstance(bot.get("screen_kind"), str) else None,
                  "chart_step": policy.get("active") if isinstance(policy.get("active"), str) else None}
        # Recheck generation after reads; never carry the previous game into a switch.
        latest, _ = _bounded_state(root / "game_switch.json")
        if latest.get("phase") != "ready" or latest.get("active") != active:
            return unavailable
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        return unavailable


def load_active_corner(state_dir: str | os.PathLike[str] | None = None, *,
                       soren_root: str | os.PathLike[str] | None = None) -> dict[str, object] | None:
    """Return one active corner snapshot, or ``None`` for ordinary Soren mode.

    Multiple active corner states are represented as a conflict snapshot so
    the renderer can fail closed instead of choosing one arbitrarily.
    """
    root = resolve_state_dir(state_dir)
    soren = Path(soren_root) if soren_root is not None else Path(__file__).resolve().parents[1]
    entries = _active_states(root)
    if not entries:
        return None
    if len(entries) != 1:
        return {
            "kind": "conflict",
            "label": "CORNER",
            "entries": entries,
        }
    entry = entries[0]
    if entry.get("kind") == "invalid":
        return entry
    state = entry["state"]
    snapshot = dict(entry)
    kind = str(entry["kind"])
    if kind == "retro":
        snapshot["game"] = _safe_game(state.get("game")) or "unknown"
        snapshot["scores"] = _score_history(root, state)
        snapshot["session_matches"] = _session_count(snapshot["scores"], state)
        snapshot["target_matches"] = _int_value(state.get("target_matches"))
        snapshot["strategy_ranking"] = _strategy_ranking(root, snapshot["game"])
        if snapshot["game"] == "hanjuku-hero":
            snapshot["hanjuku"] = _hanjuku_snapshot(root, state)
    elif kind == "paper":
        snapshot["paper"] = _paper_snapshot(root)
    elif kind == "nethack":
        snapshot.update(_nethack_snapshot(root, state))
    elif kind == "soren91":
        snapshot["game"] = _safe_game(state.get("game")) or "soren91"
        snapshot["scores"] = _soren91_history(soren)
        snapshot["session_matches"] = _session_count(snapshot["scores"], state)
    elif kind == "jev":
        snapshot["game"] = _safe_game(state.get("game")) or "sorengame"
        snapshot["policy"] = state.get("policy")
        snapshot["player_generation"] = _int_value(state.get("player_generation"))
        snapshot["scores"] = _jev_history(soren)
    return snapshot

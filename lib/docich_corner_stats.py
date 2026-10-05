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
CONSOLE_GAMES = frozenset({"ninvaders", "nsnake", "bastet", "moon-buggy", "pacman4console"})
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
# Whole ANSI/OSC escape sequences and C0/C1 control runs.  Filtering character
# by character is not enough: ESC is not printable but the "[31m" after it is,
# so a per-character check would leak a broken escape into the card.
_CONTROL_RE = re.compile(
    r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?|\x1b[@-Z\\-_]"
    r"|\[[0-?]{1,4}[ -/]*[@-~]|[\x00-\x1f\x7f-\x9f]")
# docich hanjuku_policy ages a marching sortie for this many observations
# (~10 min at 1.5 s).  A sortie older than this is not "still marching" and must
# not be shown as one.
SORTIE_BUSY_TICKS = 400


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


def _score_history(root: Path, state: Mapping[str, object]) -> tuple[list[dict[str, object]], str]:
    game = _safe_game(state.get("game"))
    if game is None:
        return [], "unavailable"
    path = root / "scores" / f"{game}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return [], "missing"
    except (OSError, UnicodeError):
        return [], "unreadable"

    history: list[dict[str, object]] = []
    complete = True
    for order, line in enumerate(lines):
        try:
            item = json.loads(line)
        except (TypeError, ValueError):
            complete = False
            continue
        if not isinstance(item, dict):
            complete = False
            continue
        item_game = _safe_game(item.get("game"))
        if item_game is None:
            complete = False
            continue
        if item_game != game:
            continue
        score = _int_value(item.get("score"))
        if score is None:
            complete = False
            continue
        timestamp = _parse_timestamp(item.get("ts"))
        if timestamp is None:
            complete = False
        history.append({"score": score, "ts": timestamp, "order": order})
    history.sort(key=lambda item: (item.get("ts") is not None, item.get("ts") or 0, item["order"]))
    return history, "readable" if complete else "partial"


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


_PAPER_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/+\\-]{0,79}$")


def _paper_token(value: object, *, limit: int = 80) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or not _PAPER_TOKEN_RE.fullmatch(text):
        return None
    return text


def _paper_decimal_text(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    text = str(value).strip()
    return text[:40] if text else None


def _paper_tokens(value: object, *, limit: int = 6) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value[:limit]:
        token = _paper_token(item)
        if token is not None and token not in result:
            result.append(token)
    return result


def _paper_age(now: float, timestamp: float | None) -> int | None:
    if timestamp is None:
        return None
    age = float(now) - timestamp
    if not math.isfinite(age) or age < 0:
        return None
    return int(age)


def _paper_snapshot(root: Path, *, now: float | None = None) -> dict[str, object]:
    value, _error = _read_json(root / "trading" / "status.json")
    if value is None:
        return {"status": "unavailable", "fills": [], "positions": {}}

    moment = time.time() if now is None else float(now)
    eligible_symbols = _paper_tokens(value.get("eligible_symbols"), limit=32)
    raw_fills = value.get("recent_fills")
    fills: list[dict[str, object]] = []
    if isinstance(raw_fills, list):
        for fill in raw_fills:
            if not isinstance(fill, Mapping):
                continue
            symbol = _paper_token(fill.get("symbol"), limit=32)
            if symbol is None:
                continue
            side = str(fill.get("side") or "").lower()
            if side not in {"buy", "sell"}:
                side = "unknown"
            fills.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "quote_notional": _paper_decimal_text(fill.get("quote_notional")),
                    "price": _paper_decimal_text(fill.get("price")),
                    "filled_at": _parse_timestamp(fill.get("filled_at")),
                }
            )
    fills.sort(key=lambda item: item.get("filled_at") or 0)

    raw_positions = value.get("open_positions")
    positions: dict[str, str] = {}
    if isinstance(raw_positions, Mapping):
        for raw_symbol, raw_amount in sorted(raw_positions.items()):
            symbol = _paper_token(raw_symbol, limit=32)
            amount = _paper_decimal_text(raw_amount)
            if symbol is not None and amount is not None:
                positions[symbol] = amount

    signal: dict[str, object] = {}
    raw_signal = value.get("signal_summary")
    if isinstance(raw_signal, Mapping):
        for key in ("candidate_count", "selected_count", "rejected_count"):
            number = _int_value(raw_signal.get(key))
            if number is not None and number >= 0:
                signal[key] = number
        signal["strategy_ids"] = _paper_tokens(raw_signal.get("strategy_ids"), limit=4)
        signal["candidate_reason_codes"] = _paper_tokens(
            raw_signal.get("candidate_reason_codes"), limit=4
        )
        signal["candidate_symbols"] = _paper_tokens(
            raw_signal.get("candidate_symbols"), limit=32
        )
        signal["selected_symbols"] = _paper_tokens(
            raw_signal.get("selected_symbols"), limit=32
        )

    skipped: list[dict[str, str]] = []
    raw_skipped = value.get("skipped_decisions")
    if isinstance(raw_skipped, list):
        for item in raw_skipped[:5]:
            if not isinstance(item, Mapping):
                continue
            reason = _paper_token(item.get("reason_code"))
            if reason is None:
                continue
            symbol = _paper_token(item.get("symbol"), limit=32)
            side = str(item.get("side") or "").lower()
            if side not in {"buy", "sell"}:
                side = "unknown"
            skipped.append({"symbol": symbol or "", "side": side, "reason_code": reason})
    if not skipped:
        skipped = [
            {"symbol": "", "side": "unknown", "reason_code": reason}
            for reason in _paper_tokens(value.get("skipped_reason_codes"), limit=5)
        ]

    candidate_symbols = set(signal.get("candidate_symbols") or [])
    selected_symbols = set(signal.get("selected_symbols") or [])
    rejected_by_symbol = {
        item["symbol"]: item["reason_code"]
        for item in skipped
        if item.get("symbol") and item.get("reason_code")
    }
    symbol_states: list[dict[str, str]] = []
    for symbol in sorted(set(eligible_symbols)):
        if symbol in rejected_by_symbol:
            state = "rejected_after_signal"
            reason = rejected_by_symbol[symbol]
        elif symbol in selected_symbols:
            state = "selected"
            reason = ""
        elif symbol in candidate_symbols:
            state = "candidate"
            reason = ""
        else:
            state = "no_signal"
            reason = ""
        symbol_states.append({"symbol": symbol, "state": state, "reason_code": reason})

    worker: dict[str, object] = {}
    raw_worker = value.get("worker_summary")
    if isinstance(raw_worker, Mapping):
        for key in (
            "cycle_index",
            "frame_error_count",
            "arbitrage_candidate_count",
            "new_fill_count",
            "new_settlement_count",
        ):
            number = _int_value(raw_worker.get(key))
            if number is not None and number >= 0:
                worker[key] = number
        last_success_at = _parse_timestamp(raw_worker.get("last_success_at"))
        next_cycle_at = _parse_timestamp(raw_worker.get("next_cycle_at"))
        worker["last_success_at"] = last_success_at
        worker["last_success_age"] = _paper_age(moment, last_success_at)
        worker["next_cycle_at"] = next_cycle_at
        worker["next_cycle_in"] = (
            None
            if next_cycle_at is None
            else max(0, int(next_cycle_at - moment))
        )
        worker["error_codes"] = _paper_tokens(raw_worker.get("error_codes"), limit=5)
        experiment_status = _paper_token(raw_worker.get("experiment_status"), limit=32)
        experiment_reason = _paper_token(raw_worker.get("experiment_reason_code"), limit=64)
        if experiment_status is not None:
            worker["experiment_status"] = experiment_status
        if experiment_reason is not None:
            worker["experiment_reason_code"] = experiment_reason
        if type(raw_worker.get("experiment_entries_allowed")) is bool:
            worker["experiment_entries_allowed"] = raw_worker["experiment_entries_allowed"]

    freshness_counts = {"fresh": 0, "stale": 0, "missing": 0, "invalid": 0, "unknown": 0}
    freshness_issues: list[dict[str, str]] = []
    raw_freshness = value.get("market_freshness")
    if isinstance(raw_freshness, Mapping):
        for raw_symbol, raw_entry in sorted(raw_freshness.items()):
            symbol = _paper_token(raw_symbol, limit=32)
            if symbol is None or not isinstance(raw_entry, Mapping):
                continue
            quality = _paper_token(raw_entry.get("quality"), limit=16) or "unknown"
            if quality not in freshness_counts:
                quality = "unknown"
            freshness_counts[quality] += 1
            if quality != "fresh" and len(freshness_issues) < 4:
                reason = _paper_token(raw_entry.get("reason_code"), limit=64) or "unknown"
                freshness_issues.append(
                    {"symbol": symbol, "quality": quality, "reason_code": reason}
                )

    coverage: dict[str, object] = {}
    raw_coverage = value.get("coverage")
    if isinstance(raw_coverage, Mapping):
        for key in ("attempted", "total"):
            number = _int_value(raw_coverage.get(key))
            if number is not None and number >= 0:
                coverage[key] = number
        if type(raw_coverage.get("budget_exceeded")) is bool:
            coverage["budget_exceeded"] = raw_coverage["budget_exceeded"]
        carried = raw_coverage.get("carried_symbols")
        coverage["carried_count"] = len(carried) if isinstance(carried, list) else 0

    performance: dict[str, object] = {}
    raw_performance = value.get("performance_summary")
    if isinstance(raw_performance, Mapping):
        performance_as_of = _parse_timestamp(raw_performance.get("as_of"))
        performance["as_of"] = performance_as_of
        performance["age"] = _paper_age(moment, performance_as_of)
        if type(raw_performance.get("complete")) is bool:
            performance["complete"] = raw_performance["complete"]
        for key in ("position_count", "priced_positions", "valued_positions"):
            number = _int_value(raw_performance.get(key))
            if number is not None and number >= 0:
                performance[key] = number
        for key in (
            "realized_total_jpy",
            "today_realized_pnl_jpy",
            "unrealized_pnl_jpy",
            "cumulative_pnl_jpy",
            "equity_jpy",
        ):
            performance[key] = _paper_decimal_text(raw_performance.get(key))

    snapshot_at = _parse_timestamp(value.get("snapshot_generated_at"))
    heartbeat_at = _parse_timestamp(value.get("heartbeat_at"))
    return {
        "status": _paper_token(value.get("worker_state"), limit=40) or "unknown",
        "capital": _paper_decimal_text(value.get("capital_reference")),
        "deployed": _paper_decimal_text(value.get("deployed_reference")),
        "fills": fills[-20:],
        "positions": positions,
        "snapshot_at": snapshot_at,
        "snapshot_age": _paper_age(moment, snapshot_at),
        "heartbeat_at": heartbeat_at,
        "heartbeat_age": _paper_age(moment, heartbeat_at),
        "market_count": _int_value(value.get("market_count")),
        "signal": signal,
        "symbol_states": symbol_states,
        "skipped": skipped,
        "worker": worker,
        "freshness": {"counts": freshness_counts, "issues": freshness_issues},
        "coverage": coverage,
        "performance": performance,
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


def _hanjuku_clean(value):
    """Printable-only text from cached game memory.

    ``str.isprintable()`` rejects the ESC itself but keeps the printable
    ``[31m`` that follows it, so filtering character by character would leak a
    broken escape fragment into the card.  Drop whole escape sequences and
    control runs first, then reject anything still not printable.  The headless
    form also drops an escape that already lost its ESC.
    """
    if not isinstance(value, str):
        return ""
    text = _CONTROL_RE.sub("", value)
    return "".join(ch for ch in text if ch.isprintable()).strip()


def _hanjuku_names(value, limit=3, each=10):
    """Bounded, printable name list from cached game memory (never raw text)."""
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        name = _hanjuku_clean(item)
        if not name or len(name) > 24 or name in out:
            continue
        out.append(name[:each])
        if len(out) >= limit:
            break
    return out


def _hanjuku_name(value, each=10):
    """One bounded printable name, or None. Never a raw or unbounded string."""
    names = _hanjuku_names([value] if isinstance(value, str) else [], limit=1, each=each)
    return names[0] if names else None


def _hanjuku_text(value, limit=80):
    """Bounded printable status text from cached policy memory."""
    if not isinstance(value, str):
        return None
    text = _hanjuku_clean(value)
    if not text:
        return None
    return text[:max(1, int(limit))]


def _hanjuku_garrison(policy: Mapping[str, object], limit=3, each=8):
    """castle -> generals actually read from a castle panel, bounded."""
    raw = policy.get("garrison")
    if not isinstance(raw, dict):
        return []
    rows = []
    for castle in sorted(raw):
        castle_name = _hanjuku_name(castle, each=max(10, each))
        generals = _hanjuku_names(raw[castle], limit=8, each=each)
        if castle_name and generals:
            rows.append({"castle": castle_name, "generals": generals})
        if len(rows) >= limit:
            break
    return rows


def _hanjuku_active_order(policy: Mapping[str, object]) -> dict[str, str] | None:
    """Bounded current sortie intent from an already-recorded launched order.

    This is not a prediction of the next move.  It only exposes the active
    order after the policy has persisted that exact order in launched_orders.
    """
    step = policy.get("active")
    launched = policy.get("launched_orders")
    if not isinstance(step, str) or not step or not isinstance(launched, Mapping):
        return None
    order = launched.get(step)
    if not isinstance(order, Mapping):
        return None
    general = _hanjuku_name(order.get("general"), each=24)
    source = _hanjuku_name(order.get("source"), each=24)
    target = _hanjuku_name(order.get("target"), each=24)
    purpose = _hanjuku_text(order.get("purpose"), 16)
    statuses = policy.get("orders")
    status = _hanjuku_text(statuses.get(step), 20) if isinstance(statuses, Mapping) else None
    if not any((general, source, target, purpose, status)):
        return None
    return {
        "step": _hanjuku_text(step, 24) or "",
        "general": general or "",
        "source": source or "",
        "target": target or "",
        "purpose": purpose or "",
        "status": status or "",
    }


def _hanjuku_marching(policy: Mapping[str, object], tick, limit=3, each=10):
    """Sorties still counted as marching by the policy's own busy window."""
    raw = policy.get("sorties")
    if not isinstance(raw, dict) or type(tick) is not int:
        return []
    rows = []
    for step in sorted(raw):
        sortie = raw[step]
        if not isinstance(sortie, dict):
            continue
        if sortie.get("status") not in ("en_route", "launched_unconfirmed"):
            continue
        general = _hanjuku_name(sortie.get("general"), each=each)
        if general is None:
            continue
        seen = sortie.get("tick")
        if type(seen) is not int:
            continue
        age = tick - seen
        if not 0 <= age < SORTIE_BUSY_TICKS:
            continue
        rows.append({"general": general, "target": _hanjuku_name(sortie.get("target"), each=each)})
        if len(rows) >= limit:
            break
    return rows


def _hanjuku_gap(runtime, policy, now):
    """Only a measured edge projection of this fenced runtime may expose cards."""
    try:
        state, _ = _bounded_state(runtime / 'presentation.json')
    except (OSError, ValueError, TypeError):
        return None
    projection = state.get('projection')
    if state.get('status') != 'ready' or not isinstance(projection, dict):
        return None
    content = projection.get('content')
    align = projection.get('align')
    if (align not in ('left', 'right') or projection.get('viewport') != [0, 90, 960, 540]
            or not isinstance(content, list) or len(content) != 4
            or any(type(n) is not int for n in content)):
        return None
    x, y, w, h = content
    expected_x = 0 if align == 'left' else 960-w
    if x != expected_x or not (0 < w <= 960 and 0 < h <= 540) or y != (540-h)//2:
        return None
    # A narrow gap cannot maintain 18px text with readable Japanese rows.
    if 960-w < 180:
        return None
    def recent(stamp, age=30):
        return type(stamp) in (int, float) and math.isfinite(stamp) and 0 <= now-stamp <= age
    stamps = policy.get('garrison_observed_at')
    stamps = stamps if isinstance(stamps, dict) else {}
    garrison = [dict(row, until=stamps[row['castle']]+30) for row in _hanjuku_garrison(policy, limit=24, each=24)
                if recent(stamps.get(row['castle']))]
    battle = policy.get('battle')
    battle = battle if isinstance(battle, dict) else {}
    hp = None
    battle_detail = None
    stamp = battle.get('hp_observed_at')
    if recent(stamp, 10):
        until = float(stamp) + 10
        egg_battle = battle.get('egg_battle') is True
        values = [battle.get('enemy_hp'), battle.get('ally_hp')]
        if not egg_battle and all(type(n) is int and 0 <= n <= 1000000 for n in values):
            hp = {'until': until, 'enemy': _hanjuku_name(battle.get('enemy'), each=24),
                  'ally': _hanjuku_name(battle.get('ally'), each=24),
                  'enemy_hp': values[0], 'ally_hp': values[1]}
        counts = [battle.get('enemy_soldiers'), battle.get('ally_soldiers')]
        soldiers_current = (battle.get('card_soldiers_current') is True and not egg_battle
                            and all(type(n) is int and 0 <= n <= 1000000 for n in counts))
        flow = battle.get('card_flow')
        flow = flow if isinstance(flow, dict) else {}
        guard = battle.get('okunote_egg_preempt')
        guard = guard if isinstance(guard, dict) else {}
        guard_stage = guard.get('stage')
        if guard_stage not in {'opening', 'menu', 'selected', 'completed'}:
            guard_stage = None
        side = battle.get('side') if battle.get('side') in {'attack', 'defense'} else None
        battle_detail = {
            'until': until,
            'side': side,
            'castle': _hanjuku_name(battle.get('castle'), each=24),
            'step': _hanjuku_text(battle.get('step'), 24),
            'variant': _hanjuku_text(battle.get('strategy_variant'), 24),
            'deviation': _hanjuku_text(battle.get('deviation_reason'), 96),
            'planned_cards': _hanjuku_names(battle.get('planned_cards'), limit=4, each=16),
            'used_cards': _hanjuku_names(battle.get('cards_used'), limit=4, each=16),
            'selected_cards': _hanjuku_names(battle.get('cards_selected'), limit=4, each=16),
            'card': _hanjuku_name(flow.get('card'), each=16),
            'card_stage': _hanjuku_text(flow.get('stage'), 16),
            'egg_battle': egg_battle,
            'egg_guard_stage': guard_stage,
            'egg_guard_exhausted': guard.get('exhausted') is True,
            'enemy_soldiers': counts[0] if soldiers_current else None,
            'ally_soldiers': counts[1] if soldiers_current else None,
        }
    gap_left = w if align == 'left' else 0
    return {'width': 960-w, 'left': gap_left, 'side': 'right' if align == 'left' else 'left',
            'captured_names': _hanjuku_names(policy.get('captured'), limit=24, each=24),
            'garrison': garrison, 'marching': _hanjuku_marching(policy, policy.get('tick'), limit=24, each=24),
            'active_order': _hanjuku_active_order(policy),
            'hp': hp, 'battle': battle_detail}


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
        tally = policy.get("tally") if isinstance(policy.get("tally"), dict) else {}
        number = lambda value: value if type(value) is int and 0 <= value <= 1000000 else None
        battles_started = number(tally.get("battles_started"))
        battles_judged = number(tally.get("battles_judged"))
        # A started battle with no verdict is neither a win nor a loss. The
        # panel must disclose that gap instead of showing the judged subset as
        # if it were every battle (2026-10-02: "1 losses" while six castles
        # were observed falling to the enemy).
        battles_unjudged = (max(0, battles_started - battles_judged)
                            if battles_started is not None and battles_judged is not None else None)
        captured = policy.get("captured")
        chapter = number(policy.get("chapter"))
        month = policy.get("month")
        month_match = re.fullmatch(r"([1-9][0-9]{0,3})-([1-9]|1[0-2])", month) if isinstance(month, str) else None
        pending = next((label for key, label in (("month_sub", "monthly"), ("recall", "recall"),
                        ("house", "repair")) if isinstance(policy.get(key), dict) and policy[key]), None)
        if pending is None and isinstance(policy.get("active"), str) and policy["active"]:
            pending = "sortie"
        battle = policy.get("battle") if isinstance(policy.get("battle"), dict) else None
        tick = policy.get("tick")
        orders = policy.get("orders") if isinstance(policy.get("orders"), dict) else None
        egg_uses = policy.get("egg_uses") if isinstance(policy.get("egg_uses"), dict) else {}
        # Chapter and currency are last observations, not predictions or orders.
        result = {"gap": _hanjuku_gap(runtime, policy, now), "availability": "fresh", "age": int(now - observed),
                  "chapter": chapter if chapter is not None and 1 <= chapter <= 12 else None,
                  "gold": number(policy.get("gold")),
                  "year": int(month_match[1]) if month_match else None,
                  "month": int(month_match[2]) if month_match else None,
                  "pending_plan": pending,
                  "captured": len(captured) if isinstance(captured, list) else None,
                  "captured_names": _hanjuku_names(captured),
                  "lost_names": _hanjuku_names(policy.get("lost")),
                  "home_lost": policy.get("home_lost") is True,
                  "garrison": _hanjuku_garrison(policy),
                  "marching": _hanjuku_marching(policy, tick),
                  "active_order": _hanjuku_active_order(policy),
                  "soldiers": number(policy.get("soldiers_seen")),
                  "wins": number(stats.get("wins")), "losses": number(stats.get("losses")),
                  "unclassified": number(stats.get("unclassified")),
                  # Policy-side battle accounting. Distinct from the
                  # run-side battles_started/finished pair below, which counts
                  # debounced frame transitions; the two are reconciled on the
                  # panel instead of being silently mixed.
                  "battles_recorded": battles_started,
                  "battles_judged": battles_judged,
                  "battles_unjudged": battles_unjudged,
                  "castle_losses": number(tally.get("castle_losses")),
                  "cards_confirmed": number(stats.get("cards_confirmed")),
                  "orders_launched": (sum(1 for value in orders.values() if value == "launched")
                                      if orders is not None else None),
                  "orders_failed": (sum(1 for value in orders.values() if value == "failed")
                                    if orders is not None else None),
                  "eggs": [row for row in
                           ({"general": _hanjuku_name(name), "uses": uses}
                            for name, uses in sorted(egg_uses.items(), key=lambda kv: str(kv[0]))
                            if _hanjuku_name(name) and type(uses) is int and 0 <= uses <= 4)
                           if row["general"]][:3],
                  "enemy": _hanjuku_name(battle.get("enemy")) if battle else None,
                  "ally": _hanjuku_name(battle.get("ally")) if battle else None,
                  "enemy_hp": number(battle.get("enemy_hp")) if battle else None,
                  "ally_hp": number(battle.get("ally_hp")) if battle else None,
                  "battles_started": number(run.get("battles_started")),
                  "battles_finished": number(run.get("battles_finished")),
                  "observations": number(run.get("observations")),
                  "unchanged_seconds": int(run["unchanged_seconds"])
                                       if type(run.get("unchanged_seconds")) in (int, float)
                                       and math.isfinite(run["unchanged_seconds"])
                                       and run["unchanged_seconds"] >= 0 else None,
                  "actions": number(run.get("actions_sent")),
                  "phase": run.get("phase") if run.get("phase") in {"field", "battle", "title", "name", "unknown"} else None,
                  "screen": bot.get("screen_kind") if isinstance(bot.get("screen_kind"), str) else None,
                  # Bounded like every other cached string: a control character or
                  # an unbounded blob in policy memory must not reach the card.
                  "variant": _hanjuku_name(policy.get("variant"), each=24),
                  "chart_step": _hanjuku_name(policy.get("active"), each=24)}
        # Recheck generation after reads; never carry the previous game into a switch.
        if result.get('gap'):
            result['gap']['until'] = observed + 30
        latest, _ = _bounded_state(root / "game_switch.json")
        if latest.get("phase") != "ready" or latest.get("active") != active:
            return unavailable
        return result
    except (OSError, ValueError, TypeError, OverflowError):
        return unavailable


def _console_progress(root, state, history, history_status, now):
    """Display recorded outcomes and the plan, never infer a current score.

    A result timestamp is an observation of a completed match. The corner
    state's mtime and this projection's creation time are not gameplay
    observations. Missing or unreadable history must not become zero results.
    """
    start = _parse_timestamp(state.get("started_at"))
    completed = _parse_timestamp(state.get("completed_at"))
    stop = completed if completed is not None else now
    valid_window = (start is not None and 0 < start <= stop <= now)
    session = ([row for row in history if row["ts"] is not None
                and start <= row["ts"] <= stop] if valid_window else None)
    if history_status != "readable":
        session = None
    scores = [row["score"] for row in session] if session is not None else None
    target = _int_value(state.get("target_matches"))
    target = target if target is not None and 1 <= target <= 100 else None
    deadline = _parse_timestamp(state.get("ends_at"))
    if deadline is not None and (start is None or deadline < start):
        deadline = None
    status = state.get("status")
    # Only a generation bound to this corner proves the active record is
    # current. Same game in a later runtime, old/manual state, or missing
    # canonical data remain unverified. Reading this file changes nothing.
    ownership = "unverified"
    try:
        canonical, _ = _bounded_state(root / "game_switch.json")
        active = canonical.get("active") or {}
        if (canonical.get("phase") in {"ready", "draining"}
                and isinstance(active, dict) and active.get("game") == state.get("game")
                and isinstance(state.get("rotation_runtime_id"), str)
                and state.get("rotation_runtime_id")
                and active.get("runtime_id") == state.get("rotation_runtime_id")):
            ownership = "matched"
    except (OSError, ValueError, TypeError):
        pass
    if (status in {"preparing", "starting"}
            or (ownership != "matched" and completed is None)):
        # Start-time filtering alone cannot bind rows from a later visit of
        # the same game to a stale corner. Do not publish them as this run.
        session = None
        scores = None
    # A plan describes the recorded lifecycle, not a command or recovery action.
    if status in {"preparing", "starting"}:
        next_step = "switch-wait"
    elif status == "restoring":
        next_step = "restore-wait"
    elif status == "recovery_required":
        next_step = "recovery-wait"
    elif ownership != "matched" or not valid_window or canonical.get("phase") != "ready":
        next_step = "unverified"
    elif target is not None and scores is not None and len(scores) >= target:
        next_step = "target-recorded"
    elif deadline is not None and now >= deadline:
        next_step = "deadline-passed"
    else:
        next_step = "collect-results"
    # Fixed categories only; do not publish exception text, paths or arbitrary
    # fields from corner state. Unknown reasons stay unknown.
    reasons = {"game_over", "screen_stalled", "manual_saved_stop", "manual_forced_stop",
               "switch-terminal-before-corner-active"}
    errors = {"recovery_required", "quiesce_failed", "readiness_timeout", "deadline_exceeded"}
    reason = state.get("end_reason")
    error = state.get("last_error_code")
    return {"history_status": history_status, "session_count": len(scores) if scores is not None else None,
            "session_mean": sum(scores) / len(scores) if scores else None,
            "session_best": max(scores) if scores else None,
            "latest": ({"score": session[-1]["score"], "at": session[-1]["ts"],
                        "age": int(now-session[-1]["ts"])} if session else None),
            "target": target, "remaining": max(0, target-len(scores))
            if target is not None and scores is not None else None,
            "remaining_seconds": max(0, int(deadline-now)) if deadline is not None else None,
            "ownership": ownership, "next": next_step,
            "reason": reason if isinstance(reason, str) and reason in reasons else
                      error if isinstance(error, str) and error in errors else None}


def load_active_corner(state_dir: str | os.PathLike[str] | None = None, *,
                       soren_root: str | os.PathLike[str] | None = None,
                       now: float | None = None) -> dict[str, object] | None:
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
        snapshot["scores"], history_status = _score_history(root, state)
        snapshot["session_matches"] = _session_count(snapshot["scores"], state)
        snapshot["target_matches"] = _int_value(state.get("target_matches"))
        snapshot["strategy_ranking"] = _strategy_ranking(root, snapshot["game"])
        if snapshot["game"] in CONSOLE_GAMES:
            observed_now = time.time() if now is None else now
            snapshot["scores"] = [row for row in snapshot["scores"]
                                  if row["ts"] is None or row["ts"] <= observed_now]
            snapshot["console"] = _console_progress(root, state, snapshot["scores"], history_status,
                                                     observed_now)
            snapshot["session_matches"] = snapshot["console"]["session_count"]
        if snapshot["game"] == "hanjuku-hero":
            snapshot["hanjuku"] = _hanjuku_snapshot(root, state)
    elif kind == "paper":
        observed_now = time.time() if now is None else float(now)
        snapshot["paper"] = _paper_snapshot(root, now=observed_now)
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

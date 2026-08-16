#!/usr/bin/env python3
"""Render the active, explicit AI rate-limit backoff for status surfaces.

The backoff files are created only for provider responses classified as an
explicit rate limit by ``lib/ai_generate.sh``.  This module deliberately
reads the effective comment agent list so the status reflects the models that
can actually be used for comment replies, rather than unrelated classifier or
translation calls.
"""

from __future__ import annotations

import argparse
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional


DEFAULT_COMMENT_MAIN = "codex:deepseek-v4-flash"
DEFAULT_COMMENT_FALLBACK = "codex:minimax-m3"


def _dotenv_values(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values.setdefault(key, value)
    return values


def _configured_value(name: str, dotenv: Dict[str, str], default: str = "") -> str:
    value = os.environ.get(name)
    if value is not None and value.strip():
        return value.strip()
    return str(dotenv.get(name, default) or default).strip()


def _split_agents(raw: str) -> List[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def effective_comment_agents(base_dir: Optional[Path] = None) -> List[str]:
    """Return the configured primary/fallback comment agents in priority order."""

    root = base_dir or Path.cwd()
    dotenv = _dotenv_values(root / ".env")
    raw = _configured_value("COMMENT_AGENTS", dotenv)
    agents = _split_agents(raw)
    if agents:
        return agents
    main = _configured_value("COMMENT_MAIN_AGENT", dotenv, DEFAULT_COMMENT_MAIN)
    fallback = _configured_value("COMMENT_MAIN_FALLBACK", dotenv, DEFAULT_COMMENT_FALLBACK)
    return [agent for agent in (main, fallback) if agent]


def sanitize_agent(agent: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "_", str(agent or "").lower())
    return value.strip("_")


def _model_name(agent: str) -> str:
    value = str(agent or "")
    if ":" in value:
        value = value.split(":", 1)[1]
    return value or agent


def _fmt_remaining(seconds: int) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 3600:
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        return f"{hours}h{minutes:02d}m" if minutes else f"{hours}h"
    if seconds >= 60:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def _compact_role_label(row: dict) -> str:
    role = "main" if row["role"] == "main" else "fb"
    if row["active"]:
        return f"{role}={row['model']}({row['remaining_text']})"
    return f"{role}=ready"


def _state_dir(base_dir: Optional[Path] = None) -> Path:
    if os.environ.get("AI_BACKOFF_DIR"):
        return Path(os.environ["AI_BACKOFF_DIR"])
    if os.environ.get("TMP_STATE_DIR"):
        return Path(os.environ["TMP_STATE_DIR"]) / "ai_backoff"
    return (base_dir or Path.cwd()) / "tmp/state/ai_backoff"


def _read_until(path: Path) -> Optional[int]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore").strip().splitlines()[0]
        value = int(raw)
    except (OSError, IndexError, TypeError, ValueError):
        return None
    return value if value > 0 else None


def load_status(now: Optional[int] = None, base_dir: Optional[Path] = None) -> Optional[dict]:
    """Return status data for active main/fallback rate-limit backoffs."""

    root = base_dir or Path.cwd()
    state_dir = _state_dir(root)
    epoch = int(time.time()) if now is None else int(now)
    agents = effective_comment_agents(root)
    roles = ("main", "fallback")
    rows = []
    seen = set()
    for index, agent in enumerate(agents[:2]):
        role = roles[index]
        key = sanitize_agent(agent)
        if not key or key in seen:
            continue
        seen.add(key)
        until = _read_until(state_dir / key)
        remaining = max(0, int(until or 0) - epoch)
        active = until is not None and remaining > 0
        rows.append(
            {
                "role": role,
                "agent": agent,
                "model": _model_name(agent),
                "active": active,
                "remaining": remaining,
                "remaining_text": _fmt_remaining(remaining) if active else "ready",
            }
        )

    active_rows = [row for row in rows if row["active"]]
    if not active_rows:
        return None
    return {
        "active": True,
        "both_limited": len(active_rows) >= 2,
        "roles": rows,
        "label": " ".join(_compact_role_label(row) for row in rows),
    }


def status_label(now: Optional[int] = None, base_dir: Optional[Path] = None) -> str:
    status = load_status(now=now, base_dir=base_dir)
    return status["label"] if status else ""


def status_lines(now: Optional[int] = None, base_dir: Optional[Path] = None) -> List[str]:
    status = load_status(now=now, base_dir=base_dir)
    if not status:
        return []
    return [_compact_role_label(row) for row in status["roles"]]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", action="store_true", help="print a compact status label")
    parser.add_argument("--lines", action="store_true", help="print one role per line")
    args = parser.parse_args()
    if args.lines:
        for line in status_lines():
            print(line)
    elif args.label:
        print(status_label())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

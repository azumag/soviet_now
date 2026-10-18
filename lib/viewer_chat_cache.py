"""Refresh ChatObs only when its successfully read source snapshot changes."""

import json
import os
import subprocess


def source_snapshot(stat):
    """Use input identity, not the later JSON publication time, as the watermark."""
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def monitor_lookback(value):
    try:
        return max(20, int(value))
    except (TypeError, ValueError):
        return 200


def _valid_cache(payload, source_file, snapshot, lookback):
    return (
        isinstance(payload, dict)
        and payload.get("source") == source_file
        and "source_snapshot" in payload
        and payload["source_snapshot"] == snapshot
        and payload.get("lookback") == lookback
        and isinstance(payload.get("epoch"), int)
        and isinstance(payload.get("latest"), str)
        and isinstance(payload.get("recent"), list)
        and all(isinstance(line, str) for line in payload["recent"])
        and isinstance(payload.get("count"), int)
    )


def refresh_viewer_chat_monitor_if_changed(source_file, monitor_file, lookback=200):
    """Keep failed reads retryable and leave the previous summary intact.

    This runs in the HTML renderer's existing Python process, so an unchanged
    source does not launch a stat command or another Python interpreter.
    """
    source_file = os.fspath(source_file)
    monitor_file = os.fspath(monitor_file)
    lookback = monitor_lookback(lookback)
    try:
        snapshot = source_snapshot(os.stat(source_file))
    except FileNotFoundError:
        snapshot = None
    except OSError:
        return False
    try:
        with open(monitor_file, encoding="utf-8") as stream:
            payload = json.load(stream)
        if _valid_cache(payload, source_file, snapshot, lookback):
            return False
    except (OSError, ValueError):
        # Absent input and absent cache need no producer. A removed input with
        # an existing summary still refreshes to an empty, valid snapshot.
        if snapshot is None and not os.path.exists(monitor_file):
            return False
    env = dict(os.environ, VIEWER_CHAT_MONITOR_SOURCE=source_file,
               VIEWER_CHAT_MONITOR_FILE=monitor_file,
               VIEWER_CHAT_MONITOR_LOOKBACK=str(lookback))
    try:
        result = subprocess.run(["./viewer_chat_monitor.sh", "json"], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return False
    return result.returncode == 0

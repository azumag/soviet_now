#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
GAME_HISTORY_DIR = ROOT / "game_history"
RUSSIA_HISTORY_FILE = ROOT / "tmp/history/russia_creation_history.tsv"
SOVIET_HISTORY_FILE = ROOT / "tmp/history/soviet_creation_history.tsv"
GAME_HISTORY_RE = re.compile(r"^(?P<stamp>\d{8}_\d{6})_score\d+\.jsonl$")


def local_timezone():
    return datetime.now().astimezone().tzinfo


def parse_archive_time(path: Path) -> tuple[str, str] | None:
    match = GAME_HISTORY_RE.match(path.name)
    if not match:
        return None
    stamp = match.group("stamp")
    dt = datetime.strptime(stamp, "%Y%m%d_%H%M%S").replace(tzinfo=local_timezone())
    return dt.isoformat(), dt.strftime("%Y-%m-%d %H:%M %Z")


def scan_game_file(path: Path) -> dict[str, tuple[str, str] | None]:
    found: dict[str, tuple[str, str] | None] = {"russia": None, "soviet": None}
    try:
        with path.open(encoding="utf-8", errors="ignore") as f:
            for raw in f:
                try:
                    item = json.loads(raw)
                except Exception:
                    continue
                if found["russia"] is None and item.get("russia_created"):
                    found["russia"] = (str(item.get("score", "")), str(item.get("turn", "")))
                if found["soviet"] is None and item.get("soviet_created"):
                    found["soviet"] = (str(item.get("score", "")), str(item.get("turn", "")))
                if found["russia"] is not None and found["soviet"] is not None:
                    break
    except Exception:
        return {"russia": None, "soviet": None}
    return found


def read_existing(path: Path) -> list[tuple[str, str, str, str, str]]:
    rows: list[tuple[str, str, str, str, str]] = []
    if not path.exists():
        return rows
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            cols = raw.rstrip("\n").split("\t")
            if len(cols) < 5:
                continue
            rows.append(tuple(cols[:5]))
    except Exception:
        return []
    return rows


def collect_backfill_rows() -> dict[str, list[tuple[str, str, str, str, str]]]:
    rows: dict[str, list[tuple[str, str, str, str, str]]] = {"russia": [], "soviet": []}
    for path in sorted(GAME_HISTORY_DIR.glob("*.jsonl")):
        if path.name == "latest.jsonl":
            continue
        ts_pair = parse_archive_time(path)
        if ts_pair is None:
            continue
        iso_ts, local_ts = ts_pair
        found = scan_game_file(path)
        for kind in ("russia", "soviet"):
            if found[kind] is None:
                continue
            score, turns = found[kind]
            rows[kind].append((iso_ts, local_ts, "", score, turns))
    return rows


def merge_rows(
    existing: list[tuple[str, str, str, str, str]],
    backfilled: list[tuple[str, str, str, str, str]],
) -> list[tuple[str, str, str, str, str]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    merged: list[tuple[str, str, str, str, str]] = []
    for row in sorted(existing + backfilled, key=lambda r: (r[0], r[2], r[3], r[4])):
        if row in seen:
            continue
        seen.add(row)
        merged.append(row)
    return merged


def write_rows(path: Path, rows: list[tuple[str, str, str, str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join("\t".join(row) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    backfilled = collect_backfill_rows()
    targets = {
        "russia": RUSSIA_HISTORY_FILE,
        "soviet": SOVIET_HISTORY_FILE,
    }

    for kind in ("russia", "soviet"):
        existing = read_existing(targets[kind])
        merged = merge_rows(existing, backfilled[kind])
        print(f"{kind}\tbackfilled={len(backfilled[kind])}\tmerged={len(merged)}\tfile={targets[kind]}")
        if args.write:
            write_rows(targets[kind], merged)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

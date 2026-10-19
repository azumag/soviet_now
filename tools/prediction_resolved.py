"""Hand one settled Twitch prediction row to the docich 予想王 leaderboard.

The resolve paths already fetch the PATCH response today and discard it. This
adapter appends the response's single prediction row to a bounded, append-only
handoff file under the Soren state directory. docich owns the ledger and reads
that file (`src/docich/soren_predictions.py`); Soren never writes the ledger and
never keeps a viewer name beyond this local handoff.

Fixed contract:
  ``<TMP_STATE_DIR>/prediction_resolved.jsonl``
  one JSON object per line = one Helix prediction row, oldest first, max 50 lines.

Two kinds of rows are appended:

* a well-formed ``RESOLVED`` row (the default);
* a well-formed ``CANCELED`` row **only when the caller asserts the fixed
  no-contest refund reason** (``--cancel-reason no-contest``). The reason is
  part of the record: without it a canceled row is never handed over, and a
  resolved row is never annotated as a refund.

A malformed body writes nothing and still exits 0, so a broken response can
never break the settle path.
"""
from __future__ import annotations

import argparse
import fcntl
import json
from pathlib import Path
import sys

FILE = "prediction_resolved.jsonl"
LOCK_NAME = ".prediction_resolved.lock"
MAX_LINES = 50
MAX_BYTES = 512 * 1024
MAX_TEXT = 128
#: Fixed vocabulary for the reason a canceled row was refunded. Only the
#: owner-decided no-contest cancellation is handed over today.
CANCEL_REASONS = frozenset({"no-contest"})


def _single_row(body: bytes) -> dict | None:
    """Return the structurally valid single prediction row of a response body."""
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    rows = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    row = rows[0]
    if (not isinstance(row, dict)
            or not isinstance(row.get("id"), str) or not 1 <= len(row["id"]) <= MAX_TEXT
            or not isinstance(row.get("title"), str)
            or not isinstance(row.get("outcomes"), list)
            or not 2 <= len(row["outcomes"]) <= 10):
        return None
    for outcome in row["outcomes"]:
        if (not isinstance(outcome, dict) or not isinstance(outcome.get("id"), str)
                or not 1 <= len(outcome["id"]) <= MAX_TEXT
                or not isinstance(outcome.get("title"), str)):
            return None
    return row


def resolved_row(body: bytes) -> dict | None:
    """Return the single ``RESOLVED`` prediction row of a Helix response body."""
    row = _single_row(body)
    if row is None or row.get("status") != "RESOLVED":
        return None
    winner = row.get("winning_outcome_id")
    if not isinstance(winner, str) or not 1 <= len(winner) <= MAX_TEXT:
        return None
    if winner not in {outcome["id"] for outcome in row["outcomes"]}:
        return None
    return row


def settled_row(body: bytes, *, cancel_reason: str | None = None) -> dict | None:
    """Return the single settled row, annotating a no-contest refund when asked.

    ``cancel_reason`` must be one of :data:`CANCEL_REASONS`. With it the row has
    to be a ``CANCELED`` prediction that names no winner: the caller is the
    settle path that proved the prediction never formed a contest, so the reason
    is recorded with the row instead of being re-guessed downstream. Without it
    only a ``RESOLVED`` row is handed over, exactly as before.
    """
    if cancel_reason is not None and cancel_reason not in CANCEL_REASONS:
        return None
    if cancel_reason is None:
        return resolved_row(body)
    row = _single_row(body)
    if row is None or row.get("status") != "CANCELED":
        return None
    if row.get("winning_outcome_id") not in (None, ""):
        return None
    return {**row, "cancel_reason": cancel_reason}


def append(row: dict, target: Path, *, limit: int = MAX_LINES) -> bool:
    """Append one row under the shared lock, keeping the file bounded."""
    if type(limit) is not int or limit < 1:
        raise ValueError("invalid limit")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with (target.parent / LOCK_NAME).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        lines = []
        if target.exists():
            if target.is_symlink() or not target.is_file() or target.stat().st_size > MAX_BYTES:
                return False
            lines = [line for line in target.read_text(encoding="utf-8").splitlines()
                     if line.strip()]
        lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True))
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text("\n".join(lines[-limit:]) + "\n", encoding="utf-8")
        temporary.replace(target)
        return True


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="settled prediction handoff for docich")
    parser.add_argument("response", type=Path)
    parser.add_argument("--target", type=Path, default=Path("tmp/state") / FILE,
                        help="bounded handoff file docich reads")
    parser.add_argument("--cancel-reason", choices=sorted(CANCEL_REASONS), default=None,
                        help="assert the fixed no-contest refund reason of a CANCELED row")
    args = parser.parse_args(argv)
    try:
        row = settled_row(args.response.read_bytes(), cancel_reason=args.cancel_reason)
        if row is None:
            print("prediction resolved handoff skipped", file=sys.stderr)
            return 0
        if not append(row, args.target):
            print("prediction resolved handoff refused", file=sys.stderr)
    except OSError:
        print("prediction resolved handoff unavailable", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Retain bounded positive stage observations; never decide or promote a policy."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat

MAX_BYTES = 16 * 1024 * 1024
MAX_ROWS = 20000
MAX_PIECES = 256
_HASH = re.compile(r"[0-9a-f]{12,64}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_BINDING = ("idx", "arm", "game_num", "archive", "hash", "played_hash", "history_hash", "turns")


class EvidenceError(ValueError):
    """Only fixed categories, never raw input or paths."""


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise EvidenceError("duplicate_json_key")
        obj[key] = value
    return obj


def _constant(_):
    raise EvidenceError("nonfinite_json")


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise EvidenceError("nonfinite_json")
    return value


def _decode(line):
    try:
        return json.loads(line, object_pairs_hook=_unique, parse_constant=_constant, parse_float=_float)
    except EvidenceError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvidenceError("invalid_json") from exc


def _revision(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def read_archive(path):
    path = Path(path)
    try:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
            raise EvidenceError("safe_read_unsupported")
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise EvidenceError("symlink_archive")
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise EvidenceError("not_bounded_regular_file")
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (len(raw) > MAX_BYTES or _revision(before) != _revision(after)
                or _revision(after) != _revision(os.stat(path, follow_symlinks=False))):
            raise EvidenceError("archive_changed")
        return raw
    except OSError as exc:
        raise EvidenceError("archive_unreadable") from exc


def row_binding(row):
    if not isinstance(row, dict):
        raise EvidenceError("invalid_row_identity")
    h = row.get("hash")
    turns = row.get("turns")
    game = row.get("game_num")
    archive = row.get("archive")
    if (type(row.get("idx")) is not int or not 0 <= row["idx"] <= 2**53
            or row.get("arm") not in ("A", "B")
            or not isinstance(game, str) or not re.fullmatch(r"[0-9]{1,12}", game)
            or not isinstance(archive, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,200}\.jsonl", archive)
            or not isinstance(h, str) or not _HASH.fullmatch(h)
            or row.get("played_hash") != h or row.get("history_hash") != h
            or row.get("tainted") is not False
            or type(turns) not in (int, float) or not 1 <= turns <= MAX_ROWS or int(turns) != turns):
        raise EvidenceError("invalid_row_identity")
    data = {key: row[key] for key in _BINDING}
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def summarize(raw, row):
    binding = row_binding(row)
    if len(raw) > MAX_BYTES:
        raise EvidenceError("archive_oversize")
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeError as exc:
        raise EvidenceError("archive_encoding") from exc
    lines = [line for line in lines if line.strip()]
    if not 1 <= len(lines) <= MAX_ROWS or len(lines) != row["turns"]:
        raise EvidenceError("turn_count_mismatch")
    first_single = first_pair = None
    max_count = 0
    # Every row must belong to one contiguous, single-policy recorded session.
    # A missing frame or a captured count of zero never proves non-attainment.
    for turn, line in enumerate(lines, 1):
        item = _decode(line)
        if (not isinstance(item, dict) or type(item.get("turn")) is not int
                or item["turn"] != turn or item.get("strategy_hash") != row["hash"]):
            raise EvidenceError("history_lineage_unverified")
        snapshot = item.get("state_snapshot")
        pieces = snapshot.get("pieces") if isinstance(snapshot, dict) else None
        if (not isinstance(pieces, list) or len(pieces) > MAX_PIECES
                or any(not isinstance(p, dict) or type(p.get("type")) is not int
                       or not 1 <= p["type"] <= 16 for p in pieces)):
            raise EvidenceError("history_pieces_unknown")
        # Duplicate IDs can create a fictitious pair. Missing IDs are legacy
        # observations, not permission to assert two different pieces existed.
        ids = [p.get("id") for p in pieces]
        if any(type(value) is not int or not -(2**53) <= value <= 2**53 for value in ids):
            raise EvidenceError("piece_identity_unknown")
        if len(set(ids)) != len(ids):
            raise EvidenceError("duplicate_piece_identity")
        count = sum(p["type"] == 15 for p in pieces)
        max_count = max(max_count, count)
        if count and first_single is None:
            first_single = turn
        if count >= 2 and first_pair is None:
            first_pair = turn
    digest = hashlib.sha256(raw).hexdigest()
    return {"archive_sha256": digest, "stage_evidence": {
        "schema_version": 1, "status": "observed", "reason": None,
        "basis": "contiguous_snapshot_observations", "archive_sha256": digest,
        "row_binding_sha256": binding, "strategy_hash": row["hash"],
        "recorded_turns": len(lines), "max_t15_observed": max_count,
        "first_turn_t15_observed": first_single, "first_turn_two_t15_observed": first_pair,
        "first_russia_observed": True if first_single is not None else None,
        "two_russias_observed": True if first_pair is not None else None,
        "absence_is_unknown": True,
    }}


def capture_stage_evidence(path, row):
    """Called by the existing completed-game writer; failures add no success."""
    try:
        row_binding(row)
        if Path(path).name != row["archive"]:
            raise EvidenceError("archive_name_mismatch")
        return summarize(read_archive(path), row)
    except EvidenceError as exc:
        return {"stage_evidence": {"schema_version": 1, "status": "unavailable", "reason": str(exc)}}


def retained_pair(row):
    """Consume the producer-bound observation after raw history is pruned.

    Returns (positive observation or None, fixed reason). Hashes establish
    internal linkage, not authenticity, full bundle identity or causality.
    """
    evidence = row.get("stage_evidence") if isinstance(row, dict) else None
    if evidence is None:
        return None, "not_recorded"
    if not isinstance(evidence, dict):
        return None, "invalid_stage_evidence"
    if evidence.get("status") == "unavailable":
        return None, "producer_unavailable"
    try:
        digest = row.get("archive_sha256")
        n = evidence.get("recorded_turns")
        maximum = evidence.get("max_t15_observed")
        first = evidence.get("first_turn_t15_observed")
        pair = evidence.get("first_turn_two_t15_observed")
        if (type(evidence.get("schema_version")) is not int or evidence["schema_version"] != 1
                or evidence.get("status") != "observed"
                or evidence.get("basis") != "contiguous_snapshot_observations"
                or evidence.get("absence_is_unknown") is not True
                or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                or evidence.get("archive_sha256") != digest
                or evidence.get("row_binding_sha256") != row_binding(row)
                or evidence.get("strategy_hash") != row.get("hash")
                or type(n) is not int or n != row.get("turns")
                or type(maximum) is not int or not 0 <= maximum <= MAX_PIECES):
            raise EvidenceError("invalid_stage_evidence")
        for observed, at, threshold in (("first_russia_observed", first, 1), ("two_russias_observed", pair, 2)):
            yes = maximum >= threshold
            if yes:
                if evidence.get(observed) is not True or type(at) is not int or not 1 <= at <= n:
                    raise EvidenceError("invalid_stage_evidence")
            elif evidence.get(observed) is not None or at is not None:
                raise EvidenceError("invalid_stage_evidence")
        if pair is not None and (first is None or pair < first):
            raise EvidenceError("invalid_stage_evidence")
        return (True if pair is not None else None), "retained_observation"
    except EvidenceError:
        return None, "invalid_stage_evidence"

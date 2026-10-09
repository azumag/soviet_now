#!/usr/bin/env python3
"""Read-only fixed-baseline audit of SorenGame's existing A/B ledger.

This is descriptive evidence, not a promotion gate or a gameplay evaluator.
No imports from strategy code, network calls, subprocesses, or input writes.
"""
import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import statistics
import sys

MAX_BYTES = 32 * 1024 * 1024
HASH = re.compile(r"[0-9a-f]{12,64}\Z")
CLASSES = {"continue", "significant_win", "provisional_win", "loss", "neutral",
           "inconclusive", "invalid"}
VERDICTS = {"ADOPT", "CONTINUE", "ABORT", "REJECT_HARM", "REJECT_FUTILE",
            "REJECT_INCONCLUSIVE"}


class EvidenceError(ValueError):
    """Messages are fixed categories; never echo input text or local paths."""


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise EvidenceError("duplicate_json_key")
        result[key] = value
    return result


def _constant(_):
    raise EvidenceError("nonfinite_json")


def _float(text):
    value = float(text)
    if not math.isfinite(value):
        raise EvidenceError("nonfinite_json")
    return value


def decode(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant, parse_float=_float)
    except EvidenceError:
        raise
    except (ValueError, RecursionError) as exc:
        raise EvidenceError("invalid_json") from exc


def read_file(path):
    """Bounded, nonblocking read of a regular file; reject concurrent changes."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        with os.fdopen(os.open(path, flags), "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_BYTES:
                raise EvidenceError("not_bounded_regular_file")
            data = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        revision = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        current = os.stat(path, follow_symlinks=False)
        if (len(data) > MAX_BYTES or revision(before) != revision(after)
                or revision(after) != revision(current)):
            raise EvidenceError("input_changed_or_oversize")
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeError) as exc:
        raise EvidenceError("input_unreadable") from exc


def offset(value):
    if not re.fullmatch(r"[+-](?:0\d|1[0-4]):[0-5]\d", value):
        raise EvidenceError("invalid_source_offset")
    minutes = int(value[1:3]) * 60 + int(value[4:6])
    if minutes > 14 * 60:
        raise EvidenceError("invalid_source_offset")
    return timezone(timedelta(minutes=minutes * (-1 if value[0] == "-" else 1)))


def timestamp(value, source_offset=None):
    if not isinstance(value, str) or "T" not in value:
        raise EvidenceError("invalid_timestamp")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("invalid_timestamp") from exc
    if date.tzinfo is None:
        if source_offset is None:
            raise EvidenceError("naive_timestamp_requires_source_offset")
        date = date.replace(tzinfo=source_offset)
    return date.astimezone(timezone.utc)


def integer(value):
    return type(value) is int and value >= 0


def number(value):
    return (type(value) in (int, float) and 0 <= value <= 2 ** 53
            and math.isfinite(value))


def game_number(value):
    if integer(value):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value):
        return int(value)
    return None


def rate(values):
    yes = sum(v is True for v in values)
    no = sum(v is False for v in values)
    total = len(values)
    unknown = total - yes - no
    return {"successes": yes, "known": yes + no, "unknown": unknown, "total": total,
            "rate_among_known": yes / (yes + no) if yes + no else None,
            # Missing-outcome bounds, NOT a statistical confidence interval.
            "missing_outcome_bounds": [yes / total, (yes + unknown) / total] if total else None}


def distribution(values):
    known = sorted(v for v in values if number(v))
    return {"n": len(known), "missing": len(values) - len(known),
            "mean": statistics.mean(known) if known else None,
            "median": statistics.median(known) if known else None,
            "p25": known[int((len(known) - 1) * .25)] if known else None}


def stages(row):
    founded = row.get("soviet_created")
    founded = founded if type(founded) is bool else None
    russia = row.get("russia_created")
    russia = russia if type(russia) is bool else None
    t15 = row.get("t15")
    if type(t15) is int and t15 in (0, 1):
        if russia is not None and russia != bool(t15):
            russia = None
        elif russia is None:
            russia = bool(t15)
    if founded is True and russia is False:
        russia = None  # Recorded first-stage absence contradicts a positive terminal counter.
    # A positive counter does not prove that a T15 pair was captured in a frame.
    return {"founding": founded, "first_russia": russia,
            "two_russias_observed": row.get("_two_russias_observed")}


def history_pair(text, expected_hash):
    """A positive observation only. No pair in snapshots is NOT a failure."""
    rows = [decode(line) for line in text.splitlines() if line.strip()]
    if not rows:
        raise EvidenceError("empty_history")
    seen = False
    for turn, row in enumerate(rows, 1):
        if (not isinstance(row, dict) or type(row.get("turn")) is not int
                or row["turn"] != turn or row.get("strategy_hash") != expected_hash):
            raise EvidenceError("history_lineage_unverified")
        snapshot = row.get("state_snapshot")
        pieces = snapshot.get("pieces") if isinstance(snapshot, dict) else None
        if not isinstance(pieces, list):
            raise EvidenceError("history_pieces_unknown")
        if any(not isinstance(p, dict) or type(p.get("type")) is not int
               or not 1 <= p["type"] <= 16 for p in pieces):
            raise EvidenceError("history_pieces_invalid")
        seen |= sum(p["type"] == 15 for p in pieces) >= 2
    return True if seen else None


def enrich(rows, history_dir):
    result, counts, digests = [], Counter(), []
    root = Path(history_dir).resolve() if history_dir else None
    for row in rows:
        item = dict(row)
        item["_two_russias_observed"] = None
        if root is None:
            counts["not_requested"] += 1
        else:
            name = row.get("archive")
            if (not isinstance(name, str) or not name.endswith(".jsonl")
                    or "/" in name or "\\" in name or name in (".", "..")):
                counts["invalid_archive_name"] += 1
            else:
                try:
                    text, digest = read_file(root / name)
                    if row.get("archive_sha256") != digest:
                        raise EvidenceError("history_digest_unbound")
                    item["_two_russias_observed"] = history_pair(text, row["hash"])
                    counts["read"] += 1
                    digests.append({"idx": row["idx"], "sha256": digest})
                except EvidenceError as exc:
                    counts[str(exc)] += 1
        result.append(item)
    return result, {"counts": dict(sorted(counts.items())), "sources": digests}


def paired(rows, pattern, baseline_arm, key, binary=False):
    by_index = {r["idx"]: r for r in rows}
    width = len(pattern)
    diffs = []
    for block in sorted({i // width for i in by_index}):
        group = [by_index.get(block * width + i) for i in range(width)]
        if any(r is None for r in group):
            continue
        values = [stages(r)[key] if binary else r.get(key) for r in group]
        if any(type(v) is not bool if binary else not number(v) for v in values):
            continue
        base = [int(v) if binary else v for r, v in zip(group, values)
                if r["arm"] == baseline_arm]
        cand = [int(v) if binary else v for r, v in zip(group, values)
                if r["arm"] != baseline_arm]
        diffs.append(statistics.mean(cand) - statistics.mean(base))
    return {"complete_blocks": len(diffs),
            "mean_candidate_minus_baseline": statistics.mean(diffs) if diffs else None}


def build_report(state, rows, baseline_hash, *, source_offset=None, as_of=None,
                 days=None, history_dir=None, decision=None):
    if not isinstance(state, dict) or not isinstance(rows, list):
        raise EvidenceError("invalid_input_shape")
    if not isinstance(baseline_hash, str) or not HASH.fullmatch(baseline_hash):
        raise EvidenceError("invalid_baseline_hash")
    hashes = {"A": state.get("a_hash"), "B": state.get("b_hash")}
    if any(not isinstance(h, str) or not HASH.fullmatch(h) for h in hashes.values()):
        raise EvidenceError("invalid_experiment_hashes")
    arms = [arm for arm, h in hashes.items() if h == baseline_hash]
    if len(arms) != 1:
        raise EvidenceError("baseline_missing_or_ambiguous")
    base = arms[0]
    candidate = "B" if base == "A" else "A"
    pattern = state.get("pattern")
    if (not isinstance(pattern, str) or not re.fullmatch(r"[AB]{2,16}", pattern)
            or pattern.count("A") != pattern.count("B")):
        raise EvidenceError("invalid_balanced_pattern")
    count, start_game = state.get("games_recorded"), game_number(state.get("game_num_start"))
    if not integer(count) or start_game is None:
        raise EvidenceError("experiment_identity_missing")
    started = timestamp(state.get("started_at"), source_offset)
    if as_of is not None and (as_of.tzinfo is None or as_of < started):
        raise EvidenceError("invalid_as_of")
    if days is not None and (not integer(days) or not 1 <= days <= 366 or as_of is None):
        raise EvidenceError("invalid_window")
    cutoff = as_of - timedelta(days=days) if days is not None else None
    indices = Counter(r.get("idx") for r in rows if isinstance(r, dict) and integer(r.get("idx")))
    games = Counter(game_number(r.get("game_num")) for r in rows if isinstance(r, dict))
    archives = Counter(r.get("archive") for r in rows if isinstance(r, dict)
                       and isinstance(r.get("archive"), str) and r["archive"])
    accepted, rejected = [], Counter()
    for row in rows:
        reason = None
        if not isinstance(row, dict):
            reason = "invalid_row"
        elif not integer(row.get("idx")) or not 0 <= row["idx"] < count:
            reason = "invalid_index"
        elif indices[row["idx"]] != 1:
            reason = "duplicate_index"
        elif row.get("game", "sorengame") != "sorengame":
            reason = "wrong_game"
        elif game_number(row.get("game_num")) is None:
            reason = "missing_game_number"
        elif games[game_number(row["game_num"])] != 1:
            reason = "duplicate_game"
        elif isinstance(row.get("archive"), str) and row["archive"] and archives[row["archive"]] != 1:
            reason = "duplicate_archive"
        elif game_number(row["game_num"]) < start_game:
            reason = "game_before_experiment"
        elif row.get("arm") != pattern[row["idx"] % len(pattern)]:
            reason = "arm_order_mismatch"
        elif row.get("tainted") is not False:
            reason = "tainted_or_unknown"
        elif any(row.get(key) != hashes[row["arm"]] for key in ("hash", "played_hash", "history_hash")):
            reason = "hash_mismatch_or_unknown"
        else:
            try:
                at = timestamp(row.get("ts"), source_offset)
                if at < started:
                    reason = "row_before_experiment"
                elif (cutoff is not None and at < cutoff) or (as_of is not None and at > as_of):
                    reason = "outside_window"
            except EvidenceError as exc:
                reason = str(exc)
        if reason:
            rejected[reason] += 1
        else:
            # Never consume caller-supplied enrichment as trusted observation.
            accepted.append({k: v for k, v in row.items() if not k.startswith("_")})
    accepted.sort(key=lambda r: r["idx"])
    accepted, history = enrich(accepted, history_dir)
    summaries = {}
    for role, arm in (("baseline", base), ("candidate", candidate)):
        group = [r for r in accepted if r["arm"] == arm]
        observed = [stages(r) for r in group]
        summaries[role] = {"arm": arm, "hash": hashes[arm], "games": len(group),
                           "stages": {k: rate([s[k] for s in observed]) for k in
                                      ("founding", "first_russia", "two_russias_observed")},
                           "score": distribution([r.get("score") for r in group]),
                           "turns": distribution([r.get("turns") for r in group]),
                           "founding_given_first_russia": rate([s["founding"] for s in observed
                                                               if s["first_russia"] is True])}
    provided = decision if isinstance(decision, dict) else {}
    cls, verdict = provided.get("decision_class"), provided.get("verdict")
    adoption = {"decision_class": cls if isinstance(cls, str) and cls in CLASSES else "unknown",
                "verdict": verdict if isinstance(verdict, str) and verdict in VERDICTS else "unknown",
                "binding": "supplied_file_only_not_verified" if decision is not None else "not_supplied"}
    return {"schema_version": 1, "game": "sorengame", "kind": "fixed_baseline_audit",
            "comparison_status": "descriptive_only", "verified_improvement": False,
            "limitations": ["full_policy_bundle_identity_not_recorded",
                            "independent_confirmation_not_supplied",
                            "snapshot_pair_absence_is_unknown",
                            "legacy_archive_digest_missing_is_unknown",
                            "interrupted_attempts_not_in_completed_game_ledger"],
            "window": {"as_of": as_of.isoformat() if as_of else None, "days": days,
                       "source_offset": str(source_offset) if source_offset else None},
            "input_rows": len(rows), "accepted_rows": len(accepted),
            "excluded": dict(sorted(rejected.items())), "recorded_adoption": adoption,
            "arms": summaries, "paired": {k: paired(accepted, pattern, base, k, k == "founding")
                                           for k in ("score", "founding")}, "history": history}


def markdown(report):
    lines = ["# sorengame 固定ベースライン比較", "", "判定：記述統計のみ。実力向上の確認・採用操作は行いません。", "",
             "|項目|固定ベースライン|比較候補|", "|---|---:|---:|"]
    arms = report["arms"]
    for label, key in (("戦略hash", "hash"), ("有効試合数", "games")):
        lines.append("|%s|%s|%s|" % (label, arms["baseline"][key], arms["candidate"][key]))
    for label, key in (("建国", "founding"), ("第一ロシア", "first_russia"),
                       ("ロシア2個の同時観測", "two_russias_observed")):
        cells = []
        for role in ("baseline", "candidate"):
            v = arms[role]["stages"][key]
            cells.append(f'{v["successes"]}/{v["known"]}（未知 {v["unknown"]}）')
        lines.append(f'|{label}|{"|".join(cells)}|')
    lines += ["", f'採用記録：{report["recorded_adoption"]["decision_class"]}（入力ファイルとの帰属は未検証）。',
              f'入力 {report["input_rows"]} 件／有効 {report["accepted_rows"]} 件。',
              "除外理由：" + json.dumps(report["excluded"], ensure_ascii=False, sort_keys=True),
              "", "ロシア2個を画像・snapshotで観測しなかった試合は、未到達とは断定しません。",
              "helper・解析器・runner・設定まで含む同条件性と、独立した再比較は別途必要です。"]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--games", required=True)
    parser.add_argument("--baseline-hash", required=True)
    parser.add_argument("--source-offset", help="Explicit offset for legacy timestamps, e.g. +09:00")
    parser.add_argument("--as-of", help="ISO 8601 timestamp with timezone")
    parser.add_argument("--days", type=int, help="Trailing window, 1..366 days; requires --as-of")
    parser.add_argument("--history", help="Optional retained game_history directory")
    parser.add_argument("--decision", help="Optional saved ab_decide JSON; never rerun a historical gate")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        state_text, state_sha = read_file(args.state)
        game_text, game_sha = read_file(args.games)
        rows = [decode(line) for line in game_text.splitlines() if line.strip()]
        decision, decision_sha = None, None
        if args.decision:
            decision_text, decision_sha = read_file(args.decision)
            decision = decode(decision_text)
            if not isinstance(decision, dict):
                raise EvidenceError("invalid_decision_shape")
        report = build_report(decode(state_text), rows, args.baseline_hash,
                              source_offset=offset(args.source_offset) if args.source_offset else None,
                              as_of=timestamp(args.as_of) if args.as_of else None,
                              days=args.days, history_dir=args.history, decision=decision)
        report["sources"] = {"state_sha256": state_sha, "games_sha256": game_sha,
                             "decision_sha256": decision_sha}
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
              if args.json else markdown(report), end="\n" if args.json else "")
        return 0
    except EvidenceError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

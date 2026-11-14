"""Versioned terminal evaluator for Soviet-founding search."""

from __future__ import annotations

# v6 adds a staged second-Russia pipeline band.  A board with one T15 and at
# least two T14s is one deterministic merge away from the double-Russia state,
# so preserve it ahead of unrelated frontier score while keeping T15x2 and the
# terminal Soviet bands strictly dominant.
OBJECTIVE_SCHEMA_VERSION = "soviet-terminal-v6"

TYPE_BONUS = {
    1: 0,
    2: 0,
    3: 1,
    4: 3,
    5: 7,
    6: 15,
    7: 32,
    8: 67,
    9: 141,
    10: 296,
    11: 622,
    12: 1306,
    13: 2743,
    14: 5760,
    15: 12096,
    16: 25402,
}

SECOND_RUSSIA_PIPELINE_BONUS = 50_000
SECOND_RUSSIA_READY_BONUS = 100_000
SOVIET_CREATED_BONUS = 1_000_000_000


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def terminal_bonus(final_types, soviet_created=False):
    types = [_as_int(value) for value in (final_types or ())]
    bonus = sum(TYPE_BONUS.get(value, 0) for value in types)
    # One Russia plus two Kazakhstans is the immediate predecessor to T15x2:
    # T14+T14 -> T15. Preserve that pipeline without double-counting the
    # stronger double-Russia readiness band once two T15s already exist.
    if types.count(15) == 1 and types.count(14) >= 2:
        bonus += SECOND_RUSSIA_PIPELINE_BONUS
    # Two Russia pieces are the final non-terminal stage.  Give that state a
    # dedicated band so candidate culling/promotion does not trade it away for
    # unrelated frontier score before the final T15+T15 merge can happen.
    if types.count(15) >= 2:
        bonus += SECOND_RUSSIA_READY_BONUS
    # Type 16 is the Soviet Union itself. Treat its presence as authoritative
    # terminal evidence even when a runner/archive omitted the boolean flag.
    # Do not use >=16: unknown/corrupt future type IDs must not become successes.
    if soviet_created or 16 in types:
        bonus += SOVIET_CREATED_BONUS
    return bonus


def eval_score(raw_score, final_types, soviet_created=False):
    return _as_int(raw_score) + terminal_bonus(final_types, soviet_created)


def stamp(record):
    record["objective_schema_version"] = OBJECTIVE_SCHEMA_VERSION
    return record


def version_of(record):
    if not isinstance(record, dict):
        return None
    value = record.get("objective_schema_version")
    return str(value) if value else None


def is_current(record):
    return version_of(record) == OBJECTIVE_SCHEMA_VERSION


def mixed_versions(records):
    seen = []
    for record in records or ():
        version = version_of(record)
        if version not in seen:
            seen.append(version)
    return seen

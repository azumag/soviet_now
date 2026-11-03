"""Versioned terminal evaluator for Soviet-founding search."""

from __future__ import annotations

# v4 makes terminal detection robust to producers that omit the explicit
# soviet_created flag while still reporting a type-16 Soviet in final_types.
# Keep samples separate when evaluator semantics change.
OBJECTIVE_SCHEMA_VERSION = "soviet-terminal-v4"

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

# Terminal success is the real objective.  Put it in a deliberately separate
# scalar band so ordinary score/frontier noise cannot outweigh a founding event
# in search/regression averages.
SOVIET_CREATED_BONUS = 1_000_000_000


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def terminal_bonus(final_types, soviet_created=False):
    types = [_as_int(value) for value in (final_types or ())]
    bonus = sum(TYPE_BONUS.get(value, 0) for value in types)
    # Type 16 is the Soviet Union itself.  Treat its presence as authoritative
    # terminal evidence even when a runner/archive omitted the boolean flag.
    if soviet_created or any(value >= 16 for value in types):
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

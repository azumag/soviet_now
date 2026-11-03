"""Versioned terminal evaluator for Soviet-founding search."""

from __future__ import annotations

# v2 strengthens the terminal-success objective.  v1 only added 800 points for
# an actual Soviet founding, so one rare successful game could be diluted by the
# frontier-heavy scalar distribution used by regression / wildcard evaluation.
# Keep old and new samples separate by bumping the schema version.
OBJECTIVE_SCHEMA_VERSION = "soviet-terminal-v2"

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

# A completed Soviet is the terminal objective, not merely another small score
# feature.  25,403 is one point above the T16 type bonus itself and larger than
# the complete T15x2 pre-founding frontier (24,192).  A successful terminal board
# therefore gets a decisive success margin while retaining raw score/tier ordering
# among successes.  This does not change live placement; it changes evaluation and
# promotion pressure toward strategies that actually found the Soviet Union.
SOVIET_CREATED_BONUS = 25403


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def terminal_bonus(final_types, soviet_created=False):
    bonus = sum(TYPE_BONUS.get(_as_int(value), 0) for value in (final_types or ()))
    if soviet_created:
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

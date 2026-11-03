"""Versioned terminal evaluator for Soviet-founding search."""

from __future__ import annotations

# v3 reserves a separate scalar band for actual Soviet founding.  v2 fixed the
# T15x2 inversion with +25,403, but that margin was still comparable to normal
# score/frontier variance, so a rare terminal success could be diluted by a
# high-scoring non-terminal run.  Keep samples separate when semantics change.
OBJECTIVE_SCHEMA_VERSION = "soviet-terminal-v3"

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
# in search/regression averages.  1e9 is far above the observed game-score
# scale while remaining exactly representable and harmless for Python stats.
SOVIET_CREATED_BONUS = 1_000_000_000


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

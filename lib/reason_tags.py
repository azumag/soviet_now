"""Compatibility re-export of :mod:`strategy_helpers.reason_tags`.

The strategy candidate sandbox only allowlists ``strategy_helpers`` (plus a
few analysis modules), so the single registry lives there.  Runner/tooling
imports stay on ``lib.reason_tags``; every public object is the same object as
the strategy-side module, so there is still exactly one implementation.
"""
from strategy_helpers.reason_tags import (  # noqa: F401
    GENERIC_EXCEPTION_CLASSES,
    SPECIFIC_KEYS,
    TAG_SET,
    TAG_VOCAB,
    all_tags,
    attach_reason_tags,
    describe,
    has_any_marker,
    has_marker,
    has_tag,
    is_generic,
    is_merge_decision,
    is_non_generic,
    main_tag,
    normalize_tags,
    parse_reason,
    render_reason,
    tag_classes,
    tags_for_decision,
)

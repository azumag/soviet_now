"""Structured ``decision_reason`` tags (soviet_now#877 Phase 1).

The strategy/runtime contract keeps ``{"x": float, "reason": str}`` unchanged.
Decisions may additionally carry::

    decision["reason_tags"] = {"main": str, "aux": [str, ...]}

Phase 1 is deliberately output-preserving: the registry below parses the
existing reason strings and ``render_reason`` reproduces them byte for byte.
No tag is renamed here (that is Phase 2, a separate PR).

Branching must use token predicates from this module instead of substring
tests such as ``"DEADLINE_GUARD" in reason``:

* :func:`has_tag` - exact membership of one complete tag.
* :func:`has_marker` - a marker matches only when its words appear as a
  contiguous token sequence (``NO_MERGE`` never matches ``MERGE``).
* :func:`is_merge_decision` - the old ``count('MERGE') - count('NO_MERGE')``
  arithmetic expressed as tag classification.
* :func:`is_generic` - the single non-generic classification shared by
  ``strategy.merge_opportunity_alternatives`` and
  ``tools/merge_burial_baseline``.
"""
from __future__ import annotations

import re

#: Atomic semantic tags used for segmentation.  Compounds such as
#: ``DEADLINE_GUARD_DIRECT_MERGE_CROSSING`` are represented as the sequence
#: ``DEADLINE_GUARD`` + ``DIRECT_MERGE`` + ``CROSSING``.  Keeping the registry
#: atomic is what makes exact tag matching safe: no tag is a word-boundary
#: prefix or suffix of another tag.
TAG_VOCAB = (
    "ABOVE_COVERED",
    "ABOVE_OPEN",
    "ANCHOR_LANE_SEED_CONTACT",
    "ANCHOR_SECOND_T13_CONTACT_SHOT",
    "AVOID_BLOCK_NEXTNEXT",
    "AVOID_BLOCK_REACTIVE_PAIR",
    "BESIDE",
    "CHAIN_MERGE",
    "CLUSTER_SETUP",
    "CROSSES_DEADLINE",
    "CROSSING",
    "DANGER",
    "DEADLINE_GUARD",
    "DESPERATE",
    "DIRECT_MERGE",
    "EARLY_MERGE",
    "FALLBACK",
    "FAR_MERGE",
    "HEIGHT_CONTROL",
    "HIGH_LAYER",
    "HIGH_TOWER",
    "HOLD",
    "IMMEDIATE_MERGE",
    "LARGE_DIRECT",
    "MEDIUM_TOWER",
    "MERGE_OPPORTUNITY",
    "MINIMAL_CROSS",
    "NEAR_MERGE",
    "NO_MERGE",
    "NO_VALID",
    "OPEN_TWIN",
    "PENALTY",
    "PROBABLE_MERGE_CONTACT",
    "REACTIVE_MERGE",
    "REACTIVE_PAIRS",
    "REACTIVE_PAIR_GAP_BLOCK",
    "RUNTIME_DEADLINE_SAFETY_OVERRIDE",
    "RUSSIA_PHASE_BOARD_COMPRESSION",
    "RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY",
    "RUSSIA_PHASE_T14_MERGE_PRIORITY",
    "RUSSIA_RESOURCE",
    "SAFE_LANDING",
    "SAME_COUNTRY",
    "SAME_TYPE_SEED_CONTACT",
    "SAME_TYPE_STACK",
    "SAME_TYPE_WASTED_DEADLINE",
    "SEAT_LANE",
    "SECOND_RUSSIA_CHAIN_LANE",
    "SECOND_RUSSIA_T12_LADDER",
    "SECOND_RUSSIA_T12_PAIR_LOCK",
    "SOVIET_T15_CONTACT_SHOT",
    "SURFACE_DIVERSITY",
    "T12_CHAIN_LANE_GUIDANCE",
    "T12_PAIR_COVER_AVOID",
    "UKRAINE_PAIR",
    "VISIBLE_SAME_COUNTRY_CONTACT_SHOT",
)
TAG_SET = frozenset(TAG_VOCAB)

#: Longest words first so greedy segmentation prefers ``OPEN_TWIN_MERGE`` over
#: ``OPEN`` + ``TWIN`` + ``MERGE``-style fallbacks.
_VOCAB_BY_LENGTH = tuple(sorted(TAG_VOCAB, key=lambda t: (-len(t.split("_")), -len(t))))

#: Legacy non-generic markers from merge_opportunity_alternatives / PR #681.
#: They are classifications, not substrings: a tag "owns" a class only when the
#: class word is a complete ``_``-delimited word in the tag.
GENERIC_EXCEPTION_CLASSES = (
    "russia",
    "anchor",
    "contact",
    "open_twin",
    "deadline_guard",
    "direct",
    "probable",
)

#: Kept for tools/merge_burial_baseline.py compatibility.  Branching must use
#: :func:`is_generic`, which consults this one registry.
SPECIFIC_KEYS = GENERIC_EXCEPTION_CLASSES

_SEPARATOR_RE = re.compile(r"[_\s|:]+")
_TOKEN_RE = re.compile(r"[^_\s|:]+")


def _split_parts(reason: str):
    """Split into (words, separators) while preserving every byte.

    ``separators`` has ``len(words) - 1`` entries; each separator is the exact
    text between two words (``_``, `` | ``, ``:`` ...).
    """
    words = _TOKEN_RE.findall(reason)
    separators = _SEPARATOR_RE.findall(reason)
    # A reason normally starts/ends with a word; tolerate a stray separator by
    # treating the whole string as one opaque token (rendered verbatim).
    if len(separators) != max(0, len(words) - 1):
        return [reason], []
    return words, separators


def _segment(words):
    """Greedy longest-match segmentation; unknown runs become single words.

    Returns ``(tag, start_word, end_word_exclusive)`` triples.
    """
    segments = []
    i = 0
    while i < len(words):
        for candidate in _VOCAB_BY_LENGTH:
            n = len(candidate.split("_"))
            if tuple(words[i:i + n]) == tuple(candidate.split("_")):
                segments.append((candidate, i, i + n))
                i += n
                break
        else:
            segments.append((words[i], i, i + 1))
            i += 1
    return segments


def parse_reason(reason=None) -> dict:
    """Parse a reason string into ``{"main", "aux", ...}`` without changing it.

    Extra keys ``sep``/``seps`` are present only when a non-underscore
    separator is used; they let :func:`render_reason` reproduce the original
    string exactly.
    """
    text = "" if reason is None else str(reason)
    if not text:
        return {"main": "", "aux": []}
    words, separators = _split_parts(text)
    if not words:
        return {"main": text, "aux": []}
    segments = _segment(words)
    tags = [tag for tag, _start, _end in segments]
    # Separator between two adjacent tags is the separator that follows the
    # last word of the first tag.
    tag_separators = [separators[segments[i][2] - 1] for i in range(len(segments) - 1)]
    parsed = {"main": tags[0], "aux": tags[1:]}
    if parsed["aux"]:
        if all(sep == "_" for sep in tag_separators):
            pass
        elif all(sep == tag_separators[0] for sep in tag_separators):
            parsed["sep"] = tag_separators[0]
        else:
            parsed["seps"] = list(tag_separators)
    return parsed


def render_reason(tags) -> str:
    """Render structured tags back to the single reason string.

    This is the only string-generation point.  ``tags`` may be a parsed dict or
    a ``reason_tags`` value attached to a decision.
    """
    if not isinstance(tags, dict):
        return "" if tags is None else str(tags)
    main = str(tags.get("main", "") or "")
    aux = [str(tag) for tag in (tags.get("aux") or [])]
    if not aux:
        return main
    seps = tags.get("seps")
    if seps is None:
        sep = tags.get("sep", "_")
        seps = [sep] * len(aux)
    if len(seps) != len(aux):
        seps = ["_"] * len(aux)
    rendered = main
    for sep, tag in zip(seps, aux):
        rendered += str(sep) + tag
    return rendered


def normalize_tags(tags_or_reason) -> dict:
    """Return a valid ``reason_tags`` dict for a dict or a legacy string."""
    if isinstance(tags_or_reason, dict):
        main = tags_or_reason.get("main")
        if isinstance(main, str):
            result = {"main": main, "aux": [str(t) for t in (tags_or_reason.get("aux") or [])]}
            if "seps" in tags_or_reason:
                result["seps"] = list(tags_or_reason.get("seps") or [])
            elif "sep" in tags_or_reason:
                result["sep"] = tags_or_reason.get("sep")
            return result
        # A decision may carry reason_tags itself; only trust it while it still
        # renders the current reason (the runtime appends suffixes).
        if isinstance(tags_or_reason.get("reason_tags"), dict):
            return tags_for_decision(tags_or_reason)
        return parse_reason(tags_or_reason.get("reason", ""))
    return parse_reason(tags_or_reason)


def tags_for_decision(decision) -> dict:
    """Structured tags for a decision, parsing the legacy ``reason`` lazily."""
    if not isinstance(decision, dict):
        return parse_reason(decision)
    tags = decision.get("reason_tags")
    if isinstance(tags, dict) and isinstance(tags.get("main"), str):
        # Prefer the recorded string when it no longer round-trips (the runtime
        # appends an override suffix to ``reason`` after ``decide``).
        if not decision.get("reason") or render_reason(tags) == str(decision.get("reason")):
            return normalize_tags(tags)
    return parse_reason(decision.get("reason", ""))


def attach_reason_tags(decision, tags=None):
    """Attach ``reason_tags`` to a decision and return it (in place)."""
    if not isinstance(decision, dict):
        return decision
    if tags is not None:
        decision["reason_tags"] = normalize_tags(tags)
    else:
        decision["reason_tags"] = parse_reason(decision.get("reason", ""))
    return decision


def all_tags(tags_or_reason) -> list:
    tags = normalize_tags(tags_or_reason)
    return [tags["main"], *tags.get("aux", [])]


def main_tag(tags_or_reason) -> str:
    return normalize_tags(tags_or_reason)["main"]


def _tag_words(tag: str) -> list:
    return [word for word in str(tag).split("_") if word]


def has_tag(tags_or_reason, name: str) -> bool:
    """Exact complete-tag membership (main or aux)."""
    return str(name) in normalize_tags(tags_or_reason)["aux"] or main_tag(tags_or_reason) == str(name)


def has_marker(tags_or_reason, marker: str) -> bool:
    """Match a legacy marker as a contiguous run of complete tags.

    ``has_marker(parse_reason("NO_MERGE_PENALTY"), "MERGE")`` is False, while
    ``has_marker(parse_reason("DEADLINE_GUARD_NO_VALID"), "DEADLINE_GUARD")``
    is True.  Compound markers are segmented with the same registry, so
    ``DEADLINE_GUARD_NO_VALID`` matches the tag run
    ``DEADLINE_GUARD`` + ``NO_VALID`` without a substring test.
    """
    wanted = _segment(_tag_words(marker))
    wanted_tags = [tag for tag, _start, _end in wanted]
    if not wanted_tags:
        return False
    tags = all_tags(tags_or_reason)
    n = len(wanted_tags)
    return any(tags[i:i + n] == wanted_tags for i in range(len(tags) - n + 1))


def has_any_marker(tags_or_reason, markers) -> bool:
    return any(has_marker(tags_or_reason, marker) for marker in markers)


def _net_merge_count(tag: str) -> int:
    """Old arithmetic scoped to one complete tag."""
    upper = str(tag).upper()
    return upper.count("MERGE") - upper.count("NO_MERGE")


def is_merge_decision(tags_or_reason) -> bool:
    """Equivalent of ``(reason.count('MERGE') - reason.count('NO_MERGE')) > 0``."""
    return sum(_net_merge_count(tag) for tag in all_tags(tags_or_reason)) > 0


def tag_classes(tag: str) -> frozenset:
    """Classification words for one tag (exact ``_``-delimited words)."""
    words = set(_tag_words(tag))
    classes = set()
    if "RUSSIA" in words:
        classes.add("russia")
    if "ANCHOR" in words:
        classes.add("anchor")
    if "CONTACT" in words:
        classes.add("contact")
    if {"OPEN", "TWIN"} <= words:
        classes.add("open_twin")
    if {"DEADLINE", "GUARD"} <= words:
        classes.add("deadline_guard")
    if "DIRECT" in words:
        classes.add("direct")
    if "PROBABLE" in words:
        classes.add("probable")
    return frozenset(classes)


def is_generic(tags_or_reason) -> bool:
    """True when the decision is a generic placement for the burial hook.

    Mirrors the PR #681 gate: a genuine merge decision, or a tag classified as
    RUSSIA/ANCHOR/CONTACT/OPEN_TWIN/DEADLINE_GUARD/DIRECT/PROBABLE, is
    non-generic.  Used by both strategy.py and tools/merge_burial_baseline.py.
    """
    if is_merge_decision(tags_or_reason):
        return False
    return not any(
        tag_classes(tag) & frozenset(GENERIC_EXCEPTION_CLASSES)
        for tag in all_tags(tags_or_reason)
    )


def is_non_generic(tags_or_reason) -> bool:
    return not is_generic(tags_or_reason)


def describe(tags_or_reason) -> str:
    """Human/debug form used in tests and traces."""
    tags = normalize_tags(tags_or_reason)
    return render_reason(tags)

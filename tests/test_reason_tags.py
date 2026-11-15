"""soviet_now#877 Phase 1: structured reason tags.

Pins the registry, the string<->structure round trip, token-boundary matching
(the NO_MERGE/MERGE hazard), and the per-tag non-generic inventory used by the
burial-loss hook.
"""
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import reason_tags  # noqa: E402


REAL_SHAPES = (
    "HEIGHT_CONTROL",
    "DEADLINE_GUARD",
    "DEADLINE_GUARD_DIRECT_MERGE",
    "DEADLINE_GUARD_DIRECT_MERGE_CROSSING",
    "DEADLINE_GUARD_NEAR_MERGE",
    "DEADLINE_GUARD_SAFE_LANDING",
    "DEADLINE_GUARD_NO_VALID",
    "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR",
    "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK",
    "NO_MERGE_DEADLINE_GUARD",
    "NO_MERGE_DEADLINE_GUARD_MINIMAL_CROSS",
    "NO_MERGE_DEADLINE_GUARD_NO_VALID",
    "NO_MERGE_DEADLINE_GUARD_NO_VALID|OPEN_TWIN_MERGE_DESPERATE",
    "T12_CHAIN_LANE_GUIDANCE_T12_PAIR_COVER_AVOID_ANCHOR_LANE_SEED_CONTACT_HIGH_LAYER_SURFACE_DIVERSITY_V763",
    "T12_CHAIN_LANE_GUIDANCE_SAME_TYPE_SEED_CONTACT_ANCHOR_LANE_SEED_CONTACT_HIGH_LAYER|OPEN_TWIN_MERGE",
    "HIGH_TOWER_DEADLINE_GUARD_FIRST_RUSSIA_PAIR",
    "HEIGHT_CONTROL_OPEN_TWIN_MERGE",
    "RUNTIME_DEADLINE_SAFETY_OVERRIDE_NO_TO_NEAR_runtime_minrisk",
    "HOLD:REACTIVE_PAIRS_STACKING",
    "no analysis data",
    "legacy-fixture",
    "REACTIVE_PAIRS_NO_MERGE_PENALTY",
    "PRE_RUSSIA_T13_CONTACT_SHOT",
    "VISIBLE_SAME_COUNTRY_CONTACT_SHOT",
)


def _source_reason_literals():
    """Reason shapes currently produced by strategy.py."""
    source = (ROOT / "strategy.py").read_text(encoding="utf-8")
    literals = re.findall(r"reasons\.append\(\s*['\"]([^'\"]+)['\"]\s*\)", source)
    literals += re.findall(r"['\"]reason['\"]\s*:\s*['\"]([^'\"]+)['\"]", source)
    return literals


def _fixture_reasons():
    reasons = []
    for path in sorted((ROOT / "tests" / "fixtures").rglob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            reason = payload.get("decision_reason")
            if isinstance(reason, str):
                reasons.append(reason)
            expected = payload.get("expected")
            if isinstance(expected, dict) and isinstance(expected.get("decision_reason"), str):
                reasons.append(expected["decision_reason"])
            decision = payload.get("decision")
            if isinstance(decision, dict) and isinstance(decision.get("reason"), str):
                reasons.append(decision["reason"])
    return reasons


def corpus():
    values = set(REAL_SHAPES) | set(_source_reason_literals()) | set(_fixture_reasons())
    # Real records reach ~536 chars with repeated concatenated tags.
    values.add("_".join(["NO_MERGE_DEADLINE_GUARD_NO_VALID"] * 12))
    values.add("|".join(["DEADLINE_GUARD_DIRECT_MERGE_CROSSING"] * 6))
    values.add("_".join(["T12_CHAIN_LANE_GUIDANCE", "SAME_TYPE_SEED_CONTACT"] * 20))
    return sorted(value for value in values if value)


class RegistryTests(unittest.TestCase):
    def test_tags_are_uppercase_snake_and_unique(self):
        seen = set()
        for tag in reason_tags.TAG_VOCAB:
            self.assertRegex(tag, r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
            self.assertNotIn(tag, seen, f"duplicate tag: {tag}")
            seen.add(tag)

    def test_no_word_boundary_prefix_or_suffix_relations(self):
        # NO_MERGE is not a suffix of MERGE and DEADLINE_GUARD is not a prefix
        # of DEADLINE_GUARD_NO_VALID: compounds are split into atomic tags
        # instead, so exact matching cannot alias them.
        for left in reason_tags.TAG_VOCAB:
            for right in reason_tags.TAG_VOCAB:
                if left == right:
                    continue
                self.assertFalse(
                    right.startswith(left + "_"),
                    f"{left} is a word-boundary prefix of {right}",
                )
                self.assertFalse(
                    right.endswith("_" + left),
                    f"{left} is a word-boundary suffix of {right}",
                )

    def test_registry_has_one_classification_function(self):
        # Both callers must resolve through this module, not private copies.
        import strategy
        import tools.merge_burial_baseline as baseline

        self.assertIs(strategy.reason_tags.is_generic, reason_tags.is_generic)
        self.assertIs(baseline._registry_is_generic, reason_tags.is_generic)


class ParseRenderTests(unittest.TestCase):
    def test_roundtrip_real_reasons(self):
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                parsed = reason_tags.parse_reason(reason)
                self.assertIn("main", parsed)
                self.assertIsInstance(parsed.get("aux"), list)
                self.assertEqual(reason_tags.render_reason(parsed), reason)

    def test_parse_render_is_fixed_point(self):
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                parsed = reason_tags.parse_reason(reason)
                self.assertEqual(
                    reason_tags.parse_reason(reason_tags.render_reason(parsed)),
                    parsed,
                )

    def test_manual_tags_roundtrip(self):
        tags = {"main": "HEIGHT_CONTROL", "aux": ["DIRECT_MERGE", "SAFE_LANDING"]}
        self.assertEqual(
            reason_tags.render_reason(tags),
            "HEIGHT_CONTROL_DIRECT_MERGE_SAFE_LANDING",
        )
        self.assertEqual(reason_tags.parse_reason(reason_tags.render_reason(tags)), tags)

    def test_empty_and_unknown_reasons(self):
        for reason in ("", None, "unknown-reason", "a b c", "HOLD:XYZ"):
            with self.subTest(reason=reason):
                self.assertEqual(
                    reason_tags.render_reason(reason_tags.parse_reason(reason)),
                    "" if reason is None else reason,
                )


class TokenPredicateTests(unittest.TestCase):
    def test_no_merge_is_not_a_merge_decision(self):
        tags = reason_tags.parse_reason("NO_MERGE_DEADLINE_GUARD_NO_VALID")
        self.assertFalse(reason_tags.is_merge_decision(tags))
        self.assertFalse(reason_tags.has_marker(tags, "MERGE"))
        self.assertTrue(reason_tags.has_marker(tags, "NO_MERGE"))
        self.assertTrue(reason_tags.has_marker(tags, "DEADLINE_GUARD"))
        self.assertTrue(reason_tags.has_marker(tags, "DEADLINE_GUARD_NO_VALID"))

    def test_tokens_do_not_match_larger_words(self):
        self.assertFalse(reason_tags.has_marker("DEADLINE_GUARDX_DIRECT_MERGE", "DEADLINE_GUARD"))
        self.assertFalse(reason_tags.has_marker("HEIGHT_CONTROL_DIRECTORY", "DIRECT"))
        self.assertTrue(reason_tags.has_marker("HEIGHT_CONTROL_DIRECT_MERGE", "DIRECT_MERGE"))

    def test_has_tag_is_exact_membership(self):
        tags = {"main": "NO_MERGE", "aux": ["DEADLINE_GUARD", "NO_VALID"]}
        self.assertTrue(reason_tags.has_tag(tags, "NO_MERGE"))
        self.assertTrue(reason_tags.has_tag(tags, "DEADLINE_GUARD"))
        self.assertFalse(reason_tags.has_tag(tags, "DEADLINE"))
        self.assertFalse(reason_tags.has_tag(tags, "MERGE"))

    def test_mixed_separator_reasons(self):
        reason = "NO_MERGE_DEADLINE_GUARD_NO_VALID|OPEN_TWIN_MERGE_DESPERATE"
        tags = reason_tags.parse_reason(reason)
        self.assertTrue(reason_tags.has_marker(tags, "OPEN_TWIN_MERGE"))
        self.assertTrue(reason_tags.has_marker(tags, "OPEN_TWIN_MERGE_DESPERATE"))
        self.assertTrue(reason_tags.is_merge_decision(tags))


class ClassificationInventoryTests(unittest.TestCase):
    # Each tag in the registry has a pinned generic/non-generic classification.
    # "generic" means the burial-loss hook may apply; the PR #859 names
    # (RUSSIA/CONTACT) must stay non-generic in Phase 1.
    EXPECTED_GENERIC = {
        "ABOVE_COVERED": True,
        "ABOVE_OPEN": True,
        "ANCHOR_LANE_SEED_CONTACT": False,
        "ANCHOR_SECOND_T13_CONTACT_SHOT": False,
        "AVOID_BLOCK_NEXTNEXT": True,
        "AVOID_BLOCK_REACTIVE_PAIR": True,
        "BESIDE": True,
        "CHAIN_MERGE": False,
        "CLUSTER_SETUP": True,
        "CROSSES_DEADLINE": True,
        "CROSSING": True,
        "DANGER": True,
        "DEADLINE_GUARD": False,
        "DESPERATE": True,
        "DIRECT_MERGE": False,
        "EARLY_MERGE": False,
        "FALLBACK": True,
        "FAR_MERGE": False,
        "HEIGHT_CONTROL": True,
        "HIGH_LAYER": True,
        "HIGH_TOWER": True,
        "HOLD": True,
        "IMMEDIATE_MERGE": False,
        "LARGE_DIRECT": False,
        "MEDIUM_TOWER": True,
        "MERGE_OPPORTUNITY": False,
        "MINIMAL_CROSS": True,
        "NEAR_MERGE": False,
        "NO_MERGE": True,
        "NO_VALID": True,
        "OPEN_TWIN": False,
        "PENALTY": True,
        "PROBABLE_MERGE_CONTACT": False,
        "REACTIVE_MERGE": False,
        "REACTIVE_PAIRS": True,
        "REACTIVE_PAIR_GAP_BLOCK": True,
        "RUNTIME_DEADLINE_SAFETY_OVERRIDE": True,
        "RUSSIA_PHASE_BOARD_COMPRESSION": False,
        "RUSSIA_PHASE_IMMEDIATE_MERGE_PRIORITY": False,
        "RUSSIA_PHASE_T14_MERGE_PRIORITY": False,
        "RUSSIA_RESOURCE": False,
        "SAFE_LANDING": True,
        "SAME_COUNTRY": True,
        "SAME_TYPE_SEED_CONTACT": False,
        "SAME_TYPE_STACK": True,
        "SAME_TYPE_WASTED_DEADLINE": True,
        "SEAT_LANE": True,
        "SECOND_RUSSIA_CHAIN_LANE": False,
        "SECOND_RUSSIA_T12_LADDER": False,
        "SECOND_RUSSIA_T12_PAIR_LOCK": False,
        "SOVIET_T15_CONTACT_SHOT": False,
        "SURFACE_DIVERSITY": True,
        "T12_CHAIN_LANE_GUIDANCE": True,
        "T12_PAIR_COVER_AVOID": True,
        "UKRAINE_PAIR": True,
        "VISIBLE_SAME_COUNTRY_CONTACT_SHOT": False,
    }

    def test_every_registry_tag_is_inventoried(self):
        self.assertEqual(set(self.EXPECTED_GENERIC), set(reason_tags.TAG_VOCAB))

    def test_per_tag_classification(self):
        for tag, expected in self.EXPECTED_GENERIC.items():
            with self.subTest(tag=tag):
                self.assertEqual(reason_tags.is_generic(tag), expected)

    def test_pr859_names_are_non_generic(self):
        for reason in (
            "PRE_RUSSIA_T13_CONTACT_SHOT",
            "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR",
            "VISIBLE_SAME_COUNTRY_CONTACT_SHOT",
            "SAME_TYPE_SEED_CONTACT",
            "HEIGHT_CONTROL_OPEN_TWIN_MERGE",
        ):
            with self.subTest(reason=reason):
                self.assertFalse(reason_tags.is_generic(reason))


if __name__ == "__main__":
    unittest.main()

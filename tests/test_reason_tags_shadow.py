"""soviet_now#877 Phase 1 shadow equivalence for reason predicates.

The old substring predicates and the new structured-tag predicates must agree
on the real-shape corpus (including long concatenated reasons).  The legacy
definitions are copied verbatim from strategy.py / tools/merge_burial_baseline.py
before the migration so drift is caught by CI.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib import reason_tags  # noqa: E402
from tests.test_reason_tags import corpus  # noqa: E402


def legacy_merge_decision(reason):
    """Verbatim old strategy.py arithmetic."""
    upper = str(reason or "").upper()
    return (upper.count("MERGE") - upper.count("NO_MERGE")) > 0


def legacy_strategy_generic(reason):
    """Verbatim old _generic expression."""
    reason = str(reason or "")
    merge_decision = legacy_merge_decision(reason)
    return not (merge_decision or any(k in reason for k in (
        "RUSSIA", "ANCHOR", "CONTACT", "OPEN_TWIN", "DEADLINE_GUARD",
        "DIRECT", "PROBABLE")))


def legacy_tool_generic(reason):
    """Verbatim old tools/merge_burial_baseline.is_generic."""
    low = str(reason or "")
    for key in ("RUSSIA", "ANCHOR", "CONTACT", "OPEN_TWIN", "DEADLINE_GUARD",
                "DIRECT", "PROBABLE", "MERGE"):
        idx = 0
        while True:
            idx = low.find(key, idx)
            if idx < 0:
                break
            if key == "MERGE" and low[max(0, idx - 3):idx] == "NO_":
                idx += len(key)
                continue
            return False
    return True


class ShadowEquivalenceTests(unittest.TestCase):
    def test_merge_decision_matches_old_arithmetic(self):
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                self.assertEqual(
                    reason_tags.is_merge_decision(reason),
                    legacy_merge_decision(reason),
                )

    def test_generic_matches_old_strategy_gate(self):
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                self.assertEqual(
                    reason_tags.is_generic(reason),
                    legacy_strategy_generic(reason),
                )

    def test_generic_matches_old_baseline_tool(self):
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                self.assertEqual(
                    reason_tags.is_generic(reason),
                    legacy_tool_generic(reason),
                )

    def test_old_strategy_and_tool_gates_agree(self):
        # The two hand-mirrored copies were already equivalent; the shared
        # registry keeps that property structurally.
        for reason in corpus():
            with self.subTest(reason=reason[:80]):
                self.assertEqual(
                    legacy_strategy_generic(reason),
                    legacy_tool_generic(reason),
                )

    def test_runner_gate_markers(self):
        # Characterization of the four strategy_runner gate sites (plus the
        # direct-cross hook and the MEDIUM_TOWER fallback): the token predicate
        # agrees with the legacy tuple/`in` tests on the exact reason shapes.
        cases = (
            (
                "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR",
                ("PRE_RUSSIA_T12_CONSOLIDATE", "PRE_RUSSIA_T11_DENSITY_LATCH",
                 "PRE_RUSSIA_SINGLE_T12_ANCHOR_LADDER",
                 "DEADLINE_GUARD_PRE_RUSSIA_SINGLE_T12_ANCHOR",
                 "PRE_RUSSIA_NEXT_UP_LATCH"),
            ),
            (
                "HIGH_TOWER_DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK",
                ("SECOND_RUSSIA_T12_PAIR_LOCK",
                 "DEADLINE_GUARD_SECOND_RUSSIA_T12_PAIR_LOCK"),
            ),
            (
                "MEDIUM_TOWER_SECOND_RUSSIA_T12_LADDER",
                ("SECOND_RUSSIA_T12_LADDER",
                 "DEADLINE_GUARD_SECOND_RUSSIA_T12_LADDER"),
            ),
            (
                "HIGH_TOWER_FIRST_RUSSIA_T13_PAIR_LIFT",
                ("FIRST_RUSSIA_T13_PAIR_LIFT",
                 "DEADLINE_GUARD_FIRST_RUSSIA_T13_PAIR_LIFT"),
            ),
            (
                "HIGH_TOWER_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT",
                ("FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT",
                 "DEADLINE_GUARD_FIRST_RUSSIA_SINGLE_T13_T12_BANK_LIFT"),
            ),
            (
                "NO_MERGE_DEADLINE_GUARD_NO_VALID|OPEN_TWIN_MERGE_DESPERATE",
                ("DEADLINE_GUARD_DIRECT_MERGE_CROSSING",
                 "OPEN_TWIN_MERGE_DESPERATE"),
            ),
            (
                "HEIGHT_CONTROL_MEDIUM_TOWER",
                ("MEDIUM_TOWER",),
            ),
        )
        for reason, markers in cases:
            with self.subTest(reason=reason):
                for marker in markers:
                    self.assertEqual(
                        reason_tags.has_marker(reason, marker),
                        marker in reason,
                    )


if __name__ == "__main__":
    unittest.main()

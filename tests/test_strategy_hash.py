from pathlib import Path
import tempfile
import unittest

from extract_decide_hash import compute_hash, compute_hash_from_source
from generate_phyrogenetic_tree import compute_hash_from_source as phylogenetic_hash
from recover_hash_archive_from_git import extract_decide_hash_from_source as recovery_hash


class StrategyHashTest(unittest.TestCase):
    def _hash(self, source):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "strategy.py"
            path.write_text(source, encoding="utf-8")
            return compute_hash(path)

    def _all_hashes(self, source):
        return {
            compute_hash_from_source(source),
            phylogenetic_hash(source),
            recovery_hash(source),
        }

    def test_legacy_decide_only_hash_is_unchanged(self):
        source = (
            "def decide(game_state, analysis):\n"
            "    return {'x': 0.0, 'reason': 'stable'}\n"
        )

        self.assertEqual(self._hash(source), "9674b3c75fb3")

    def test_reachable_helper_change_gets_a_new_hash(self):
        template = (
            "def contact_gap(value):\n"
            "    return value <= {threshold}\n\n"
            "def decide(game_state, analysis):\n"
            "    return {{'x': 0.0, 'reason': str(contact_gap(analysis['gap']))}}\n"
        )

        first = self._hash(template.format(threshold="0.08"))
        second = self._hash(template.format(threshold="0.09"))

        self.assertNotEqual(first, second)

    def test_transitive_helper_change_gets_a_new_hash(self):
        template = (
            "def limit():\n"
            "    return {threshold}\n\n"
            "def contact_gap(value):\n"
            "    return value <= limit()\n\n"
            "def decide(game_state, analysis):\n"
            "    return {{'x': 0.0, 'reason': str(contact_gap(analysis['gap']))}}\n"
        )

        first = self._hash(template.format(threshold="0.08"))
        second = self._hash(template.format(threshold="0.09"))

        self.assertNotEqual(first, second)

    def test_unused_helper_does_not_change_strategy_identity(self):
        first = self._hash(
            "def unused():\n"
            "    return 1\n\n"
            "def decide(game_state, analysis):\n"
            "    return {'x': 0.0, 'reason': 'stable'}\n"
        )
        second = self._hash(
            "def unused():\n"
            "    return 2\n\n"
            "def decide(game_state, analysis):\n"
            "    return {'x': 0.0, 'reason': 'stable'}\n"
        )

        self.assertEqual(first, second)

    def test_optional_finalizer_and_its_helpers_change_strategy_identity(self):
        template = (
            "def final_limit():\n"
            "    return {threshold}\n\n"
            "def finalize_decision(game_state, analysis, decision):\n"
            "    if analysis['risk'] > final_limit():\n"
            "        return {{'x': 1.0, 'reason': 'final'}}\n"
            "    return decision\n\n"
            "def decide(game_state, analysis):\n"
            "    return {{'x': 0.0, 'reason': 'stable'}}\n"
        )

        without_finalizer = self._hash(
            "def decide(game_state, analysis):\n"
            "    return {'x': 0.0, 'reason': 'stable'}\n"
        )
        first = self._hash(template.format(threshold="0.20"))
        second = self._hash(template.format(threshold="0.21"))

        self.assertNotEqual(first, without_finalizer)
        self.assertNotEqual(first, second)

    def test_runtime_policy_capability_changes_strategy_identity(self):
        template = (
            "def pre_russia_ukraine_pair_policy_id():\n"
            "    return '{policy_id}'\n\n"
            "def finalize_decision(game_state, analysis, decision):\n"
            "    if not pre_russia_ukraine_pair_policy_id():\n"
            "        return {{'x': 0.0, 'reason': 'fallback'}}\n"
            "    return decision\n\n"
            "def decide(game_state, analysis):\n"
            "    return {{'x': 0.0, 'reason': 'stable'}}\n"
        )

        first = self._hash(template.format(policy_id="ukraine-pair-v1"))
        second = self._hash(template.format(policy_id="ukraine-pair-v2"))

        self.assertNotEqual(first, second)

    def test_all_hash_consumers_agree_for_legacy_and_helper_policies(self):
        sources = [
            (
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': 'legacy'}\n"
            ),
            (
                "def contact_gap(value):\n"
                "    return value <= 0.08\n\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': str(contact_gap(analysis['gap']))}\n"
            ),
            (
                "def limit():\n"
                "    return 0.08\n\n"
                "def contact_gap(value):\n"
                "    return value <= limit()\n\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': str(contact_gap(analysis['gap']))}\n"
            ),
            (
                "def unused():\n"
                "    return 99\n\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': 'unused'}\n"
            ),
            (
                "def final_limit():\n"
                "    return 0.20\n\n"
                "def finalize_decision(game_state, analysis, decision):\n"
                "    if analysis['risk'] > final_limit():\n"
                "        return {'x': 1.0, 'reason': 'final'}\n"
                "    return decision\n\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': 'stable'}\n"
            ),
            (
                "def pre_russia_ukraine_pair_policy_id():\n"
                "    return 'ukraine-pair-v1'\n\n"
                "def finalize_decision(game_state, analysis, decision):\n"
                "    if not pre_russia_ukraine_pair_policy_id():\n"
                "        return {'x': 0.0, 'reason': 'fallback'}\n"
                "    return decision\n\n"
                "def decide(game_state, analysis):\n"
                "    return {'x': 0.0, 'reason': 'stable'}\n"
            ),
        ]

        for source in sources:
            with self.subTest(source=source):
                self.assertEqual(len(self._all_hashes(source)), 1)


if __name__ == "__main__":
    unittest.main()

"""Regression checks for the executed strategy prompts (no game/VM side effects)."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("analyze_strategy.md", "implement_strategy.md", "improve_strategy.md")
OLD_GATES = ("100%未満の最初", "100%になってから次の段階", "低段階ゲート未達なのに")

class FoundingPolicyContract(unittest.TestCase):
    def test_executed_prompts_have_no_old_lower_stage_gate(self):
        for path in (ROOT / "prompts").glob("*strategy.md"):
            text = path.read_text(encoding="utf-8")
            for gate in OLD_GATES:
                with self.subTest(path=path.name, gate=gate):
                    self.assertNotIn(gate, text)

    def test_all_decision_stages_use_shared_success_definition(self):
        for name in STAGES:
            text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
            with self.subTest(stage=name):
                self.assertIn("目的と対象段階の正本は `prompts/game_theory.md`", text)
                self.assertIn("makeSorenCount", text)
                self.assertIn("下位局面の回帰検証は省略しない", text)
                self.assertIn("旧 EVAL_SCORE の建国時の逆転は未修正", text)

    def test_common_priority_contract_is_identical(self):
        blocks = []
        for name in STAGES:
            text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
            match = re.search(r"## 段階別の改善対象（建国率優先）\n(.*?)(?=\n## )", text, re.S)
            self.assertIsNotNone(match, name)
            blocks.append(match.group(1).splitlines()[:7])
        self.assertEqual(blocks[0], blocks[1])
        self.assertEqual(blocks[0], blocks[2])

    def test_review_uses_same_policy_without_relaxing_constraints(self):
        text = (ROOT / "prompts/review_strategy.md").read_text(encoding="utf-8")
        self.assertIn("prompts/game_theory.md", text)
        self.assertIn("makeSorenCount", text)
        for required in ("mandatory_themes.txt", "review_verdict", "Bash", "FAIL"):
            self.assertIn(required, text)

    def test_no_claim_of_monotonic_final_merge_bonus(self):
        for name in STAGES:
            text = (ROOT / "prompts" / name).read_text(encoding="utf-8")
            self.assertNotIn("併合して1つ上のtypeにした方が常にボーナスが高い", text)

    def test_historical_live_memo_is_preserved_but_not_an_instruction(self):
        import hashlib
        text = (ROOT / "prompts/improve_strategy.md").read_text(encoding="utf-8")
        match = re.search(r"```historical-evidence\n(.*?)```", text, re.S)
        self.assertIsNotNone(match, "historical live evidence must remain available")
        self.assertEqual(hashlib.sha256(match.group(1).encode()).hexdigest(), "e5b08ccebb9f0e4926cc696a7331eaf90e9f536a3efb69fffb809a00f9002a5d")
        self.assertIn("原文中の命令形は今回の指示ではない", text)
        self.assertIn("v743 / v750 と同じ変更を再提案しない", text)
        theory = (ROOT / "prompts/game_theory.md").read_text(encoding="utf-8")
        self.assertIn("過去メモの単一目的・普遍的禁止は採用しない", theory)

if __name__ == "__main__":
    unittest.main()

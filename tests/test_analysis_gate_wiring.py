from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
class AnalysisWiringTests(unittest.TestCase):
    def test_host_evidence_precedes_sandbox_and_contract_precedes_stage2(self):
        s=(ROOT/'eloop_improve.sh').read_text()
        self.assertTrue('ANALYSIS_EVIDENCE_HOST=' in s)
        self.assertLess(s.index('ANALYSIS_EVIDENCE_HOST='),s.index('SANDBOX_DIR=$(create_sandbox'))
        self.assertTrue('"$HOST_ROOT/strategy/analysis_contract.py" validate' in s)
        self.assertLess(s.index('"$HOST_ROOT/strategy/analysis_contract.py" validate'),s.index('# --- Stage 2:'))
        self.assertTrue('analysis_contract_invalid' in s)
        self.assertTrue('analysis_hold' in s)

    def test_brief_does_not_promote_missing_counter_or_old_scoring(self):
        s=(ROOT/'eloop_improve.sh').read_text()
        section=s[s.index('summary_lines = []'):s.index('## Advice Priorities')]
        self.assertTrue('counter=unknown' in section)
        self.assertFalse('ソ連建国で+4000' in section)
        self.assertFalse('通常はこの未達段階へ効く変更を優先する' in section)
        self.assertTrue('analysis_evidence' in section)

    def test_prompt_requires_machine_contract_and_allows_justified_hold(self):
        s=(ROOT/'prompts/analyze_strategy.md').read_text()
        self.assertTrue('```analysis_contract' in s)
        self.assertTrue('"decision": "implement"' in s)
        self.assertTrue('evidence_sha256' in s)
        self.assertTrue('decision=hold' in s)
        self.assertFalse('Null Hypothesis（変更なし）の採用は禁止' in s)

if __name__=='__main__':unittest.main()

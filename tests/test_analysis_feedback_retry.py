"""Exercise the actual Stage1 loop, host validator, and monotonic budget guard."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'strategy/analysis_contract.py'
spec = importlib.util.spec_from_file_location('analysis_gate', HELPER)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


class AnalysisFeedbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / 'game_history').mkdir()
        (self.root / 'game_history/g.jsonl').write_text(
            json.dumps({'turn': 1, 'next_type': 3}) + '\n')
        self.evidence = gate.build_evidence(self.root, ['game_history/g.jsonl'])
        (self.root / 'evidence.json').write_bytes(gate.encode(self.evidence))

    def document(self, plan='', **changes):
        contract = {
            'version': 1, 'decision': 'implement',
            'evidence_sha256': hashlib.sha256(gate.encode(self.evidence)).hexdigest(),
            'game_count': 1, 'founded_games': None,
            'hypotheses': [{'id': 'H1', 'claim': 'One evidenced change',
                            'evidence': [{'file': 'game_history/g.jsonl', 'turn': 1}]}],
            'changes': [{'hypothesis_id': 'H1', 'target': 'strategy.py.staging',
                         'mechanism': 'Replace one test', 'required_next_types': [3]}],
        }
        contract.update(changes)
        return ('## Implementation Plan\n' + plan + '\n## Contract\n'
                '```analysis_contract\n' + json.dumps(contract) + '\n```\n')

    def run_stage(self, responses, retries=2, missing_evidence=False):
        (self.root / 'responses.json').write_text(json.dumps(responses))
        if missing_evidence:
            (self.root / 'evidence.json').unlink()
        (self.root / 'model.py').write_text('''import json, pathlib, sys
root = pathlib.Path(__file__).resolve().parent
calls_path = root / 'calls.json'
calls = json.loads(calls_path.read_text()) if calls_path.exists() else []
args = sys.argv[1:]
response = json.loads((root / 'responses.json').read_text())[len(calls)]
feedback = [pathlib.Path(p).read_text() for p in args[4:]
            if pathlib.Path(p).name == 'analysis-retry-feedback.md']
calls.append({'label': args[0], 'feedback': feedback,
              'result_existed': pathlib.Path(args[3]).exists()})
calls_path.write_text(json.dumps(calls))
if response.get('text') is not None:
    pathlib.Path(args[3]).write_text(response['text'])
if response.get('expire'):
    (root / 'expire').touch()
sys.exit(response.get('rc', 0))
''')
        source = (ROOT / 'eloop_improve.sh').read_text()
        start = source.index('\t_analysis_feedback_refs=()')
        block = source[start:source.index('\n\t# 分析用に絞った', start)]
        values = {
            'HOST_ROOT': str(ROOT),
            'ANALYSIS_EVIDENCE_HOST': str(self.root / 'evidence.json'),
            'IMPROVE_RUN_RECEIPT_DIR': str(self.root / 'receipts'),
            'ANALYSIS_RESULT_FILE': str(self.root / 'analysis.md'),
            'RUN_CMD_LOG_FILE': str(self.root / 'log'),
            'ANALYSIS_MAX_RETRIES': str(retries),
            'IMPROVE_WALL_TIMEOUT': '3600',
            'IMPROVE_JOB_DEADLINE_MONOTONIC': str(time.monotonic() + 1200),
            'IMPROVE_STAGE_DEADLINE_MONOTONIC': str(time.monotonic() + 600),
            'RUN_AI_IMPROVEMENT_MODE': '1',
        }
        setup = '\n'.join(f'{key}={shlex.quote(value)}' for key, value in values.items())
        setup += f'''
source {shlex.quote(str(ROOT / 'strategy/ai.sh'))}
_improve_wall_start=$(date +%s)
analysis_ok=false
IMPROVE_FAILURE_CODE=""
VALIDATE_ERROR=""
improve_ref_files=()
log() {{ :; }}
_improve_progress() {{ :; }}
_improve_note() {{ :; }}
_get_improve_agents() {{ echo fake; }}
_is_peak_hours() {{ return 1; }}
run_ai_list() {{
    _improve_budget_check
    local rc=$?
    [ "$rc" -eq 0 ] || return "$rc"
    python3 model.py "$@"
    rc=$?
    if [ -f expire ]; then IMPROVE_STAGE_DEADLINE_MONOTONIC=1; fi
    return "$rc"
}}
'''
        ending = '\nprintf "%s|%s|%s" "$analysis_ok" "$IMPROVE_FAILURE_CODE" "$VALIDATE_ERROR"'
        result = subprocess.run(['bash', '-c', setup + block + ending],
                                cwd=self.root, env=os.environ.copy(), text=True,
                                capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout, json.loads((self.root / 'calls.json').read_text())

    def test_prohibited_example_is_regenerated_and_fully_validated(self):
        rejected = self.document('`next_type==14` 等の直接供給条件を導入しない。')
        accepted = self.document('合成された盤面駒と直接供給を区別する。')
        result, calls = self.run_stage([{'text': rejected}, {'text': accepted}])
        self.assertEqual(result, 'true||')
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]['feedback'], [])
        self.assertEqual(len(calls[1]['feedback']), 1)
        self.assertIn('unreachable_plan_condition', calls[1]['feedback'][0])
        self.assertNotIn('next_type==14', calls[1]['feedback'][0])
        self.assertFalse(calls[1]['result_existed'])
        receipts = self.root / 'receipts'
        self.assertEqual((receipts / 'analysis-check-1.md').read_text(), rejected)
        self.assertEqual(json.loads((receipts / 'analysis-check-1.json').read_text())['errors'],
                         ['unreachable_plan_condition'])
        self.assertTrue(json.loads((receipts / 'analysis-check-2.json').read_text())['ok'])

    def test_valid_range_exclusion_can_be_reexpressed_without_weakening_gate(self):
        rejected = self.document('```python\nif next_type < 8 or next_type > 11:\n    return None\n```')
        accepted = self.document('```python\nif next_type not in (8, 9, 10, 11):\n    return None\n```')
        result, calls = self.run_stage([{'text': rejected}, {'text': accepted}])
        self.assertEqual(result, 'true||')
        self.assertEqual(len(calls), 2)
        self.assertIn('Do not change the intended range', calls[1]['feedback'][0])

    def test_unreachable_mechanism_still_fails_after_one_feedback_attempt(self):
        invalid = self.document('Add next_type==14 bonus.')
        result, calls = self.run_stage([{'text': invalid}, {'text': invalid}], retries=5)
        self.assertEqual(result, 'false|analysis_contract_invalid|analysis_contract_invalid')
        self.assertEqual(len(calls), 2)
        self.assertFalse(json.loads((self.root / 'receipts/analysis-check-2.json').read_text())['ok'])

    def test_feedback_does_not_bypass_evidence_validation_on_new_document(self):
        rejected = self.document('`next_type==14` を導入しない。')
        forged = self.document('Use observed input types only.', evidence_sha256='0' * 64)
        result, calls = self.run_stage([{'text': rejected}, {'text': forged}], retries=5)
        self.assertEqual(result, 'false|analysis_contract_invalid|analysis_contract_invalid')
        self.assertEqual(len(calls), 2)
        second = json.loads((self.root / 'receipts/analysis-check-2.json').read_text())
        self.assertEqual(second['errors'], ['evidence_digest_mismatch'])

    def test_no_retry_slot_preserves_terminal_failure(self):
        result, calls = self.run_stage([{'text': self.document('Add next_type==14 bonus.')}], retries=1)
        self.assertTrue(result.startswith('false|analysis_contract_invalid|'))
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.root / 'receipts/analysis-retry-feedback.md').exists())

    def test_deadline_is_not_extended_for_feedback(self):
        result, calls = self.run_stage([{'text': self.document('Add next_type==14 bonus.'), 'expire': True}])
        self.assertEqual(result, 'false|stage_deadline_exhausted|stage_deadline_exhausted')
        self.assertEqual(len(calls), 1)

    def test_hold_is_terminal_without_feedback(self):
        text = self.document(decision='hold', hypotheses=[], changes=[], reason='More evidence needed')
        result, calls = self.run_stage([{'text': text}])
        self.assertEqual(result, 'false|analysis_hold|analysis_hold')
        self.assertEqual(len(calls), 1)

    def test_other_or_mixed_validation_errors_do_not_retry(self):
        for plan in ['', 'Add next_type==14 bonus.']:
            with self.subTest(plan=plan):
                (self.root / 'calls.json').unlink(missing_ok=True)
                invalid = self.document(plan, evidence_sha256='0' * 64)
                result, calls = self.run_stage([{'text': invalid}])
                self.assertTrue(result.startswith('false|analysis_contract_invalid|'))
                self.assertEqual(len(calls), 1)

    def test_missing_host_evidence_does_not_retry(self):
        result, calls = self.run_stage([{'text': self.document('Add next_type==14 bonus.')}], missing_evidence=True)
        self.assertTrue(result.startswith('false|analysis_contract_invalid|'))
        self.assertEqual(len(calls), 1)

    def test_empty_or_transport_failure_does_not_reuse_rejected_output(self):
        for rc in [0, 1, 79, 80, 81, 92]:
            with self.subTest(rc=rc):
                (self.root / 'calls.json').unlink(missing_ok=True)
                invalid = self.document('Add next_type==14 bonus.')
                result, calls = self.run_stage([{'text': invalid}, {'rc': rc}], retries=5)
                self.assertTrue(result.startswith('false|'))
                self.assertEqual(len(calls), 2)
                self.assertFalse(calls[1]['result_existed'])

    def test_feedback_is_fixed_and_accepts_only_the_exact_host_error(self):
        result = {'ok': False, 'decision': 'reject', 'errors': ['unreachable_plan_condition']}
        feedback = gate.retry_feedback({**result, 'extra': 'UNTRUSTED MODEL TEXT'})
        self.assertIsNotNone(feedback)
        self.assertNotIn(b'UNTRUSTED MODEL TEXT', feedback)
        for altered in [None, [], {}, {**result, 'ok': 0}, {**result, 'decision': 'hold'},
                        {**result, 'errors': ['unreachable_plan_condition', 'unsupported_next_type']}]:
            with self.subTest(altered=altered):
                self.assertIsNone(gate.retry_feedback(altered))

    def test_feedback_cli_fails_closed_on_missing_malformed_or_symlink_receipt(self):
        bad = self.root / 'bad.json'
        malformed = self.root / 'malformed.json'
        malformed.write_text('{broken')
        bad.symlink_to(malformed)
        out = self.root / 'feedback.md'
        for path in [bad, malformed, self.root / 'absent']:
            result = subprocess.run(['python3', str(HELPER), 'retry-feedback', '--result', str(path),
                                     '--output', str(out)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 81)
            self.assertEqual(result.stdout, '')
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()

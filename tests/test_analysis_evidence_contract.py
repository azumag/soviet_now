import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
SCRIPT=ROOT/'strategy/analysis_contract.py'

def load():
    if not SCRIPT.is_file():raise AssertionError('analysis evidence gate is missing')
    spec=importlib.util.spec_from_file_location('gate',SCRIPT)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class EvidenceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();(self.root/'game_history').mkdir()

    def evidence(self,rows):
        p=self.root/'game_history/g.jsonl';p.write_text(''.join(json.dumps(x)+'\n' for x in rows))
        return load().build_evidence(self.root,['game_history/g.jsonl'])

    def contract(self,e):
        return {'version':1,'decision':'implement','evidence_sha256':hashlib.sha256(load().encode(e)).hexdigest(),
                'game_count':e['game_count'],'founded_games':e['founded_games'],
                'hypotheses':[{'id':'H1','claim':'Recover one blocked path','evidence':[{'file':'game_history/g.jsonl','turn':1}]}],
                'changes':[{'hypothesis_id':'H1','target':'strategy.py.staging','mechanism':'Replace one route test','required_next_types':[3]}]}

    def validate(self,c,e,prose=''):
        raw=prose+'\n```analysis_contract\n'+json.dumps(c)+'\n```\n'
        return load().validate(raw,e)

    def test_missing_counter_is_unknown_even_with_type16(self):
        e=self.evidence([{'turn':1,'next_type':3,'state_snapshot':{'pieces':[{'type':16}]}}])
        self.assertIsNone(e['founded_games']);self.assertEqual(e['unknown_counter_games'],1)

    def test_confirmed_increase_survives_disappearing_final_piece(self):
        e=self.evidence([{'turn':1,'next_type':3,'makeSorenCount':0},
                         {'turn':2,'makeSorenCount':1}, {'turn':3,'final_types':[2]}])
        self.assertEqual(e['founded_games'],1)

    def test_zero_requires_start_baseline_and_terminal_observation(self):
        self.assertIsNone(self.evidence([{'turn':1,'makeSorenCount':0}])['founded_games'])
        e=self.evidence([{'turn':1,'makeSorenCount':0},{'turn':2,'game_over':True,'state_snapshot':{'makeSorenCount':0}}])
        self.assertEqual(e['founded_games'],0)

    def test_inherited_positive_boolean_reset_and_conflicts_stay_unknown(self):
        for rows in ([{'turn':1,'makeSorenCount':1}],
                     [{'turn':1,'makeSorenCount':False}],
                     [{'turn':1,'makeSorenCount':0},{'turn':2,'makeSorenCount':1},{'turn':3,'makeSorenCount':0}],
                     [{'turn':1,'makeSorenCount':0,'state_snapshot':{'makeSorenCount':1}}]):
            with self.subTest(rows=rows):self.assertIsNone(self.evidence(rows)['founded_games'])

    def test_valid_one_hypothesis_unknown_count_can_continue(self):
        e=self.evidence([{'turn':1,'next_type':3}]);result=self.validate(self.contract(e),e)
        self.assertTrue(result['ok'],result)

    def test_missing_counter_cannot_be_declared_zero(self):
        e=self.evidence([{'turn':1,'next_type':3}]);c=self.contract(e);c['founded_games']=0
        self.assertIn('founding_count_mismatch',self.validate(c,e)['errors'])

    def test_unsupported_direct_input_is_rejected(self):
        e=self.evidence([{'turn':1,'next_type':3}]);c=self.contract(e);c['changes'][0]['required_next_types']=[14]
        self.assertIn('unsupported_next_type',self.validate(c,e)['errors'])
        c=self.contract(e)
        self.assertIn('unreachable_plan_condition',self.validate(c,e,'## Implementation Plan\nAdd next_type==14 bonus.\n')['errors'])

    def test_multiple_changes_and_hypotheses_are_rejected(self):
        e=self.evidence([{'turn':1,'next_type':3}]);c=self.contract(e)
        c['changes'].append(copy.deepcopy(c['changes'][0]));c['hypotheses'].append(copy.deepcopy(c['hypotheses'][0]))
        result=self.validate(c,e)
        self.assertIn('one_change_required',result['errors']);self.assertIn('one_hypothesis_required',result['errors'])

    def test_forged_turn_path_hash_and_target_refuse(self):
        e=self.evidence([{'turn':1,'next_type':3}])
        for field,value,expected in [('turn',999,'unverified_reference'),('file','../secret','unverified_reference')]:
            c=self.contract(e);c['hypotheses'][0]['evidence'][0][field]=value
            self.assertIn(expected,self.validate(c,e)['errors'])
        c=self.contract(e);c['evidence_sha256']='0'*64;self.assertIn('evidence_digest_mismatch',self.validate(c,e)['errors'])
        c=self.contract(e);c['changes'][0]['target']='core/config.sh';self.assertIn('unapproved_target',self.validate(c,e)['errors'])

    def test_plaintext_and_duplicate_json_keys_do_not_pass(self):
        m=load();e=self.evidence([{'turn':1,'next_type':3}])
        self.assertFalse(m.validate('analysis OK; soviet=0/1',e)['ok'])
        text='```analysis_contract\n'+json.dumps(self.contract(e)).replace('"version": 1','"version": 1, "version": 1')+'\n```'
        self.assertIn('invalid_contract_json',m.validate(text,e)['errors'])

    def test_explicit_hold_is_not_success(self):
        e=self.evidence([{'turn':1,'next_type':3}]);c=self.contract(e)
        c.update(decision='hold',hypotheses=[],changes=[],reason='Need counter evidence')
        result=self.validate(c,e);self.assertFalse(result['ok']);self.assertEqual(result['decision'],'hold');self.assertEqual(result['errors'],[])

    def test_duplicate_inputs_symlinks_and_malformed_data_refuse(self):
        m=load();p=self.root/'game_history/g.jsonl';p.write_text('{"turn":1}\n')
        with self.assertRaises(ValueError):m.build_evidence(self.root,['game_history/g.jsonl']*2)
        link=self.root/'game_history/link.jsonl';link.symlink_to(p)
        with self.assertRaises(ValueError):m.build_evidence(self.root,['game_history/link.jsonl'])
        p.write_text('broken\n')
        with self.assertRaises(ValueError):m.build_evidence(self.root,['game_history/g.jsonl'])

    def test_actual_brief_preserves_unknown_counter(self):
        import os, subprocess
        e=self.evidence([{'turn':1,'next_type':3,'score':1,'state_snapshot':{'pieces':[{'type':16}]}}])
        ep=self.root/'evidence.json';ep.write_bytes(load().encode(e))
        worker=(ROOT/'eloop_improve.sh').read_text()
        start=worker.index('python3 - "$IMPROVE_BRIEF_FILE"')
        start=worker.index("<<'PY'\n",start)+len("<<'PY'\n")
        code=worker[start:worker.index('\nPY\n',start)]
        out=self.root/'brief.md'
        args=[str(out),'missing','missing','missing','1','1','','','game_history/g.jsonl','50']
        env={**os.environ,'ANALYSIS_EVIDENCE_HOST':str(ep),'ANALYSIS_EVIDENCE_SHA256':hashlib.sha256(ep.read_bytes()).hexdigest()}
        result=subprocess.run(['python3','-',*args],input=code,cwd=self.root,env=env,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        brief=out.read_text();self.assertIn('soviet_counter=unknown',brief);self.assertNotIn('soviet=0/1',brief)
        self.assertNotIn('ソ連建国で+4000',brief)

    def test_real_stage1_stops_invalid_analysis_and_persists_rejection(self):
        import shlex,subprocess
        e=self.evidence([{'turn':1,'next_type':3}]);ep=self.root/'evidence.json';ep.write_bytes(load().encode(e))
        worker=(ROOT/'eloop_improve.sh').read_text();start=worker.index('\tfor _analysis_retry in ')
        block=worker[start:worker.index('\n\t# 分析用に絞った',start)]
        q=shlex.quote
        setup=f"""HOST_ROOT={q(str(ROOT))}
ANALYSIS_EVIDENCE_HOST={q(str(ep))}
IMPROVE_RUN_RECEIPT_DIR={q(str(self.root/'receipts'))}
ANALYSIS_RESULT_FILE={q(str(self.root/'analysis.md'))}
RUN_CMD_LOG_FILE={q(str(self.root/'log'))}
ANALYSIS_MAX_RETRIES=1
IMPROVE_WALL_TIMEOUT=3600
_improve_wall_start=$(date +%s)
analysis_ok=false
log() {{ :; }}
_improve_progress() {{ :; }}
_improve_note() {{ :; }}
_get_improve_agents() {{ echo fake; }}
_is_peak_hours() {{ return 1; }}
run_ai_list() {{ echo 'soviet=0/1; no verified contract' > "$ANALYSIS_RESULT_FILE"; return 0; }}
"""
        p=subprocess.run(['bash','-c',setup+block+'\nprintf "%s %s" "$analysis_ok" "$IMPROVE_FAILURE_CODE"'],text=True,capture_output=True,cwd=self.root)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(p.stdout,'false analysis_contract_invalid')
        record=json.loads((self.root/'receipts/analysis-check-1.json').read_text())
        self.assertFalse(record['ok']);self.assertTrue((self.root/'receipts/analysis-check-1.md').exists())

if __name__=='__main__':unittest.main()

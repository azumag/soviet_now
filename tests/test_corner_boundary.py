import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('corner_boundary', ROOT/'lib/corner_boundary.py')
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class BoundaryTests(unittest.TestCase):
    def test_atomic_public_marker(self):
        with tempfile.TemporaryDirectory() as d:
            module.publish(d,'prediction')
            first=json.loads((Path(d)/'corner_boundary_prediction.json').read_text())
            module.publish(d,'improvement')
            self.assertEqual(set(first),{'completed_at'})
            self.assertGreater(first['completed_at'],0)
            self.assertEqual(len(list(Path(d).iterdir())),2)
    def test_unknown_kind_rejected(self):
        with self.assertRaises(ValueError):module.publish('/unused','game')

class PredictionHookTests(unittest.TestCase):
    def test_only_successful_api_resolution_publishes(self):
        import subprocess
        source=(ROOT/'twitch_predictions.sh').read_text()
        function=source.split('_resolve_prediction_with_best_outcome() {',1)[1].split('\n_clear_stale_prediction_state_if_any()',1)[0]
        for code in (500,200):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as d:
                state=Path(d)/'current_prediction.json'
                state.write_text(json.dumps({'prediction_id':'fixture','outcome_ids':['fixture'],'best_outcome':0}))
                script='''_prediction_retry_clear() { :; }
_prediction_retry_record() { :; }
_prediction_api_error_message() { echo fixture; }
_log() { :; }
curl() { echo "$TEST_HTTP"; }
_resolve_prediction_with_best_outcome() {'''+function+'\n_resolve_prediction_with_best_outcome\n'
                import os
                env=dict(os.environ,TMP_STATE_DIR=d,PREDICTION_STATE_FILE=str(state),PREDICTION_RETRY_DIR=d,TEST_HTTP=str(code),TOKEN='fixture',CLIENT_ID='fixture',BROADCASTER_ID='fixture')
                subprocess.run(['bash','-c',script],cwd=ROOT,env=env,check=False,capture_output=True)
                self.assertEqual((Path(d)/'corner_boundary_prediction.json').exists(),code==200)

class CancelPublishTests(unittest.TestCase):
    """docich#3291: cancel 経路でも予想サイクルの確定的終了として境界を打つ。"""

    def _run(self, script, env_extra=None):
        import os, subprocess
        env = dict(os.environ)
        env.update(env_extra or {})
        return subprocess.run(['bash', '-c', script], cwd=ROOT, env=env,
                              check=False, capture_output=True, text=True)

    def test_no_contest_cancel_publishes_boundary(self):
        # resolve) 内の no-contest elif ブロックを抜き出し、PATCH 成功系だけ通す。
        source = (ROOT / 'twitch_predictions.sh').read_text()
        start = source.index('elif [ "$contested" -le 1 ]; then')
        end_marker = '_log "WARN: no-contest cancel failed; resolving as before"\n\tfi'
        region = source[start:source.index(end_marker) + len(end_marker)]
        stubs = '''_log() { :; }
_patch_prediction_cancel() { :; }
_record_canceled_prediction_handoff() { :; }
_prediction_retry_clear() { :; }
_sync_prediction_state_with_remote() { return 1; }
contested=1
PREDICTION_ID=fixture
if false; then :;
'''
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'current_prediction.json'
            state.write_text('{"prediction_id":"fixture"}')
            env = {'TMP_STATE_DIR': d, 'PREDICTION_STATE_FILE': str(state),
                   'PREDICTION_RETRY_DIR': d}
            proc = self._run(stubs + region, env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            boundary = Path(d) / 'corner_boundary_prediction.json'
            self.assertTrue(boundary.exists())
            self.assertGreater(json.loads(boundary.read_text())['completed_at'], 0)
            self.assertFalse(state.exists())

    def test_manual_cancel_publishes_boundary(self):
        source = (ROOT / 'twitch_predictions.sh').read_text()
        region = source.split('\ncancel)\n', 1)[1].split('\nautovote)', 1)[0]
        # case 分岐末尾の ;; を除いて単独スクリプトとして実行する。
        region = region.replace('\t;;\n', '\n')
        stubs = '''_log() { :; }
_patch_prediction_cancel() { :; }
_sync_prediction_state_with_remote() { return 1; }
_clear_stale_prediction_state_if_any() { return 1; }
'''
        with tempfile.TemporaryDirectory() as d:
            state = Path(d) / 'current_prediction.json'
            state.write_text('{"prediction_id":"fixture"}')
            env = {'TMP_STATE_DIR': d, 'PREDICTION_STATE_FILE': str(state)}
            proc = self._run(stubs + region, env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue((Path(d) / 'corner_boundary_prediction.json').exists())

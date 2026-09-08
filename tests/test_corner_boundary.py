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

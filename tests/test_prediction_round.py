import unittest
from lib.prediction_round import record_game

class PredictionRoundTest(unittest.TestCase):
    def state(self):
        return dict(round_version=2, created_at=100, max_games=2, games_completed=0, best_outcome=0)

    def test_excludes_game_already_running(self):
        s=self.state(); record_game(s, 10, 99, True, True)
        self.assertEqual(s['games_completed'],0)
        self.assertEqual(s['best_outcome'],0)

    def test_both_arms_and_duplicate_result(self):
        s=self.state()
        record_game(s, 10, 101, False, True)
        record_game(s, 10, 101, False, True)
        record_game(s, 11, 102, False, False)
        self.assertEqual(s['games_completed'],2)
        self.assertEqual(s['best_outcome'],1)

    def test_result_frozen_after_limit(self):
        s=self.state()
        record_game(s, 10, 101, False, False)
        record_game(s, 11, 102, False, False)
        record_game(s, 12, 103, True, True)
        self.assertEqual(s['games_completed'],2)
        self.assertEqual(s['best_outcome'],0)

    def test_soviet_takes_precedence(self):
        s=self.state();record_game(s,10,101,True,True)
        self.assertEqual(s['best_outcome'],2)

class WorkerRoundIntegration(unittest.TestCase):
    def run_tick(self, state=None, fenced=False):
        import json
        import os
        from pathlib import Path
        import subprocess
        import tempfile
        root=Path(__file__).resolve().parents[1]
        source=(root/'workers/prediction_worker.sh').read_text()
        block=source.split('\t# Independent rounds',1)[1].split('\n\tif [ -f "$HOT_STREAK',1)[0]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)
            (path/"tmp").mkdir()
            (path/"lib").mkdir()
            (path/"lib"/"prediction_round.py").write_text((root/"lib"/"prediction_round.py").read_text())
            if state is not None: state=dict(prediction_id="test-id", **state)
            if state is not None:
                (path/'current_prediction.json').write_text(json.dumps(state))
            if fenced:(path/'regression_check_in_progress').touch()
            stub=path/'twitch_predictions.sh'
            stub.write_text('#!/bin/bash\necho "$*" >> calls\n')
            stub.chmod(0o755)
            helpers='''
_read_json_field() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2],sys.argv[3]))' "$1" "$2" "$3" 2>/dev/null || echo "$3"; }
_has_prediction() { [ -f "$TMP_STATE_DIR/current_prediction.json" ]; }
_get_best_outcome() { _read_json_field "$TMP_STATE_DIR/current_prediction.json" best_outcome 0; }
_prediction_retry_active_for() { return 1; }
_log() { :; }
current_game_num=42
_LAST_GAME_NUM=42
current_acc_count=33
improve_status=running
POLL_INTERVAL=0
TWITCH_PREDICTIONS_ENABLED=1
for tick in 1; do
'''
            env=dict(os.environ,TMP_STATE_DIR=directory)
            subprocess.run(['bash','-c',helpers+'# Independent rounds'+block+'\ndone'],cwd=directory,env=env,check=True,timeout=5)
            return (path/'calls').read_text().strip() if (path/'calls').exists() else ''

    def test_creates_despite_nonzero_accumulator_and_running_improve(self):
        self.assertEqual(self.run_tick(),'create 42')

    def test_waits_for_own_round_not_improve_reset(self):
        self.assertEqual(self.run_tick(dict(round_version=2,games_completed=1,max_games=2,best_outcome=1)),'')

    def test_settles_at_own_limit(self):
        self.assertEqual(self.run_tick(dict(round_version=2,games_completed=2,max_games=2,best_outcome=1)),'resolve 1')

    def test_waits_for_result_bookkeeping_and_regression(self):
        self.assertEqual(self.run_tick(dict(round_version=2,games_completed=2,max_games=2,best_outcome=1),True),'')

    def test_does_not_create_during_bookkeeping(self):
        self.assertEqual(self.run_tick(fenced=True),'')

class CleanupIntegration(unittest.TestCase):
    def test_stale_check_ignores_improvement_count_and_age(self):
        import json
        import os
        from pathlib import Path
        import subprocess
        import tempfile
        root=Path(__file__).resolve().parents[1]
        source=(root/'twitch_predictions.sh').read_text()
        fn='_prediction_state_stale_reason() {'+source.split('_prediction_state_stale_reason() {',1)[1].split('\n}\n',1)[0]+'\n}\n'
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)
            (p/'state').write_text(json.dumps(dict(prediction_id='id',outcome_ids=['a','b','c','d'],round_version=2,created_at=1,games_completed=3,max_games=48)))
            (p/'acc').write_text(json.dumps(dict(count=999)))
            env=dict(os.environ,PREDICTION_STATE_FILE=str(p/'state'),ACCUMULATED_GAMES_FILE=str(p/'acc'),PREDICTION_MAX_GAMES='48',PREDICTION_STATE_MAX_AGE_SEC='1')
            result=subprocess.run(['bash','-c',fn+'_prediction_state_stale_reason'],env=env,text=True,capture_output=True,check=True)
            self.assertEqual(result.stdout,'')

class DecisionRetryTest(unittest.TestCase):
    def test_retry_does_not_change_already_decided_outcome(self):
        import json
        from pathlib import Path
        import subprocess
        import tempfile
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'current_prediction.json'
            state=dict(prediction_id='id',round_version=2,games_completed=48,max_games=48,best_outcome=1)
            path.write_text(json.dumps(state))
            command=['python3',str(root/'lib/prediction_round.py'),str(path),'decision']
            self.assertEqual(subprocess.check_output(command,text=True).strip(),'1')
            state['best_outcome']=3
            path.write_text(json.dumps(state))
            self.assertEqual(subprocess.check_output(command,text=True).strip(),'1')

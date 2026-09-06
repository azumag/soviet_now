import importlib.util
import subprocess
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
WORKER=ROOT/'eloop_improve.sh'
HELPER=ROOT/'strategy/improve_command.py'

class StageBudgetTests(unittest.TestCase):
    def test_worker_initializes_deadline_after_env_and_before_summary(self):
        s=WORKER.read_text()
        self.assertIn('_improve_budget_pair=',s)
        self.assertLess(s.index('_improve_job_override='),s.index('source ./eloop_lib.sh'))
        self.assertLess(s.index('source ./eloop_lib.sh'),s.index('_improve_budget_pair='))
        self.assertLess(s.index('_improve_budget_pair='),s.index('SANDBOX_DIR=$(create_sandbox'))
        self.assertEqual(s.count('_improve_budget_pair='),1)
        self.assertIn('IMPROVE_STAGE_DEADLINE_MONOTONIC="$IMPROVE_ANALYSIS_DEADLINE_MONOTONIC"',s)
        self.assertIn('unset IMPROVE_STAGE_DEADLINE_MONOTONIC',s)

    def test_analysis_requires_success_not_only_nonempty_output(self):
        s=WORKER.read_text()
        self.assertIn('[ "${_analysis_rc:-1}" -eq 0 ] && [ -s "$ANALYSIS_RESULT_FILE" ]',s)
        section=s[s.index('_analysis_rc=$?'):s.index('# 分析用に絞った')]
        self.assertIn('"$_analysis_rc" -eq 80',section)
        self.assertIn('RUN_AI_LIST_FAILURE_KIND',section)

    def test_late_review_and_apply_cannot_ignore_deadline(self):
        s=WORKER.read_text()
        self.assertIn('[ "${_review_rc:-0}" -eq 80 ]',s)
        self.assertIn('deadline before harvest',s)

    def test_half_budget_is_reserved_once(self):
        p=subprocess.run(['python3',str(HELPER),'init','--seconds','40'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        job,analysis=map(float,p.stdout.split());self.assertAlmostEqual(job-analysis,20,places=4)
        p=subprocess.run(['python3',str(HELPER),'init','--seconds','40','--analysis-seconds','40'],capture_output=True)
        self.assertNotEqual(p.returncode,0)

    def test_queue_wait_is_clamped_and_subsecond_budget_does_not_round_up(self):
        import time
        now=time.clock_gettime(time.CLOCK_MONOTONIC)
        p=subprocess.run(['python3',str(HELPER),'queue-limit','--job-deadline',str(now+3.9),'--limit','180'],capture_output=True,text=True)
        self.assertEqual(p.returncode,0,p.stderr);self.assertGreater(int(p.stdout),0);self.assertLessEqual(int(p.stdout),3)
        p=subprocess.run(['python3',str(HELPER),'queue-limit','--job-deadline',str(now+.05),'--limit','180'],capture_output=True,text=True)
        self.assertEqual(p.returncode,80)

    def test_one_job_override_cannot_increase_persistent_budget(self):
        p=subprocess.run(['python3',str(HELPER),'init','--seconds','41','--max-seconds','40'],capture_output=True)
        self.assertNotEqual(p.returncode,0)
        p=subprocess.run(['python3',str(HELPER),'init','--seconds','20','--max-seconds','40'],capture_output=True)
        self.assertEqual(p.returncode,0)

if __name__=='__main__':unittest.main()

import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]

class BudgetIntegrationTests(unittest.TestCase):
    def shell(self,code,*args,env=None):
        return subprocess.run(['bash','-c','source "$1/strategy/ai.sh"; log() { :; };\n'+code,'test',str(ROOT),*args],
                              cwd=ROOT,env={**os.environ,**(env or {})},text=True,capture_output=True,timeout=12)

    def test_expired_list_budget_does_not_call_first_model(self):
        code='''RUN_AI_IMPROVEMENT_MODE=1
IMPROVE_JOB_DEADLINE_MONOTONIC=0
run_ai() { echo WRONG; return 0; }
run_ai_list ANALYZE a,b ignored ignored
printf 'rc=%s kind=%s\n' "$?" "${RUN_AI_LIST_FAILURE_KIND:-}"
'''
        p=self.shell(code)
        self.assertNotIn('WRONG',p.stdout);self.assertIn('rc=80 kind=job_deadline_exhausted',p.stdout)

    def test_stage_expiry_is_not_provider_backoff(self):
        code='''RUN_AI_IMPROVEMENT_MODE=1
IMPROVE_STAGE_DEADLINE_MONOTONIC=0
run_ai() { echo WRONG; return 0; }
_ai_backoff_set() { echo BAD_BACKOFF; }
run_ai_list ANALYZE a,b ignored ignored
printf 'rc=%s kind=%s\n' "$?" "${RUN_AI_LIST_FAILURE_KIND:-}"
'''
        p=self.shell(code);self.assertNotIn('WRONG',p.stdout);self.assertNotIn('BAD_BACKOFF',p.stdout)
        self.assertIn('rc=80 kind=stage_deadline_exhausted',p.stdout)

    def test_partial_file_after_deadline_does_not_become_success(self):
        with tempfile.TemporaryDirectory() as d:
            code='''RUN_AI_IMPROVEMENT_MODE=1
RUN_AI_SHARED_FAILURE_BACKOFF=1
build_prompt() { echo prompt; }
run_cmd() { printf 'partial' > "$2"; return 80; }
_ai_backoff_set() { echo BAD_BACKOFF; }
run_cmd() { printf 'partial' > "$TARGET"; return 80; }
TARGET="$2/out"
run_ai ANALYZE a '' p "$TARGET"
echo rc=$?
'''
            p=self.shell(code,d);self.assertIn('rc=80',p.stdout);self.assertNotIn('BAD_BACKOFF',p.stdout)

    def test_mixed_rate_limit_and_timeout_are_not_all_rate_limited(self):
        code='''run_ai() { [ "$2" = a ] && return 79; return 124; }
run_ai_list ANALYZE a,b ignored ignored
printf 'rc=%s kind=%s\n' "$?" "${RUN_AI_LIST_FAILURE_KIND:-}"
'''
        p=self.shell(code);self.assertIn('rc=1 kind=mixed_model_failures',p.stdout)

    def test_all_rate_limited_preserve_existing_return_code(self):
        p=self.shell('run_ai() { return 79; }; run_ai_list ANALYZE a,b p e; printf "rc=%s kind=%s" "$?" "${RUN_AI_LIST_FAILURE_KIND:-}"')
        self.assertIn('rc=79 kind=rate_limited',p.stdout)

    def test_budget_is_rechecked_between_models(self):
        deadline=time.clock_gettime(time.CLOCK_MONOTONIC)+.2
        code='''RUN_AI_IMPROVEMENT_MODE=1
run_ai() { echo CALL-$2; sleep .25; return 1; }
run_ai_list ANALYZE a,b p e
echo rc=$?
'''
        p=self.shell(code,env={'IMPROVE_JOB_DEADLINE_MONOTONIC':str(deadline)})
        self.assertIn('CALL-a',p.stdout);self.assertNotIn('CALL-b',p.stdout);self.assertIn('rc=80',p.stdout)

    def test_guarded_run_cmd_classifies_own_stderr_and_ignores_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp).resolve();fake=d/'fake';fake.write_text('''#!/usr/bin/env python3
import json,sys,os,time
assert '--format' in sys.argv and 'json' in sys.argv and '--print-logs' in sys.argv
print('timestamp="2026-09-07T00:00:00Z" level="INFO" run="own" message="created" id="ses_own" directory='+json.dumps(os.getcwd()),file=sys.stderr,flush=True)
print('timestamp="2026-09-07T00:00:01Z" level="ERROR" run="own" message="stream error" providerID="opencode" modelID="fake" session.id="ses_own" error.error="rate_limit_error"',file=sys.stderr,flush=True)
time.sleep(8)
''');fake.chmod(0o755)
            code='''RUN_AI_IMPROVEMENT_MODE=1
RUN_CMD_TIMEOUT_SEC=8
RUN_CMD_LOG_FILE="$2/log"
RUN_CMD_TMP_DIR="$2"
IMPROVE_RUN_RECEIPT_DIR="$2/receipts"
OPENCODE_BIN="$2/fake"
_run_cmd_timeout_bin() { command -v timeout || command -v gtimeout; }
_trim_log_file() { :; }; start_spinner() { :; }; stop_spinner() { :; }
_opencode_run_lock_enter() { :; }; _opencode_run_lock_leave() { :; }
_opencode_xdg_state_home() { echo "$2"; }; _opencode_xdg_data_home() { echo "$2"; }
_opencode_sync_auth_to_xdg() { :; }; _opencode_cleanup_internal_locks() { :; }
_run_cmd_start_heartbeat() { :; }; _run_cmd_stop_heartbeat() { :; }
_run_cmd_start_expected_file_watchdog() { :; }; _run_cmd_stop_expected_file_watchdog() { :; }
_run_cmd_store_resume_session() { :; }
run_cmd opencode:fake prompt
echo rc=$?
'''
            p=self.shell(code,str(d));self.assertIn('rc=79',p.stdout,p.stderr)
            receipts=list((d/'receipts').glob('*.json'));self.assertEqual(len(receipts),1)
            self.assertEqual(json.loads(receipts[0].read_text())['reason'],'rate_limited')

if __name__=='__main__':unittest.main()

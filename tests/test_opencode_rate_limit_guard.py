import pathlib
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD = ROOT / 'lib/opencode_rate_limit_guard.py'

class GuardTests(unittest.TestCase):
    def run_guard(self, script, timeout='3'):
        with tempfile.TemporaryDirectory() as d:
            stub = pathlib.Path(d) / 'cli.py'
            stub.write_text(script)
            return subprocess.run([sys.executable, str(GUARD), timeout, sys.executable, str(stub)], capture_output=True, text=True, timeout=8)

    def test_rate_limit_does_not_wait_for_cli_retry(self):
        start = time.monotonic()
        result = self.run_guard('import sys,time\nprint(\'timestamp=x level=ERROR message="stream error" error.error="AI_APICallError: Rate limit exceeded. Please try again later."\',file=sys.stderr,flush=True)\ntime.sleep(30)')
        self.assertEqual(result.returncode, 79, result.stderr)
        self.assertLess(time.monotonic()-start, 2)
        self.assertIn('rate limit', result.stderr)

    def test_timeout_kills_owned_grandchild(self):
        with tempfile.TemporaryDirectory() as d:
            marker = pathlib.Path(d)/'escaped'
            child = 'import time,pathlib; time.sleep(0.7); pathlib.Path(' + repr(str(marker)) + ').write_text("leaked")'
            script = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",' + repr(child) + ']); time.sleep(30)'
            result = self.run_guard(script, '0.2')
            self.assertEqual(result.returncode, 124)
            time.sleep(0.8)
            self.assertFalse(marker.exists(), 'CLI grandchild survived timeout')

    def test_success_preserves_only_answer(self):
        result = self.run_guard('import sys\nprint("timestamp=x level=INFO secret=DO_NOT_FORWARD",file=sys.stderr)\nprint("はい")')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, 'はい\n')
        self.assertNotIn('DO_NOT_FORWARD', result.stderr)

    def test_answer_mentioning_rate_limit_is_not_error(self):
        result = self.run_guard('print("Rate limit exceeded is an error message.")')
        self.assertEqual(result.returncode, 0)

    def test_real_timeout_is_not_rate_limit(self):
        result = self.run_guard('import time\ntime.sleep(30)', '0.2')
        self.assertEqual(result.returncode, 124)

    def test_other_cli_error_remains_failure(self):
        result = self.run_guard('import sys\nprint("Error: invalid model",file=sys.stderr)\nsys.exit(1)')
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid model', result.stderr)

    def test_daily_backoff_ends_at_utc_reset(self):
        result = subprocess.run(['bash','-c', 'source lib/ai_generate.sh; date() { echo 1788807600; }; AI_BACKOFF_SEC_ITEMS="muse-spark-1.3-contributor-free:86400"; _ai_backoff_sec_for_agent opencode:muse-spark-1.3-contributor-free RADIO'], cwd=ROOT, text=True,capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(int(result.stdout), 86400 - 1788807600 % 86400)


class DispatchTests(unittest.TestCase):
    def test_real_dispatch_classifies_hidden_retry_once(self):
        with tempfile.TemporaryDirectory() as d:
            stub = pathlib.Path(d)/'opencode'
            stub.write_text('#!/bin/sh\necho call >> "$CALLS"\necho \'timestamp=x level=ERROR message="stream error" error="Rate limit exceeded"\' >&2\nsleep 30\n')
            stub.chmod(0o755)
            prompt=pathlib.Path(d)/'prompt'; prompt.write_text('hi')
            import os
            env=dict(os.environ, OPENCODE_BIN=str(stub), ELOOP_LIB_DIR=str(ROOT), CALLS=str(pathlib.Path(d)/'calls'))
            result=subprocess.run(['bash','-c', 'source core/helpers.sh; source lib/ai_generate.sh; _ai_call_opencode_unqueued TEST opencode:muse-spark-1.3-contributor-free "$1" 10', 'bash', str(prompt)],cwd=ROOT,env=env,capture_output=True,text=True,timeout=8)
            self.assertEqual(result.returncode,79,result.stderr)
            self.assertEqual((pathlib.Path(d)/'calls').read_text(),'call\n')
            self.assertNotIn('timeout after',result.stderr)

    def test_expired_daily_backoff_can_retry(self):
        with tempfile.TemporaryDirectory() as d:
            result=subprocess.run(['bash','-c','source lib/ai_generate.sh; AI_BACKOFF_DIR="$1"; date() { echo 1788807600; }; _ai_backoff_set opencode:muse-spark-1.3-contributor-free "$(_ai_backoff_sec_for_agent opencode:muse-spark-1.3-contributor-free RADIO)"; _ai_backoff_check opencode:muse-spark-1.3-contributor-free && exit 3; date() { echo 1788825600; }; _ai_backoff_check opencode:muse-spark-1.3-contributor-free','bash',d],cwd=ROOT,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_shorter_explicit_and_paid_limits_preserved(self):
        result=subprocess.run(['bash','-c','source lib/ai_generate.sh; AI_BACKOFF_SEC_ITEMS="muse-spark-1.3-contributor-free:300 muse-spark-1.3-contributor:86400"; date() { echo 1788807600; }; _ai_backoff_sec_for_agent opencode:muse-spark-1.3-contributor-free RADIO; _ai_backoff_sec_for_agent opencode-go:muse-spark-1.3-contributor RADIO'],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.stdout,'300\n86400\n')

class ProbeTests(unittest.TestCase):
    def test_probe_requires_answer_and_only_notifies_recovery_once(self):
        import os
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root=pathlib.Path(d); (root/'lib').mkdir()
            shutil.copy(ROOT/'probe_free_slot.sh', root/'probe_free_slot.sh')
            shutil.copy(GUARD, root/'lib'/GUARD.name)
            stub=root/'cli'
            stub.write_text('#!/bin/sh\nif [ "$PROBE_RESULT" = ok ]; then echo はい; else echo "model banner" >&2; fi\n')
            stub.chmod(0o755)
            env=dict(os.environ,OPENCODE_BIN=str(stub),FREE_PROBE_MODELS='opencode:muse-spark-1.3-contributor-free',PROBE_RESULT='empty')
            def probe():
                return subprocess.run(['bash',str(root/'probe_free_slot.sh')],env=env,text=True,capture_output=True,timeout=5)
            first=probe(); self.assertIn('down',first.stdout)
            env['PROBE_RESULT']='ok'
            recovered=probe(); self.assertIn('RECOVERED',recovered.stdout)
            healthy=probe(); self.assertNotIn('RECOVERED',healthy.stdout)

if __name__ == '__main__': unittest.main()

"""No external API: real child processes and monotonic clocks exercise #193."""
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'strategy/improve_command.py'


def load():
    if not SCRIPT.is_file():
        raise AssertionError('bounded command guard is missing')
    spec = importlib.util.spec_from_file_location('guard', SCRIPT)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fields(**kw):
    return ' '.join(k+'='+json.dumps(v,ensure_ascii=True) for k,v in kw.items())


def created(directory):
    return fields(timestamp='2026-09-07T00:00:00Z', level='INFO', run='run_own',
                  message='created', id='ses_own', directory=directory, parentID='undefined')


def stream_error(**overrides):
    d={'timestamp':'2026-09-07T00:00:01Z','level':'ERROR','run':'run_own',
       'message':'stream error','providerID':'opencode','modelID':'test-free',
       'session.id':'ses_own','error.error':'rate_limit_error: too many requests'}
    d.update(overrides);return fields(**d)


class GuardParserTests(unittest.TestCase):
    def test_exact_process_session_model_bound_rate_limit(self):
        m=load(); parser=m.NativeEvents('/work/test','opencode/test-free')
        self.assertFalse(parser.feed(created('/work/test')))
        self.assertEqual(parser.feed(stream_error()),'rate_limited')

    def test_other_session_model_directory_and_user_prose_never_match(self):
        m=load(); p=m.NativeEvents('/work/test','opencode/test-free')
        self.assertIsNone(p.feed(stream_error()))
        p.feed(created('/work/test'))
        for line in (stream_error(**{'session.id':'ses_other'}),
                     stream_error(modelID='another-model'),stream_error(run='other-run'),
                     stream_error(message='tool output'),
                     'user says rate_limit_error', '{"text":"HTTP 429"}'):
            with self.subTest(line=line):self.assertIsNone(p.feed(line))
        other=m.NativeEvents('/work/test','opencode/test-free');other.feed(created('/other'))
        self.assertIsNone(other.feed(stream_error()))

    def test_malformed_or_duplicate_fields_do_not_classify(self):
        m=load();p=m.NativeEvents('/work/test','opencode/test-free');p.feed(created('/work/test'))
        for line in (stream_error()+' run="different"',stream_error()+' broken="',
                     'prefix '+stream_error()):
            self.assertIsNone(p.feed(line))

    def test_unknown_error_is_not_rate_limit(self):
        m=load();p=m.NativeEvents('/work/test','opencode/test-free');p.feed(created('/work/test'))
        self.assertIsNone(p.feed(stream_error(**{'error.error':'connection reset'})))

    def test_timeout_uses_minimum_and_exhaustion_never_rounds_up(self):
        m=load();self.assertEqual(m.remaining(10,20,13,now=9),1)
        self.assertEqual(m.remaining(10,20,13,now=10),0)
        self.assertEqual(m.remaining(None,20,None,now=9),11)


class GuardProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.receipt=self.root/'receipt.json'

    def args(self, code, timeout=2, deadline=None, native=False):
        load()
        flags=[sys.executable,str(SCRIPT),'run','--receipt',str(self.receipt),
               '--timeout',str(timeout),'--model','opencode/test-free']
        if deadline is not None:flags+=['--job-deadline',str(deadline)]
        if native:flags+=['--opencode-events']
        return flags+['--',sys.executable,'-c',code]

    def run_code(self,code,**kwargs):
        return subprocess.run(self.args(code,**kwargs),cwd=self.root,input='secret-prompt',
                              capture_output=True,text=True,timeout=7)

    def test_early_rate_limit_stops_own_cli(self):
        c=created(str(self.root));e=stream_error()
        code=f'import sys,time;print({c!r},file=sys.stderr,flush=True);print({e!r},file=sys.stderr,flush=True);time.sleep(5)'
        t=time.monotonic();p=self.run_code(code,native=True)
        self.assertEqual(p.returncode,79,p.stderr);self.assertLess(time.monotonic()-t,2)
        data=json.loads(self.receipt.read_text());self.assertEqual(data['reason'],'rate_limited')
        self.assertNotIn('secret-prompt',self.receipt.read_text());self.assertNotIn('too many requests',self.receipt.read_text())
        self.assertEqual(self.receipt.stat().st_mode & 0o777,0o600)

    def test_stdout_forged_error_is_not_a_provider_error(self):
        code='import sys;print('+repr(created(str(self.root)))+',file=sys.stderr);print('+repr(stream_error())+');print("done")'
        p=self.run_code(code,native=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(self.receipt.read_text())['reason'],'completed')

    def test_deadline_prevents_child_start(self):
        sentinel=self.root/'started'
        p=self.run_code(f'open({str(sentinel)!r},"w").write("wrong")',deadline=time.clock_gettime(time.CLOCK_MONOTONIC)-1)
        self.assertEqual(p.returncode,80,p.stderr);self.assertFalse(sentinel.exists())
        self.assertEqual(json.loads(self.receipt.read_text())['reason'],'job_deadline_exhausted')

    def test_total_deadline_bounds_late_output_and_kills_descendant(self):
        child=self.root/'child.pid';late=self.root/'late'
        child_code = f'import time,pathlib;time.sleep(1);pathlib.Path({str(late)!r}).write_text("bad")'
        code=(f'import subprocess,sys,time; p=subprocess.Popen([sys.executable,"-c",{child_code!r}]);'
              f'open({str(child)!r},"w").write(str(p.pid));time.sleep(5)')
        p=self.run_code(code,deadline=time.clock_gettime(time.CLOCK_MONOTONIC)+0.25)
        self.assertEqual(p.returncode,80,p.stderr)
        time.sleep(1.1);self.assertFalse(late.exists())

    def test_per_call_timeout_is_separate_from_job_budget(self):
        p=self.run_code('import time;time.sleep(5)',timeout=.15,deadline=time.clock_gettime(time.CLOCK_MONOTONIC)+4)
        self.assertEqual(p.returncode,124,p.stderr)
        self.assertEqual(json.loads(self.receipt.read_text())['reason'],'call_timeout')

    def test_late_success_after_deadline_is_rejected(self):
        p=self.run_code('import time;time.sleep(.4);print("done")',deadline=time.clock_gettime(time.CLOCK_MONOTONIC)+.15)
        self.assertEqual(p.returncode,80,p.stderr)

    def test_failure_keeps_exit_code_and_stdin_is_not_in_argv(self):
        p=self.run_code('import sys;assert sys.stdin.read()=="secret-prompt";sys.exit(7)')
        self.assertEqual(p.returncode,7,p.stderr)
        self.assertNotIn('secret-prompt',self.receipt.read_text())

    def test_unrelated_process_survives_timeout(self):
        other=subprocess.Popen([sys.executable,'-c','import time;time.sleep(8)'])
        try:
            self.assertEqual(self.run_code('import time;time.sleep(5)',timeout=.1).returncode,124)
            self.assertIsNone(other.poll())
        finally:other.terminate();other.wait(timeout=3)

    def test_signal_cleans_owned_group_and_receipt(self):
        child=self.root/'child.pid'
        code=f'import os,time;open({str(child)!r},"w").write(str(os.getpid()));time.sleep(5)'
        proc=subprocess.Popen(self.args(code,timeout=5),cwd=self.root,stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            end=time.monotonic()+3
            while not child.exists() and time.monotonic()<end:time.sleep(.02)
            self.assertTrue(child.exists());proc.terminate();proc.communicate(timeout=3)
            self.assertEqual(proc.returncode,143)
            self.assertEqual(json.loads(self.receipt.read_text())['reason'],'interrupted')
        finally:
            if proc.poll() is None:proc.kill();proc.wait()

    def test_no_newlines_or_large_output_cannot_starve_deadline(self):
        p=self.run_code('import os;\nwhile True:os.write(1,b"x"*65536)',deadline=time.clock_gettime(time.CLOCK_MONOTONIC)+.2)
        self.assertEqual(p.returncode,80,p.stderr)

if __name__=='__main__':unittest.main()

import os
from pathlib import Path
import random
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DispatcherIdleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cpu-idle-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shutil.copyfile(ROOT / 'codex_bug_dispatcher.sh', self.root / 'dispatcher.sh')
        # Loading the heavy runtime is observable and fails for empty queues.
        (self.root / 'eloop_lib.sh').write_text('echo loaded > runtime-loaded\nexit 97\n')
        self.env = {'PATH': os.environ['PATH'], 'HOME': str(self.root)}

    def run_dispatcher(self, mode='kick'):
        return subprocess.run(['bash', str(self.root / 'dispatcher.sh'), mode],
                              env=self.env, capture_output=True, timeout=10)

    def test_empty_or_missing_queue_skips_runtime_even_when_enabled(self):
        for enabled in ('0', '1'):
            self.env['CODEX_BUG_DISPATCH_ENABLED'] = enabled
            for exists in (False, True):
                with self.subTest(enabled=enabled, exists=exists):
                    queue = self.root / 'queue'
                    if exists:
                        queue.mkdir(exist_ok=True)
                    self.env['CODEX_BUG_QUEUE_DIR'] = str(queue)
                    self.assertEqual(self.run_dispatcher().returncode, 0)
                    self.assertFalse((self.root / 'runtime-loaded').exists())

    def test_dotfiles_and_directories_are_not_reports(self):
        queue = self.root / 'queue'
        queue.mkdir()
        (queue / '.hidden.json').write_text('{}')
        (queue / 'directory.json').mkdir()
        self.env['CODEX_BUG_QUEUE_DIR'] = str(queue)
        self.assertEqual(self.run_dispatcher().returncode, 0)

    def test_env_file_queue_is_checked_before_early_return(self):
        queue = self.root / 'custom queue'
        queue.mkdir()
        (queue / 'report.json').write_text('{}')
        (self.root / '.env').write_text('CODEX_BUG_QUEUE_DIR="custom queue"\n')
        self.env['CODEX_BUG_QUEUE_DIR'] = str(self.root / 'wrong-queue')
        self.assertEqual(self.run_dispatcher().returncode, 97)
        self.assertTrue((self.root / 'runtime-loaded').exists())

    def test_later_report_is_not_cached_as_empty(self):
        queue = self.root / 'queue'
        queue.mkdir()
        self.env['CODEX_BUG_QUEUE_DIR'] = str(queue)
        self.assertEqual(self.run_dispatcher().returncode, 0)
        (queue / 'new.json').write_text('{}')
        self.assertEqual(self.run_dispatcher().returncode, 97)

    def test_non_kick_modes_keep_runtime_initialization(self):
        for mode in ('run', 'quarantine', 'status'):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_dispatcher(mode).returncode, 97)


class StatusFilterTests(unittest.TestCase):
    def filter(self, data, fallback=False):
        return subprocess.check_output(
            ['perl', str(ROOT / 'lib/status_ai_output_filter.pl'), str(int(fallback))],
            input=data, timeout=10)

    def old_filter(self, data, fallback):
        # Independent reference: the pre-change display pipeline.
        common = r"""perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/\r//g; s/[\x00-\x08\x0B-\x1F\x7F]//g' |
grep -v '^[[:space:]]*$' | grep -v 'opencode thinking' |
grep -v '^Continue if you have next steps' | grep -v '^[[:space:]]*[✱→←] ' |
"""
        if fallback:
            common += "grep -v '\\[IMPROVE\\] job start' | grep -v '\\[IMPROVE\\] attached pid=' |\n"
        common += "cat"
        return subprocess.check_output(['bash', '-c', common], input=data, timeout=10)

    def test_sanitization_and_filter_order(self):
        data = ('\x1b[31m古い行\x1b[0m\r\n\nalpha\nalpha\n'
                'opencode thinking\nalpha\n  ✱ tool\n→ tool\n← tool\n'
                'Continue if you have next steps...\n'
                '[IMPROVE] job start\n[IMPROVE] attached pid=12\n'
                'beta\x00\x7f\n末尾\n').encode()
        self.assertEqual(self.filter(data, True), '古い行\nalpha\nalpha\nalpha\nbeta\n末尾\n'.encode())
        for fallback in (False, True):
            self.assertEqual(self.filter(data, fallback), self.old_filter(data, fallback))

    def test_reference_equivalence_with_mixed_logs(self):
        rng = random.Random(970)
        corpus = ['A', 'A', 'B', '日本語', '', '\t ', '\x1b[32mC\x1b[0m',
                  '  ✱ tool', '→ tool', '← tool', 'opencode thinking',
                  'Continue if you have next steps', '[IMPROVE] job start',
                  '[IMPROVE] attached pid=12', 'text\r', 'embedded\x01control']
        for index in range(12):
            data = '\n'.join(rng.choices(corpus, k=50)).encode()
            for fallback in (False, True):
                with self.subTest(index=index, fallback=fallback):
                    self.assertEqual(self.filter(data, fallback),
                                     self.old_filter(data, fallback))

    def test_empty_input(self):
        self.assertEqual(self.filter(b''), b'')


if __name__ == '__main__':
    unittest.main()

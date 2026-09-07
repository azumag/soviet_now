import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class BackgroundPriorityTests(unittest.TestCase):
    def run_policy(self, platform='linux-gnu', configured='10', current='0'):
        code = '''
source "$1/lib/background_priority.sh" || exit 90
OSTYPE=$2
SOREN_BACKGROUND_NICE=$3
# Explicit argument fixtures avoid depending on host ps/renice syntax.
ps() { printf '%s\\n' "$CURRENT_NICE"; }
renice() { printf 'renice:%s\\n' "$*" > "$POLICY_RESULT"; }
soren_background_priority
'''
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'result'
            result = subprocess.run(['bash', '-c', code, 'test', str(ROOT), platform, configured], env={**os.environ, 'CURRENT_NICE':current, 'POLICY_RESULT':str(target)}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return target.read_text() if target.exists() else ''

    def test_linux_background_gets_lower_priority(self):
        self.assertIn('renice:-n 10 -p', self.run_policy())

    def test_non_linux_is_unchanged(self):
        self.assertEqual(self.run_policy(platform='darwin25'), '')

    def test_opt_out(self):
        self.assertEqual(self.run_policy(configured='0'), '')

    def test_never_raises_existing_low_priority(self):
        self.assertEqual(self.run_policy(current='15'), '')

    def test_invalid_and_privileged_values_fall_back(self):
        for v in ['-5', '20', 'oops']:
            with self.subTest(v=v):
                self.assertIn('renice:-n 10 -p', self.run_policy(configured=v))

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux priority integration')
    def test_child_inherits_actual_priority(self):
        code = 'source "$1/lib/background_priority.sh"; soren_background_priority; python3 -c "import os; print(os.getpriority(os.PRIO_PROCESS, 0))"'
        p = subprocess.run(['bash', '-c', code, 'test', str(ROOT)], env={**os.environ,'SOREN_BACKGROUND_NICE':'10'}, capture_output=True, text=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertGreaterEqual(int(p.stdout.strip()),10)

if __name__ == '__main__':
    unittest.main()

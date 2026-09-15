import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TitleDayTest(unittest.TestCase):
    def run_script(self, owner='123', memo=None, title='Keep Title DAY 177 intact'):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            shutil.copy(Path(__file__).resolve().parents[1] / 'update_stream_title_day.sh', p)
            if memo is not None:
                (p / 'prompts').mkdir()
                (p / 'prompts/ops_brief.md').write_text(memo)
            (p / 'curl').write_text(r'''#!/bin/bash
case "$*" in
*oauth2/validate*)
 case "$*" in
 *OAuth\ bad*) echo '{"client_id":"app","user_id":"123","scopes":[]}' ;;
 *) printf '{"client_id":"app","user_id":"%s","scopes":["channel:manage:broadcast"]}' "$TEST_OWNER" ;;
 esac ;;
*) printf '{"data":[{"title":"%s"}]}' "$TEST_TITLE" ;;
esac
''')
            (p / 'curl').chmod(0o755)
            env = {
                **os.environ,
                'PATH': d + ':' + os.environ['PATH'],
                'TWITCH_BROADCASTER_ID': '123',
                'TWITCH_TITLE_TOKEN': '',
                'TWITCH_PREDICTIONS_TOKEN': 'bad',
                'TWITCH_BOT_TOKEN': 'good',
                'TEST_OWNER': owner,
                'TEST_TITLE': title,
            }
            return subprocess.run(
                ['bash', str(p / 'update_stream_title_day.sh'), '--dry-run'],
                env=env,
                text=True,
                capture_output=True,
            )

    def test_scope_fallback_normalizes_day_prefix(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('TWITCH_BOT_TOKEN', r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] Keep Title intact\n$')
        self.assertNotIn('good', r.stderr)

    def test_wrong_broadcaster_is_rejected(self):
        self.assertEqual(self.run_script('999').returncode, 3)

    def test_body_updates_from_ops_brief(self):
        r = self.run_script(memo='# memo\n- 新しい配信内容\n- 過去の内容\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] 新しい配信内容\n$')

    def test_empty_memo_preserves_body(self):
        r = self.run_script(memo='# empty\n')
        self.assertRegex(r.stdout, r'^\[day\d+\] Keep Title intact\n$')

    def test_missing_day_marker_self_heals(self):
        r = self.run_script(title='Counter disappeared')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] Counter disappeared\n$')

    def test_legacy_game_prefix_is_removed(self):
        r = self.run_script(title='[Robots] day177 play status')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] play status\n$')
        self.assertNotIn('[Robots]', r.stdout)

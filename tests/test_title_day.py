import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class TitleDayTest(unittest.TestCase):
    def run_script(self, owner='123', memo=None, title='Keep Title DAY 177 intact',
                   channel_failure=False):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            self.last_skip_log = ""
            (p / "lib").mkdir()
            (p / "lib/stream_title_sync.py").write_text(
                "\n".join([
                    "import os, sys",
                    'with open(os.environ["SYNC_SKIP_LOG"], "a", encoding="utf-8") as f:',
                    '    f.write(" ".join(sys.argv[1:]) + "\\n")',
                    "",
                ]),
                encoding="utf-8",
            )
            skip_log = p / "skip.log"
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
*)
 if [ "$TEST_CHANNEL_FAILURE" = "1" ]; then
  printf '{"data":[]}'
 else
  printf '{"data":[{"title":"%s"}]}' "$TEST_TITLE"
 fi ;;
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
                'TEST_CHANNEL_FAILURE': '1' if channel_failure else '0',
                'SYNC_SKIP_LOG': str(skip_log),
            }
            result = subprocess.run(
                ['bash', str(p / 'update_stream_title_day.sh'), '--dry-run'],
                env=env,
                text=True,
                capture_output=True,
            )
            if skip_log.exists():
                self.last_skip_log = skip_log.read_text(encoding="utf-8")
            return result

    def test_scope_fallback_normalizes_day_prefix(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('TWITCH_BOT_TOKEN', r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] Keep Title intact\n$')
        self.assertNotIn('good', r.stderr)


    def test_twitch_read_failure_records_skip_before_returning(self):
        r = self.run_script(channel_failure=True)
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertNotIn("command not found", r.stderr)
        self.assertEqual(self.last_skip_log, "--record-skip twitch_read_failed\n")

    def test_wrong_broadcaster_is_rejected(self):
        self.assertEqual(self.run_script('999').returncode, 3)

    def test_daily_tick_cannot_replace_game_body_from_ops_brief(self):
        r = self.run_script(memo='# memo\n- 新しい配信内容\n- 過去の内容\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r'^\[day\d+\] Keep Title intact\n$')

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

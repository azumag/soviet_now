import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

class TitleDayTest(unittest.TestCase):
    def run_script(self, owner='123'):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d); shutil.copy(Path(__file__).resolve().parents[1]/'update_stream_title_day.sh',p)
            (p/'curl').write_text(r'''#!/bin/bash
case "$*" in
*oauth2/validate*)
 case "$*" in
 *OAuth\ bad*) echo '{"client_id":"app","user_id":"123","scopes":[]}' ;;
 *) printf '{"client_id":"app","user_id":"%s","scopes":["channel:manage:broadcast"]}' "$TEST_OWNER" ;;
 esac ;;
*) echo '{"data":[{"title":"Keep Title DAY 177 intact"}]}' ;;
esac
'''); (p/'curl').chmod(0o755)
            env={**os.environ,'PATH':d+':'+os.environ['PATH'],'TWITCH_BROADCASTER_ID':'123','TWITCH_TITLE_TOKEN':'','TWITCH_PREDICTIONS_TOKEN':'bad','TWITCH_BOT_TOKEN':'good','TEST_OWNER':owner}
            return subprocess.run(['bash',str(p/'update_stream_title_day.sh'),'--dry-run'],env=env,text=True,capture_output=True)
    def test_scope_fallback_preserves_other_title_text(self):
        r=self.run_script();self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('TWITCH_BOT_TOKEN',r.stderr)
        self.assertRegex(r.stdout,r'^Keep Title DAY \d+ intact\n$')
        self.assertNotIn('good',r.stderr)
    def test_wrong_broadcaster_is_rejected(self):
        self.assertEqual(self.run_script('999').returncode,3)

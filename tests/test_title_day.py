import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TitleDayTest(unittest.TestCase):
    def run_script(
        self,
        owner="123",
        viewer=None,
        title="Keep Title DAY 177 intact",
        ops_brief=None,
    ):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            shutil.copy(ROOT / "update_stream_title_day.sh", p)
            (p / "lib").mkdir()
            shutil.copy(ROOT / "lib/stream_title_public.py", p / "lib/stream_title_public.py")

            if viewer is not None or ops_brief is not None:
                (p / "prompts").mkdir()
            if viewer is not None:
                (p / "prompts/viewer_title.md").write_text(viewer, encoding="utf-8")
            if ops_brief is not None:
                (p / "prompts/ops_brief.md").write_text(ops_brief, encoding="utf-8")

            (p / "curl").write_text(
                r'''#!/bin/bash
case "$*" in
*oauth2/validate*)
 case "$*" in
 *OAuth\ bad*) echo '{"client_id":"app","user_id":"123","scopes":[]}' ;;
 *) printf '{"client_id":"app","user_id":"%s","scopes":["channel:manage:broadcast"]}' "$TEST_OWNER" ;;
 esac ;;
*) printf '{"data":[{"title":"%s"}]}' "$TEST_TITLE" ;;
esac
'''
            )
            (p / "curl").chmod(0o755)
            env = {
                **os.environ,
                "PATH": d + ":" + os.environ["PATH"],
                "TWITCH_BROADCASTER_ID": "123",
                "TWITCH_TITLE_TOKEN": "",
                "TWITCH_PREDICTIONS_TOKEN": "bad",
                "TWITCH_BOT_TOKEN": "good",
                "TEST_OWNER": owner,
                "TEST_TITLE": title,
            }
            return subprocess.run(
                ["bash", str(p / "update_stream_title_day.sh"), "--dry-run"],
                env=env,
                text=True,
                capture_output=True,
            )

    def test_scope_fallback_normalizes_day_prefix(self):
        r = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("TWITCH_BOT_TOKEN", r.stderr)
        self.assertRegex(r.stdout, r"^\[day\d+\] Keep Title intact\n$")
        self.assertNotIn("good", r.stderr)

    def test_wrong_broadcaster_is_rejected(self):
        self.assertEqual(self.run_script("999").returncode, 3)

    def test_body_updates_from_viewer_title(self):
        r = self.run_script(
            viewer="# generated\n- AIの取引コーナーからゲームへ、画面切替を改善\n"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(
            r.stdout,
            r"^\[day\d+\] AIの取引コーナーからゲームへ、画面切替を改善\n$",
        )

    def test_ops_brief_is_not_a_title_source(self):
        r = self.run_script(
            ops_brief="# memo\n- PR770をmainへマージ、VM反映と実機状態を確認\n"
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\[day\d+\] Keep Title intact\n$")
        self.assertNotIn("PR770", r.stdout)

    def test_empty_viewer_title_preserves_safe_body(self):
        r = self.run_script(viewer="# empty\n")
        self.assertRegex(r.stdout, r"^\[day\d+\] Keep Title intact\n$")

    def test_internal_current_title_uses_public_fallback(self):
        r = self.run_script(
            viewer="# empty\n",
            title="[day190] PR770をmainへマージ、VM反映と実機状態を確認",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(
            r.stdout,
            r"^\[day\d+\] AIたちがゲーム・ニュース・会話に挑戦する実験配信\n$",
        )
        self.assertNotIn("PR770", r.stdout)

    def test_missing_day_marker_self_heals(self):
        r = self.run_script(title="Counter disappeared")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\[day\d+\] Counter disappeared\n$")

    def test_legacy_game_prefix_is_removed(self):
        r = self.run_script(title="[Robots] day177 play status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertRegex(r.stdout, r"^\[day\d+\] play status\n$")
        self.assertNotIn("[Robots]", r.stdout)


if __name__ == "__main__":
    unittest.main()

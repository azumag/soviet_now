"""tools/ab_guard.py: ab_status.json の verdict 判定と PR コメント本文の生成 (soviet_now#865)。"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import ab_guard as guard  # noqa: E402


def _status(**kw):
    base = {
        "schema_version": 1,
        "observed_at": "2026-11-15T01:00:00",
        "observed_epoch": int(time.time()) - 60,
        "active": False,
        "aborted": False,
        "candidate_pending": False,
        "a_hash": None,
        "b_hash": None,
        "pattern": None,
        "primary": "eval",
        "started_at": None,
        "games_recorded": 0,
        "confirmation_phase": None,
        "confirmation_required": False,
        "verified_improvement": False,
        "retroactive": None,
        "root_hash": "aaaabbbb",
        "root_matches_a": None,
    }
    base.update(kw)
    return base


class TestEvaluate(unittest.TestCase):
    def test_empty_is_unknown(self):
        self.assertEqual(guard.evaluate({}, 3600)["verdict"], "unknown")
        self.assertEqual(guard.evaluate(None, 3600)["verdict"], "unknown")

    def test_wrong_schema_is_unknown(self):
        # workflow の代替 JSON (取得失敗時) や未知 schema は「実験なし」ではなく unknown。
        self.assertEqual(guard.evaluate(
            {"schema_version": 0, "note": "取得失敗"}, 3600)["verdict"], "unknown")
        s = _status(active=False)
        s["schema_version"] = 2
        self.assertEqual(guard.evaluate(s, 3600)["verdict"], "unknown")

    def test_missing_or_nonbool_active_is_unknown(self):
        s = _status()
        del s["active"]
        self.assertEqual(guard.evaluate(s, 3600)["verdict"], "unknown")
        s = _status(active="yes")
        self.assertEqual(guard.evaluate(s, 3600)["verdict"], "unknown")

    def test_inactive(self):
        ev = guard.evaluate(_status(active=False), 3600)
        self.assertEqual(ev["verdict"], "inactive")

    def test_active(self):
        ev = guard.evaluate(_status(
            active=True, a_hash="aaaa", b_hash="bbbb",
            confirmation_phase="confirmation", games_recorded=108,
            root_matches_a=True), 3600)
        self.assertEqual(ev["verdict"], "active")
        self.assertEqual(ev["phase"], "confirmation")
        self.assertEqual(ev["games_recorded"], 108)

    def test_drifted(self):
        ev = guard.evaluate(_status(active=True, a_hash="aaaa", b_hash="bbbb",
                                    root_matches_a=False), 3600)
        self.assertEqual(ev["verdict"], "drifted")

    def test_candidate_pending(self):
        ev = guard.evaluate(_status(active=False, candidate_pending=True), 3600)
        self.assertEqual(ev["verdict"], "candidate_pending")

    def test_stale(self):
        ev = guard.evaluate(_status(active=False,
                                    observed_epoch=int(time.time()) - 7200), 3600)
        self.assertEqual(ev["verdict"], "stale")
        # max_age を無効化 (0) すると時刻だけでは stale にしない
        ev2 = guard.evaluate(_status(active=False,
                                     observed_epoch=int(time.time()) - 7200), 0)
        self.assertEqual(ev2["verdict"], "inactive")

    def test_active_selection_phase_label(self):
        ev = guard.evaluate(_status(active=True, a_hash="a", b_hash="b"), 3600)
        self.assertEqual(ev["phase"], "selection")


class TestRender(unittest.TestCase):
    def test_active_comment_has_key_fields_and_marker(self):
        ev = guard.evaluate(_status(active=True, a_hash="aaaa", b_hash="bbbb",
                                    confirmation_phase="confirmation",
                                    games_recorded=108, root_matches_a=True), 3600)
        md = guard.render_markdown(ev, "https://github.com/azumag/soviet_now")
        self.assertIn("<!-- soren-ab-guard -->", md)
        self.assertIn("A/B", md)
        self.assertIn("confirmation", md)
        self.assertIn("108", md)
        self.assertIn("ab_retroactive.sh", md)

    def test_inactive_comment_clear(self):
        md = guard.render_markdown(
            guard.evaluate(_status(active=False), 3600),
            "https://github.com/azumag/soviet_now")
        self.assertIn("実施中の A/B 実験はありません", md)

    def test_unknown_warns(self):
        md = guard.render_markdown(guard.evaluate({}, 3600),
                                   "https://github.com/azumag/soviet_now")
        self.assertIn("確認できません", md)


class TestCli(unittest.TestCase):
    def test_missing_file_is_unknown_but_rc0(self):
        out = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tools", "ab_guard.py"),
             "--file", "/nonexistent/ab_status.json", "--json"],
            capture_output=True, text=True)
        self.assertEqual(out.returncode, 0)
        self.assertEqual(json.loads(out.stdout)["verdict"], "unknown")

    def test_reads_file(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(_status(active=True, a_hash="a", b_hash="b",
                              root_matches_a=True), fh)
            path = fh.name
        try:
            out = subprocess.run(
                [sys.executable, os.path.join(ROOT, "tools", "ab_guard.py"),
                 "--file", path, "--json"],
                capture_output=True, text=True)
            self.assertEqual(out.returncode, 0)
            self.assertEqual(json.loads(out.stdout)["verdict"], "active")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()

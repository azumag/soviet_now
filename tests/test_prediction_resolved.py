"""Offline contracts for the settled-prediction handoff to docich (#933).

No Twitch call, token, OAuth file or production write: the Helix response body is
synthetic and the shell integration runs the extracted function against a
temporary state directory.
"""
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "prediction_resolved.py"
SCRIPT = ROOT / "twitch_predictions.sh"


def _load_tool():
    spec = importlib.util.spec_from_file_location("prediction_resolved_test", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _extract_function(source, name):
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


def _body(*, status="RESOLVED", winning="outcome-1", outcomes=None, rows=None):
    if rows is None:
        rows = [{
            "id": "prediction-1", "title": "12ゲーム中に建国できる？", "status": status,
            "winning_outcome_id": winning,
            "outcomes": outcomes if outcomes is not None else [
                {"id": "outcome-0", "title": "建国なし", "top_predictors": [
                    {"user_id": "u1", "user_login": "alice", "user_name": "Alice",
                     "channel_points_used": 100, "channel_points_won": 0}]},
                {"id": "outcome-1", "title": "ソ連建国", "top_predictors": [
                    {"user_id": "u2", "user_login": "bob", "user_name": "Bob",
                     "channel_points_used": 50, "channel_points_won": 200}]},
            ],
        }]
    return json.dumps({"data": rows}).encode("utf-8")


class ResolvedRowTests(unittest.TestCase):
    def test_only_a_well_formed_resolved_row_is_accepted(self):
        tool = _load_tool()

        row = tool.resolved_row(_body())

        self.assertEqual(row["id"], "prediction-1")
        self.assertEqual(row["winning_outcome_id"], "outcome-1")

    def test_malformed_or_unsettled_bodies_are_refused(self):
        tool = _load_tool()
        for body in (
            b"",
            b"{not json",
            b"<html>error</html>",
            json.dumps({"data": []}).encode("utf-8"),
            json.dumps({"data": [{"id": "x"}, {"id": "y"}]}).encode("utf-8"),
            _body(status="ACTIVE"),
            _body(winning="missing-outcome"),
            _body(outcomes=[{"id": "outcome-0", "title": "a"}]),
            _body(outcomes=[{"id": "", "title": "a"}, {"id": "outcome-1", "title": "b"}]),
        ):
            self.assertIsNone(tool.resolved_row(body), body[:40])

    def test_append_keeps_the_newest_rows_under_a_bounded_limit(self):
        tool = _load_tool()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / tool.FILE
            for index in range(7):
                self.assertTrue(tool.append({"id": f"p{index}"}, target, limit=3))

            lines = target.read_text(encoding="utf-8").splitlines()

            self.assertEqual([json.loads(line)["id"] for line in lines], ["p4", "p5", "p6"])

    def test_append_never_follows_or_overwrites_a_symlink(self):
        tool = _load_tool()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            victim = directory / "victim.json"
            victim.write_text("keep\n", encoding="utf-8")
            target = directory / tool.FILE
            target.symlink_to(victim)

            self.assertFalse(tool.append({"id": "p1"}, target))
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep\n")

    def test_main_writes_the_row_and_keeps_a_malformed_body_out(self):
        tool = _load_tool()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            response = directory / "resolve.body"
            response.write_bytes(_body())

            self.assertEqual(
                tool.main([str(response), "--target", str(directory / tool.FILE)]), 0)

            lines = (directory / tool.FILE).read_text(encoding="utf-8").splitlines()
            self.assertEqual([json.loads(line)["id"] for line in lines], ["prediction-1"])

            bad = directory / "bad.body"
            bad.write_bytes(b"{not json")
            self.assertEqual(tool.main([str(bad), "--target", str(directory / tool.FILE)]), 0)
            self.assertEqual(len((directory / tool.FILE).read_text(encoding="utf-8").splitlines()), 1)

    def test_main_is_silent_about_an_unreadable_response(self):
        tool = _load_tool()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / tool.FILE
            with mock.patch.object(Path, "read_bytes", side_effect=OSError("gone")):
                self.assertEqual(
                    tool.main([str(Path(directory) / "missing"), "--target", str(target)]), 0)
            self.assertFalse(target.exists())


class ShellHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.function = _extract_function(SCRIPT.read_text(encoding="utf-8"),
                                         "_record_resolved_prediction_handoff")

    def _run(self, response: Path, state_dir: Path):
        script = (
            f"{self.function}\n"
            "_log() { :; }\n"
            f"TMP_STATE_DIR={shlex.quote(str(state_dir))}\n"
            'PREDICTION_RESOLVED_HANDOFF_FILE="${TMP_STATE_DIR}/prediction_resolved.jsonl"\n'
            f"_record_resolved_prediction_handoff {shlex.quote(str(response))}\n"
            "printf 'done\\n'\n"
        )
        return subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                              capture_output=True, text=True)

    def test_resolve_success_hands_the_row_over_and_never_fails_the_caller(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            response = directory / "resolve.body"
            response.write_bytes(_body())

            result = self._run(response, directory)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "done")
            handoff = directory / "prediction_resolved.jsonl"
            self.assertEqual([json.loads(line)["id"]
                              for line in handoff.read_text(encoding="utf-8").splitlines()],
                             ["prediction-1"])

    def test_malformed_or_missing_bodies_leave_the_handoff_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            malformed = directory / "malformed.body"
            malformed.write_bytes(b"<html>nope</html>")

            result = self._run(malformed, directory)
            empty = self._run(directory / "absent.body", directory)

            self.assertEqual((result.returncode, empty.returncode), (0, 0))
            self.assertFalse((directory / "prediction_resolved.jsonl").exists())

    def test_bounded_handoff_keeps_the_newest_rows(self):
        tool = _load_tool()
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            response = directory / "resolve.body"
            for index in range(tool.MAX_LINES + 5):
                response.write_bytes(_body(rows=[{
                    "id": f"prediction-{index}", "title": "t", "status": "RESOLVED",
                    "winning_outcome_id": "outcome-0",
                    "outcomes": [{"id": "outcome-0", "title": "a"},
                                 {"id": "outcome-1", "title": "b"}],
                }]))
                self.assertEqual(self._run(response, directory).returncode, 0)

            lines = (directory / tool.FILE).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), tool.MAX_LINES)
            self.assertEqual(json.loads(lines[-1])["id"], f"prediction-{tool.MAX_LINES + 4}")
            self.assertEqual(json.loads(lines[0])["id"], "prediction-5")


if __name__ == "__main__":
    unittest.main()

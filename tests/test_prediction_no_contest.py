"""Offline contract for the no-contest prediction settle (#2236 follow-up).

A prediction where only one outcome (or none) ever received a bet never formed a
contest: the resolve path must cancel it instead so every staked channel point is
returned. No Twitch call, token or production write happens here: the Helix row is
synthetic and the shell functions run against a temporary directory.
"""
import json
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "twitch_predictions.sh"


def _extract_function(source, name):
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}", source)
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


def _extract_case(source, label):
    match = re.search(rf"(?ms)^{re.escape(label)}\)\n.*?^\t;;", source)
    if not match:
        raise AssertionError(f"case not found: {label}")
    return match.group(0)


def _row(outcomes):
    # _fetch_remote_prediction_json prints the single Helix prediction item, not
    # the {"data": [...]} envelope.
    return json.dumps({"id": "prediction-1", "status": "ACTIVE", "outcomes": outcomes})


def _outcome(identifier, **counts):
    return {"id": identifier, "title": identifier, **counts}


class ContestedOutcomeCountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.function = _extract_function(cls.source, "_contested_outcome_count")

    def _run(self, row, outcomes=None):
        with tempfile.TemporaryDirectory() as directory:
            remote = Path(directory) / "remote.json"
            remote.write_text(row if isinstance(row, str) else "{}", encoding="utf-8")
            script = (
                f"{self.function}\n"
                '_fetch_remote_prediction_json() { cat "$REMOTE_FILE"; }\n'
                f"REMOTE_FILE={shlex.quote(str(remote))}\n"
                '_contested_outcome_count prediction-1\n'
                "printf 'rc=%s\\n' \"$?\"\n"
            )
            return subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                                  capture_output=True, text=True)

    def _count(self, outcomes):
        result = self._run(_row(outcomes))
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.strip().splitlines()
        self.assertEqual(lines[-1], "rc=0", result.stdout)
        return int(lines[0])

    def test_no_bets_at_all_is_zero(self):
        self.assertEqual(self._count([
            _outcome("a", users=0, channel_points=0),
            _outcome("b", users=0, channel_points=0),
        ]), 0)

    def test_a_single_outcome_with_bets_is_one(self):
        self.assertEqual(self._count([
            _outcome("a", users=3, channel_points=250),
            _outcome("b", users=0, channel_points=0),
        ]), 1)

    def test_two_outcomes_with_bets_are_a_real_contest(self):
        self.assertEqual(self._count([
            _outcome("a", users=1, channel_points=10),
            _outcome("b", users=2, channel_points=30),
        ]), 2)

    def test_channel_points_alone_counts_as_a_bet(self):
        self.assertEqual(self._count([
            _outcome("a", users=None, channel_points=10),
            _outcome("b", users=None, channel_points=0),
        ]), 1)

    def test_unreadable_counts_are_refused_so_the_caller_resolves_as_before(self):
        for row in (
            _row([{"id": "a", "title": "a"}]),
            _row([_outcome("a", users=True, channel_points=None)]),
            _row([_outcome("a", users="3", channel_points="10")]),
            _row([]),
            "{not json",
            _row([_outcome("a", users=1), "not-an-object"]),
        ):
            result = self._run(row)
            self.assertEqual(result.stdout.strip(), "rc=1", row)

    def test_a_failed_remote_fetch_is_refused(self):
        script = (
            f"{self.function}\n"
            "_fetch_remote_prediction_json() { return 1; }\n"
            '_contested_outcome_count prediction-1\n'
            "printf 'rc=%s\\n' \"$?\"\n"
        )
        result = subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                                capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "rc=1")


class PatchPredictionCancelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.function = _extract_function(
            SCRIPT.read_text(encoding="utf-8"), "_patch_prediction_cancel")

    def _run(self, *, curl_rc=0, prediction_id="prediction-1"):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "curl.args"
            script = (
                f"{self.function}\n"
                "TOKEN=token\nCLIENT_ID=client\nBROADCASTER_ID=broadcaster\n"
                f'curl() {{ printf "%s\\n" "$@" > {shlex.quote(str(capture))}; '
                f"return {curl_rc}; }}\n"
                f"_patch_prediction_cancel {shlex.quote(prediction_id)}\n"
                "printf 'rc=%s\\n' \"$?\"\n"
            )
            result = subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                                    capture_output=True, text=True)
            args = capture.read_text(encoding="utf-8").splitlines() if capture.exists() else []
            return result, args

    def test_cancel_patches_the_prediction_with_canceled_status(self):
        result, args = self._run()

        self.assertEqual(result.stdout.strip(), "rc=0", result.stderr)
        self.assertIn("https://api.twitch.tv/helix/predictions", args)
        self.assertIn("PATCH", args)
        payload = json.loads(args[args.index("-d") + 1])
        self.assertEqual(payload, {"broadcaster_id": "broadcaster",
                                   "id": "prediction-1", "status": "CANCELED"})

    def test_a_failed_cancel_reports_failure_to_the_caller(self):
        result, _args = self._run(curl_rc=22)

        self.assertEqual(result.stdout.strip(), "rc=1")

    def test_an_empty_prediction_id_is_refused_without_calling_out(self):
        result, args = self._run(prediction_id="")

        self.assertEqual(result.stdout.strip(), "rc=1")
        self.assertEqual(args, [])


class ResolveBranchWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.resolve = _extract_case(cls.source, "resolve")
        cls.cancel = _extract_case(cls.source, "cancel")

    def test_resolve_checks_the_contested_count_before_publishing_a_winner(self):
        self.assertIn("_contested_outcome_count \"$PREDICTION_ID\"", self.resolve)
        self.assertIn("_patch_prediction_cancel \"$PREDICTION_ID\"", self.resolve)
        # The no-contest cancel must come before the RESOLVED payload is built,
        # or the winner would already be published.
        self.assertLess(self.resolve.index("_patch_prediction_cancel"),
                        self.resolve.index('"status": "RESOLVED"'))

    def test_resolve_keeps_settling_when_the_counts_are_unavailable(self):
        self.assertIn("outcome counts unavailable; resolving as before", self.resolve)
        self.assertIn('"status": "RESOLVED"', self.resolve)

    def test_cancel_reuses_the_shared_patch_helper(self):
        self.assertIn('_patch_prediction_cancel "$PREDICTION_ID"', self.cancel)
        self.assertNotIn('"status": "CANCELED"', self.cancel)


if __name__ == "__main__":
    unittest.main()

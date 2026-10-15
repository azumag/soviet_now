"""Offline-only contract tests for azumagdev prediction vote receipt handling.

The test never calls Twitch, uses no credentials, and never submits a wager.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "twitch_predictions.sh").read_text(encoding="utf-8")


def _extract_function(name):
    # The auto-vote body embeds multiline Python with a top-level closing brace,
    # so a naive Bash function regex would stop before the function actually ends.
    if name == "_auto_vote_prediction":
        start = SOURCE.index("_auto_vote_prediction() {")
        end = SOURCE.index("\n# --- サブコマンド ---", start)
        return SOURCE[start:end].rstrip()
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{.*?^\}}", SOURCE)
    if not match:
        raise AssertionError(f"function not found: {name}")
    return match.group(0)


def _accepted(response):
    script = _extract_function("_auto_vote_response_accepted") + "\n_auto_vote_response_accepted\n"
    body = response if isinstance(response, str) else json.dumps(response)
    return subprocess.run(
        ["bash", "-c", script], input=body, text=True,
        capture_output=True, cwd=ROOT, timeout=5,
    )


class AutoVoteReceiptTests(unittest.TestCase):
    def test_valid_vote_receipt(self):
        payload = {"data": {"makePrediction": {
            "prediction": {"id": "voted-1"}, "error": None
        }}}
        result = _accepted(payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_error_from_graphql_must_not_be_logged_as_success(self):
        good = {"prediction": {"id": "voted-1"}, "error": None}
        for payload in (
            {"errors": [{"message": "Unauthorized"}], "data": {"makePrediction": good}},
            {"data": {"makePrediction": {"prediction": None, "error": {"code": "UNAUTHORIZED"}}}},
            {"data": {"makePrediction": {"prediction": {"id": "voted-1"},
                                          "error": {"code": "ALREADY_VOTED"}}}},
            {"data": {"makePrediction": {"prediction": None, "error": None}}},
            {"data": {"makePrediction": {"prediction": {}, "error": None}}},
            {"data": {"makePrediction": {"prediction": {"id": ""}, "error": None}}},
            {"data": {"makePrediction": {"prediction": {"id": 42}, "error": None}}},
            {"data": None},
            {"data": {}},
            [],
            None,
            "{invalid json",
            "",
        ):
            with self.subTest(payload=payload):
                result = _accepted(payload)
                self.assertNotEqual(result.returncode, 0, repr(payload))
                self.assertEqual(result.stdout, "")

    def test_receipt_gate_is_wired_before_success_log(self):
        function = _extract_function("_auto_vote_prediction")
        self.assertIn("vote_rc=$?", function)
        self.assertIn("_auto_vote_response_accepted", function)
        self.assertIn('"${#vote_resp}" -le 65536', function)
        self.assertLess(function.index("_auto_vote_response_accepted"),
                        function.index("azumagdev voted"))
        self.assertIn("HTTP or GQL result not confirmed", function)

    def test_missing_bot_token_is_an_explicit_skip(self):
        function = _extract_function("_auto_vote_prediction")
        script = (function + """
_log() { printf '%s\\n' "$*" >&2; }
sleep() { echo 'unexpected sleep' >&2; exit 30; }
curl() { echo 'unexpected curl' >&2; exit 31; }
TWITCH_BOT_TOKEN=''
TWITCH_BOT_GQL_TOKEN='not-used'
AUTO_VOTE_POINTS=10
_auto_vote_prediction '{}'
""")
        result = subprocess.run(
            ["bash", "-c", script], text=True, capture_output=True,
            cwd=ROOT, timeout=5,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("missing bot token", result.stderr)
        self.assertNotIn("unexpected", result.stderr)


if __name__ == "__main__":
    unittest.main()

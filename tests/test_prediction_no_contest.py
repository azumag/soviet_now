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

    def test_the_response_body_is_saved_to_the_requested_file(self):
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "cancel.body"
            script = (
                f"{self.function}\n"
                "TOKEN=token\nCLIENT_ID=client\nBROADCASTER_ID=broadcaster\n"
                "curl() {\n"
                "  local out=/dev/null\n"
                "  while [ \"$#\" -gt 0 ]; do\n"
                '    if [ "$1" = "-o" ]; then out="$2"; shift 2; continue; fi\n'
                "    shift\n"
                "  done\n"
                "  printf '%s' '{\"data\":[{\"id\":\"prediction-1\",\"status\":\"CANCELED\"}]}' > \"$out\"\n"
                "  return 0\n"
                "}\n"
                f"_patch_prediction_cancel prediction-1 {shlex.quote(str(body))}\n"
                "printf 'rc=%s\\n' \"$?\"\n"
            )
            result = subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                                    capture_output=True, text=True)

            self.assertEqual(result.stdout.strip(), "rc=0", result.stderr)
            saved = json.loads(body.read_text(encoding="utf-8"))["data"][0]
            self.assertEqual((saved["id"], saved["status"]),
                             ("prediction-1", "CANCELED"))


class ResolveBranchWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.resolve = _extract_case(cls.source, "resolve")
        cls.cancel = _extract_case(cls.source, "cancel")
        cls.settle = _extract_case(cls.source, "settle-window")
        cls.no_contest = _extract_function(cls.source, "_cancel_prediction_no_contest")

    def test_resolve_checks_the_contested_count_before_publishing_a_winner(self):
        self.assertIn("_contested_outcome_count \"$PREDICTION_ID\"", self.resolve)
        self.assertIn('_cancel_prediction_no_contest "$PREDICTION_ID"', self.resolve)
        # The no-contest cancel must come before the RESOLVED payload is built,
        # or the winner would already be published.
        self.assertLess(self.resolve.index("_cancel_prediction_no_contest"),
                        self.resolve.index('"status": "RESOLVED"'))

    def test_resolve_keeps_settling_when_the_counts_are_unavailable(self):
        self.assertIn("outcome counts unavailable; resolving as before", self.resolve)
        self.assertIn('"status": "RESOLVED"', self.resolve)

    def test_cancel_reuses_the_shared_patch_helper(self):
        self.assertIn('_patch_prediction_cancel "$PREDICTION_ID"', self.cancel)
        self.assertNotIn('"status": "CANCELED"', self.cancel)

    def test_no_contest_helper_patches_hands_over_and_clears_state(self):
        self.assertIn('_patch_prediction_cancel "$prediction_id" "$cancel_response_file"',
                      self.no_contest)
        self.assertIn('_record_canceled_prediction_handoff "$cancel_response_file"',
                      self.no_contest)
        # The participation evidence is handed over before the state is removed,
        # and the response file is always cleaned up on both outcomes.
        self.assertLess(self.no_contest.index("_record_canceled_prediction_handoff"),
                        self.no_contest.index('rm -f "$PREDICTION_STATE_FILE"'))
        self.assertEqual(self.no_contest.count('rm -f "$cancel_response_file"'), 2)
        self.assertIn('python3 lib/corner_boundary.py "$TMP_STATE_DIR" prediction',
                      self.no_contest)
        self.assertIn('rm -f "$PREDICTION_STATE_FILE" "$PREDICTION_WINDOW_CHECK_FILE"',
                      self.no_contest)

    def test_manual_cancel_never_hands_a_refund_over(self):
        # 手動 cancel は不成立返金ではないため、参加実績としては引き渡さない。
        self.assertNotIn("_record_canceled_prediction_handoff", self.cancel)
        self.assertNotIn("_cancel_prediction_no_contest", self.cancel)

    def test_settle_window_case_uses_the_shared_no_contest_helper(self):
        self.assertIn('_cancel_prediction_no_contest "$PREDICTION_ID"', self.settle)
        self.assertIn("window_ends_at", self.settle)
        self.assertIn('"status":"canceled"', self.settle)
        self.assertIn('"status":"contested"', self.settle)
        self.assertIn("PREDICTION_WINDOW_CHECK_FILE", self.settle)


class WindowEndSettlementTests(unittest.TestCase):
    """The window-end path cancels only a confirmed no-contest prediction."""

    @classmethod
    def setUpClass(cls):
        cls.source = SCRIPT.read_text(encoding="utf-8")
        cls.settle = _extract_case(cls.source, "settle-window")

    def _run(self, *, state=None, remote_status="LOCKED", contested="1",
             cancel_rc=0, checked=None, window_offset=-10):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state_file = root / "current_prediction.json"
            marker = root / "prediction_window_checked.json"
            cancel_marker = root / "cancel.called"
            remote_called = root / "remote.called"
            remote_file = root / "remote.json"
            now = int(__import__("time").time())
            if state is None:
                state = {
                    "prediction_id": "prediction-1",
                    "created_at": now - 100,
                    "prediction_window": 10,
                    "window_ends_at": now + window_offset,
                    "round_version": 2,
                    "max_games": 48,
                    "games_completed": 0,
                    "best_outcome": 0,
                }
            state_file.write_text(json.dumps(state), encoding="utf-8")
            if checked:
                marker.write_text(json.dumps(checked), encoding="utf-8")
            remote_file.write_text(json.dumps({
                "id": "prediction-1", "status": remote_status,
                "outcomes": [],
            }), encoding="utf-8")
            script = f"""
set -u
PREDICTION_STATE_FILE={shlex.quote(str(state_file))}
PREDICTION_RETRY_DIR={shlex.quote(str(root / 'prediction_retry'))}
PREDICTION_WINDOW_CHECK_FILE={shlex.quote(str(marker))}
PREDICTION_WINDOW_SEC=1800
TMP_STATE_DIR={shlex.quote(str(root))}
_log() {{ :; }}
_load_prediction_state_json() {{ cat "$PREDICTION_STATE_FILE"; }}
_prediction_id_from_state_json() {{ python3 -c 'import json,sys; print(json.load(sys.stdin).get("prediction_id",""))'; }}
_fetch_remote_prediction_json() {{ printf 'called' > {shlex.quote(str(remote_called))}; cat {shlex.quote(str(remote_file))}; }}
_contested_outcome_count() {{ printf '%s' {shlex.quote(str(contested))}; }}
_cancel_prediction_no_contest() {{ printf 'cancel:%s' "$1" > {shlex.quote(str(cancel_marker))}; return {cancel_rc}; }}
case "settle-window" in
{self.settle}
esac
"""
            result = subprocess.run(["bash", "-c", script], cwd=str(ROOT),
                                    capture_output=True, text=True)
            return {
                "result": result,
                "output": json.loads(result.stdout.strip() or "{}"),
                "marker": json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else None,
                "cancel_called": cancel_marker.read_text(encoding="utf-8") if cancel_marker.exists() else "",
                "remote_called": remote_called.exists(),
            }

    def test_one_outcome_with_bets_is_canceled(self):
        run = self._run(contested="1")
        self.assertEqual(run["result"].returncode, 0, run["result"].stderr)
        self.assertEqual(run["output"], {
            "ok": True, "status": "canceled", "reason": "no-contest",
            "outcomes_with_bets": 1,
        })
        self.assertEqual(run["cancel_called"], "cancel:prediction-1")

    def test_zero_outcomes_with_bets_is_canceled(self):
        run = self._run(contested="0")
        self.assertEqual(run["output"]["status"], "canceled")

    def test_two_outcomes_with_bets_stays_for_cycle_resolve(self):
        run = self._run(contested="2")
        self.assertEqual(run["output"], {
            "ok": True, "status": "contested", "outcomes_with_bets": 2,
        })
        self.assertEqual(run["marker"]["prediction_id"], "prediction-1")
        self.assertFalse(run["cancel_called"])

    def test_the_checked_marker_skips_the_remote_api(self):
        run = self._run(checked={"prediction_id": "prediction-1"},
                        contested="2")
        self.assertEqual(run["output"]["reason"], "already-checked")
        self.assertFalse(run["remote_called"])

    def test_a_still_active_window_is_not_canceled(self):
        run = self._run(window_offset=600)
        self.assertEqual(run["output"]["reason"], "window-not-ended")
        self.assertFalse(run["remote_called"])
        self.assertFalse(run["cancel_called"])

    def test_a_remote_active_prediction_is_not_canceled(self):
        run = self._run(remote_status="ACTIVE")
        self.assertEqual(run["output"]["status"], "active")
        self.assertFalse(run["cancel_called"])

    def test_unavailable_counts_do_not_cancel(self):
        run = self._run(contested="")
        self.assertEqual(run["output"], {
            "ok": True, "status": "unavailable", "reason": "outcome-counts",
        })
        self.assertFalse(run["cancel_called"])

    def test_invalid_state_is_cleared(self):
        run = self._run(state={})
        self.assertEqual(run["output"]["status"], "cleared")


class WorkerWindowWiringTests(unittest.TestCase):
    def test_worker_checks_the_window_and_calls_settle_window(self):
        worker = (ROOT / "workers" / "prediction_worker.sh").read_text(encoding="utf-8")
        self.assertIn("_prediction_window_due", worker)
        self.assertIn("./twitch_predictions.sh settle-window", worker)
        self.assertIn("_PREDICTION_WINDOW_CHECK_NEXT", worker)



class CreateStateTests(unittest.TestCase):
    def test_create_records_the_prediction_window_end(self):
        source = SCRIPT.read_text(encoding="utf-8")
        create = _extract_case(source, "create")
        self.assertIn('"prediction_window": window', create)
        self.assertIn('"window_ends_at": now + window', create)


if __name__ == "__main__":
    unittest.main()

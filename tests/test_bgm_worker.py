import json
import os
from pathlib import Path
import subprocess
import tempfile
import signal
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
REQUEST_ID = "2ea6a169-9b94-4a11-8b1c-76b62ccda0f7"


class BgmPolicyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.canonical = self.root / "canonical.json"
        self.bgm = self.root / "bgm.ogg"
        self.bgm.touch()
        self.state = self.root / "state"
        self.lifecycle = self.state / "game_lifecycle"
        self.lifecycle.mkdir(parents=True)
        self.marker = self.state / "soren_loop.paused"
        self.request = {
            "schema": 1, "request_id": REQUEST_ID, "game": "sorengame",
            "generation": 7, "deadline_epoch": 1,
            "deadline_at": "1970-01-01T00:00:01Z",
        }
        self.env = {
            **os.environ, "DOCICH_CANONICAL": str(self.canonical),
            "SOREN_BGM_FILE": str(self.bgm),
            "SOREN_BGM_STATE_DIR": str(self.state),
        }
        self.canonical_state()
        self.stopped_handover()

    def canonical_state(self, phase="ready", game="nsnake"):
        self.canonical.write_text(json.dumps({"phase": phase, "active": {"game": game}}))

    def stopped_handover(self, status="stopped"):
        (self.lifecycle / "request.json").write_text(json.dumps(self.request))
        (self.lifecycle / "ack.json").write_text(json.dumps({**self.request, "status": status}))
        self.marker.write_text(f"lifecycle:{REQUEST_ID}\n")

    def policy(self):
        return subprocess.run(
            ["bash", "-c", 'source "$1"; cli_game_active', "test", str(ROOT / "bgm_worker.sh")],
            env=self.env, capture_output=True, timeout=5,
        )

    def assert_policy(self, expected):
        result = self.policy()
        self.assertEqual(result.returncode, expected, result.stderr)

    def test_hanjuku_suppresses_fallback_and_next_cli_game_restores_it(self):
        for phase, game, expected in (
            ("ready", "nsnake", 0), ("ready", "hanjuku-hero", 1),
            ("ready", "nsnake", 0), ("ready", "sorengame", 1),
            ("ready", "soren91", 1), ("ready", None, 1),
            ("draining", "nsnake", 1),
        ):
            with self.subTest(phase=phase, game=game):
                self.canonical_state(phase, game)
                self.assert_policy(expected)

    def test_stale_cli_state_cannot_play_after_soren_resumes(self):
        self.stopped_handover("resumed")
        self.marker.unlink()
        self.assert_policy(1)

    def test_cli_name_alone_is_not_stop_proof(self):
        self.marker.unlink()
        self.assert_policy(1)

    def test_only_completed_native_stop_permits_fallback(self):
        for status in ("accepted", "waiting", "boundary", "stop_requested", "stopping",
                       "resume_requested", "resumed", "timeout", "failed", "cancelled"):
            with self.subTest(status=status):
                self.stopped_handover(status)
                self.assert_policy(1)

    def test_stale_or_mismatched_ack_is_rejected(self):
        for field, value in (
            ("request_id", "bb45bbef-a48b-48ae-93d6-dc6fce2b5fa5"),
            ("game", "other"), ("generation", 8),
            ("deadline_epoch", 2), ("deadline_at", "different"),
        ):
            with self.subTest(field=field):
                ack = {**self.request, "status": "stopped", field: value}
                (self.lifecycle / "ack.json").write_text(json.dumps(ack))
                self.assert_policy(1)

    def test_stopped_ack_remains_valid_after_request_deadline(self):
        # A terminal stop stays parked until explicit resume; it has no TTL.
        self.assert_policy(0)

    def test_equivalent_numeric_deadline_serializations_are_accepted(self):
        ack = {**self.request, "status": "stopped", "deadline_epoch": 1.0}
        (self.lifecycle / "ack.json").write_text(json.dumps(ack))
        self.assert_policy(0)

    def test_invalid_or_missing_state_is_fail_closed(self):
        for name in ("request.json", "ack.json"):
            path = self.lifecycle / name
            for value in ("", "not json", "[]", "null", "{}", "x" * 65537):
                with self.subTest(name=name, value=value[:16]):
                    self.stopped_handover()
                    path.write_text(value)
                    self.assert_policy(1)
            self.stopped_handover()
            path.unlink()
            self.assert_policy(1)
        self.stopped_handover()
        self.canonical.write_text("[]")
        self.assert_policy(1)
        self.canonical.unlink()
        self.assert_policy(1)

    def test_missing_audio_does_not_start_player(self):
        self.bgm.unlink()
        self.assert_policy(1)

    def test_canonical_path_is_data_not_python_source(self):
        self.canonical = self.root / "owner's canonical.json"
        self.env["DOCICH_CANONICAL"] = str(self.canonical)
        self.canonical_state()
        self.assert_policy(0)


    def test_malformed_identities_and_nonregular_files_are_rejected(self):
        for field, value in (("schema", True), ("generation", True), ("generation", 0),
                             ("request_id", "not-a-uuid"), ("deadline_epoch", False),
                             ("deadline_epoch", float("nan")), ("deadline_at", "")):
            with self.subTest(field=field, value=value):
                bad = {**self.request, field: value}
                (self.lifecycle / "request.json").write_text(json.dumps(bad))
                (self.lifecycle / "ack.json").write_text(json.dumps({**bad, "status": "stopped"}))
                self.assert_policy(1)
        self.stopped_handover()
        self.marker.unlink()
        self.marker.symlink_to(self.bgm)
        self.assert_policy(1)
        self.marker.unlink()
        os.mkfifo(self.marker)
        self.assert_policy(1)

    def setup_player(self, ignore_term=False):
        directory = self.root / "bin"
        directory.mkdir()
        player = directory / "ffplay"
        player.write_text("#!" + sys.executable + "\n" +
            "import os, signal, sys, time\n"
            "def event(kind):\n"
            "    with open(os.environ['PLAYER_LOG'], 'a') as f:\n"
            "        f.write(f'{kind} {os.getpid()}\\n')\n"
            "def stop(*args):\n"
            "    event('term')\n"
            "    if os.environ.get('IGNORE_TERM') != '1': sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "event('start')\n"
            "while True: time.sleep(0.05)\n")
        player.chmod(0o700)
        self.player = player
        self.player_log = self.root / "player.log"
        self.env.update({"PATH": str(directory) + os.pathsep + os.environ["PATH"],
                         "PLAYER_LOG": str(self.player_log),
                         "IGNORE_TERM": "1" if ignore_term else "0"})

    def player_events(self, kind):
        if not self.player_log.exists():
            return []
        return [int(line.split()[1]) for line in self.player_log.read_text().splitlines()
                if line.startswith(kind + " ")]

    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("fixture did not reach the expected state")

    def process_alive(self, pid):
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    def start_worker(self):
        worker = subprocess.Popen(["bash", str(ROOT / "bgm_worker.sh")], env=self.env,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        self.addCleanup(self.cleanup_process, worker)
        return worker

    @staticmethod
    def cleanup_process(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=3)

    def test_worker_stops_owned_player_when_native_resumes(self):
        self.setup_player()
        self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        pid = self.player_events("start")[0]
        self.stopped_handover("resumed")
        self.marker.unlink()  # canonical deliberately still says ready/nsnake
        self.wait_for(lambda: not self.process_alive(pid))
        self.assertEqual(self.player_events("term"), [pid])

    def test_worker_shutdown_reaps_owned_player(self):
        self.setup_player()
        worker = self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        pid = self.player_events("start")[0]
        self.cleanup_process(worker)
        self.assertFalse(self.process_alive(pid))

    def test_duplicate_worker_cannot_launch_second_player(self):
        self.setup_player()
        worker = self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        duplicate = self.start_worker()
        self.assertNotEqual(duplicate.wait(timeout=5), 0)
        self.assertIsNone(worker.poll())
        self.assertEqual(len(self.player_events("start")), 1)

    def test_unrelated_tagged_player_is_never_terminated(self):
        self.setup_player()
        unrelated_log = self.root / "unrelated.log"
        unrelated = subprocess.Popen(
            [str(self.player), "-window_title", "soren-bgm-loop"],
            env={**self.env, "PLAYER_LOG": str(unrelated_log)}, start_new_session=True,
        )
        self.addCleanup(self.cleanup_process, unrelated)
        self.wait_for(unrelated_log.exists)
        worker = self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        pid = self.player_events("start")[0]
        self.canonical_state(game="sorengame")
        self.wait_for(lambda: not self.process_alive(pid))
        self.cleanup_process(worker)
        self.assertIsNone(unrelated.poll())
        self.assertNotIn("term ", unrelated_log.read_text())

    def test_stubborn_owned_player_is_stopped_before_replacement(self):
        self.setup_player(ignore_term=True)
        self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        pid = self.player_events("start")[0]
        self.canonical_state(game="sorengame")
        self.wait_for(lambda: not self.process_alive(pid))
        self.canonical_state()
        self.wait_for(lambda: len(self.player_events("start")) == 2)
        self.assertFalse(self.process_alive(pid))


if __name__ == "__main__":
    unittest.main()

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
            "import json, os, signal, sys, time\n"
            "if os.environ.get('PLAYER_AUDIO_ENV'):\n"
            "    with open(os.environ['PLAYER_AUDIO_ENV'], 'w') as f:\n"
            "        json.dump({'pulse_prop': os.environ.get('PULSE_PROP'),\n"
            "                   'server': os.environ.get('PULSE_SERVER'),\n"
            "                   'sink': os.environ.get('PULSE_SINK'), 'argv': sys.argv[1:]}, f)\n"
            "def event(kind):\n"
            "    with open(os.environ['PLAYER_LOG'], 'a') as f:\n"
            "        f.write(f'{kind} {os.getpid()}\\n')\n"
            "def stop(*args):\n"
            "    event('term')\n"
            "    if os.environ.get('IGNORE_TERM') != '1': sys.exit(0)\n"
            "signal.signal(signal.SIGTERM, stop)\n"
            "event('start')\n"
            "if os.environ.get('PLAYER_EXIT'):\n"
            "    print('PRIVATE_PLAYER_ERROR', file=sys.stderr)\n"
            "    sys.exit(int(os.environ['PLAYER_EXIT']))\n"
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


    def test_ordinary_corners_get_bgm_except_native_audio_and_exclusions(self):
        for game, expected in (
            ("weather-view", 0), ("paper-view", 0), ("tsuitate-view", 0),
            ("nethack", 0), ("yosou-view", 0), ("nsnake", 0), ("future-ordinary-view", 0),
            ("hanjuku-hero", 1), ("external-video-view", 1),
            ("sorengame", 1), ("soren91", 1),
        ):
            with self.subTest(game=game):
                self.canonical_state(game=game)
                self.assert_policy(expected)

    def test_weather_cannot_bypass_native_stop_or_transition_gates(self):
        self.canonical_state(game="weather-view")
        for status in ("resumed", "stopping", "failed"):
            with self.subTest(status=status):
                self.stopped_handover(status)
                self.assert_policy(1)
        self.stopped_handover()
        for phase in ("draining", "starting", "failed"):
            with self.subTest(phase=phase):
                self.canonical_state(phase=phase, game="weather-view")
                self.assert_policy(1)
        self.canonical_state(game="weather-view")
        self.assert_policy(0)

    def test_worker_stops_for_both_exclusions_and_restarts_for_weather(self):
        self.setup_player()
        self.canonical_state(game="weather-view")
        self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        for number, game in enumerate(("external-video-view", "hanjuku-hero"), 1):
            with self.subTest(game=game):
                pid = self.player_events("start")[-1]
                self.canonical_state(game=game)
                self.wait_for(lambda: not self.process_alive(pid))
                self.assertIn(pid, self.player_events("term"))
                time.sleep(1.2)  # Stay excluded across another worker poll.
                self.assertEqual(len(self.player_events("start")), number)
                self.canonical_state(game="weather-view")
                self.wait_for(lambda: len(self.player_events("start")) == number + 1)
        # Ordinary-to-ordinary ready state does not duplicate/restart the loop.
        pid = self.player_events("start")[-1]
        self.canonical_state(game="paper-view")
        time.sleep(1.2)
        self.assertTrue(self.process_alive(pid))
        self.assertEqual(len(self.player_events("start")), 3)

    def test_player_has_isolated_restore_identity_and_preserves_audio_settings(self):
        self.setup_player()
        audio_env = self.root / "audio-env.json"
        self.env.update({
            "PLAYER_AUDIO_ENV": str(audio_env),
            "PULSE_PROP": "module-stream-restore.id=other-player",
            "SOREN_BGM_SINK": "test_corner_sink",
            "SOREN_BGM_VOLUME": "0",  # An explicit operator silence is retained.
            "PULSE_SERVER": "unix:/operator/audio/native",
        })
        self.canonical_state(game="weather-view")
        self.start_worker()
        self.wait_for(lambda: len(self.player_events("start")) == 1)
        recorded = json.loads(audio_env.read_text())
        self.assertEqual(recorded["pulse_prop"],
                         "application.id=docich.corner-bgm "
                         "module-stream-restore.id=docich-corner-bgm")
        self.assertEqual(recorded["sink"], "test_corner_sink")
        self.assertEqual(recorded["server"], "unix:/operator/audio/native")
        self.assertEqual(recorded["argv"], [
            "-nodisp", "-loop", "0", "-volume", "0", "-window_title",
            "soren-bgm-loop", str(self.bgm),
        ])

    def test_system_service_without_session_env_uses_encoder_runtime_socket(self):
        self.setup_player()
        audio_env = self.root / 'audio-env.json'
        self.env['PLAYER_AUDIO_ENV'] = str(audio_env)
        self.env.pop('XDG_RUNTIME_DIR', None)
        self.env.pop('PULSE_SERVER', None)
        self.start_worker()
        self.wait_for(lambda: len(self.player_events('start')) == 1)
        recorded = json.loads(audio_env.read_text())
        self.assertEqual(recorded['server'], f'unix:/run/user/{os.getuid()}/pulse/native')

    def status(self):
        try:
            return json.loads((self.state / "bgm_worker.json").read_text())
        except (OSError, ValueError):
            return {}

    def test_policy_reasons_are_fixed_and_never_include_private_errors(self):
        self.assertEqual(self.policy().stdout.strip(), b"eligible")
        self.canonical_state(game="external-video-view")
        self.assertEqual(self.policy().stdout.strip(), b"excluded_game")
        self.canonical_state(phase="draining")
        self.assertEqual(self.policy().stdout.strip(), b"switch_not_ready")
        self.canonical_state()
        self.stopped_handover(status="stopping")
        self.assertEqual(self.policy().stdout.strip(), b"native_stop_unconfirmed")
        self.stopped_handover()
        (self.lifecycle / "request.json").write_text("PRIVATE_STATE_VALUE")
        result = self.policy()
        self.assertEqual(result.stdout.strip(), b"invalid_state")
        self.assertNotIn(b"PRIVATE", result.stdout + result.stderr)
        self.marker.unlink()
        self.assertEqual(self.policy().stdout.strip(), b"pause_unreadable")

    def test_snapshot_follows_blocked_running_and_shutdown_without_rewriting_pause(self):
        self.setup_player()
        self.canonical_state(game="sorengame")
        worker = self.start_worker()
        self.wait_for(lambda: self.status().get("state") == "blocked")
        self.assertEqual(self.status()["reason"], "excluded_game")
        before = self.marker.read_bytes()
        self.canonical_state(game="paper-view")
        self.wait_for(lambda: self.status().get("state") == "running")
        status = self.status()
        self.assertEqual(status["player_pid"], self.player_events("start")[0])
        self.assertEqual(status["worker_pid"], worker.pid)
        self.assertEqual((self.state / "bgm_worker.json").stat().st_mode & 0o777, 0o600)
        self.cleanup_process(worker)
        self.assertEqual(self.status()["state"], "stopped")
        self.assertEqual(self.status()["player_pid"], 0)
        self.assertEqual(self.marker.read_bytes(), before)

    def test_player_exit_is_recorded_and_retried_without_releasing_worker_lock(self):
        self.setup_player()
        self.env["PLAYER_EXIT"] = "7"
        worker = self.start_worker()
        self.wait_for(lambda: self.status().get("last_exit") == 7)
        self.assertGreaterEqual(self.status()["player_exits"], 1)
        duplicate = self.start_worker()
        self.assertNotEqual(duplicate.wait(timeout=5), 0)
        self.assertEqual(self.status()["worker_pid"], worker.pid)
        self.assertNotIn("PRIVATE", json.dumps(self.status()))

    def test_repeated_failure_logging_is_bounded_and_drops_raw_player_stderr(self):
        self.setup_player()
        self.env["PLAYER_EXIT"] = "7"
        with (self.root / "journal").open("w+") as journal:
            worker = subprocess.Popen(["bash", str(ROOT / "bgm_worker.sh")], env=self.env,
                                      stdout=journal, stderr=journal, start_new_session=True)
            self.addCleanup(self.cleanup_process, worker)
            self.wait_for(lambda: len(self.player_events("start")) >= 4)
            self.cleanup_process(worker)
            journal.seek(0)
            output = journal.read()
        self.assertEqual(output.count("state=restarting"), 1)
        self.assertNotIn("PRIVATE_PLAYER_ERROR", output)


if __name__ == "__main__":
    unittest.main()

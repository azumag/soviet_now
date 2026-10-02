"""Isolated tests for the weather-corner shared-queue consumer.

The shared queue, GameSwitch and dummy owned player all live in a temporary
directory. These tests never call a TTS provider or play audio.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import fcntl
import json
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "lib" / "weather_audio_consumer.py"
EXECUTION_ID = "12345678-1234-4234-8234-123456789abc"
IDENTITY = {
    "game": "weather-view",
    "runtime_id": "g7-a1b2c3d4",
    "generation": 7,
    "lease_id": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
}
PROCESS_GROUP_PERMISSION_MARKER = "TestOnlyProcessGroupPermissionDenied"


def _request(*, index=1, text="札幌。晴れ。最高気温は18度。", expires_at=None, **changes):
    now = time.time()
    issued = datetime.fromtimestamp(now - 30, ZoneInfo("Asia/Tokyo")).isoformat()
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    value = {
        "schema_version": 1,
        "source": "weather_corner",
        "execution_id": EXECUTION_ID,
        "item_index": index,
        "item_key": f"weather_corner:{EXECUTION_ID}:{index:02d}",
        "text": text,
        "runtime_fence": {
            **IDENTITY,
            "expires_at": expires_at if expires_at is not None else now + 300,
        },
        "forecast": {
            "source_url": "https://www.jma.go.jp/bosai/forecast/",
            "date": today,
            "issued_at": issued,
            "report_digest": "a" * 64,
        },
    }
    value.update(changes)
    return value


class TestWeatherAudioConsumer(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="weather-audio-consumer-")
        self.root = Path(self.temp.name)
        self.queue = self.root / "comment_queue"
        self.context = self.root / "run-soren-live" / "game_switch.json"
        self.context.parent.joinpath("locks").mkdir(parents=True)
        (self.context.parent / "locks" / "game-switch.lock").touch()
        self.set_identity(IDENTITY)
        self.env = {
            **os.environ,
            "COMMENT_QUEUE_DIR": str(self.queue),
            "ELOOP_LIB_DIR": str(ROOT),
            "OUTBOUND_CHAT_QUEUE_DIR": str(self.root / "outbound_chat"),
            "SOREN_ACTIVE_GAME_CONTEXT_FILE": str(self.context),
        }
        self.dummy_player = self.root / "dummy_player.py"
        self.dummy_player.write_text(
            "import os, pathlib, signal, sys, time\n"
            "if os.environ.get('DUMMY_IGNORE_TERM') == '1': signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "pathlib.Path(sys.argv[1]).write_text('started', encoding='utf-8')\n"
            "time.sleep(float(sys.argv[3]) if len(sys.argv) > 3 else 0)\n"
            "code = int(sys.argv[2])\n"
            "marker = 'completed' if code == 0 else 'failed'\n"
            "pathlib.Path(sys.argv[1] + '.' + marker).write_text(marker, encoding='utf-8')\n"
            "raise SystemExit(code)\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def set_identity(self, identity):
        self.context.parent.mkdir(parents=True, exist_ok=True)
        self.context.write_text(json.dumps({
            "schema_version": 1,
            "phase": "ready",
            "active": {**identity, "runtime_note": "ignored non-identity metadata"},
        }), encoding="utf-8")

    def shell(self, source, *args):
        return subprocess.run(
            ["bash", "-c", source, "weather-audio-test", *args],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )

    def enqueue(self, request):
        raw = json.dumps(request, ensure_ascii=False, separators=(",", ":"))
        return self.shell(
            'source "$ELOOP_LIB_DIR/lib/outbound_queue.sh"; enqueue_weather_audio_request "$1"',
            raw,
        )

    def helper(self, *args):
        return subprocess.run(
            [sys.executable, str(HELPER), *map(str, args)],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )

    def _shell_function(self, path, signature):
        source = path.read_text(encoding="utf-8")
        start = source.index(signature)
        end = source.index("\n}\n", start) + 3
        return source[start:end]

    def receipt(self, item_key):
        result = self.shell(
            'source "$ELOOP_LIB_DIR/lib/outbound_queue.sh"; get_weather_audio_receipt "$1"',
            item_key,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def player(self, item_path, *, exit_code=0, duration=0):
        sentinel = self.root / f"player-{time.time_ns()}.sentinel"
        target = item_path
        if item_path.suffix == ".txt":
            target = item_path.with_suffix(".playing")
            item_path.rename(target)
        self.plan_players(target, 1)
        result = self.helper(
            "play", "--queue-dir", self.queue, target, "--",
            sys.executable, self.dummy_player, sentinel, str(exit_code), str(duration),
        )
        return result, sentinel, target

    def plan_players(self, target, count):
        result = self.helper("plan", "--queue-dir", self.queue, target, str(count))
        assert result.returncode == 0, result.stderr

    def ack_player(self, target):
        result = self.helper("ack", "--queue-dir", self.queue, target)
        assert result.returncode == 0, result.stderr

    def start_long_player(self, item_path, *, duration="10", permission_marker=None):
        target = item_path
        if item_path.suffix == ".txt":
            target = item_path.with_suffix(".playing")
            item_path.rename(target)
        self.plan_players(target, 1)
        sentinel = self.root / f"player-{time.time_ns()}.sentinel"
        command = [
            sys.executable, str(HELPER), "play", "--queue-dir", str(self.queue),
            str(target), "--", sys.executable, str(self.dummy_player),
            str(sentinel), "0", duration,
        ]
        env = self.env
        if permission_marker is not None:
            wrapper = self.root / "test_weather_audio_consumer_marker_wrapper.py"
            wrapper.write_text(
                "import importlib.util, os, sys\n"
                "from pathlib import Path\n"
                "spec = importlib.util.spec_from_file_location('weather_audio_consumer', os.environ['WEATHER_AUDIO_CONSUMER_PATH'])\n"
                "consumer = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(consumer)\n"
                "class TestOnlyProcessGroupPermissionDenied(PermissionError): pass\n"
                "real_killpg = consumer.os.killpg\n"
                "def marked_killpg(pid, sig):\n"
                "    try:\n"
                "        return real_killpg(pid, sig)\n"
                "    except PermissionError as exc:\n"
                "        Path(os.environ['WEATHER_TEST_PERMISSION_MARKER']).write_text(\n"
                "            'TestOnlyProcessGroupPermissionDenied', encoding='utf-8')\n"
                "        raise TestOnlyProcessGroupPermissionDenied(\n"
                "            'test observed process-group stop denial') from exc\n"
                "consumer.os.killpg = marked_killpg\n"
                "raise SystemExit(consumer.main(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            command[1] = str(wrapper)
            env = {
                **self.env,
                "WEATHER_AUDIO_CONSUMER_PATH": str(HELPER),
                "WEATHER_TEST_PERMISSION_MARKER": str(permission_marker),
            }
        process = subprocess.Popen(
            command,
            cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while not sentinel.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sentinel.exists(), "dummy owned player did not start"
        return process, sentinel, Path(str(sentinel) + ".completed"), target

    def _start_gated_monitor_player(self, item_path):
        target = item_path
        if item_path.suffix == ".txt":
            target = item_path.with_suffix(".playing")
            item_path.rename(target)
        self.plan_players(target, 1)
        sentinel = self.root / "three-party-player.sentinel"
        ready = self.root / "player-monitor-ready"
        release = self.root / "player-monitor-release"
        wrapper = self.root / "gated_weather_player.py"
        wrapper.write_text(
            "import importlib.util, os, sys, time\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('weather_audio_consumer', os.environ['WEATHER_HELPER_PATH'])\n"
            "consumer = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(consumer)\n"
            "monitor = consumer._monitor_runtime_matches\n"
            "def gated_monitor(canonical, request):\n"
            "    ready = Path(os.environ['WEATHER_MONITOR_READY'])\n"
            "    if not ready.exists():\n"
            "        ready.write_text('ready')\n"
            "        release = Path(os.environ['WEATHER_MONITOR_RELEASE'])\n"
            "        deadline = time.monotonic() + 4\n"
            "        while not release.exists() and time.monotonic() < deadline:\n"
            "            time.sleep(0.005)\n"
            "    return monitor(canonical, request)\n"
            "consumer._monitor_runtime_matches = gated_monitor\n"
            "raise SystemExit(consumer.main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        env = {
            **self.env,
            "DUMMY_IGNORE_TERM": "1",
            "WEATHER_HELPER_PATH": str(HELPER),
            "WEATHER_MONITOR_READY": str(ready),
            "WEATHER_MONITOR_RELEASE": str(release),
        }
        process = subprocess.Popen(
            [
                sys.executable, str(wrapper), "play", "--queue-dir", str(self.queue),
                str(target), "--", sys.executable, str(self.dummy_player),
                str(sentinel), "0", "10",
            ],
            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        while (not sentinel.exists() or not ready.exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert sentinel.exists() and ready.exists(), "player did not reach the gated runtime monitor"
        return process, sentinel, Path(str(sentinel) + ".completed"), target, release

    def _start_runtime_contender(self, operation, target=None, request=None):
        ready = self.root / f"{operation}-runtime-read-ready"
        wrapper = self.root / f"{operation}_weather_contender.py"
        wrapper.write_text(
            "import importlib.util, os, sys\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('weather_audio_consumer', os.environ['WEATHER_HELPER_PATH'])\n"
            "consumer = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(consumer)\n"
            "runtime_matches = consumer._runtime_matches\n"
            "def announce_runtime_read(canonical, request, **kwargs):\n"
            "    Path(os.environ['WEATHER_CONTENDER_READY']).write_text('inside runtime read')\n"
            "    return runtime_matches(canonical, request, **kwargs)\n"
            "consumer._runtime_matches = announce_runtime_read\n"
            "raise SystemExit(consumer.main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        env = {
            **self.env,
            "WEATHER_HELPER_PATH": str(HELPER),
            "WEATHER_CONTENDER_READY": str(ready),
        }
        if operation == "check":
            args = ["check", "--queue-dir", str(self.queue), str(target)]
        else:
            args = [
                "enqueue", "--queue-dir", str(self.queue),
                json.dumps(request, ensure_ascii=False, separators=(",", ":")),
            ]
        process = subprocess.Popen(
            [sys.executable, str(wrapper), *args], cwd=ROOT, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        return process, ready

    def _assert_runtime_contender_does_not_delay_audio_stop(self, operation):
        request = _request(index=6)
        assert self.enqueue(request).returncode == 0
        item = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_06_" in path.name)
        player, sentinel, completed, playing, release_monitor = self._start_gated_monitor_player(item)
        candidate = _request(index=7) if operation == "enqueue" else None
        switch_lock = self.context.parent / "locks" / "game-switch.lock"
        contender = None
        try:
            with switch_lock.open("rb") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                contender, contender_ready = self._start_runtime_contender(
                    operation, target=playing, request=candidate,
                )
                deadline = time.monotonic() + 3
                while not contender_ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert contender_ready.exists(), "contender did not reach its GameSwitch read under the ledger lock"

                started = time.monotonic()
                release_monitor.touch()
                stdout, stderr = player.communicate(timeout=4)
                elapsed = time.monotonic() - started
                assert player.returncode == 74, stderr or stdout
                assert elapsed < 3.25, f"runtime loss did not stop audio within its bound: {elapsed:.2f}s"
                assert not completed.exists(), "owned audio process survived while GameSwitch EX remained held"
                try:
                    with switch_lock.open("rb") as probe:
                        fcntl.flock(probe.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("GameSwitch EX lock was released before audio stopped")
                contender_stdout, contender_stderr = contender.communicate(timeout=1)
                assert contender.returncode != 0, contender_stdout or contender_stderr
        finally:
            release_monitor.touch()
            if player.poll() is None:
                player.terminate()
                player.communicate(timeout=4)
            if contender is not None and contender.poll() is None:
                contender.terminate()
                contender.communicate(timeout=2)
        receipt = self.receipt(request["item_key"])
        assert receipt["status"] == "interrupted"
        assert receipt["reason"] == "runtime_fence_lost"

    def test_check_contender_cannot_hold_ledger_ahead_of_runtime_stop(self):
        self._assert_runtime_contender_does_not_delay_audio_stop("check")

    def test_enqueue_contender_cannot_hold_ledger_ahead_of_runtime_stop(self):
        self._assert_runtime_contender_does_not_delay_audio_stop("enqueue")

    def test_deduplicates_by_stable_item_key_and_rejects_whole_payload_conflicts(self):
        same_text_a = _request(index=1, text="同じ本文")
        same_text_b = _request(index=2, text="同じ本文")
        first = self.enqueue(same_text_a)
        retry = self.enqueue(same_text_a)
        second = self.enqueue(same_text_b)
        assert first.returncode == retry.returncode == second.returncode == 0
        assert json.loads(first.stdout)["status"] == "queued"
        assert json.loads(retry.stdout)["item_key"] == json.loads(first.stdout)["item_key"]
        assert json.loads(second.stdout)["item_key"] != json.loads(first.stdout)["item_key"]
        assert len(list(self.queue.glob("*_weather_audio_item.txt"))) == 2

        conflict = self.enqueue(_request(index=1, text="本文を変更"))
        assert conflict.returncode != 0
        assert "本文を変更" not in conflict.stderr
        assert len(list(self.queue.glob("*_weather_audio_item.txt"))) == 2

    def test_same_key_conflicts_when_forecast_metadata_changes(self):
        original_request = _request(index=3)
        changed_request = deepcopy(original_request)
        changed_request["forecast"]["report_digest"] = "b" * 64
        original = self.enqueue(original_request)
        changed_forecast = self.enqueue(changed_request)
        assert original.returncode == 0
        assert changed_forecast.returncode != 0
        assert self.receipt(f"weather_corner:{EXECUTION_ID}:03")["status"] == "queued"

    def test_queue_rejects_expired_or_wrong_runtime_without_publishing(self):
        expired = self.enqueue(_request(index=4, expires_at=time.time() - 1))
        assert expired.returncode == 0
        expired_receipt = json.loads(expired.stdout)
        assert expired_receipt["status"] == "rejected"
        assert expired_receipt["reason"] == "expired"

        self.set_identity({**IDENTITY, "runtime_id": "g8-a1b2c3d4", "generation": 8})
        mismatch = self.enqueue(_request(index=5))
        assert mismatch.returncode == 0
        mismatch_receipt = json.loads(mismatch.stdout)
        assert mismatch_receipt["status"] == "rejected"
        assert mismatch_receipt["reason"] == "runtime_mismatch"
        assert list(self.queue.glob("*_weather_audio_item.txt")) == []

    def test_player_start_rechecks_identity_and_expiry_before_dummy_process(self):
        queued = self.enqueue(_request(index=6))
        assert queued.returncode == 0
        item_path = next(self.queue.glob("*_weather_audio_item.txt"))
        self.set_identity({**IDENTITY, "runtime_id": "g8-a1b2c3d4", "generation": 8})

        result, sentinel, playing_path = self.player(item_path)
        assert result.returncode == 75
        assert not sentinel.exists()
        receipt = self.receipt(f"weather_corner:{EXECUTION_ID}:06")
        assert receipt["status"] == "rejected"
        assert receipt["reason"] == "runtime_mismatch"

    def test_player_start_rejects_an_item_that_expired_while_queued(self):
        queued = self.enqueue(_request(index=10, expires_at=time.time() + 1.2))
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_10_" in path.name)
        time.sleep(1.25)

        result, sentinel, playing_path = self.player(item_path)
        assert result.returncode == 75
        assert not sentinel.exists()
        receipt = self.receipt(f"weather_corner:{EXECUTION_ID}:10")
        assert receipt["status"] == "rejected"
        assert receipt["reason"] == "expired"

    def test_success_receipt_is_written_only_after_dummy_owned_player_exits(self):
        queued = self.enqueue(_request(index=7))
        assert queued.returncode == 0
        item_path = next(self.queue.glob("*_weather_audio_item.txt"))
        sidecar = item_path.with_suffix("").with_name(item_path.stem + ".weather_audio.json")

        result, sentinel, playing_path = self.player(item_path)
        assert result.returncode == 0
        assert sentinel.read_text(encoding="utf-8") == "started"
        assert Path(str(sentinel) + ".completed").read_text(encoding="utf-8") == "completed"
        assert self.receipt(f"weather_corner:{EXECUTION_ID}:07")["status"] == "queued"

        self.ack_player(playing_path)

        finished = self.helper("finish", "--queue-dir", self.queue, playing_path, "success")
        assert finished.returncode == 0, finished.stderr
        assert json.loads(finished.stdout)["status"] == "played"
        assert self.receipt(f"weather_corner:{EXECUTION_ID}:07")["status"] == "played"
        assert not sidecar.exists()

    def test_stop_ack_fails_closed_if_owned_process_group_survives_sigkill(self):
        import importlib.util
        from unittest.mock import patch

        spec = importlib.util.spec_from_file_location("weather_audio_consumer_under_test", HELPER)
        consumer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(consumer)
        signals = []

        class ExitedLeader:
            pid = 43210

            @staticmethod
            def poll():
                return 0

        def group_remains(_pgid, sig):
            signals.append(sig)

        # A persistent process group makes killpg(..., 0) succeed even after
        # SIGKILL. The stop helper must time out without permitting a stop ack.
        with patch.object(consumer, "PLAYER_STOP_GRACE_SEC", 0.01), \
                patch.object(consumer.os, "killpg", side_effect=group_remains):
            with self.assertRaisesRegex(
                consumer.WeatherAudioError, "process group remained after SIGKILL",
            ):
                consumer._stop_owned_player(ExitedLeader())

        self.assertIn(consumer.signal.SIGTERM, signals)
        self.assertIn(consumer.signal.SIGKILL, signals)

    def test_runtime_identity_change_stops_owned_player_and_terminal_receipt_survives_finish_success(self):
        request = _request(index=5)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_05_" in path.name)
        permission_marker = self.root / "runtime-identity-stop-permission.marker"
        player, sentinel, completed, playing_path = self.start_long_player(
            item_path, permission_marker=permission_marker,
        )

        switch_lock = self.context.parent / "locks" / "game-switch.lock"
        with switch_lock.open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self.set_identity({**IDENTITY, "runtime_id": "g8-b2c3d4e5", "generation": 8})
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        stdout, stderr = player.communicate(timeout=4)
        if (player.returncode == 1 and permission_marker.is_file()
                and permission_marker.read_text(encoding="utf-8") == PROCESS_GROUP_PERMISSION_MARKER):
            unconfirmed = self.helper(
                "quiescence", "--queue-dir", self.queue, f"weather_corner:{EXECUTION_ID}:05",
            )
            assert unconfirmed.returncode == 0, unconfirmed.stderr
            assert json.loads(unconfirmed.stdout)["quiescent"] is False
            if os.environ.get("WEATHER_TEST_REQUIRE_PROCESS_GROUP_STOP") == "1":
                self.fail("CI forbids skipping after an observed process-group stop PermissionError")
            self.skipTest("sandbox denied process-group stop; no player-stop acknowledgement was written")
        assert player.returncode == 74, stderr or stdout
        assert not completed.exists(), "owned player completed after the runtime changed"
        receipt = self.receipt(request["item_key"])
        assert receipt["status"] == "interrupted"
        assert receipt["reason"] == "runtime_fence_lost"

        retry = self.enqueue(request)
        assert retry.returncode == 0
        assert json.loads(retry.stdout)["status"] == "interrupted"
        assert not list(self.queue.glob("*_weather_audio_item.txt"))

        finished = self.helper("finish", "--queue-dir", self.queue, playing_path, "success")
        assert finished.returncode == 0, finished.stderr
        assert json.loads(finished.stdout)["status"] == "interrupted"
        assert self.receipt(request["item_key"])["reason"] == "runtime_fence_lost"
        assert not completed.exists()

    def test_terminal_interrupt_receipt_is_not_player_stop_acknowledgement(self):
        request = _request(index=11)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_11_" in path.name)
        player, sentinel, completed, playing_path, release = self._start_gated_monitor_player(item_path)
        try:
            assert sentinel.exists()
            interrupted = self.helper("interrupt", "--queue-dir", self.queue, playing_path)
            assert interrupted.returncode == 0, interrupted.stderr
            assert json.loads(interrupted.stdout)["status"] == "interrupted"

            # The receipt is terminal, but the owned wrapper has not yet read
            # it and its dummy player is still alive behind the test gate.
            pending = self.helper("quiescence", "--queue-dir", self.queue, request["item_key"])
            assert pending.returncode == 0, pending.stderr
            pending_value = json.loads(pending.stdout)
            assert pending_value["receipt"]["status"] == "interrupted"
            assert pending_value["quiescent"] is False
            assert player.poll() is None
            assert not completed.exists()

            release.write_text("release", encoding="utf-8")
            stdout, stderr = player.communicate(timeout=5)
            assert player.returncode == 74, stderr or stdout
            assert not completed.exists(), "owned dummy player survived terminal interruption"
            stopped = self.helper("quiescence", "--queue-dir", self.queue, request["item_key"])
            assert stopped.returncode == 0, stopped.stderr
            stopped_value = json.loads(stopped.stdout)
            assert stopped_value["receipt"]["status"] == "interrupted"
            assert stopped_value["quiescent"] is True
        finally:
            release.write_text("release", encoding="utf-8")
            if player.poll() is None:
                player.terminate()
                player.communicate(timeout=5)

    def test_sigterm_finally_stops_group_after_leader_exits_before_acknowledging(self):
        request = _request(index=9)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_09_" in path.name)
        playing_path = item_path.with_suffix(".playing")
        item_path.rename(playing_path)
        self.plan_players(playing_path, 1)

        wrapper = self.root / "sigterm-gated-consumer" / "lib" / "weather_audio_consumer.py"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(
            "import importlib.util, os, signal, sys, time\n"
            "from pathlib import Path\n"
            "spec = importlib.util.spec_from_file_location('weather_audio_consumer', os.environ['WEATHER_HELPER_PATH'])\n"
            "consumer = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(consumer)\n"
            "monitor = consumer._monitor_runtime_matches\n"
            "stop = consumer._stop_owned_player\n"
            "def traced_stop(child):\n"
            "    try: return stop(child)\n"
            "    except Exception as exc:\n"
            "        Path(os.environ['WEATHER_STOP_ERROR']).write_text(type(exc).__name__ + ':' + str(exc))\n"
            "        raise\n"
            "consumer._stop_owned_player = traced_stop\n"
            "real_popen = consumer.subprocess.Popen\n"
            "class TrackedPopen(real_popen):\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        super().__init__(*args, **kwargs)\n"
            "        Path(os.environ['WEATHER_CHILD_PID']).write_text(str(self.pid))\n"
            "consumer.subprocess.Popen = TrackedPopen\n"
            "def gated_monitor(canonical, request):\n"
            "    Path(os.environ['WEATHER_MONITOR_READY']).write_text('ready')\n"
            "    release = Path(os.environ['WEATHER_PARENT_RELEASE'])\n"
            "    deadline = time.monotonic() + 5\n"
            "    while not release.exists() and time.monotonic() < deadline: time.sleep(0.005)\n"
            "    child_pid = int(Path(os.environ['WEATHER_CHILD_PID']).read_text())\n"
            "    exited = Path(os.environ['WEATHER_LEADER_EXITED'])\n"
            "    while time.monotonic() < deadline:\n"
            "        result = os.waitid(os.P_PID, child_pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)\n"
            "        if result is not None and result.si_pid == child_pid:\n"
            "            exited.write_text('exited')\n"
            "            break\n"
            "        time.sleep(0.005)\n"
            "    while time.monotonic() < deadline: time.sleep(0.005)\n"
            "    return monitor(canonical, request)\n"
            "consumer._monitor_runtime_matches = gated_monitor\n"
            "raise SystemExit(consumer.main(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        player_script = self.root / "long_lived_same_group_descendant.py"
        player_script.write_text(
            "import pathlib, signal, subprocess, sys, time\n"
            "descendant = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(5)'], "
            "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "pathlib.Path(sys.argv[1]).write_text(str(descendant.pid))\n"
            "release = pathlib.Path(sys.argv[2])\n"
            "while not release.exists(): time.sleep(0.005)\n"
            "raise SystemExit(0)\n",
            encoding="utf-8",
        )
        child_pid_file = self.root / "owned-leader.pid"
        monitor_ready = self.root / "monitor-ready"
        parent_release = self.root / "parent-release"
        leader_exited = self.root / "leader-exited"
        stop_error = self.root / "stop-error"
        helper_env = self.env | {
            "WEATHER_HELPER_PATH": str(HELPER),
            "WEATHER_CHILD_PID": str(child_pid_file),
            "WEATHER_MONITOR_READY": str(monitor_ready),
            "WEATHER_PARENT_RELEASE": str(parent_release),
            "WEATHER_LEADER_EXITED": str(leader_exited),
            "WEATHER_STOP_ERROR": str(stop_error),
        }
        player = subprocess.Popen(
            [sys.executable, str(wrapper), "play", "--queue-dir", str(self.queue),
             str(playing_path), "--", sys.executable, str(player_script),
             str(child_pid_file.with_suffix(".descendant")), str(parent_release)],
            cwd=self.root, env=helper_env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        descendant_pid = None
        try:
            deadline = time.monotonic() + 3
            while (not child_pid_file.with_suffix(".descendant").exists()
                   or not monitor_ready.exists()) and time.monotonic() < deadline:
                time.sleep(0.01)
            assert child_pid_file.with_suffix(".descendant").exists() and monitor_ready.exists()
            descendant_pid = int(child_pid_file.with_suffix(".descendant").read_text())

            interrupted = self.helper("interrupt", "--queue-dir", self.queue, playing_path)
            assert interrupted.returncode == 0, interrupted.stderr
            assert json.loads(interrupted.stdout)["status"] == "interrupted"
            pending = self.helper("quiescence", "--queue-dir", self.queue, request["item_key"])
            assert json.loads(pending.stdout)["quiescent"] is False
            os.kill(descendant_pid, 0)

            parent_release.write_text("release")
            deadline = time.monotonic() + 3
            while not leader_exited.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert leader_exited.exists(), "owned group leader did not exit behind monitor gate"
            assert json.loads(self.helper(
                "quiescence", "--queue-dir", self.queue, request["item_key"],
            ).stdout)["quiescent"] is False

            # SIGTERM arrives after waitid proves the group leader exited; its
            # live same-group descendant must still be stopped before ack.
            os.kill(player.pid, signal.SIGTERM)
            stdout, stderr = player.communicate(timeout=6)
            if player.returncode != 74 and stop_error.exists():
                error = stop_error.read_text()
                if "PermissionError" in error:
                    unconfirmed = self.helper(
                        "quiescence", "--queue-dir", self.queue, request["item_key"],
                    )
                    assert unconfirmed.returncode == 0, unconfirmed.stderr
                    state = json.loads(unconfirmed.stdout)
                    assert state["receipt"]["status"] == "interrupted"
                    assert state["quiescent"] is False
                    self.skipTest("sandbox denied process-group stop; consumer kept stop acknowledgement false")
            assert player.returncode == 74, stop_error.read_text() if stop_error.exists() else stderr or stdout
            stopped = self.helper("quiescence", "--queue-dir", self.queue, request["item_key"])
            assert stopped.returncode == 0, stopped.stderr
            assert json.loads(stopped.stdout)["quiescent"] is True
            with self.assertRaises(ProcessLookupError):
                os.kill(descendant_pid, 0)
        finally:
            parent_release.write_text("release")
            if player.poll() is None:
                player.terminate()
                player.communicate(timeout=6)

    def test_legacy_terminal_interrupted_record_is_not_assumed_quiescent(self):
        request = _request(index=12)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_12_" in path.name)
        player, _sentinel, _completed, playing_path, release = self._start_gated_monitor_player(item_path)
        try:
            interrupted = self.helper("interrupt", "--queue-dir", self.queue, playing_path)
            assert interrupted.returncode == 0, interrupted.stderr
            assert json.loads(interrupted.stdout)["status"] == "interrupted"

            record_path = self.queue / ".weather_audio_receipts" / f"{EXECUTION_ID}_12.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["schema_version"] = 2
            record.pop("player_stop_confirmed")
            record.pop("player_pid")
            record_path.write_text(json.dumps(record), encoding="utf-8")

            status = self.helper("quiescence", "--queue-dir", self.queue, request["item_key"])
            assert status.returncode == 0, status.stderr
            result = json.loads(status.stdout)
            assert result["receipt"]["status"] == "interrupted"
            assert result["quiescent"] is False
            assert player.poll() is None
        finally:
            release.write_text("release", encoding="utf-8")
            if player.poll() is None:
                player.terminate()
                player.communicate(timeout=5)

    def test_monitor_lock_contention_fails_closed_and_kills_ignoring_player_within_bound(self):
        request = _request(index=6)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_06_" in path.name)
        self.env["DUMMY_IGNORE_TERM"] = "1"
        player, sentinel, completed, _playing_path = self.start_long_player(item_path)

        switch_lock = self.context.parent / "locks" / "game-switch.lock"
        started = time.monotonic()
        with switch_lock.open("rb") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                stdout, stderr = player.communicate(timeout=4)
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

        elapsed = time.monotonic() - started
        assert player.returncode == 74, stderr or stdout
        assert elapsed < 3.2, f"runtime monitor blocked too long under EX lock: {elapsed:.2f}s"
        assert not completed.exists(), "owned process group survived bounded fail-closed stop"
        receipt = self.receipt(request["item_key"])
        assert receipt["status"] == "interrupted"
        assert receipt["reason"] == "runtime_fence_lost"

    def test_success_without_an_owned_player_is_rejected(self):
        queued = self.enqueue(_request(index=0))
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_00_" in path.name)
        playing_path = item_path.with_suffix(".playing")
        item_path.rename(playing_path)

        # This is the say_enqueue exit-zero boundary for render-only or
        # metadata-only paths: no weather-owned player start was recorded.
        finished = self.helper("finish", "--queue-dir", self.queue, playing_path, "success")

        assert finished.returncode == 0, finished.stderr
        receipt = json.loads(finished.stdout)
        assert receipt["status"] == "rejected"
        assert receipt["reason"] == "player_rejected"
        assert self.receipt("weather_corner:" + EXECUTION_ID + ":00")["status"] == "rejected"

    def test_say_enqueue_weather_failure_stays_interrupted_before_partial_success_heuristic(self):
        function = self._shell_function(ROOT / "say_enqueue.sh", "_play_with_retry() {")
        partial_marker = self.root / "partial-success-called"
        stopped_marker = self.root / "chrome-player-stopped"
        script = function + r'''
WEATHER_AUDIO_ITEM=1
CONTENT_FILE=weather.txt
_weather_audio_plan_players() { return 0; }
_weather_audio_interrupt() { :; }
PID_FILE="$TEST_PID_FILE"
SAY_RETRY_MAX=0
SAY_RETRY_SLEEP_SEC=0
_hanjuku_audio_allowed() { return 0; }
_set_current_source() { :; }
_log() { :; }
docich_cc_clear() { :; }
_launch_say() { LAUNCHED_SAY_PID=123; LAUNCHED_EXPECTED_SEC=10; CHROME_AUDIO_USED="$TEST_CHROME"; }
_wait_for_player_pid() {
  PLAYER_WAIT_RC="$TEST_PLAYER_RC"
  PLAYER_WAIT_ELAPSED="$TEST_PLAYER_ELAPSED"
  PLAYER_WAIT_TIMED_OUT="$TEST_PLAYER_TIMEOUT"
  [ "$PLAYER_WAIT_RC" -eq 0 ] && [ "$PLAYER_WAIT_TIMED_OUT" -eq 0 ]
}
_is_truncated_playback() { [ "$TEST_TRUNCATED" = 1 ]; }
_partial_playback_already_heard() { touch "$TEST_PARTIAL_MARKER"; return 0; }
_stop_chrome_audio_players() { touch "$TEST_STOPPED_MARKER"; }
_sleep_with_heartbeat() { return 99; }
_play_with_retry
result=$?
printf '%s' "$result"
'''
        cases = (
            # The old shared behavior maps a nearly-complete nonzero player to
            # exit zero. Weather must bail out before that heuristic.
            ("partial", {"TEST_PLAYER_RC": "9", "TEST_PLAYER_ELAPSED": "9", "TEST_PLAYER_TIMEOUT": "0", "TEST_TRUNCATED": "0", "TEST_CHROME": "0"}),
            ("timeout", {"TEST_PLAYER_RC": "124", "TEST_PLAYER_ELAPSED": "9", "TEST_PLAYER_TIMEOUT": "1", "TEST_TRUNCATED": "0", "TEST_CHROME": "1"}),
            ("truncated-zero", {"TEST_PLAYER_RC": "0", "TEST_PLAYER_ELAPSED": "1", "TEST_PLAYER_TIMEOUT": "0", "TEST_TRUNCATED": "1", "TEST_CHROME": "0"}),
        )
        for label, values in cases:
            with self.subTest(case=label):
                env = {
                    **self.env,
                    **values,
                    "TEST_PID_FILE": str(self.root / f"{label}.pid"),
                    "TEST_PARTIAL_MARKER": str(partial_marker),
                    "TEST_STOPPED_MARKER": str(stopped_marker),
                }
                partial_marker.unlink(missing_ok=True)
                stopped_marker.unlink(missing_ok=True)
                result = subprocess.run(["bash", "-c", script], cwd=self.root, env=env, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                assert result.stdout == "74"
                assert not partial_marker.exists()
                assert stopped_marker.exists() is (values["TEST_CHROME"] == "1")

    def test_weather_prerendered_player_cannot_map_an_early_zero_exit_to_success(self):
        function = self._shell_function(ROOT / "say_enqueue.sh", "_play_prerendered_voicevox_chunks() {")
        wav = self.root / "chunk.wav"
        wav.write_bytes(b"dummy audio data; never played")
        playlist = self.root / "playlist.txt"
        playlist.write_text(str(wav) + "\n", encoding="utf-8")
        stopped_marker = self.root / "chrome-player-stopped"
        interrupt_marker = self.root / "weather-player-interrupted"
        script = function + r'''
WEATHER_AUDIO_ITEM=1
SAY_PRESERVE_PRERENDERED_CHUNKS=1
_weather_audio_plan_players() { return 0; }
_weather_audio_ack_player() { return 0; }
_weather_audio_interrupt() { touch "$TEST_INTERRUPT_MARKER"; }
_log() { :; }
_set_current_source() { :; }
docich_cc_prepare() { return 1; }
docich_cc_clear() { :; }
_launch_stream_wav() { true & return 0; }
_estimate_audio_duration_sec() { printf '10'; }
_wait_for_player_pid() { PLAYER_WAIT_RC=0; PLAYER_WAIT_ELAPSED=1; PLAYER_WAIT_TIMED_OUT=0; return 0; }
_is_truncated_playback() { [ "$TEST_TRUNCATED" = 1 ]; }
_stop_chrome_audio_players() { touch "$TEST_STOPPED_MARKER"; }
_play_prerendered_voicevox_chunks "$TEST_PLAYLIST"
result=$?
printf '%s' "$result"
'''
        env = {
            **self.env,
            "TEST_TRUNCATED": "1",
            "TEST_PLAYLIST": str(playlist),
            "TEST_STOPPED_MARKER": str(stopped_marker),
            "TEST_INTERRUPT_MARKER": str(interrupt_marker),
        }
        result = subprocess.run(["bash", "-c", script], cwd=self.root, env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "1"
        assert wav.exists()
        assert interrupt_marker.exists()

    def _run_prerendered_weather_shell(self, item_path, playlist, *, elapsed, second_exit=0):
        signatures = (
            "_weather_audio_plan_players() {",
            "_weather_audio_ack_player() {",
            "_weather_audio_interrupt() {",
            "_launch_bg_exec() {",
            "_is_truncated_playback() {",
            "_play_prerendered_voicevox_chunks() {",
        )
        pieces = [self._shell_function(ROOT / "say_enqueue.sh", sig) for sig in signatures]
        harness = r'''
CONTENT_FILE="$TEST_ITEM_PATH"
WEATHER_AUDIO_ITEM=1
SAY_PRESERVE_PRERENDERED_CHUNKS=1
SAY_TRUNCATE_MIN_EXPECTED_SEC=1
SAY_TRUNCATE_RATIO=0.5
SAY_TRUNCATE_GRACE_SEC=0
LAUNCH_COUNT=0
_log() { :; }
_set_current_source() { :; }
docich_cc_prepare() { return 1; }
docich_cc_clear() { :; }
docich_cc_commit() { return 0; }
_estimate_audio_duration_sec() { printf '10'; }
_wait_for_player_pid() {
  wait "$1"
  PLAYER_WAIT_RC=$?
  PLAYER_WAIT_ELAPSED="$TEST_ELAPSED"
  PLAYER_WAIT_TIMED_OUT=0
  [ "$PLAYER_WAIT_RC" -eq 0 ]
}
_launch_stream_wav() {
  local index="$LAUNCH_COUNT" exit_code=0 sentinel
  LAUNCH_COUNT=$((LAUNCH_COUNT + 1))
  sentinel="$TEST_SENTINEL_PREFIX.$index"
  if [ "$index" -eq 1 ]; then
    exit_code="$TEST_SECOND_EXIT"
    python3 ./lib/weather_audio_consumer.py get --queue-dir "$COMMENT_QUEUE_DIR" \
      "weather_corner:$TEST_EXECUTION_ID:12" >"$TEST_BEFORE_SECOND"
  fi
  _launch_bg_exec "" "$PYTHON_BIN" "$TEST_DUMMY_PLAYER" "$sentinel" "$exit_code" 0.01
}
_stop_chrome_audio_players() { :; }
_play_prerendered_voicevox_chunks "$TEST_PLAYLIST"
result=$?
printf '%s %s' "$result" "$LAUNCH_COUNT"
'''
        env = {
            **self.env,
            "TEST_ITEM_PATH": str(item_path),
            "TEST_PLAYLIST": str(playlist),
            "TEST_ELAPSED": str(elapsed),
            "TEST_SECOND_EXIT": str(second_exit),
            "TEST_SENTINEL_PREFIX": str(self.root / "real-shell-player"),
            "TEST_BEFORE_SECOND": str(self.root / "receipt-before-second.json"),
            "TEST_EXECUTION_ID": EXECUTION_ID,
            "PYTHON_BIN": sys.executable,
            "TEST_DUMMY_PLAYER": str(self.dummy_player),
        }
        return subprocess.run(
            ["bash", "-c", "\n".join(pieces) + harness],
            cwd=ROOT, env=env, capture_output=True, text=True,
        )

    def test_actual_prerendered_shell_path_plays_two_chunks_before_item_success(self):
        request = _request(index=12)
        assert self.enqueue(request).returncode == 0
        item = next(self.queue.glob("*_weather_audio_item.txt"))
        playing = item.with_suffix(".playing")
        item.rename(playing)
        playlist = self.root / "two-chunks.txt"
        wav1, wav2 = self.root / "chunk-1.wav", self.root / "chunk-2.wav"
        wav1.write_bytes(b"dummy 1")
        wav2.write_bytes(b"dummy 2")
        playlist.write_text(f"{wav1}\n{wav2}\n", encoding="utf-8")

        result = self._run_prerendered_weather_shell(playing, playlist, elapsed=10)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "0 2"
        assert json.loads((self.root / "receipt-before-second.json").read_text())["status"] == "queued"
        for index in (0, 1):
            sentinel = self.root / f"real-shell-player.{index}"
            assert sentinel.read_text(encoding="utf-8") == "started"
            assert Path(str(sentinel) + ".completed").exists()
        assert self.receipt(request["item_key"])["status"] == "queued"

        finished = self.helper("finish", "--queue-dir", self.queue, playing, "success")
        assert finished.returncode == 0, finished.stderr
        assert json.loads(finished.stdout)["status"] == "played"

    def test_actual_prerendered_shell_path_second_chunk_failure_stays_interrupted(self):
        request = _request(index=12)
        assert self.enqueue(request).returncode == 0
        item = next(self.queue.glob("*_weather_audio_item.txt"))
        playing = item.with_suffix(".playing")
        item.rename(playing)
        playlist = self.root / "two-chunks.txt"
        wav1, wav2 = self.root / "chunk-1.wav", self.root / "chunk-2.wav"
        wav1.write_bytes(b"dummy 1")
        wav2.write_bytes(b"dummy 2")
        playlist.write_text(f"{wav1}\n{wav2}\n", encoding="utf-8")

        result = self._run_prerendered_weather_shell(playing, playlist, elapsed=10, second_exit=9)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "1 2"
        assert json.loads((self.root / "receipt-before-second.json").read_text())["status"] == "queued"
        first = self.root / "real-shell-player.0"
        second = self.root / "real-shell-player.1"
        assert Path(str(first) + ".completed").exists()
        assert Path(str(second) + ".failed").exists()
        assert self.receipt(request["item_key"])["status"] == "interrupted"

        finished = self.helper("finish", "--queue-dir", self.queue, playing, "failure")
        assert finished.returncode == 0, finished.stderr
        assert json.loads(finished.stdout)["status"] == "interrupted"
        assert self.receipt(request["item_key"])["reason"] == "playback_interrupted"

    def test_actual_prerendered_shell_path_exit_zero_early_truncation_never_plays_item(self):
        request = _request(index=12)
        assert self.enqueue(request).returncode == 0
        item = next(self.queue.glob("*_weather_audio_item.txt"))
        playing = item.with_suffix(".playing")
        item.rename(playing)
        playlist = self.root / "one-chunk.txt"
        wav = self.root / "early.wav"
        wav.write_bytes(b"dummy only")
        playlist.write_text(f"{wav}\n", encoding="utf-8")

        result = self._run_prerendered_weather_shell(playing, playlist, elapsed=0)

        assert result.returncode == 0, result.stderr
        assert result.stdout == "1 1"
        sentinel = self.root / "real-shell-player.0"
        assert Path(str(sentinel) + ".completed").exists()
        assert self.receipt(request["item_key"])["status"] == "interrupted"

        # Even an apparently successful finalizer cannot overwrite the
        # stricter duration-check interruption.
        finished = self.helper("finish", "--queue-dir", self.queue, playing, "success")
        assert finished.returncode == 0, finished.stderr
        receipt = json.loads(finished.stdout)
        assert receipt["status"] == "interrupted"
        assert receipt["reason"] == "playback_interrupted"

    def test_same_key_retry_while_owned_player_is_live_does_not_interrupt_or_duplicate(self):
        request = _request(index=12)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_12_" in path.name)
        sentinel = self.root / "long-player.sentinel"
        playing_path = item_path.with_suffix(".playing")
        item_path.rename(playing_path)
        self.plan_players(playing_path, 1)
        player = subprocess.Popen(
            [
                sys.executable, str(HELPER), "play", "--queue-dir", str(self.queue),
                str(playing_path), "--", sys.executable, str(self.dummy_player),
                str(sentinel), "0", "1.0",
            ],
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + 3
        try:
            while not sentinel.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert sentinel.exists()

            retry = self.enqueue(request)
            assert retry.returncode == 0
            assert json.loads(retry.stdout)["status"] == "queued"
            assert playing_path.exists()
            assert self.receipt(request["item_key"])["status"] == "queued"

            stdout, stderr = player.communicate(timeout=5)
            assert player.returncode == 0, stderr or stdout
        finally:
            if player.poll() is None:
                player.terminate()
                player.communicate(timeout=5)
            else:
                player.communicate(timeout=5)
        self.ack_player(playing_path)
        finished = self.helper("finish", "--queue-dir", self.queue, playing_path, "success")
        assert finished.returncode == 0
        assert json.loads(finished.stdout)["status"] == "played"
        assert len(list(self.queue.glob("*_weather_audio_item.*"))) == 1

    def test_same_key_retry_after_claim_before_player_start_keeps_inflight_item(self):
        request = _request(index=2)
        queued = self.enqueue(request)
        assert queued.returncode == 0
        item_path = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_02_" in path.name)
        playing_path = item_path.with_suffix(".playing")
        item_path.rename(playing_path)

        retry = self.enqueue(request)

        assert retry.returncode == 0
        assert json.loads(retry.stdout)["status"] == "queued"
        assert playing_path.exists()
        assert self.receipt(request["item_key"])["status"] == "queued"

        old = time.time() - 60
        os.utime(playing_path, (old, old))
        (self.root / "tmp" / ".say_queue").mkdir(parents=True)
        self.env["TMP_ROOT"] = str(self.root)
        recovered = self.shell('''
source "$ELOOP_LIB_DIR/broadcast/comment_lib.sh"
_cp_my_pid=test
_recover_orphan_comment_playing_files
''')
        assert recovered.returncode == 0, recovered.stderr
        assert not playing_path.exists()
        assert self.receipt(request["item_key"])["status"] == "interrupted"

    def test_nonzero_owned_player_and_orphaned_playing_item_become_terminal_interruptions(self):
        failed = self.enqueue(_request(index=8))
        assert failed.returncode == 0
        failed_item = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_08_" in path.name)
        player_result, sentinel, failed_playing = self.player(failed_item, exit_code=9)
        assert player_result.returncode != 0
        assert sentinel.exists()
        failed_receipt = self.helper("finish", "--queue-dir", self.queue, failed_playing, "failure")
        assert failed_receipt.returncode == 0
        assert json.loads(failed_receipt.stdout)["status"] == "interrupted"
        assert self.receipt(f"weather_corner:{EXECUTION_ID}:08")["reason"] == "playback_interrupted"

        orphaned = self.enqueue(_request(index=9))
        assert orphaned.returncode == 0
        orphan = next(path for path in self.queue.glob("*_weather_audio_item.txt") if "_09_" in path.name)
        playing = orphan.with_suffix(".playing")
        orphan.rename(playing)
        old = time.time() - 60
        os.utime(playing, (old, old))
        (self.root / "tmp" / ".say_queue").mkdir(parents=True)
        self.env["TMP_ROOT"] = str(self.root)
        recovered = self.shell('''
source "$ELOOP_LIB_DIR/broadcast/comment_lib.sh"
_cp_my_pid=test
_recover_orphan_comment_playing_files
''')
        assert recovered.returncode == 0, recovered.stderr
        assert not playing.exists()
        receipt = self.receipt(f"weather_corner:{EXECUTION_ID}:09")
        assert receipt["status"] == "interrupted"
        assert receipt["reason"] == "worker_interrupted"

    def test_existing_comment_consumer_records_receipt_after_dummy_owned_player(self):
        queued = self.enqueue(_request(index=11))
        assert queued.returncode == 0
        item_key = f"weather_corner:{EXECUTION_ID}:11"
        sentinel = self.root / "comment-consumer-player.sentinel"
        fake_say = self.root / "say_enqueue.sh"
        fake_say.write_text(
            "#!/usr/bin/env bash\n"
            "set -e\n"
            "target=\"$2\"\n"
            "\"$PYTHON_BIN\" \"$ELOOP_LIB_DIR/lib/weather_audio_consumer.py\" plan "
            "--queue-dir \"$COMMENT_QUEUE_DIR\" \"$target\" 1\n"
            "\"$PYTHON_BIN\" \"$ELOOP_LIB_DIR/lib/weather_audio_consumer.py\" play "
            "--queue-dir \"$COMMENT_QUEUE_DIR\" \"$target\" -- "
            "\"$PYTHON_BIN\" \"$DUMMY_PLAYER\" \"$PLAYER_SENTINEL\" 0\n"
            "\"$PYTHON_BIN\" \"$ELOOP_LIB_DIR/lib/weather_audio_consumer.py\" ack "
            "--queue-dir \"$COMMENT_QUEUE_DIR\" \"$target\"\n",
            encoding="utf-8",
        )
        fake_say.chmod(0o755)
        env = {
            **self.env,
            "PYTHON_BIN": sys.executable,
            "DUMMY_PLAYER": str(self.dummy_player),
            "PLAYER_SENTINEL": str(sentinel),
        }
        script = f'''
source "$ELOOP_LIB_DIR/broadcast/comment_lib.sh"
COMMENT_PLAYED_HASHES_FILE="$TMP_ROOT/played_hashes.txt"
mkdir -p "$TMP_ROOT/tmp/.say_queue"
: > "$COMMENT_PLAYED_HASHES_FILE"
_cp_my_pid=test
RADIO_SAY_RATE=120
_comment_hash_file() {{ printf fixed-hash; }}
_broadcast_read_expected_mode() {{ return 1; }}
_broadcast_host_mode() {{ printf main; }}
_broadcast_clear_expected_mode() {{ :; }}
_comment_clear_generation_meta() {{ :; }}
_comment_generation_debug_summary() {{ :; }}
_remember_spoken_comment() {{ :; }}
_play_deferred_radio_queue_once() {{ :; }}
_play_comment_queue
'''
        env["TMP_ROOT"] = str(self.root)
        result = subprocess.run(
            ["bash", "-c", script], cwd=self.root, env=env,
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
        assert sentinel.read_text(encoding="utf-8") == "started"
        assert Path(str(sentinel) + ".completed").read_text(encoding="utf-8") == "completed"
        assert self.receipt(item_key)["status"] == "played"
        assert list(self.queue.glob("*_weather_audio_item.txt")) == []

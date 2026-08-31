#!/usr/bin/env python3
from __future__ import annotations

import json
import signal
import tempfile
import unittest
import urllib.error
from io import BytesIO
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from lib.youtube_broadcast_guard import (
    Config,
    GuardError,
    YouTubeAPI,
    _select_stream,
    check_once,
    restart_publisher,
)


class FakeAPI:
    def __init__(self, *, active=None, upcoming=None, streams=None):
        self.active = list(active or [])
        self.upcoming = list(upcoming or [])
        self.stream_items = list(streams or [])
        self.created = 0
        self.bound = []
        self.refreshed = 0

    def refresh(self):
        self.refreshed += 1

    def broadcasts(self, status):
        if status == "active":
            return list(self.active)
        if status == "upcoming":
            return list(self.upcoming)
        raise AssertionError(status)

    def streams(self):
        return list(self.stream_items)

    def create_broadcast(self, _config):
        self.created += 1
        return "new-broadcast"

    def bind(self, broadcast_id, stream_id):
        self.bound.append((broadcast_id, stream_id))
        self.active = [{"id": broadcast_id, "status": {"lifeCycleStatus": "live"}}]


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = Config(
            enabled=True,
            auto_create=True,
            restart_publisher=True,
            poll_sec=60,
            missing_confirmations=3,
            create_cooldown_sec=900,
            live_timeout_sec=10,
            api_timeout_sec=2,
            title="test",
            privacy="public",
            stream_id="",
            state_file=root / "guard.json",
            pid_file=root / "guard.pid",
            direct_status_file=root / "direct.json",
            expected_local_target="rtmp://127.0.0.1:1935/soren/live",
        )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def active_broadcast(bid="live-id"):
        return {"id": bid, "status": {"lifeCycleStatus": "live"}}

    @staticmethod
    def ready_broadcast(bid="ready-id"):
        return {"id": bid, "status": {"lifeCycleStatus": "ready"}}

    @staticmethod
    def active_stream(sid="stream-id"):
        return {"id": sid, "status": {"streamStatus": "active"}}

    def test_live_broadcast_never_creates_or_restarts(self):
        api = FakeAPI(active=[self.active_broadcast()])
        state = check_once(self.config, api, restart_fn=lambda _: self.fail("restart"))
        self.assertEqual("live", state["status"])
        self.assertEqual(0, api.created)

    def test_upcoming_broadcast_never_duplicates(self):
        api = FakeAPI(upcoming=[self.ready_broadcast()])
        state = check_once(self.config, api, restart_fn=lambda _: self.fail("restart"))
        self.assertEqual("upcoming", state["status"])
        self.assertEqual(0, api.created)

    def test_own_pending_broadcast_resumes_publisher_restart(self):
        self.config.state_file.write_text(
            json.dumps(
                {
                    "broadcast_id": "ready-id",
                    "last_create_at": 100,
                    "last_restart_at": 0,
                }
            ),
            encoding="utf-8",
        )
        api = FakeAPI(upcoming=[self.ready_broadcast()])

        def restart(_config):
            api.active = [self.active_broadcast("ready-id")]
            return (10, 11)

        state = check_once(self.config, api, restart_fn=restart)
        self.assertEqual("live", state["status"])
        self.assertEqual("recovered", state["action"])
        self.assertGreaterEqual(state["last_restart_at"], 100)

    def test_unrelated_upcoming_broadcast_is_never_restarted(self):
        self.config.state_file.write_text(
            json.dumps({"broadcast_id": "ours", "last_create_at": 100}),
            encoding="utf-8",
        )
        api = FakeAPI(upcoming=[self.ready_broadcast("theirs")])
        state = check_once(self.config, api, restart_fn=lambda _: self.fail("restart"))
        self.assertEqual("upcoming", state["status"])
        self.assertEqual("theirs", state["broadcast_id"])

    def test_missing_requires_three_confirmations(self):
        api = FakeAPI(streams=[self.active_stream()])
        for expected in (1, 2):
            state = check_once(self.config, api, restart_fn=lambda _: (10, 11))
            self.assertEqual("missing", state["status"])
            self.assertEqual(expected, state["missing_count"])
            self.assertEqual(0, api.created)
        state = check_once(self.config, api, restart_fn=lambda _: (10, 11))
        self.assertEqual("live", state["status"])
        self.assertEqual("recovered", state["action"])
        self.assertEqual([("new-broadcast", "stream-id")], api.bound)

    def test_disabled_is_noop_without_refresh(self):
        api = FakeAPI()
        state = check_once(replace(self.config, enabled=False), api)
        self.assertEqual("disabled", state["status"])
        self.assertEqual(0, api.refreshed)

    def test_auto_create_off_reports_missing_only(self):
        api = FakeAPI(streams=[self.active_stream()])
        config = replace(self.config, auto_create=False, missing_confirmations=1)
        state = check_once(config, api)
        self.assertEqual("missing", state["status"])
        self.assertEqual(0, api.created)

    def test_ambiguous_active_stream_fails_closed(self):
        api = FakeAPI(streams=[self.active_stream("a"), self.active_stream("b")])
        config = replace(self.config, missing_confirmations=1)
        state = check_once(config, api)
        self.assertEqual("error", state["status"])
        self.assertEqual("active_stream_count:2", state["error"])
        self.assertEqual(0, api.created)

    def test_configured_stream_must_exist(self):
        with self.assertRaisesRegex(GuardError, "configured_stream_not_found"):
            _select_stream([self.active_stream("other")], "wanted")

    def test_restart_validates_ffmpeg_identity_and_waits_for_new_pid(self):
        self.config.direct_status_file.write_text(
            json.dumps({"running": True, "ffmpeg_pid": 100}), encoding="utf-8"
        )
        calls = []
        ticks = iter([0.0, 0.0, 1.0, 1.0])

        def sleep(_seconds):
            self.config.direct_status_file.write_text(
                json.dumps({"running": True, "ffmpeg_pid": 101}), encoding="utf-8"
            )

        old_pid, new_pid = restart_publisher(
            self.config,
            kill_fn=lambda pid, sig: calls.append((pid, sig)),
            cmdline_fn=lambda _pid: (
                "/usr/bin/ffmpeg -f flv rtmp://127.0.0.1:1935/soren/live"
            ),
            sleep_fn=sleep,
            monotonic_fn=lambda: next(ticks),
        )
        self.assertEqual((100, 101), (old_pid, new_pid))
        self.assertEqual([(100, signal.SIGTERM)], calls)

    def test_restart_refuses_wrong_process(self):
        self.config.direct_status_file.write_text(
            json.dumps({"running": True, "ffmpeg_pid": 100}), encoding="utf-8"
        )
        with self.assertRaisesRegex(GuardError, "identity_mismatch"):
            restart_publisher(
                self.config,
                kill_fn=lambda *_: self.fail("must not kill"),
                cmdline_fn=lambda _pid: "python unrelated.py",
            )

    def test_oauth_failure_is_safe_and_does_not_create(self):
        api = FakeAPI()

        def fail_refresh():
            raise GuardError("oauth_refresh:invalid_grant")

        api.refresh = fail_refresh
        state = check_once(self.config, api)
        self.assertEqual("error", state["status"])
        self.assertEqual("oauth_refresh:invalid_grant", state["error"])
        self.assertNotIn("token", json.dumps(state))

    def test_create_payload_disables_auto_stop(self):
        api = YouTubeAPI(timeout=2)
        calls = []

        def fake_api(path, *, params, method="GET", payload=None):
            calls.append((path, params, method, payload))
            return {"id": "created-id"}

        api._api = fake_api
        self.assertEqual("created-id", api.create_broadcast(self.config))
        payload = calls[0][3]
        self.assertIs(payload["contentDetails"]["enableAutoStart"], True)
        self.assertIs(payload["contentDetails"]["enableAutoStop"], False)

    def test_refresh_error_reports_reason_without_response_body(self):
        api = YouTubeAPI(timeout=2)
        error = urllib.error.HTTPError(
            "https://oauth2.googleapis.com/token",
            400,
            "Bad Request",
            {},
            BytesIO(b'{"error":"invalid_grant","error_description":"sensitive"}'),
        )
        with patch("urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(GuardError, "oauth_refresh:invalid_grant") as raised:
                api._request_json(
                    "https://oauth2.googleapis.com/token",
                    method="POST",
                    body=b"hidden",
                    auth=False,
                )
        self.assertNotIn("sensitive", str(raised.exception))


if __name__ == "__main__":
    unittest.main()

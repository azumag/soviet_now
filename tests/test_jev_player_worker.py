import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lib.jev_player import JevPlayer, JevPlayerConfig  # noqa: E402
from lib.jev_player_contract import MODEL, dumps  # noqa: E402
from lib.jev_player_worker import (  # noqa: E402
    ENDPOINT,
    JevWorkerError,
    WorkerConfig,
    WorkerResult,
    bounded_process,
    http_worker,
)


IDS = ["c00", "c01"]


def request():
    return {
        "schema_version": 1,
        "rules_version": "sorengame-v1",
        "model": MODEL,
        "questions": {"drop_position": {"type": "choice", "candidate_ids": IDS}},
        "state": {"observation": {}, "candidates": [{"candidate_id": item} for item in IDS]},
    }


def success_result(selected_id="c00"):
    return WorkerResult(
        status="ok",
        selected_id=selected_id,
        confidence=0.7,
        probabilities={"c00": 0.7, "c01": 0.3},
        model=MODEL,
        usage={"input_tokens": 10, "output_tokens": 2},
    )


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        if limit >= 0:
            return self.body[:limit]
        return self.body


class FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return self.response


class WorkerTransportTests(unittest.TestCase):
    def test_http_worker_uses_fixed_endpoint_and_validates_response_without_network(self):
        response = {
            "model": MODEL,
            "answers": {
                "drop_position": {
                    "type": "choice",
                    "choice": "c00",
                    "probabilities": {"c00": 0.7, "c01": 0.3},
                    "confidence": 0.7,
                }
            },
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }
        opener = FakeOpener(FakeResponse(dumps(response).encode("utf-8")))
        with patch("lib.jev_player_worker.urllib.request.build_opener", return_value=opener):
            result = http_worker(request(), "test-key", 500)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.selected_id, "c00")
        self.assertEqual(opener.request.full_url, ENDPOINT)
        self.assertEqual(opener.request.method, "POST")
        self.assertIn("Bearer test-key", opener.request.headers["Authorization"])

    def test_http_worker_rejects_oversized_body_and_server_status(self):
        opener = FakeOpener(FakeResponse(b"x" * (64 * 1024 + 1)))
        with patch("lib.jev_player_worker.urllib.request.build_opener", return_value=opener):
            self.assertEqual(http_worker(request(), "key", 500).status, "response_too_large")

        opener = FakeOpener(FakeResponse(b"", status=429))
        with patch("lib.jev_player_worker.urllib.request.build_opener", return_value=opener):
            self.assertEqual(http_worker(request(), "key", 500).status, "rate_limited")

    def test_bounded_process_kills_and_reaps_timeout(self):
        with self.assertRaises(JevWorkerError) as caught:
            bounded_process(
                [sys.executable, "-c", "import time; time.sleep(2)"],
                b"",
                20,
            )
        self.assertEqual(caught.exception.status, "timeout")

    def test_child_runs_under_isolated_python(self):
        # `python -I` does not put the script directory on sys.path (3.11+), so
        # the child must bootstrap its sibling import or it exits non-zero and
        # the parent reports worker_exit.
        child = ROOT / "lib" / "jev_player_worker.py"
        completed = subprocess.run(
            [sys.executable, "-I", str(child), "--jev-http-worker"],
            input=b"{}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertEqual(json.loads(completed.stdout)["status"], "auth_error")

    def test_player_is_disabled_by_default_without_transport(self):
        player = JevPlayer(JevPlayerConfig())
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            result = player.choose(request(), IDS)
        self.assertEqual(result.status, "disabled")
        self.assertEqual(result.source, "fallback")
        self.assertEqual(player.request_count, 0)

    def test_player_success_is_validated_and_failure_latches(self):
        calls = []

        def transport(payload, key, timeout_ms):
            calls.append((payload, key, timeout_ms))
            return success_result()

        player = JevPlayer(
            JevPlayerConfig(enabled=True, max_requests_per_run=2),
            transport=transport,
        )
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            result = player.choose(request(), IDS)
        self.assertTrue(result.is_jev)
        self.assertEqual(result.selected_id, "c00")
        self.assertEqual(calls[0][1], "test-key")
        self.assertEqual(calls[0][2], 1000)

        def failure(payload, key, timeout_ms):
            return WorkerResult("timeout")

        player = JevPlayer(
            JevPlayerConfig(enabled=True, consecutive_failures_to_latch=2),
            transport=failure,
        )
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            first = player.choose(request(), IDS)
            second = player.choose(request(), IDS)
        self.assertEqual(first.status, "timeout")
        self.assertEqual(second.status, "timeout")
        self.assertTrue(second.failure_latched)
        self.assertEqual(player.choose(request(), IDS).status, "failure_latched")

    def test_missing_key_latches_before_request(self):
        calls = []

        def transport(payload, key, timeout_ms):
            calls.append(1)
            return success_result()

        player = JevPlayer(JevPlayerConfig(enabled=True), transport=transport)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(player.choose(request(), IDS).status, "auth_error")
        self.assertEqual(len(calls), 0)

    def test_budget_stops_new_requests(self):
        calls = []

        def transport(payload, key, timeout_ms):
            calls.append(1)
            return success_result()

        player = JevPlayer(
            JevPlayerConfig(enabled=True, max_requests_per_run=1),
            transport=transport,
        )
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            self.assertEqual(player.choose(request(), IDS).status, "ok")
            self.assertEqual(player.choose(request(), IDS).status, "budget_exhausted")
        self.assertTrue(player.failure_latched)

    def test_invalid_success_from_injected_transport_is_not_accepted(self):
        invalid = WorkerResult(
            status="ok",
            selected_id="c00",
            confidence=0.5,
            probabilities={"c00": 0.1, "c01": 0.9},
            model=MODEL,
            usage={"input_tokens": 1, "output_tokens": 1},
        )
        player = JevPlayer(
            JevPlayerConfig(enabled=True, consecutive_failures_to_latch=1),
            transport=lambda payload, key, timeout_ms: invalid,
        )
        with patch.dict(os.environ, {"TYPESAFE_API_KEY": "test-key"}):
            result = player.choose(request(), IDS)
        self.assertEqual(result.status, "invalid_response")
        self.assertTrue(result.failure_latched)


if __name__ == "__main__":
    unittest.main()

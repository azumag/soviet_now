"""Bounded, fail-closed worker for the JEV sorengame choice API.

This module deliberately keeps network and process handling outside the game
runner.  The parent process owns the deadline and reaps the child; the child
gets the API key only through its environment and never returns response
headers or body text to the caller.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

try:  # package import when used by the game
    from .jev_player_contract import (
        ENDPOINT,
        MAX_REQUEST_BYTES,
        MODEL,
        JevContractError,
        dumps,
        strict_json,
        validate_choice,
    )
except ImportError:  # direct child invocation from this file's directory
    from jev_player_contract import (  # type: ignore
        ENDPOINT,
        MAX_REQUEST_BYTES,
        MODEL,
        JevContractError,
        dumps,
        strict_json,
        validate_choice,
    )


API_KEY_ENV = "TYPESAFE_API_KEY"
MAX_RESPONSE_BYTES = 64 * 1024
MAX_CHILD_STDOUT_BYTES = MAX_RESPONSE_BYTES
WORKER_ARG = "--jev-http-worker"


class JevWorkerError(RuntimeError):
    """A stable, non-secret worker failure."""

    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        super().__init__(message or status)


@dataclass(frozen=True)
class WorkerConfig:
    """Boundaries for one JEV request.

    The values are intentionally small enough that a timed-out JEV request
    cannot hold a drop opportunity indefinitely.  `decision_budget_ms` is the
    parent-side wall-clock budget and `http_timeout_ms` is the child-side
    socket budget.
    """

    decision_budget_ms: int = 1500
    http_timeout_ms: int = 1000
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_response_bytes: int = MAX_RESPONSE_BYTES

    def __post_init__(self) -> None:
        for name in (
            "decision_budget_ms",
            "http_timeout_ms",
            "max_request_bytes",
            "max_response_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"invalid {name}")
        if self.http_timeout_ms > self.decision_budget_ms:
            raise ValueError("http timeout exceeds decision budget")
        if self.max_request_bytes > MAX_REQUEST_BYTES:
            raise ValueError("request limit exceeds contract limit")
        if self.max_response_bytes > MAX_RESPONSE_BYTES:
            raise ValueError("response limit exceeds worker limit")


@dataclass(frozen=True)
class WorkerResult:
    status: str
    selected_id: str | None = None
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None
    model: str | None = None
    usage: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": self.status}
        if self.selected_id is not None:
            result["selected_id"] = self.selected_id
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.probabilities is not None:
            result["probabilities"] = dict(self.probabilities)
        if self.model is not None:
            result["model"] = self.model
        if self.usage is not None:
            result["usage"] = dict(self.usage)
        return result


def _choice_result_to_worker_result(choice: Any) -> WorkerResult:
    return WorkerResult(
        status="ok",
        selected_id=choice.selected_id,
        confidence=choice.confidence,
        probabilities=choice.probabilities,
        model=choice.resolved_model,
        usage=choice.usage,
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        return None


def _request_candidate_ids(request: Mapping[str, Any]) -> list[str]:
    state = request.get("state")
    if not isinstance(state, Mapping):
        raise JevContractError("invalid_request", "state must be an object")
    candidates = state.get("candidates")
    if not isinstance(candidates, list):
        raise JevContractError("invalid_request", "candidates must be a list")
    ids: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping) or not isinstance(candidate.get("candidate_id"), str):
            raise JevContractError("invalid_request", "candidate id missing")
        ids.append(candidate["candidate_id"])
    return ids


def _classify_http_status(status: int) -> str:
    if status in (401, 403):
        return "auth_error"
    if status == 429:
        return "rate_limited"
    if status == 529:
        return "overloaded"
    if 500 <= status <= 599:
        return "server_error"
    return "invalid_response"


def _read_limited(response: Any, limit: int) -> bytes:
    body = response.read(limit + 1)
    if len(body) > limit:
        raise JevWorkerError("response_too_large")
    return body


def http_worker(request_payload: Mapping[str, Any], api_key: str, timeout_ms: int) -> WorkerResult:
    """Perform one fixed-endpoint request and return only validated data."""

    if not isinstance(api_key, str) or not api_key:
        return WorkerResult("auth_error")
    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        return WorkerResult("invalid_request")

    try:
        expected_ids = _request_candidate_ids(request_payload)
        payload = dumps(dict(request_payload)).encode("utf-8")
        if len(payload) > MAX_REQUEST_BYTES:
            return WorkerResult("request_too_large")
        request = urllib.request.Request(
            ENDPOINT,
            data=payload,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirect(),
        )
        with opener.open(request, timeout=timeout_ms / 1000.0) as response:
            if getattr(response, "status", None) != 200:
                return WorkerResult(_classify_http_status(int(response.status)))
            raw_response = _read_limited(response, MAX_RESPONSE_BYTES)
        response_obj = strict_json(raw_response)
        if not isinstance(response_obj, Mapping):
            return WorkerResult("invalid_response")
        choice = validate_choice(
            response_obj,
            expected_ids,
            expected_model=str(request_payload.get("model", MODEL)),
        )
        return _choice_result_to_worker_result(choice)
    except JevContractError as exc:
        return WorkerResult(exc.reason)
    except urllib.error.HTTPError as exc:
        return WorkerResult(_classify_http_status(int(exc.code)))
    except (TimeoutError, socket.timeout):
        return WorkerResult("timeout")
    except urllib.error.URLError:
        return WorkerResult("network_error")
    except JevWorkerError as exc:
        return WorkerResult(exc.status)
    except json.JSONDecodeError:
        return WorkerResult("invalid_response")
    except (OSError, ValueError, TypeError):
        return WorkerResult("network_error")


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


def bounded_process(
    argv: Sequence[str],
    input_bytes: bytes,
    timeout_ms: int,
    *,
    env: Mapping[str, str] | None = None,
    max_output_bytes: int = MAX_CHILD_STDOUT_BYTES,
) -> bytes:
    """Run a child in its own process group and always reap it."""

    if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or timeout_ms <= 0:
        raise JevWorkerError("invalid_request")
    if len(input_bytes) > MAX_REQUEST_BYTES:
        raise JevWorkerError("request_too_large")
    if max_output_bytes <= 0 or max_output_bytes > MAX_RESPONSE_BYTES:
        raise JevWorkerError("invalid_request")

    child_env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
    if env:
        child_env.update({str(key): str(value) for key, value in env.items()})
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=child_env,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout_ms / 1000.0
        try:
            stdout, _ = process.communicate(input=input_bytes, timeout=max(0.001, timeout_ms / 1000.0))
        except subprocess.TimeoutExpired:
            _kill_process_group(process)
            stdout, _ = process.communicate(timeout=1.0)
            raise JevWorkerError("timeout")
        if time.monotonic() > deadline:
            raise JevWorkerError("timeout")
        if len(stdout) > max_output_bytes:
            raise JevWorkerError("response_too_large")
        if process.returncode != 0:
            raise JevWorkerError("worker_exit")
        return stdout
    except JevWorkerError:
        raise
    except subprocess.TimeoutExpired:
        if process is not None:
            _kill_process_group(process)
            try:
                process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
        raise JevWorkerError("timeout")
    except (OSError, ValueError):
        raise JevWorkerError("worker_start")
    finally:
        if process is not None and process.poll() is None:
            _kill_process_group(process)
            try:
                process.communicate(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def _child_main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != WORKER_ARG:
        return 2
    raw_request = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
    if len(raw_request) > MAX_REQUEST_BYTES:
        result = WorkerResult("request_too_large")
    else:
        try:
            request = strict_json(raw_request)
            if not isinstance(request, Mapping):
                result = WorkerResult("invalid_request")
            else:
                result = http_worker(
                    request,
                    os.environ.get(API_KEY_ENV, ""),
                    int(os.environ.get("JEV_HTTP_TIMEOUT_MS", "1000")),
                )
        except (JevContractError, ValueError, TypeError, json.JSONDecodeError):
            result = WorkerResult("invalid_request")
    sys.stdout.write(dumps(result.to_dict()))
    sys.stdout.flush()
    return 0


def request_once(
    request_payload: Mapping[str, Any],
    config: WorkerConfig,
    *,
    api_key: str | None = None,
    transport: Callable[[Mapping[str, Any], str, int], WorkerResult] | None = None,
) -> WorkerResult:
    """Execute one request, using an injectable transport for tests."""

    if transport is not None:
        try:
            result = transport(request_payload, api_key or "", config.http_timeout_ms)
            if not isinstance(result, WorkerResult):
                return WorkerResult("invalid_response")
            return result
        except TimeoutError:
            return WorkerResult("timeout")
        except Exception:
            return WorkerResult("worker_error")

    if not api_key:
        return WorkerResult("auth_error")
    try:
        raw_request = dumps(dict(request_payload)).encode("utf-8")
        child_path = os.path.abspath(__file__)
        raw_response = bounded_process(
            [sys.executable, "-I", child_path, WORKER_ARG],
            raw_request,
            config.decision_budget_ms,
            env={
                API_KEY_ENV: api_key,
                "JEV_HTTP_TIMEOUT_MS": str(config.http_timeout_ms),
            },
            max_output_bytes=config.max_response_bytes,
        )
        response = strict_json(raw_response)
        if not isinstance(response, Mapping) or not isinstance(response.get("status"), str):
            return WorkerResult("invalid_response")
        allowed = {
            "status",
            "selected_id",
            "confidence",
            "probabilities",
            "model",
            "usage",
        }
        if set(response) - allowed:
            return WorkerResult("invalid_response")
        if response["status"] not in {
            "ok",
            "auth_error",
            "rate_limited",
            "overloaded",
            "server_error",
            "invalid_response",
            "network_error",
            "timeout",
            "response_too_large",
            "request_too_large",
            "invalid_request",
            "worker_exit",
            "worker_start",
            "worker_error",
        }:
            return WorkerResult("invalid_response")
        return WorkerResult(
            status=response["status"],
            selected_id=response.get("selected_id"),
            confidence=response.get("confidence"),
            probabilities=response.get("probabilities"),
            model=response.get("model"),
            usage=response.get("usage"),
        )
    except JevContractError:
        return WorkerResult("invalid_response")
    except JevWorkerError as exc:
        return WorkerResult(exc.status)
    except (ValueError, TypeError, OSError, json.JSONDecodeError):
        return WorkerResult("invalid_response")


if __name__ == "__main__":  # pragma: no cover - exercised through bounded_process
    raise SystemExit(_child_main())

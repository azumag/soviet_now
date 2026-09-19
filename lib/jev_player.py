"""Run-level supervisor for JEV choices.

The player only returns a validated candidate id or an explicit failure.  It
does not know how to operate the game, apply a fallback, or mutate normal
strategy history.  Those responsibilities belong to the later bridge/runner
changes.
"""

from __future__ import annotations

import math
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .jev_player_contract import MODEL
from .jev_player_worker import API_KEY_ENV, WorkerConfig, WorkerResult, request_once


IMMEDIATE_LATCH_STATUSES = frozenset(
    {
        "auth_error",
        "rate_limited",
        "overloaded",
        "model_mismatch",
        "budget_exhausted",
        "request_too_large",
    }
)


@dataclass(frozen=True)
class JevPlayerConfig:
    enabled: bool = False
    model: str = MODEL
    worker: WorkerConfig = WorkerConfig()
    max_requests_per_run: int = 500
    consecutive_failures_to_latch: int = 3

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be bool")
        if self.model != MODEL:
            raise ValueError("model mismatch")
        if isinstance(self.max_requests_per_run, bool) or not isinstance(self.max_requests_per_run, int):
            raise ValueError("invalid request budget")
        if self.max_requests_per_run <= 0:
            raise ValueError("invalid request budget")
        if (
            isinstance(self.consecutive_failures_to_latch, bool)
            or not isinstance(self.consecutive_failures_to_latch, int)
            or self.consecutive_failures_to_latch <= 0
        ):
            raise ValueError("invalid failure latch")

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"invalid {name}") from exc
        return value

    @classmethod
    def from_env(cls) -> "JevPlayerConfig":
        enabled = os.environ.get("JEV_PLAYER_ENABLED", "0") == "1"
        decision_budget_ms = cls._env_int("JEV_DECISION_BUDGET_MS", 1500)
        http_timeout_ms = cls._env_int("JEV_HTTP_TIMEOUT_MS", 1000)
        max_requests = cls._env_int("JEV_MAX_REQUESTS_PER_RUN", 500)
        latch_after = cls._env_int("JEV_FAILURE_LATCH_AFTER", 3)
        return cls(
            enabled=enabled,
            worker=WorkerConfig(
                decision_budget_ms=decision_budget_ms,
                http_timeout_ms=http_timeout_ms,
            ),
            max_requests_per_run=max_requests,
            consecutive_failures_to_latch=latch_after,
        )


@dataclass(frozen=True)
class PlayerOutcome:
    status: str
    source: str
    selected_id: str | None = None
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None
    model: str | None = None
    usage: Mapping[str, Any] | None = None
    request_count: int = 0
    consecutive_failures: int = 0
    failure_latched: bool = False
    elapsed_ms: int = 0

    @property
    def is_jev(self) -> bool:
        return self.source == "jev" and self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "status": self.status,
            "source": self.source,
            "request_count": self.request_count,
            "consecutive_failures": self.consecutive_failures,
            "failure_latched": self.failure_latched,
            "elapsed_ms": self.elapsed_ms,
        }
        for name in ("selected_id", "confidence", "probabilities", "model", "usage"):
            value = getattr(self, name)
            if value is not None:
                result[name] = dict(value) if isinstance(value, Mapping) else value
        return result


Transport = Callable[[Mapping[str, Any], str, int], WorkerResult]


class JevPlayer:
    """One bounded JEV run with at most one request in flight."""

    def __init__(self, config: JevPlayerConfig, *, transport: Transport | None = None) -> None:
        self.config = config
        self._transport = transport
        self._request_count = 0
        self._consecutive_failures = 0
        self._failure_latched = False
        self._lock = threading.Lock()

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def failure_latched(self) -> bool:
        return self._failure_latched

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def _outcome(self, status: str, source: str = "fallback", started: float | None = None, **kwargs: Any) -> PlayerOutcome:
        elapsed_ms = 0 if started is None else max(0, int((time.monotonic() - started) * 1000))
        return PlayerOutcome(
            status=status,
            source=source,
            request_count=self._request_count,
            consecutive_failures=self._consecutive_failures,
            failure_latched=self._failure_latched,
            elapsed_ms=elapsed_ms,
            **kwargs,
        )

    def _latch(self) -> None:
        self._failure_latched = True

    @staticmethod
    def _valid_success(result: WorkerResult, expected_ids: list[str], expected_model: str) -> bool:
        if result.status != "ok":
            return False
        if result.model != expected_model or result.selected_id not in expected_ids:
            return False
        if isinstance(result.confidence, bool) or not isinstance(result.confidence, (int, float)):
            return False
        if not math.isfinite(float(result.confidence)) or not 0 <= float(result.confidence) <= 1:
            return False
        probabilities = result.probabilities
        if not isinstance(probabilities, Mapping) or set(probabilities) != set(expected_ids):
            return False
        numeric: dict[str, float] = {}
        for candidate_id in expected_ids:
            value = probabilities[candidate_id]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return False
            value_float = float(value)
            if not math.isfinite(value_float) or not 0 <= value_float <= 1:
                return False
            numeric[candidate_id] = value_float
        if abs(sum(numeric.values()) - 1.0) > 1e-6:
            return False
        max_probability = max(numeric.values())
        return numeric[result.selected_id] == max_probability

    def choose(self, request_payload: Mapping[str, Any], expected_ids: list[str]) -> PlayerOutcome:
        """Request a choice; never perform a game action or choose a fallback."""

        started = time.monotonic()
        if not self.config.enabled:
            return self._outcome("disabled", started=started)
        if self._failure_latched:
            return self._outcome("failure_latched", started=started)
        if not self._lock.acquire(blocking=False):
            return self._outcome("inflight_limit", started=started)
        try:
            if self._request_count >= self.config.max_requests_per_run:
                self._latch()
                return self._outcome("budget_exhausted", started=started)
            if not expected_ids or len(expected_ids) > 25:
                self._latch()
                return self._outcome("invalid_request", started=started)
            if request_payload.get("model") != self.config.model:
                self._latch()
                return self._outcome("model_mismatch", started=started)
            api_key = os.environ.get(API_KEY_ENV, "")
            if not api_key:
                self._latch()
                return self._outcome("auth_error", started=started)

            self._request_count += 1
            result = request_once(
                request_payload,
                self.config.worker,
                api_key=api_key,
                transport=self._transport,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if elapsed_ms > self.config.worker.decision_budget_ms:
                result = WorkerResult("timeout")
            if self._valid_success(result, expected_ids, self.config.model):
                self._consecutive_failures = 0
                return self._outcome(
                    "ok",
                    source="jev",
                    started=started,
                    selected_id=result.selected_id,
                    confidence=float(result.confidence),
                    probabilities=dict(result.probabilities or {}),
                    model=result.model,
                    usage=dict(result.usage) if isinstance(result.usage, Mapping) else None,
                )

            status = result.status if isinstance(result.status, str) else "worker_error"
            if status == "ok":
                status = "invalid_response"
            self._consecutive_failures += 1
            if status in IMMEDIATE_LATCH_STATUSES or self._consecutive_failures >= self.config.consecutive_failures_to_latch:
                self._latch()
            return self._outcome(status, started=started)
        finally:
            self._lock.release()

"""The JEV policy branch for the legacy strategy runner.

This adapter owns only observation normalization, candidate/request assembly,
and evidence joins.  It intentionally has no command-file or browser access;
the caller remains the single owner that dispatches a selected candidate.
"""

from __future__ import annotations

import datetime as _dt
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .jev_player import JevPlayer, JevPlayerConfig, PlayerOutcome
from .jev_player_candidates import build_candidates
from .jev_player_contract import (
    CANDIDATE_VERSION,
    DEFAULT_RUBRIC,
    MODEL,
    Candidate,
    JevContractError,
    build_request,
    normalize_observation,
)
from .jev_player_evidence import EvidenceError, JevEvidence


POLICY_EXISTING = "existing"
POLICY_JEV = "jev"
IDENTITY_FIELD_NAMES = (
    "run_id",
    "game_instance_id",
    "game_generation",
    "player_generation",
    "opportunity_seq",
    "frame_seq",
    "observed_at",
    "drop_piece_id",
    "board_bounds",
)


class JevRunnerError(ValueError):
    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(self.reason)


@dataclass(frozen=True)
class JevSelection:
    decision: dict[str, Any] | None
    outcome: PlayerOutcome
    candidate: Candidate | None = None
    observation: Any | None = None
    candidates: tuple[Candidate, ...] = ()
    failure_reason: str | None = None

    @property
    def applied(self) -> bool:
        return self.decision is not None and self.outcome.is_jev and self.candidate is not None


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_uuid(value: Any, field: str) -> str:
    if not isinstance(value, str):
        raise JevRunnerError(f"missing_{field}")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise JevRunnerError(f"invalid_{field}") from exc
    if str(parsed) != value or parsed.int == 0:
        raise JevRunnerError(f"invalid_{field}")
    return value


def identity_from_game_state(game_state: Mapping[str, Any]) -> dict[str, Any]:
    """Read bridge-provided identity; never derive it from score or turn."""

    if not isinstance(game_state, Mapping):
        raise JevRunnerError("invalid_game_state")
    raw = game_state.get("jev_identity")
    if not isinstance(raw, Mapping):
        raise JevRunnerError("identity_missing")
    identity: dict[str, Any] = {}
    for name in IDENTITY_FIELD_NAMES:
        if name not in raw:
            raise JevRunnerError(f"missing_{name}")
        identity[name] = raw[name]
    _canonical_uuid(identity["run_id"], "run_id")
    _canonical_uuid(identity["game_instance_id"], "game_instance_id")
    for name in ("game_generation", "player_generation", "frame_seq", "drop_piece_id"):
        value = identity[name]
        if type(value) is not int or value < 0:
            raise JevRunnerError(f"invalid_{name}")
    if type(identity["opportunity_seq"]) is not int or identity["opportunity_seq"] < 1:
        raise JevRunnerError("invalid_opportunity_seq")
    if not isinstance(identity["observed_at"], str) or not identity["observed_at"]:
        raise JevRunnerError("invalid_observed_at")
    if not isinstance(identity["board_bounds"], Mapping):
        raise JevRunnerError("missing_board_bounds")
    return identity


def _evidence_root() -> Path:
    return Path(os.environ.get("JEV_EVIDENCE_ROOT", "tmp/jev_player/runs"))


class JevRunner:
    """Build and apply one JEV choice without calling the legacy strategy."""

    def __init__(
        self,
        *,
        player: JevPlayer | None = None,
        evidence: JevEvidence | None = None,
        evidence_root: str | os.PathLike[str] | None = None,
        command_x_min: int = 410,
        command_x_max: int = 830,
    ) -> None:
        self.player = player or JevPlayer(JevPlayerConfig.from_env())
        self.command_x_min = command_x_min
        self.command_x_max = command_x_max
        self.evidence = evidence
        self.evidence_incomplete = False
        if self.evidence is None:
            # The game loop exports SOREN_JEV_RUN_ID (see
            # game_lifecycle_load_player_policy).  Reading only JEV_RUN_ID made
            # production runners build no ledger at all (evidence_incomplete).
            run_id = os.environ.get("SOREN_JEV_RUN_ID") or os.environ.get("JEV_RUN_ID", "")
            if run_id:
                try:
                    self.evidence = JevEvidence(
                        evidence_root or _evidence_root(),
                        run_id,
                        config={
                            "model": MODEL,
                            "candidate_version": CANDIDATE_VERSION,
                            "decision_budget_ms": self.player.config.worker.decision_budget_ms,
                            "http_timeout_ms": self.player.config.worker.http_timeout_ms,
                            "max_requests_per_run": self.player.config.max_requests_per_run,
                        },
                    )
                except (EvidenceError, OSError, ValueError):
                    self.evidence_incomplete = True

    def _record_decision(
        self,
        identity: Mapping[str, Any],
        selection: JevSelection,
    ) -> None:
        if self.evidence is None:
            self.evidence_incomplete = True
            return
        try:
            outcome = selection.outcome
            self.evidence.record_decision(
                game_instance_id=identity["game_instance_id"],
                opportunity_seq=identity["opportunity_seq"],
                player_generation=identity["player_generation"],
                candidate_count=len(selection.candidates),
                status=outcome.status,
                source="jev" if outcome.is_jev else "fallback",
                selected_id=outcome.selected_id if outcome.is_jev else None,
                confidence=outcome.confidence if outcome.is_jev else None,
                fallback_reason=selection.failure_reason,
            )
        except (EvidenceError, OSError, TypeError, ValueError):
            self.evidence_incomplete = True

    def choose(self, game_state: Mapping[str, Any], analysis: Mapping[str, Any]) -> JevSelection:
        try:
            identity = identity_from_game_state(game_state)
            observation = normalize_observation(game_state, identity)
            geometry = {
                "drop_x_min": observation.board_bounds["drop_x_min"],
                "drop_x_max": observation.board_bounds["drop_x_max"],
                "command_x_min": self.command_x_min,
                "command_x_max": self.command_x_max,
                "analysis_results": list(analysis.get("results", [])) if isinstance(analysis, Mapping) else [],
            }
            candidates = build_candidates(observation, geometry)
            request = build_request(observation, candidates, DEFAULT_RUBRIC, MODEL)
            outcome = self.player.choose(
                request,
                [candidate.candidate_id for candidate in candidates],
            )
            selected = None
            decision = None
            if outcome.is_jev:
                selected = next(
                    candidate for candidate in candidates if candidate.candidate_id == outcome.selected_id
                )
                # This reason is local runner metadata only; it is never put
                # into the JEV request or used by the selection contract.
                decision = {
                    "x": selected.x,
                    "reason": f"JEV_CANDIDATE_{selected.candidate_id}",
                    "jev_candidate_id": selected.candidate_id,
                    "jev_command_x": selected.command_x,
                }
            selection = JevSelection(
                decision=decision,
                outcome=outcome,
                candidate=selected,
                observation=observation,
                candidates=tuple(candidates),
                failure_reason=None if outcome.is_jev else outcome.status,
            )
            self._record_decision(identity, selection)
            return selection
        except (JevRunnerError, JevContractError, EvidenceError, KeyError, TypeError, ValueError) as exc:
            # No request is issued when identity/contract preflight fails.
            outcome = PlayerOutcome(
                status=getattr(exc, "reason", "invalid_observation"),
                source="fallback",
                failure_latched=True,
            )
            selection = JevSelection(
                decision=None,
                outcome=outcome,
                failure_reason=outcome.status,
            )
            self.evidence_incomplete = self.evidence_incomplete or self.evidence is None
            return selection

    def record_dispatch(self, identity: Mapping[str, Any], candidate: Candidate) -> None:
        if self.evidence is None:
            self.evidence_incomplete = True
            return
        try:
            self.evidence.record_game_event(
                "action_dispatched",
                game_instance_id=identity["game_instance_id"],
                payload={
                    "opportunity_seq": identity["opportunity_seq"],
                    "player_generation": identity["player_generation"],
                    "candidate_id": candidate.candidate_id,
                    "selected_x": candidate.x,
                },
            )
        except (EvidenceError, OSError, TypeError, ValueError):
            self.evidence_incomplete = True

    def record_accepted(self, identity: Mapping[str, Any], candidate: Candidate, ack_status: str) -> None:
        if self.evidence is None:
            self.evidence_incomplete = True
            return
        try:
            self.evidence.record_game_event(
                "action_accepted",
                game_instance_id=identity["game_instance_id"],
                payload={
                    "opportunity_seq": identity["opportunity_seq"],
                    "player_generation": identity["player_generation"],
                    "candidate_id": candidate.candidate_id,
                    "executed_x": candidate.x,
                    "command_status": ack_status,
                },
            )
        except (EvidenceError, OSError, TypeError, ValueError):
            self.evidence_incomplete = True

    def finalize(self, summary: Mapping[str, Any]) -> None:
        if self.evidence is None:
            self.evidence_incomplete = True
            return
        try:
            body = dict(summary)
            body["evidence_incomplete"] = bool(self.evidence_incomplete)
            self.evidence.finalize(body)
        except (EvidenceError, OSError, TypeError, ValueError):
            self.evidence_incomplete = True


__all__ = [
    "JevRunner",
    "JevRunnerError",
    "JevSelection",
    "POLICY_EXISTING",
    "POLICY_JEV",
    "identity_from_game_state",
]

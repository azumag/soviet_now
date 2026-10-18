"""Deterministic, score-free Jev candidate generation for sorengame."""

from __future__ import annotations

import math
from typing import Any, Mapping

from .jev_player_contract import (
    CANDIDATE_VERSION,
    Candidate,
    JevContractError,
    Observation,
)


_CANDIDATE_COUNT = 25
_PREDICTION_KEYS = (
    "predicted_landing_y",
    "predicted_first_contact_id",
    "predicted_first_contact_type",
    "predicted_top_y",
    "predicted_merge_targets",
    "predicted_deadline_margin",
)


def _finite(value: Any, *, field: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise JevContractError(f"invalid_{field}")
    return float(value)


def _command_coordinate(x: float, x_min: float, x_max: float, command_min: int, command_max: int) -> int:
    ratio = (x - x_min) / (x_max - x_min)
    return int(ratio * (command_max - command_min) + command_min)


def _result_for_x(results: Any, x: float) -> Mapping[str, Any] | None:
    if results is None:
        return None
    if not isinstance(results, list):
        raise JevContractError("invalid_analysis_results")
    best = None
    best_distance = None
    for result in results:
        if not isinstance(result, Mapping) or "x" not in result:
            raise JevContractError("invalid_analysis_results")
        result_x = _finite(result["x"], field="analysis_x")
        distance = abs(result_x - x)
        if best_distance is None or distance < best_distance:
            best = result
            best_distance = distance
    if best is None or best_distance is None or best_distance > 0.011:
        return None
    return best


def _prediction(observation: Observation, result: Mapping[str, Any] | None) -> dict[str, Any]:
    prediction: dict[str, Any] = {
        "predicted_landing_y": None,
        "predicted_first_contact_id": None,
        "predicted_first_contact_type": None,
        "predicted_top_y": None,
        "predicted_merge_targets": [],
        "predicted_deadline_margin": None,
    }
    if result is None:
        return prediction

    def optional_number(name: str) -> float | None:
        value = result.get(name)
        return None if value is None else _finite(value, field=f"prediction_{name}")

    prediction["predicted_landing_y"] = optional_number("landing_y")
    prediction["predicted_top_y"] = optional_number("top_y_after_drop")
    margin = result.get("deadline_margin")
    if margin is None and result.get("deadline_y") is not None and result.get("risk_top_y_after_drop") is not None:
        margin = _finite(result["deadline_y"], field="prediction_deadline_y") - _finite(
            result["risk_top_y_after_drop"], field="prediction_risk_top_y"
        )
    prediction["predicted_deadline_margin"] = (
        None if margin is None else _finite(margin, field="prediction_deadline_margin")
    )

    contact_id = result.get("landing_hit_id")
    if contact_id is not None:
        if type(contact_id) is not int or contact_id < 0:
            raise JevContractError("invalid_prediction_contact_id")
        prediction["predicted_first_contact_id"] = contact_id
        piece_types = {piece["id"]: piece["type"] for piece in observation.pieces}
        prediction["predicted_first_contact_type"] = piece_types.get(contact_id)

    merges = result.get("merges", [])
    if merges is None:
        merges = []
    if not isinstance(merges, list):
        raise JevContractError("invalid_prediction_merges")
    targets: list[int] = []
    for merge in merges:
        if not isinstance(merge, Mapping):
            raise JevContractError("invalid_prediction_merges")
        target_id = merge.get("id")
        grade = merge.get("grade")
        if grade in ("DIRECT", "NEAR"):
            if type(target_id) is not int or target_id < 0:
                raise JevContractError("invalid_prediction_merge_target")
            if target_id not in targets:
                targets.append(target_id)
    prediction["predicted_merge_targets"] = targets
    return prediction


def build_candidates(
    observation: Observation,
    geometry_config: Mapping[str, Any],
) -> tuple[Candidate, ...]:
    """Build uniformly spaced legal X candidates and dedupe input coordinates.

    ``analysis_results`` is an optional result list from the existing analyzer
    evaluated at the same X values.  It contributes geometry hints only; no
    analyzer score, strategy reason, or recommendation is copied.
    """

    if not isinstance(observation, Observation):
        raise JevContractError("invalid_observation")
    if not isinstance(geometry_config, Mapping):
        raise JevContractError("invalid_geometry_config")

    def bound(name: str, fallback: Any = None) -> float:
        value = geometry_config.get(name, fallback)
        if value is None:
            raise JevContractError(f"missing_{name}")
        return _finite(value, field=name)

    x_min = bound("drop_x_min", observation.board_bounds.get("drop_x_min"))
    x_max = bound("drop_x_max", observation.board_bounds.get("drop_x_max"))
    if x_min >= x_max:
        raise JevContractError("invalid_geometry_config")
    command_min = geometry_config.get("command_x_min")
    command_max = geometry_config.get("command_x_max")
    if type(command_min) is not int or type(command_max) is not int or command_min >= command_max:
        raise JevContractError("invalid_command_bounds")

    results = geometry_config.get("analysis_results")
    candidates: list[Candidate] = []
    seen_command_x: set[int] = set()
    span = x_max - x_min
    for index in range(_CANDIDATE_COUNT):
        x = x_min + span * index / (_CANDIDATE_COUNT - 1)
        if index == 0:
            x = x_min
        elif index == _CANDIDATE_COUNT - 1:
            x = x_max
        x = round(x, 6)
        command_x = _command_coordinate(x, x_min, x_max, command_min, command_max)
        if command_x in seen_command_x:
            continue
        seen_command_x.add(command_x)
        candidates.append(
            Candidate(
                candidate_id=f"c{len(candidates):02d}",
                x=x,
                command_x=command_x,
                prediction=_prediction(observation, _result_for_x(results, x)),
            )
        )

    if not candidates:
        raise JevContractError("input_limit")
    if len(candidates) > _CANDIDATE_COUNT:
        raise JevContractError("candidate_limit")
    for candidate in candidates:
        if set(candidate.prediction) != set(_PREDICTION_KEYS):
            raise JevContractError("invalid_prediction")
    return tuple(candidates)


__all__ = ["build_candidates", "CANDIDATE_VERSION"]

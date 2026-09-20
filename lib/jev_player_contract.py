"""Pure contracts for the opt-in Jev sorengame player.

This module deliberately has no filesystem, process, network, or game-control
side effects.  It turns the existing ``game_state.json`` shape into a small
allowlisted observation and validates the choice response used by the later
bounded worker.  Keeping this boundary pure makes it possible to test the
player contract without an API key or a running bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as _datetime
import json
import math
import re
from typing import Any, Mapping, Sequence
import uuid


SCHEMA_VERSION = 1
RULES_VERSION = "sorengame-v1"
CANDIDATE_VERSION = "uniform25-v1"
MODEL = "jev-1.13.0"
ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MAX_CANDIDATES = 25
MAX_REQUEST_BYTES = 24 * 1024
MAX_PIECES = 256
MAX_PREVIEW = 2

_MODEL_RE = re.compile(r"jev-[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\Z")
_VALID_PHASES = frozenset(("MOVE", "GAMEOVER", "STOP"))
_PIECE_NUMBER_FIELDS = (
    "r",
    "x",
    "y",
    "rx",
    "ry",
    "angle",
    "vx",
    "vy",
    "av",
    "redLineTime",
)
_PIECE_BOOL_FIELDS = ("awake",)
_PREDICTION_KEYS = (
    "predicted_landing_y",
    "predicted_first_contact_id",
    "predicted_first_contact_type",
    "predicted_top_y",
    "predicted_merge_targets",
    "predicted_deadline_margin",
)


class JevContractError(ValueError):
    """A stable, non-secret contract failure reason."""

    def __init__(self, reason: str):
        self.reason = str(reason)
        super().__init__(self.reason)


def dumps(value: Any) -> str:
    """Serialize contract data without allowing non-finite JSON numbers."""

    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def strict_json(raw: str | bytes) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite constants."""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise JevContractError("duplicate_json_key")
            result[key] = value
        return result

    def invalid_constant(_value: str) -> None:
        raise JevContractError("nonfinite_number")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
    except JevContractError:
        raise
    except (TypeError, ValueError, UnicodeError) as exc:
        raise JevContractError("invalid_json") from exc


def _number(value: Any, *, field: str, minimum: float | None = None) -> float:
    if type(value) not in (int, float):
        raise JevContractError(f"invalid_{field}")
    converted = float(value)
    if not math.isfinite(converted):
        raise JevContractError(f"invalid_{field}")
    if minimum is not None and converted < minimum:
        raise JevContractError(f"invalid_{field}")
    return converted


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise JevContractError(f"invalid_{field}")
    return value


def _text(value: Any, *, field: str, nonempty: bool = True) -> str:
    if not isinstance(value, str) or (nonempty and not value):
        raise JevContractError(f"invalid_{field}")
    return value


def _uuid_text(value: Any, *, field: str) -> str:
    text = _text(value, field=field)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError) as exc:
        raise JevContractError(f"invalid_{field}") from exc
    # Canonical lower-case UUIDs avoid two spellings of the same identity in
    # ledger joins and command deduplication.
    if str(parsed) != text:
        raise JevContractError(f"invalid_{field}")
    if parsed.int == 0:
        raise JevContractError(f"invalid_{field}")
    return text


def _timestamp(value: Any) -> str:
    text = _text(value, field="observed_at")
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = _datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise JevContractError("invalid_observed_at") from exc
    if parsed.tzinfo is None:
        raise JevContractError("invalid_observed_at")
    return text


def _mapping(value: Any, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise JevContractError(f"invalid_{field}")
    return value


def _piece(value: Any, *, field: str, piece_id: Any = None) -> dict[str, Any]:
    source = _mapping(value, field=field)
    if "type" not in source or "r" not in source:
        raise JevContractError(f"missing_{field}")
    result: dict[str, Any] = {
        "type": _integer(source["type"], field=f"{field}_type", minimum=1),
        "r": _number(source["r"], field=f"{field}_r", minimum=0.0),
    }
    if result["r"] <= 0:
        raise JevContractError(f"invalid_{field}_r")

    raw_id = source.get("id", piece_id)
    if raw_id is not None:
        result["id"] = _integer(raw_id, field=f"{field}_id")
        if piece_id is not None and result["id"] != piece_id:
            raise JevContractError("drop_piece_identity_mismatch")

    for name in _PIECE_NUMBER_FIELDS:
        if name in source and name not in result:
            result[name] = _number(source[name], field=f"{field}_{name}")
    for name in _PIECE_BOOL_FIELDS:
        if name in source:
            if type(source[name]) is not bool:
                raise JevContractError(f"invalid_{field}_{name}")
            result[name] = source[name]
    return result


def _board_bounds(raw: Any, identity: Mapping[str, Any]) -> dict[str, float]:
    source = raw if isinstance(raw, Mapping) else identity.get("board_bounds")
    if not isinstance(source, Mapping):
        raise JevContractError("missing_board_bounds")

    def get_bound(primary: str, alias: str | None = None) -> Any:
        if primary in source:
            return source[primary]
        if alias is not None and alias in source:
            return source[alias]
        return None

    minimum = get_bound("drop_x_min", "x_min")
    maximum = get_bound("drop_x_max", "x_max")
    if minimum is None or maximum is None:
        raise JevContractError("missing_board_bounds")
    result = {
        "drop_x_min": _number(minimum, field="drop_x_min"),
        "drop_x_max": _number(maximum, field="drop_x_max"),
    }
    if result["drop_x_min"] >= result["drop_x_max"]:
        raise JevContractError("invalid_board_bounds")
    for name in ("wall_left", "wall_right", "floor_y", "deadline_y"):
        if name in source:
            result[name] = _number(source[name], field=name)
    return result


def _identity(identity: Any) -> Mapping[str, Any]:
    source = _mapping(identity, field="identity")
    for name in ("run_id", "game_instance_id"):
        _uuid_text(source.get(name), field=name)
    _integer(source.get("game_generation"), field="game_generation")
    _integer(source.get("player_generation"), field="player_generation")
    _integer(source.get("opportunity_seq"), field="opportunity_seq", minimum=1)
    _integer(source.get("frame_seq"), field="frame_seq")
    _timestamp(source.get("observed_at"))
    return source


@dataclass(frozen=True)
class Observation:
    schema_version: int
    run_id: str
    game_instance_id: str
    game_generation: int
    player_generation: int
    opportunity_seq: int
    frame_seq: int
    observed_at: str
    phase: str
    drop_piece: dict[str, Any]
    preview: tuple[dict[str, Any] | None, ...]
    pieces: tuple[dict[str, Any], ...]
    score: int
    make_soren_count: int
    board_bounds: dict[str, float]

    def to_public_dict(self) -> dict[str, Any]:
        """Return the only observation shape allowed into a request."""

        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "game_instance_id": self.game_instance_id,
            "game_generation": self.game_generation,
            "player_generation": self.player_generation,
            "opportunity_seq": self.opportunity_seq,
            "frame_seq": self.frame_seq,
            "observed_at": self.observed_at,
            "phase": self.phase,
            "drop_piece": dict(self.drop_piece),
            "preview": [dict(item) if item is not None else None for item in self.preview],
            "pieces": [dict(item) for item in self.pieces],
            "score": self.score,
            "make_soren_count": self.make_soren_count,
            "board_bounds": dict(self.board_bounds),
        }


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    x: float
    command_x: int
    prediction: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.candidate_id,
            "x": self.x,
            "prediction": dict(self.prediction),
        }


@dataclass(frozen=True)
class ChoiceResult:
    status: str
    selected_id: str
    probabilities: dict[str, float]
    confidence: float
    resolved_model: str
    usage: dict[str, int]


DEFAULT_RUBRIC = {
    "rules_version": RULES_VERSION,
    "instructions": (
        "Choose one candidate for the current falling piece. Aim to form the "
        "Soviet Union, then improve score while avoiding game over. Predictions "
        "are approximate geometry aids, not guaranteed outcomes. Use only the "
        "supplied observation and candidates."
    ),
    "candidate_note": (
        "Choose exactly one supplied candidate ID. Do not invent coordinates, "
        "free-text actions, or commands."
    ),
}


def normalize_observation(raw: Any, identity: Any) -> Observation:
    """Normalize an existing game state without forwarding arbitrary fields.

    ``identity`` is intentionally separate from ``game_state.json``.  The
    current state file does not prove a game nonce or a unique opportunity;
    callers must obtain those from the bridge/lifecycle layer rather than
    deriving them from score, mtime, or piece type.
    """

    source = _mapping(raw, field="observation")
    meta = _identity(identity)
    phase = source.get("state", source.get("phase"))
    if phase not in _VALID_PHASES:
        raise JevContractError("invalid_phase")

    raw_next = _mapping(source.get("next"), field="next")
    identity_next_id = meta.get("drop_piece_id")
    if identity_next_id is None:
        raise JevContractError("missing_drop_piece_id")
    identity_next_id = _integer(identity_next_id, field="drop_piece_id")
    drop_piece = _piece(
        raw_next,
        field="drop_piece",
        piece_id=identity_next_id,
    )

    preview: list[dict[str, Any] | None] = []
    for name in ("nextNext", "nextNextNext"):
        value = source.get(name)
        preview.append(None if value is None else _piece(value, field=name))
    if len(preview) != MAX_PREVIEW:
        raise JevContractError("invalid_preview")

    raw_pieces = source.get("pieces")
    if not isinstance(raw_pieces, list) or len(raw_pieces) > MAX_PIECES:
        raise JevContractError("invalid_pieces")
    pieces: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, value in enumerate(raw_pieces):
        piece = _piece(value, field=f"piece_{index}")
        if "id" not in piece or "x" not in piece or "y" not in piece:
            raise JevContractError("missing_piece_field")
        if piece["id"] in seen_ids:
            raise JevContractError("duplicate_piece_id")
        seen_ids.add(piece["id"])
        pieces.append(piece)

    score = _integer(source.get("score"), field="score")
    make_soren_count = _integer(source.get("makeSorenCount"), field="make_soren_count")
    bounds_source = source.get("board_bounds")
    bounds = _board_bounds(bounds_source, meta)

    return Observation(
        schema_version=SCHEMA_VERSION,
        run_id=_uuid_text(meta["run_id"], field="run_id"),
        game_instance_id=_uuid_text(meta["game_instance_id"], field="game_instance_id"),
        game_generation=_integer(meta["game_generation"], field="game_generation"),
        player_generation=_integer(meta["player_generation"], field="player_generation"),
        opportunity_seq=_integer(meta["opportunity_seq"], field="opportunity_seq", minimum=1),
        frame_seq=_integer(meta["frame_seq"], field="frame_seq"),
        observed_at=_timestamp(meta["observed_at"]),
        phase=phase,
        drop_piece=drop_piece,
        preview=tuple(preview),
        pieces=tuple(pieces),
        score=score,
        make_soren_count=make_soren_count,
        board_bounds=bounds,
    )


def _validate_model(model: Any) -> str:
    if not isinstance(model, str) or not _MODEL_RE.fullmatch(model):
        raise JevContractError("invalid_model")
    return model


def _validate_candidates(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    if not isinstance(candidates, (list, tuple)) or not candidates:
        raise JevContractError("input_limit")
    if len(candidates) > MAX_CANDIDATES:
        raise JevContractError("input_limit")
    ids: set[str] = set()
    previous_x: float | None = None
    for candidate in candidates:
        if not isinstance(candidate, Candidate):
            raise JevContractError("invalid_candidate")
        if not re.fullmatch(r"c[0-9]{2}", candidate.candidate_id):
            raise JevContractError("invalid_candidate_id")
        if candidate.candidate_id in ids:
            raise JevContractError("duplicate_candidate_id")
        ids.add(candidate.candidate_id)
        x = _number(candidate.x, field="candidate_x")
        if type(candidate.command_x) is not int:
            raise JevContractError("invalid_command_x")
        if previous_x is not None and x <= previous_x:
            raise JevContractError("candidate_order")
        previous_x = x
        if set(candidate.prediction) != set(_PREDICTION_KEYS):
            raise JevContractError("invalid_prediction")
    return tuple(candidates)


def build_request(
    observation: Observation,
    candidates: Sequence[Candidate],
    rubric: Mapping[str, Any] | None,
    model: str,
) -> dict[str, Any]:
    """Build the fixed one-question Choice request for a single opportunity."""

    if not isinstance(observation, Observation) or observation.phase != "MOVE":
        raise JevContractError("phase_not_eligible")
    model = _validate_model(model)
    if model != MODEL:
        raise JevContractError("model_mismatch")
    checked_candidates = _validate_candidates(candidates)
    selected_rubric = DEFAULT_RUBRIC if rubric is None else rubric
    if not isinstance(selected_rubric, Mapping):
        raise JevContractError("invalid_rubric")
    if set(selected_rubric) != {"rules_version", "instructions", "candidate_note"}:
        raise JevContractError("invalid_rubric")
    if selected_rubric.get("rules_version") != RULES_VERSION:
        raise JevContractError("rubric_mismatch")
    instructions = _text(selected_rubric.get("instructions"), field="rubric_instructions")
    candidate_note = _text(selected_rubric.get("candidate_note"), field="rubric_candidate_note")
    if len(instructions.encode("utf-8")) > 4096 or len(candidate_note.encode("utf-8")) > 2048:
        raise JevContractError("input_limit")

    ids = [candidate.candidate_id for candidate in checked_candidates]
    criteria = {
        candidate.candidate_id: (
            f"Select {candidate.candidate_id}; use only its supplied x and "
            "approximate geometry prediction."
        )
        for candidate in checked_candidates
    }
    request = {
        "model": model,
        "state": {
            "rules_version": RULES_VERSION,
            "observation": observation.to_public_dict(),
            "candidates": [candidate.to_public_dict() for candidate in checked_candidates],
        },
        "questions": {
            "drop_position": {
                "type": "choice",
                "instructions": instructions,
                "candidate_note": candidate_note,
                "candidate_ids": ids,
                "criteria": criteria,
            }
        },
    }
    if len(dumps(request).encode("utf-8")) > MAX_REQUEST_BYTES:
        raise JevContractError("input_limit")
    return request


def _probabilities(value: Any, expected_ids: tuple[str, ...]) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(expected_ids):
        raise JevContractError("invalid_response")
    result = {}
    for candidate_id in expected_ids:
        result[candidate_id] = _number(
            value[candidate_id], field="probability", minimum=0.0
        )
        if result[candidate_id] > 1.0:
            raise JevContractError("invalid_response")
    total = sum(result.values())
    # The API rounds each probability to two decimals, so a genuine
    # distribution can sum to 0.99 (or 1.01) by up to 0.005 per candidate.
    # Accept that rounding band and normalize, so downstream always sees a
    # distribution that sums to 1.0 instead of rejecting ~4% of real requests.
    tolerance = 0.005 * len(expected_ids) + 1e-6
    if total <= 0.0 or abs(total - 1.0) > tolerance:
        raise JevContractError("invalid_response")
    if abs(total - 1.0) > 1e-9:
        result = {
            candidate_id: probability / total
            for candidate_id, probability in result.items()
        }
    return result


def validate_choice(
    response: Any,
    expected_ids: Sequence[str],
    *,
    expected_model: str = MODEL,
) -> ChoiceResult:
    """Validate one exact Choice answer; no free-text reason is accepted."""

    ids = tuple(expected_ids)
    if not ids or len(ids) > MAX_CANDIDATES or len(set(ids)) != len(ids):
        raise JevContractError("invalid_candidate_id")
    if any(not re.fullmatch(r"c[0-9]{2}", item) for item in ids):
        raise JevContractError("invalid_candidate_id")
    expected_model = _validate_model(expected_model)
    if not isinstance(response, Mapping):
        raise JevContractError("invalid_response")
    response_keys = set(response)
    if response_keys not in ({"model", "answers"}, {"model", "answers", "usage"}):
        raise JevContractError("invalid_response")
    resolved_model = _validate_model(response.get("model"))
    if resolved_model != expected_model:
        raise JevContractError("model_mismatch")
    answers = response.get("answers")
    if not isinstance(answers, Mapping) or set(answers) != {"drop_position"}:
        raise JevContractError("invalid_response")
    answer = answers["drop_position"]
    if not isinstance(answer, Mapping) or set(answer) != {
        "type",
        "choice",
        "probabilities",
        "confidence",
    }:
        raise JevContractError("invalid_response")
    if answer.get("type") != "choice":
        raise JevContractError("invalid_response")
    selected_id = answer.get("choice")
    if not isinstance(selected_id, str) or selected_id not in ids:
        raise JevContractError("unknown_candidate")
    probabilities = _probabilities(answer.get("probabilities"), ids)
    if probabilities[selected_id] + 1e-7 < max(probabilities.values()):
        raise JevContractError("choice_not_max_probability")
    confidence = _number(answer.get("confidence"), field="confidence", minimum=0.0)
    if confidence > 1.0:
        raise JevContractError("invalid_response")

    if "usage" not in response:
        raise JevContractError("usage_unknown")
    usage = response.get("usage")
    if not isinstance(usage, Mapping) or set(usage) != {"input_tokens", "output_tokens"}:
        raise JevContractError("usage_unknown")
    clean_usage: dict[str, int] = {}
    for name in ("input_tokens", "output_tokens"):
        value = usage.get(name)
        if type(value) is not int or not 0 <= value <= 1_000_000_000:
            raise JevContractError("usage_unknown")
        clean_usage[name] = value

    return ChoiceResult(
        status="ok",
        selected_id=selected_id,
        probabilities=probabilities,
        confidence=confidence,
        resolved_model=resolved_model,
        usage=clean_usage,
    )

"""Filesystem-backed evidence ledger for an isolated JEV experiment.

Evidence is written below a run-specific directory and never shares the
normal strategy history or improvement ledger.  The writer accepts only
small, JSON-safe summaries; raw prompts, API responses, headers, and secrets
are intentionally rejected.
"""

from __future__ import annotations

import datetime as _dt
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .jev_player_contract import SCHEMA_VERSION, dumps


EVIDENCE_VERSION = "jev-evidence-v1"
MAX_EVENT_BYTES = 32 * 1024
MAX_SUMMARY_BYTES = 16 * 1024
EVENT_TYPES = frozenset(
    {
        "run_started",
        "observation",
        "decision",
        "action_dispatched",
        "action_accepted",
        "observation_after",
        "game_finished",
        "fallback",
        "run_finished",
    }
)
FORBIDDEN_FIELD_PARTS = (
    "authorization",
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "cookie",
    "header",
    "prompt",
    "raw_request",
    "raw_response",
)


class EvidenceError(RuntimeError):
    pass


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_uuid(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"invalid {field}")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise EvidenceError(f"invalid {field}") from exc
    canonical = str(parsed)
    if value.lower() != canonical:
        raise EvidenceError(f"invalid {field}")
    return canonical


def _reject_secret_fields(value: Any, path: str = "root") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in FORBIDDEN_FIELD_PARTS):
                raise EvidenceError(f"forbidden evidence field: {path}.{key}")
            _reject_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _reject_secret_fields(child, f"{path}[{index}]")


def _safe_json(value: Mapping[str, Any], limit: int) -> bytes:
    _reject_secret_fields(value)
    try:
        encoded = dumps(dict(value)).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise EvidenceError("invalid evidence json") from exc
    if len(encoded) > limit:
        raise EvidenceError("evidence record too large")
    return encoded


def _assert_not_symlink(path: Path) -> None:
    try:
        if path.is_symlink():
            raise EvidenceError(f"symlink is not allowed: {path.name}")
    except OSError as exc:
        raise EvidenceError("cannot inspect evidence path") from exc


def _mkdir_private(path: Path) -> None:
    _assert_not_symlink(path)
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        _assert_not_symlink(path)
        if not path.is_dir():
            raise EvidenceError("evidence path is not a directory")
        os.chmod(path, 0o700)
    except OSError as exc:
        raise EvidenceError("cannot create evidence directory") from exc


def _atomic_write(path: Path, encoded: bytes) -> None:
    _assert_not_symlink(path)
    parent = path.parent
    _mkdir_private(parent)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=".jev-evidence-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            os.chmod(handle.fileno(), 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, 0o600)
    except OSError as exc:
        raise EvidenceError("cannot write evidence") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def _append_line(path: Path, encoded: bytes) -> None:
    _assert_not_symlink(path)
    _mkdir_private(path.parent)
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            os.chmod(fd, 0o600)
            remaining = encoded + b"\n"
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise EvidenceError("cannot append evidence")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        raise EvidenceError("cannot append evidence") from exc


class JevEvidence:
    """A private evidence directory for one manual JEV run."""

    def __init__(
        self,
        root: str | os.PathLike[str],
        run_id: str,
        *,
        config: Mapping[str, Any] | None = None,
    ) -> None:
        self.run_id = _canonical_uuid(run_id, "run_id")
        self.root = Path(root)
        _mkdir_private(self.root)
        safe_config = dict(config or {})
        _safe_json(safe_config, MAX_SUMMARY_BYTES)
        self.run_dir = self.root / self.run_id
        _assert_not_symlink(self.run_dir)
        if self.run_dir.exists():
            raise EvidenceError("run already exists")
        _mkdir_private(self.run_dir)
        self._events_path = self.run_dir / "events.jsonl"
        self._manifest_path = self.run_dir / "manifest.json"
        self._report_path = self.run_dir / "report.json"
        self._manifest: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "evidence_version": EVIDENCE_VERSION,
            "run_id": self.run_id,
            "mode": "jev_sorengame_experiment",
            "status": "active",
            "started_at": _now(),
            "config": safe_config,
        }
        _atomic_write(self._manifest_path, _safe_json(self._manifest, MAX_SUMMARY_BYTES))
        self.append_event("run_started", {"mode": self._manifest["mode"]})

    def append_event(self, event_type: str, payload: Mapping[str, Any]) -> str:
        if event_type not in EVENT_TYPES:
            raise EvidenceError("unknown event type")
        event_id = str(uuid.uuid4())
        record = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "run_id": self.run_id,
            "event_type": event_type,
            "recorded_at": _now(),
            "payload": dict(payload),
        }
        _append_line(self._events_path, _safe_json(record, MAX_EVENT_BYTES))
        return event_id

    def record_decision(
        self,
        *,
        game_instance_id: str,
        opportunity_seq: int,
        player_generation: int,
        candidate_count: int,
        status: str,
        source: str,
        selected_id: str | None = None,
        confidence: float | None = None,
        fallback_reason: str | None = None,
    ) -> str:
        _canonical_uuid(game_instance_id, "game_instance_id")
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in (opportunity_seq, player_generation, candidate_count)):
            raise EvidenceError("invalid decision counters")
        if not isinstance(status, str) or not status or len(status) > 64:
            raise EvidenceError("invalid decision status")
        if not isinstance(source, str) or source not in {"jev", "fallback"}:
            raise EvidenceError("invalid decision source")
        payload: dict[str, Any] = {
            "game_instance_id": game_instance_id,
            "opportunity_seq": opportunity_seq,
            "player_generation": player_generation,
            "candidate_count": candidate_count,
            "status": status,
            "source": source,
        }
        if selected_id is not None:
            if not isinstance(selected_id, str) or len(selected_id) > 32:
                raise EvidenceError("invalid selected id")
            payload["selected_id"] = selected_id
        if confidence is not None:
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise EvidenceError("invalid confidence")
            if not 0 <= float(confidence) <= 1:
                raise EvidenceError("invalid confidence")
            payload["confidence"] = float(confidence)
        if fallback_reason is not None:
            if not isinstance(fallback_reason, str) or len(fallback_reason) > 128:
                raise EvidenceError("invalid fallback reason")
            payload["fallback_reason"] = fallback_reason
        return self.append_event("decision", payload)

    def record_game_event(
        self,
        event_type: str,
        *,
        game_instance_id: str,
        payload: Mapping[str, Any],
    ) -> str:
        _canonical_uuid(game_instance_id, "game_instance_id")
        if event_type not in {"observation", "action_dispatched", "action_accepted", "observation_after", "game_finished", "fallback"}:
            raise EvidenceError("invalid game event type")
        body = dict(payload)
        body["game_instance_id"] = game_instance_id
        return self.append_event(event_type, body)

    def finalize(self, summary: Mapping[str, Any]) -> Path:
        safe_summary = dict(summary)
        encoded_summary = _safe_json(safe_summary, MAX_SUMMARY_BYTES)
        report = {
            "schema_version": SCHEMA_VERSION,
            "evidence_version": EVIDENCE_VERSION,
            "run_id": self.run_id,
            "status": "completed",
            "finished_at": _now(),
            "summary": safe_summary,
        }
        _atomic_write(self._report_path, _safe_json(report, MAX_SUMMARY_BYTES))
        self._manifest["status"] = "completed"
        self._manifest["finished_at"] = report["finished_at"]
        _atomic_write(self._manifest_path, _safe_json(self._manifest, MAX_SUMMARY_BYTES))
        self.append_event("run_finished", safe_summary)
        return self._report_path

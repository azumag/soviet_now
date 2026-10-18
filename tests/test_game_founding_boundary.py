import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

from lib import game_founding_boundary as boundary


def seed_founding(root, elapsed=301):
    """Drive the real witness through repeated fresh observations, without sleeps."""
    epoch, mono = time.time(), time.monotonic()
    state = {"state": "STOP", "score": 6401, "makeSorenCount": 1, "pieces": [{"type": 16}]}
    (root / "game_state.json").write_text(json.dumps(state))
    old = epoch - elapsed - 1
    os.utime(root / "game_state.json", (old, old))
    (root / boundary.RUNNER).parent.mkdir(parents=True, exist_ok=True)
    (root / boundary.RUNNER).write_text(json.dumps({"pid": os.getpid(), "game": 12, "started_at": old - 1}))
    (root / "game_count.txt").write_text("11")
    (root / boundary.MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / boundary.MARKER).write_text("1\n")
    os.utime(root / boundary.MARKER, (old, old))
    observation = {"schema": 1, "game_id": str(uuid.uuid4()), "stop_id": str(uuid.uuid4()), "board": state}
    witness = boundary.FoundingBoundaryWitness(root)
    birth = boundary._process_birth(os.getpid())
    for step in range(elapsed + 1):
        instant = epoch - elapsed + step
        observation["observed_epoch"] = instant
        (root / boundary.OBSERVATION).write_text(json.dumps(observation))
        with mock.patch.object(boundary.time, "time", return_value=instant), mock.patch.object(
            boundary.time, "monotonic", return_value=mono - elapsed + step
        ), mock.patch.object(boundary, "_process_birth", return_value=birth):
            witness.observe(state, True)
    return state, witness, observation


class FoundingBoundaryTest(unittest.TestCase):
    def test_five_minutes_uses_real_witness_and_fresh_bridge_observations(self):
        for elapsed, expected in ((0, False), (299, False), (300, True), (301, True)):
            with self.subTest(elapsed=elapsed), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state, _, _ = seed_founding(root, elapsed)
                self.assertEqual(boundary.has_founding_boundary(root, state), expected)

    def test_new_runner_cannot_inherit_completed_wait(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, _, _ = seed_founding(root)
            boundary.FoundingBoundaryWitness(root).observe(state, True)
            self.assertFalse(boundary.has_founding_boundary(root, state))

    def test_identity_freshness_and_clock_skew_fail_closed(self):
        changes = {
            "marker": lambda root: (root / boundary.MARKER).write_text("2\n"),
            "game_count": lambda root: (root / "game_count.txt").write_text("12"),
            "runner": lambda root: self.change(root / boundary.RUNNER, started_at=0),
            "observation_gap": lambda root: self.change(root / boundary.OBSERVATION, observed_epoch=time.time() - 4),
            "new_bridge": lambda root: self.change(root / boundary.OBSERVATION, game_id=str(uuid.uuid4())),
            "resumed_stop": lambda root: self.change(root / boundary.OBSERVATION, stop_id=str(uuid.uuid4())),
            "missing_observer": lambda root: (root / boundary.OBSERVATION).unlink(),
            "changed_board": lambda root: self.change(root / "game_state.json", score=10),
            "future_observation": lambda root: self.change(root / boundary.OBSERVATION, observed_epoch=time.time() + 10),
            "future_marker": lambda root: os.utime(root / boundary.MARKER, (time.time() + 10, time.time() + 10)),
            "NaN": lambda root: self.change(root / "game_state.json", makeSorenCount=float("nan")),
            "infinity": lambda root: self.change(root / "game_state.json", makeSorenCount=float("inf")),
            "zero": lambda root: self.change(root / "game_state.json", makeSorenCount=0),
            "bool": lambda root: self.change(root / "game_state.json", makeSorenCount=True),
            "fraction": lambda root: self.change(root / "game_state.json", makeSorenCount=1.5),
            "boolean_piece": lambda root: self.change(root / "game_state.json", pieces=[{"type": True}]),
            "MOVE": lambda root: self.change(root / "game_state.json", state="MOVE"),
        }
        for label, change in changes.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state, _, _ = seed_founding(root)
                change(root)
                self.assertFalse(boundary.has_founding_boundary(root, state))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state, _, _ = seed_founding(root)
            with mock.patch.object(boundary.time, "time", return_value=time.time() + 300):
                self.assertFalse(boundary.has_founding_boundary(root, state))
            with mock.patch.object(boundary, "_process_birth", return_value="reused-pid"):
                self.assertFalse(boundary.has_founding_boundary(root, state))

    def test_gap_move_and_clock_jumps_reset_elapsed_time(self):
        for label in ("gap", "MOVE", "clock_jump"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                state, witness, observation = seed_founding(root, 299)
                if label == "MOVE":
                    witness.observe({**state, "state": "MOVE"}, True)
                delay = 5 if label == "gap" else 1
                epoch, mono = time.time(), time.monotonic()
                if label == "clock_jump":
                    epoch += 300
                observation["observed_epoch"] = epoch + delay
                (root / boundary.OBSERVATION).write_text(json.dumps(observation))
                with mock.patch.object(boundary.time, "time", return_value=epoch + delay), mock.patch.object(
                    boundary.time, "monotonic", return_value=mono + delay
                ):
                    witness.observe(state, True)
                    self.assertFalse(boundary.has_founding_boundary(root, state))

    @staticmethod
    def change(file, **values):
        record = json.loads(file.read_text())
        record.update(values)
        file.write_text(json.dumps(record))

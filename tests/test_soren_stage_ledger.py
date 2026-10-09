"""Synthetic snapshots plus the actual production shell writer. No game input."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lib import soren_stage_ledger as m

spec = importlib.util.spec_from_file_location("progress", ROOT / "tools/soren_strategy_progress.py")
progress = importlib.util.module_from_spec(spec)
spec.loader.exec_module(progress)
HASH = "a" * 12


def record():
    return {"idx": 0, "arm": "A", "game_num": "100", "archive": "sample.jsonl",
            "hash": HASH, "played_hash": HASH, "history_hash": HASH, "turns": 3.0,
            "tainted": False, "ts": "2026-10-10T00:00:00+09:00"}


def snapshots(pair=True):
    result = []
    for turn, types in enumerate(([11], [15, 15] if pair else [15], [16]), 1):
        pieces = [{"id": i - 10, "type": t, "x": float(i), "y": -3.0, "r": .5}
                  for i, t in enumerate(types)]
        result.append({"turn": turn, "strategy_hash": HASH, "piece_count": len(pieces),
                       "state_snapshot": {"pieces": pieces}, "decision_x": .2,
                       "decision_reason": "PRIVATE_TEXT_NOT_FOR_REPORT", "makeSorenCount": 0})
    return result


def raw(rows=None):
    return ("\n".join(json.dumps(x) for x in (snapshots() if rows is None else rows)) + "\n").encode()


class StageTests(unittest.TestCase):
    def test_pair_persists_after_it_disappears_from_final_board(self):
        proof = m.summarize(raw(), record())
        value = proof["stage_evidence"]
        self.assertTrue(value["two_russias_observed"])
        self.assertEqual(value["first_turn_two_t15_observed"], 2)
        self.assertEqual(value["max_t15_observed"], 2)
        self.assertEqual(proof["archive_sha256"], hashlib.sha256(raw()).hexdigest())
        self.assertNotIn("PRIVATE_TEXT", json.dumps(proof))
        self.assertNotIn("soviet_created", proof)

    def test_one_russia_or_founding_never_invents_a_pair(self):
        rows = snapshots(False)
        rows[-1]["makeSorenCount"] = 1
        value = m.summarize(raw(rows), record())["stage_evidence"]
        self.assertIsNone(value["two_russias_observed"])
        self.assertEqual(value["max_t15_observed"], 1)
        self.assertIsNone(value["first_turn_two_t15_observed"])

    def test_pair_and_single_absence_are_unknown_not_false(self):
        rows = snapshots()
        for row in rows:
            row["state_snapshot"]["pieces"] = []
        proof = m.summarize(raw(rows), record())
        self.assertIsNone(proof["stage_evidence"]["first_russia_observed"])
        self.assertIsNone(proof["stage_evidence"]["two_russias_observed"])

    def test_duplicate_id_does_not_make_a_second_russia(self):
        rows = snapshots()
        rows[1]["state_snapshot"]["pieces"][1]["id"] = -10
        with self.assertRaisesRegex(m.EvidenceError, "duplicate_piece_identity"):
            m.summarize(raw(rows), record())

    def test_missing_or_boolean_piece_id_is_unknown(self):
        for value in (None, True, [], "123"):
            with self.subTest(value=value):
                rows = snapshots()
                rows[1]["state_snapshot"]["pieces"][1]["id"] = value
                with self.assertRaises(m.EvidenceError):
                    m.summarize(raw(rows), record())

    def test_signed_piece_ids_are_valid(self):
        self.assertTrue(m.summarize(raw(), record())["stage_evidence"]["two_russias_observed"])

    def test_mixed_hash_reset_gap_and_boolean_turn_rejected(self):
        for change in ({"strategy_hash": "b" * 12}, {"turn": 1}, {"turn": 4}, {"turn": True}):
            with self.subTest(change=change):
                rows = snapshots()
                rows[-1].update(change)
                with self.assertRaises(m.EvidenceError):
                    m.summarize(raw(rows), record())

    def test_invalid_json_after_a_pair_invalidates_whole_proof(self):
        for last in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b'null', b'{bad}'):
            with self.subTest(last=last):
                data = b'\n'.join(raw().splitlines()[:2] + [last])
                with self.assertRaises(m.EvidenceError):
                    m.summarize(data, record())

    def test_malformed_pieces_or_type_do_not_create_evidence(self):
        for pieces in (None, "pieces", [None], [{"id": 1, "type": True}], [{"id": 1, "type": 17}]):
            rows = snapshots()
            rows[-1]["state_snapshot"]["pieces"] = pieces
            with self.subTest(pieces=pieces):
                with self.assertRaises(m.EvidenceError):
                    m.summarize(raw(rows), record())

    def test_recorded_turns_must_match_all_rows(self):
        for turns in (2, 4, True, None, float("inf"), float("nan"), "3"):
            row = dict(record(), turns=turns)
            with self.subTest(turns=turns):
                with self.assertRaises(m.EvidenceError):
                    m.summarize(raw(), row)

    def test_missing_execution_hash_and_taint_cannot_certify(self):
        for key, value in (("tainted", True), ("tainted", None), ("played_hash", ""), ("history_hash", "b"*12)):
            with self.subTest(key=key, value=value):
                with self.assertRaises(m.EvidenceError):
                    m.summarize(raw(), dict(record(), **{key: value}))

    def test_empty_oversize_and_limits(self):
        with self.assertRaises(m.EvidenceError):
            m.summarize(b'', record())
        with patch.object(m, "MAX_BYTES", 4):
            with self.assertRaises(m.EvidenceError):
                m.summarize(raw(), record())
        with patch.object(m, "MAX_PIECES", 1):
            with self.assertRaises(m.EvidenceError):
                m.summarize(raw(), record())

    def test_persisted_pair_validates_binding_without_reading_archive(self):
        row = record()
        row.update(m.summarize(raw(), row))
        self.assertEqual(m.retained_pair(json.loads(json.dumps(row))), (True, "retained_observation"))
        for key, value in (("game_num", "101"), ("idx", 1), ("arm", "B"), ("archive", "other.jsonl"), ("archive_sha256", "0"*64)):
            with self.subTest(key=key):
                self.assertEqual(m.retained_pair(dict(row, **{key: value})), (None, "invalid_stage_evidence"))

    def test_corrupt_fields_never_become_success(self):
        for key, value in (("schema_version", True), ("recorded_turns", True), ("max_t15_observed", 1),
                           ("first_turn_t15_observed", 3), ("first_turn_two_t15_observed", 0),
                           ("two_russias_observed", 1), ("absence_is_unknown", False), ("strategy_hash", "b"*12)):
            row = record()
            row.update(m.summarize(raw(), row))
            row["stage_evidence"][key] = value
            with self.subTest(key=key):
                self.assertEqual(m.retained_pair(row), (None, "invalid_stage_evidence"))

    def test_new_record_unknown_is_not_zero(self):
        row = record()
        row.update(m.summarize(raw(snapshots(False)), row))
        self.assertEqual(m.retained_pair(row), (None, "retained_observation"))

    def test_legacy_or_unavailable_are_explicit(self):
        self.assertEqual(m.retained_pair(record()), (None, "not_recorded"))
        self.assertEqual(m.retained_pair(dict(record(), stage_evidence=None)), (None, "not_recorded"))
        self.assertEqual(m.retained_pair(dict(record(), stage_evidence={"status": "unavailable"})),
                         (None, "producer_unavailable"))
        self.assertEqual(m.retained_pair(dict(record(), stage_evidence=[])), (None, "invalid_stage_evidence"))

    def test_capture_is_read_only_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sample.jsonl")
            path.write_bytes(raw())
            original = path.read_bytes()
            value = m.capture_stage_evidence(path, record())
            self.assertTrue(value["stage_evidence"]["two_russias_observed"])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            with patch.object(m, "MAX_BYTES", 4):
                self.assertEqual(m.capture_stage_evidence(path, record())["stage_evidence"]["status"], "unavailable")

    def test_unreadable_symlink_and_fifo_are_fixed_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.joinpath("source").write_bytes(raw())
            root.joinpath("link.jsonl").symlink_to(root / "source")
            os.mkfifo(root / "pipe")
            for path in (root / "missing", root / "link.jsonl", root / "pipe", root):
                value = m.capture_stage_evidence(path, record())
                self.assertEqual(value["stage_evidence"]["status"], "unavailable")
                self.assertNotIn(directory, json.dumps(value))

    def test_archive_basename_must_match_the_ledger_row(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "other.jsonl")
            path.write_bytes(raw())
            value = m.capture_stage_evidence(path, record())
            self.assertEqual(value["stage_evidence"]["reason"], "archive_name_mismatch")

    def test_concurrent_replacement_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "sample.jsonl")
            path.write_bytes(raw())
            actual = m.os.stat
            def mutate(p, **kwargs):
                # is_symlink uses lstat, so this is the final consistency check.
                path.write_bytes(raw(snapshots(False)))
                return actual(p, **kwargs)
            with patch.object(m.os, "stat", side_effect=mutate):
                self.assertEqual(m.capture_stage_evidence(path, record())["stage_evidence"]["status"], "unavailable")

    def test_does_not_mutate_inputs(self):
        row = record()
        previous = copy.deepcopy(row)
        m.summarize(raw(), row)
        self.assertEqual(row, previous)


class WriterTests(unittest.TestCase):
    def execute(self, directory, helper="real", source=None):
        root = Path(directory)
        root.joinpath("lib").mkdir(exist_ok=True)
        if helper == "real":
            shutil.copyfile(ROOT / "lib/soren_stage_ledger.py", root / "lib/soren_stage_ledger.py")
        elif helper == "broken":
            root.joinpath("lib/soren_stage_ledger.py").write_text('raise RuntimeError("DO_NOT_ECHO")\n')
        root.joinpath("state.json").write_text(json.dumps({"games_recorded": 0, "decision_rule": {"frozen": True}}))
        root.joinpath("strategy.py.game_snapshot").write_text("snapshot")
        root.joinpath("sample.jsonl").write_bytes(raw())
        root.joinpath("ab_interleave.sh").write_text(source if source is not None else (ROOT / "strategy/ab_interleave.sh").read_text())
        shell = '''set -eu
source ./ab_interleave.sh
log() { printf '%s\\n' "$*"; }
_ab_hash() { printf '%s\\n' aaaaaaaaaaaa; }
AB_ARM=A AB_HASH=aaaaaaaaaaaa AB_IDX=0 GAME_NUM=100
AB_STATE_FILE=state.json AB_GAMES_FILE=games.jsonl
_ab_record_game 1234 5678 3 sample.jsonl false true
'''
        env = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        run = subprocess.run(["bash", "-c", shell], cwd=root, env=env, text=True, capture_output=True, timeout=10)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn("DO_NOT_ECHO", run.stdout + run.stderr)
        return json.loads(root.joinpath("games.jsonl").read_text()), json.loads(root.joinpath("state.json").read_text())

    def test_real_shell_writer_persists_pair_and_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            row, state = self.execute(directory)
            self.assertEqual(row["stage_evidence"]["status"], "observed")
            self.assertTrue(row["stage_evidence"]["two_russias_observed"])
            self.assertEqual(m.retained_pair(row), (True, "retained_observation"))
            self.assertEqual(row["archive_sha256"], hashlib.sha256(raw()).hexdigest())
            self.assertEqual(state, {"games_recorded": 1, "decision_rule": {"frozen": True}, "last_arm": "A"})

    def test_optional_helper_failure_preserves_existing_result(self):
        for helper in ("missing", "broken"):
            with self.subTest(helper=helper), tempfile.TemporaryDirectory() as directory:
                row, state = self.execute(directory, helper)
                self.assertEqual(row["stage_evidence"]["status"], "unavailable")
                self.assertEqual((row["score"], row["eval"], row["soviet_created"], row["russia_created"]), (1234, 5678, False, True))
                self.assertEqual(state["games_recorded"], 1)
                self.assertNotIn("archive_sha256", row)

    def test_legacy_record_and_frozen_state_are_unchanged(self):
        source = (ROOT / "strategy/ab_interleave.sh").read_text()
        begin = source.index("# Retain observed stages before history pruning")
        end = source.index('with open(games_file, "a", encoding="utf-8") as fh:', begin)
        baseline = source[:begin] + source[end:]
        # Compare the real producer with only the new enrichment removed.
        with tempfile.TemporaryDirectory() as old, tempfile.TemporaryDirectory() as new:
            original, original_state = self.execute(old, source=baseline)
            current, current_state = self.execute(new)
            current.pop("archive_sha256")
            current.pop("stage_evidence")
            original.pop("ts")
            current.pop("ts")
            self.assertEqual(current, original)
            self.assertEqual(current_state, original_state)
            self.assertEqual(Path(new, "sample.jsonl").read_bytes(), raw())

    def test_report_works_after_history_pruning(self):
        with tempfile.TemporaryDirectory() as directory:
            row, _ = self.execute(directory)
            Path(directory, "sample.jsonl").unlink()
            state = {"a_hash": HASH, "b_hash": "b"*12, "pattern": "ABBA", "games_recorded": 1,
                     "game_num_start": 100, "started_at": "2026-01-01T00:00:00Z"}
            report = progress.build_report(state, [row], HASH, source_offset=progress.offset("+00:00"))
            value = report["arms"]["baseline"]["stages"]["two_russias_observed"]
            self.assertEqual((value["successes"], value["unknown"]), (1, 0))
            self.assertEqual(report["history"]["counts"]["retained_observation"], 1)
            self.assertFalse(report["verified_improvement"])

    def test_invalid_retained_proof_is_not_replaced_with_unbound_history(self):
        row = record()
        row.update(m.summarize(raw(), row))
        row["stage_evidence"]["row_binding_sha256"] = "0"*64
        values, evidence = progress.enrich([row], "/not-a-real-directory")
        self.assertIsNone(values[0]["_two_russias_observed"])
        self.assertEqual(evidence["counts"], {"invalid_stage_evidence": 1})


if __name__ == "__main__":
    unittest.main()

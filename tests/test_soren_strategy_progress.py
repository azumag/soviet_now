"""Synthetic producer-shaped fixtures only; no live performance claims."""
import copy
from datetime import datetime, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "soren_strategy_progress.py"
spec = importlib.util.spec_from_file_location("soren_strategy_progress", SCRIPT)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
A, B = "a" * 12, "b" * 12


def state():
    return {"a_hash": A, "b_hash": B, "pattern": "ABBA", "games_recorded": 4,
            "game_num_start": 100, "started_at": "2026-10-09T10:00:00+09:00",
            "a_env": "PRIVATE_VALUE=not-for-output", "b_env": ""}


def rows():
    result = []
    for idx, arm in enumerate("ABBA"):
        h = A if arm == "A" else B
        result.append({"idx": idx, "arm": arm, "hash": h, "played_hash": h,
                       "history_hash": h, "score": 1000 if arm == "A" else 1100,
                       "eval": 9000, "turns": 100, "archive": f"sample{idx}.jsonl",
                       "game_num": str(100 + idx), "ts": "2026-10-09T11:00:00+09:00",
                       "tainted": False, "soviet_created": False,
                       "russia_created": True, "t15": 1})
    return result


class ProgressTests(unittest.TestCase):
    def report(self, data=None, **kwargs):
        return m.build_report(state(), rows() if data is None else data, A, **kwargs)

    def test_producer_shape_counts_and_score(self):
        report = self.report()
        self.assertEqual(report["accepted_rows"], 4)
        self.assertEqual(report["paired"]["score"],
                         {"complete_blocks": 1, "mean_candidate_minus_baseline": 100})
        self.assertEqual(report["arms"]["candidate"]["stages"]["founding"]["known"], 2)
        self.assertFalse(report["verified_improvement"])

    def test_baseline_can_be_b(self):
        report = m.build_report(state(), rows(), B)
        self.assertEqual(report["paired"]["score"]["mean_candidate_minus_baseline"], -100)
        self.assertEqual(report["arms"]["baseline"]["arm"], "B")

    def test_baseline_is_required_and_not_auto_selected(self):
        with self.assertRaisesRegex(m.EvidenceError, "baseline_missing"):
            m.build_report(state(), rows(), "c" * 12)
        s = state()
        s["b_hash"] = A
        with self.assertRaisesRegex(m.EvidenceError, "ambiguous"):
            m.build_report(s, rows(), A)

    def test_missing_founding_is_not_zero_or_eval_fallback(self):
        data = rows()
        for r in data:
            r.pop("soviet_created")
            r["eval"] = 999999
            r["max_type"] = 16
        report = self.report(data)
        for v in report["arms"].values():
            self.assertEqual(v["stages"]["founding"]["unknown"], 2)
            self.assertIsNone(v["stages"]["founding"]["rate_among_known"])
        self.assertEqual(report["paired"]["founding"]["complete_blocks"], 0)

    def test_boolean_like_strings_are_unknown(self):
        data = rows()
        data[1]["soviet_created"] = "false"
        data[2]["soviet_created"] = 0
        self.assertEqual(self.report(data)["arms"]["candidate"]["stages"]["founding"]["unknown"], 2)

    def test_missing_outcome_bounds_and_conditional_denominator(self):
        data = rows()
        data[1]["soviet_created"] = True
        data[2]["soviet_created"] = None
        report = self.report(data)
        v = report["arms"]["candidate"]["stages"]["founding"]
        self.assertEqual(v["missing_outcome_bounds"], [.5, 1.0])
        self.assertEqual(report["arms"]["candidate"]["founding_given_first_russia"]["total"], 2)
        self.assertFalse(report["verified_improvement"])

    def test_russia_conflict_is_unknown(self):
        data = rows()
        data[0]["russia_created"] = False
        self.assertEqual(self.report(data)["arms"]["baseline"]["stages"]["first_russia"]["unknown"], 1)

    def test_t15_legacy_fallback_is_strict(self):
        for value, expected in ((0, False), (1, True), (True, None), (2, None), ("1", None)):
            with self.subTest(value=value):
                self.assertIs(m.stages({"t15": value})["first_russia"], expected)

    def test_duplicate_index_excludes_both_not_first_wins(self):
        data = rows() + [dict(rows()[1], score=9999)]
        report = self.report(data)
        self.assertEqual(report["excluded"]["duplicate_index"], 2)
        self.assertEqual(report["paired"]["score"]["complete_blocks"], 0)

    def test_duplicate_game_excludes_both(self):
        data = rows()
        data[1]["game_num"] = data[0]["game_num"]
        self.assertEqual(self.report(data)["excluded"]["duplicate_game"], 2)

    def test_duplicate_archive_cannot_impersonate_two_games(self):
        data = rows()
        data[3]["archive"] = data[0]["archive"]
        self.assertEqual(self.report(data)["excluded"]["duplicate_archive"], 2)

    def test_founding_does_not_report_contradictory_first_stage_failure(self):
        self.assertIsNone(m.stages({"soviet_created": True, "russia_created": False})["first_russia"])

    def test_arm_order_is_not_just_arm_counts(self):
        data = rows()
        for idx, arm in enumerate("ABAB"):
            h = A if arm == "A" else B
            data[idx].update(arm=arm, hash=h, played_hash=h, history_hash=h)
        report = self.report(data)
        self.assertEqual(report["excluded"]["arm_order_mismatch"], 2)
        self.assertEqual(report["paired"]["score"]["complete_blocks"], 0)

    def test_gaps_do_not_form_a_complete_block(self):
        self.assertEqual(self.report(rows()[:3])["paired"]["score"]["complete_blocks"], 0)

    def test_missing_taint_and_hashes_fail_closed(self):
        for key in ("tainted", "hash", "played_hash", "history_hash"):
            with self.subTest(key=key):
                data = rows()
                del data[1][key]
                report = self.report(data)
                self.assertEqual(report["accepted_rows"], 3)
                self.assertEqual(report["paired"]["score"]["complete_blocks"], 0)

    def test_hash_mismatch_and_soren91_are_excluded(self):
        data = rows()
        data[0]["played_hash"] = B
        data[1]["game"] = "soren91"
        self.assertEqual(self.report(data)["excluded"], {"hash_mismatch_or_unknown": 1, "wrong_game": 1})

    def test_invalid_row_and_index_do_not_crash(self):
        data = rows() + [None, [], 10, {"idx": True}, {"idx": -1}, {"idx": "1"}]
        report = self.report(data)
        self.assertEqual(report["excluded"], {"invalid_row": 3, "invalid_index": 3})

    def test_bad_numbers_are_missing(self):
        for value in (True, False, float("nan"), float("inf"), -1, 10 ** 500, "1000"):
            with self.subTest(value=repr(value)[:20]):
                data = rows()
                data[1]["score"] = value
                report = self.report(data)
                self.assertEqual(report["arms"]["candidate"]["score"]["missing"], 1)
                self.assertEqual(report["paired"]["score"]["complete_blocks"], 0)
                json.dumps(report, allow_nan=False)

    def test_legacy_timestamps_need_explicit_offset(self):
        s = state()
        s["started_at"] = s["started_at"][:-6]
        with self.assertRaisesRegex(m.EvidenceError, "source_offset"):
            m.build_report(s, rows(), A)
        self.assertEqual(m.build_report(s, rows(), A, source_offset=m.offset("+09:00"))["accepted_rows"], 4)

    def test_naive_row_is_excluded_not_local_timezone_guessed(self):
        data = rows()
        data[0]["ts"] = "2026-10-09T11:00:00"
        self.assertIn("naive_timestamp_requires_source_offset", self.report(data)["excluded"])

    def test_timestamp_and_game_must_not_precede_experiment(self):
        data = rows()
        data[0]["ts"] = "2026-10-09T09:59:59+09:00"
        data[1]["game_num"] = "99"
        self.assertEqual(self.report(data)["excluded"], {"row_before_experiment": 1, "game_before_experiment": 1})

    def test_window_edges_and_future_rows(self):
        data = rows()
        data[0]["ts"] = "2026-10-10T00:00:00+09:00"
        data[1]["ts"] = "2026-10-10T00:00:01+09:00"
        as_of = m.timestamp("2026-10-10T00:00:00+09:00")
        report = self.report(data, as_of=as_of, days=1)
        self.assertEqual(report["excluded"], {"outside_window": 1})
        self.assertEqual(report["accepted_rows"], 3)

    def test_window_requires_aware_as_of(self):
        with self.assertRaises(m.EvidenceError):
            self.report(days=7)
        with self.assertRaises(m.EvidenceError):
            self.report(as_of=datetime(2026, 10, 10), days=7)

    def test_pattern_and_identity_are_required(self):
        for change in ({"pattern": "A"}, {"pattern": "AAAB"}, {"pattern": "A B B A"},
                       {"games_recorded": True}, {"game_num_start": None}):
            with self.subTest(change=change):
                s = state()
                s.update(change)
                with self.assertRaises(m.EvidenceError):
                    m.build_report(s, rows(), A)

    def test_provisional_or_significant_adoption_does_not_certify_strength(self):
        for cls in ("provisional_win", "significant_win"):
            report = self.report(decision={"decision_class": cls, "verdict": "ADOPT"})
            self.assertEqual(report["recorded_adoption"]["decision_class"], cls)
            self.assertFalse(report["verified_improvement"])
            self.assertEqual(report["comparison_status"], "descriptive_only")

    def test_unknown_decision_values_are_not_echoed(self):
        for value in ("SECRET", [], {}):
            report = self.report(decision={"verdict": value, "decision_class": value})
            self.assertEqual(report["recorded_adoption"]["decision_class"], "unknown")
            self.assertNotIn("SECRET", json.dumps(report))

    def test_inputs_are_not_mutated(self):
        s, data = state(), rows()
        old = copy.deepcopy((s, data))
        m.build_report(s, data, A)
        self.assertEqual((s, data), old)

    def test_private_fields_and_spoofed_enrichment_do_not_escape(self):
        data = rows()
        data[0]["_two_russias_observed"] = True
        data[0]["comment"] = "SECRET"
        text = json.dumps(self.report(data))
        self.assertNotIn("SECRET", text)
        self.assertNotIn("PRIVATE_VALUE", text)
        self.assertEqual(self.report(data)["arms"]["baseline"]["stages"]["two_russias_observed"]["unknown"], 2)

    def test_empty_ledger_has_no_fabricated_rates(self):
        report = self.report([])
        self.assertIsNone(report["arms"]["baseline"]["stages"]["founding"]["rate_among_known"])
        self.assertIsNone(report["paired"]["score"]["mean_candidate_minus_baseline"])

    def test_markdown_clearly_labels_unknown_and_unverified(self):
        text = m.markdown(self.report())
        self.assertIn("未知 2", text)
        self.assertIn("帰属は未検証", text)
        self.assertIn("記述統計のみ", text)


class HistoryTests(unittest.TestCase):
    def text(self, pair=True):
        return "\n".join(json.dumps({"turn": i, "strategy_hash": A,
                                      "state_snapshot": {"pieces": [{"id": j - 10, "type": 15} for j in range(2 if pair and i == 2 else 1)]}})
                         for i in (1, 2)) + "\n"

    def test_positive_observation_but_absence_is_unknown(self):
        self.assertIs(m.history_pair(self.text().encode(), dict(rows()[0], turns=2)), True)
        self.assertIsNone(m.history_pair(self.text(False).encode(), dict(rows()[0], turns=2)))

    def test_bad_history_invalidates_even_an_earlier_pair(self):
        for text in (self.text() + '{bad}\n', self.text() + self.text(),
                     self.text().replace('"turn": 2', '"turn": 3'),
                     self.text().replace(A, B), self.text().replace('"type": 15', '"type": true')):
            with self.subTest(text=text[:50]):
                with self.assertRaises(m.EvidenceError):
                    m.history_pair(text.encode(), dict(rows()[0], turns=2))

    def test_digest_bound_pair_and_pruned_history(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.text()
            Path(directory, "sample0.jsonl").write_text(text)
            data = rows()
            data[0]["archive_sha256"] = hashlib.sha256(text.encode()).hexdigest()
            data[0]["turns"] = 2
            report = m.build_report(state(), data, A, history_dir=directory)
            metric = report["arms"]["baseline"]["stages"]["two_russias_observed"]
            self.assertEqual((metric["successes"], metric["unknown"]), (1, 1))
            self.assertEqual(report["history"]["counts"]["archive_unreadable"], 3)

    def test_legacy_history_without_digest_is_not_assigned(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample0.jsonl").write_text(self.text())
            report = m.build_report(state(), rows(), A, history_dir=directory)
            self.assertEqual(report["history"]["counts"]["history_digest_unbound"], 1)

    def test_invalid_piece_identity_and_turn_count_remain_unknown(self):
        valid = [{"turn": i, "strategy_hash": A,
                  "state_snapshot": {"pieces": [{"id": -1, "type": 15}, {"id": 2, "type": 15}]}}
                 for i in (1, 2)]
        for kind, reason in (("duplicate", "duplicate_piece_identity"),
                             ("missing", "piece_identity_unknown"),
                             ("turn_count", "turn_count_mismatch"),
                             ("piece_limit", "history_pieces_unknown"),
                             ("row_limit", "invalid_row_identity")):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                frames = copy.deepcopy(valid)
                if kind == "duplicate":
                    frames[1]["state_snapshot"]["pieces"][1]["id"] = -1
                elif kind == "missing":
                    frames[1]["state_snapshot"]["pieces"][1].pop("id")
                elif kind == "piece_limit":
                    frames[1]["state_snapshot"]["pieces"] = [{"id": j, "type": 15} for j in range(257)]
                elif kind == "row_limit":
                    frames = [dict(valid[0], turn=j) for j in range(1, 20002)]
                text = "\n".join(json.dumps(frame) for frame in frames) + "\n"
                Path(directory, "sample0.jsonl").write_text(text)
                data = rows()
                data[0]["turns"] = 3 if kind == "turn_count" else len(frames)
                data[0]["archive_sha256"] = hashlib.sha256(text.encode()).hexdigest()
                report = m.build_report(state(), data, A, history_dir=directory)
                metric = report["arms"]["baseline"]["stages"]["two_russias_observed"]
                self.assertEqual((metric["successes"], metric["unknown"]), (0, 2))
                self.assertEqual(report["history"]["counts"][reason], 1)

    def test_raw_path_uses_producer_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self.text()
            Path(directory, "sample0.jsonl").write_text(text)
            data = rows()
            data[0].update(turns=2, archive_sha256=hashlib.sha256(text.encode()).hexdigest())
            with patch("lib.soren_stage_ledger.MAX_BYTES", len(text.encode()) - 1):
                report = m.build_report(state(), data, A, history_dir=directory)
            self.assertEqual(report["history"]["counts"]["not_bounded_regular_file"], 1)
            self.assertEqual(report["arms"]["baseline"]["stages"]["two_russias_observed"]["successes"], 0)

    def test_path_traversal_and_symlink_are_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "source").write_text(self.text())
            Path(directory, "sample0.jsonl").symlink_to(Path(directory, "source"))
            data = rows()
            data[1]["archive"] = "../private.jsonl"
            report = m.build_report(state(), data, A, history_dir=directory)
            self.assertEqual(report["history"]["counts"]["invalid_archive_name"], 1)
            self.assertEqual(report["arms"]["baseline"]["stages"]["two_russias_observed"]["unknown"], 2)


class IOTests(unittest.TestCase):
    def test_strict_json_rejects_duplicate_nonfinite_and_garbage(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e999}', '{broken}'):
            with self.subTest(text=text):
                with self.assertRaises(m.EvidenceError):
                    m.decode(text)

    def test_huge_json_integer_is_fixed_error(self):
        # CPython may reject by its digit limit before our numeric bounds apply.
        text = '{"n":' + '9' * 5000 + '}'
        try:
            decoded = m.decode(text)
        except m.EvidenceError:
            pass
        else:
            self.assertFalse(m.number(decoded["n"]))

    def test_read_regular_digest_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory, "data")
            p.write_bytes(b'hello')
            self.assertEqual(m.read_file(p), ('hello', hashlib.sha256(b'hello').hexdigest()))
            with patch.object(m, "MAX_BYTES", 4):
                with self.assertRaises(m.EvidenceError):
                    m.read_file(p)
            with self.assertRaises(m.EvidenceError):
                m.read_file(directory)

    def test_file_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory, "data")
            p.write_bytes(b'hello')
            real_stat = m.os.stat
            def changed(path, **kwargs):
                p.write_bytes(b'changed')
                return real_stat(path, **kwargs)
            with patch.object(m.os, "stat", side_effect=changed):
                with self.assertRaisesRegex(m.EvidenceError, "changed"):
                    m.read_file(p)

    def test_fifo_does_not_block(self):
        if not hasattr(os, "mkfifo"):
            self.skipTest("POSIX FIFO only")
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory, "pipe")
            os.mkfifo(p)
            with self.assertRaises(m.EvidenceError):
                m.read_file(p)

    def test_cli_end_to_end_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            s, g = Path(directory, "state.json"), Path(directory, "games.jsonl")
            s.write_text(json.dumps(state()))
            g.write_text("\n".join(json.dumps(r) for r in rows()) + "\n")
            before = {p.name: p.read_bytes() for p in (s, g)}
            command = [sys.executable, str(SCRIPT), "--state", str(s), "--games", str(g),
                       "--baseline-hash", A, "--as-of", "2026-10-10T00:00:00+09:00", "--days", "7", "--json"]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["accepted_rows"], 4)
            self.assertEqual(report["sources"]["games_sha256"], hashlib.sha256(before[g.name]).hexdigest())
            self.assertEqual(before, {p.name: p.read_bytes() for p in (s, g)})
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()), sorted(before))
            g.write_text('{"secret":"NEVER_ECHO",broken}\n')
            completed = subprocess.run(command, capture_output=True, text=True, timeout=5)
            self.assertEqual(completed.returncode, 2)
            self.assertNotIn("NEVER_ECHO", completed.stderr)
            self.assertFalse(completed.stdout)

    def test_offsets(self):
        self.assertEqual(m.timestamp("2026-10-10T00:00:00", m.offset("+09:00")),
                         datetime(2026, 10, 9, 15, tzinfo=timezone.utc))
        for value in ("+14:01", "+24:00", "JST", "+09:99"):
            with self.assertRaises(m.EvidenceError):
                m.offset(value)


if __name__ == "__main__":
    unittest.main()

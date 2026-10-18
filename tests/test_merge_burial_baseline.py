"""Regression for the docich#605 criterion-4 baseline tool (Python path).

Pins the recorded-burial geometry (the soviet_now PR #680 loss term with PR
#681's NO_MERGE reason gate), the trace aggregation used for post-deploy
history, and the CLI, against synthetic records in the production record
shape. Mirrors the JS-side contract in tests/test_soren91_burial_evidence.mjs.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.merge_burial_baseline import analyze_records, is_generic, pair_gaps, load_history  # noqa: E402

import strategy  # noqa: E402


def piece(i, t, x, y=-3.0, radius=0.6):
    return {'id': i, 'type': t, 'x': x, 'y': y, 'r': radius, 'rx': radius, 'ry': radius, 'angle': 0.0}


def record(turn, pieces, nt, nn, x, reason='HEIGHT_CONTROL', top=None, trace=None, strategy_hash='hash0'):
    return {'turn': turn, 'strategy_hash': strategy_hash, 'next_type': nt, 'next_next_type': nn,
            'decision_x': x, 'decision_reason': reason,
            'decision_top_y_after_drop': top if top is not None else -2.0,
            'state_snapshot': {'pieces': pieces}, 'merge_opportunity_trace': trace}


def lane_pieces():
    return [piece(1, 8, -1.0), piece(2, 8, 1.0)]


class IsGenericTests(unittest.TestCase):
    def test_no_merge_penalty_reason_is_generic(self):
        self.assertTrue(is_generic('AVOID_BLOCK_REACTIVE_PAIR_SEAT_LANE_REACTIVE_PAIRS_NO_MERGE_PENALTY'))

    def test_merge_and_phase_reasons_are_not_generic(self):
        self.assertFalse(is_generic('HEIGHT_CONTROL_DIRECT_MERGE'))
        self.assertFalse(is_generic('HEIGHT_CONTROL_OPEN_TWIN_MERGE'))
        self.assertTrue(is_generic('HEIGHT_CONTROL'))


class PairGeometryTests(unittest.TestCase):
    def test_strict_burial_matches_the_hook_gate(self):
        gs = {'pieces': lane_pieces(), 'next': {'type': 5}}
        pieces, drop = strategy._merge_route_geometry(gs)
        pairs = pair_gaps(pieces, 5)
        self.assertEqual(len(pairs), 1)
        lo, hi, bottom, top = pairs[0]
        dt, db = drop[4], -drop[5]
        # The recorded decision lands on the pair top (top-cover wedge).
        px, ptop = 0.0, top + (dt + db) + 0.1
        placed_bottom = ptop - dt - db
        self.assertTrue(lo + 0.25 < px < hi - 0.25)
        self.assertGreaterEqual(placed_bottom, top - 0.25)


class AnalyzeRecordsTests(unittest.TestCase):
    def test_recorded_burial_is_counted_and_safe_landing_is_not(self):
        rows = [
            record(0, lane_pieces(), 5, 9, 0.0, top=-1.66),
            record(1, lane_pieces(), 5, 9, 2.5, top=-4.0),
        ]
        s = analyze_records(rows)
        self.assertEqual(s['usable_turns'], 2)
        self.assertEqual(s['pair_opportunity_turns'], 2)
        self.assertEqual(s['recorded_top_cover_burial_strict'], 1)
        self.assertEqual(s['recorded_top_cover_burial_physical'], 1)
        self.assertEqual(s['recorded_gap_fill_overlap'], 0)

    def test_phase_and_large_piece_reasons_stay_physical_only(self):
        rows = [
            record(0, lane_pieces(), 5, 9, 0.0, reason='HEIGHT_CONTROL_DIRECT_MERGE', top=-1.66),
            record(1, lane_pieces(), 9, 2, 0.0, top=-1.66),
        ]
        s = analyze_records(rows)
        # The hook only builds lanes for a next piece SMALLER than the pair
        # type (a large piece over the pair is its own merge opportunity), so
        # the nt=9 record produces no lane at all.
        self.assertEqual(s['pair_opportunity_turns'], 1)
        self.assertEqual(s['recorded_top_cover_burial_physical'], 1)
        self.assertEqual(s['recorded_top_cover_burial_strict'], 0, 'DIRECT reason is not a strict burial')

    def test_trace_counts_and_redirects(self):
        rows = [
            record(0, lane_pieces(), 5, 9, 0.0, top=-1.66,
                   trace={'from_x': 0.0, 'from_reason': 'HEIGHT_CONTROL', 'to_x': 2.5, 'outcome': 'selected', 'attempts': []}),
            record(1, lane_pieces(), 5, 9, 2.5, top=-4.0,
                   trace={'from_x': 2.5, 'from_reason': 'HEIGHT_CONTROL', 'to_x': 2.5, 'outcome': 'kept_original', 'attempts': []}),
            record(2, lane_pieces(), 5, 9, 0.0, top=-1.66,
                   trace={'from_x': 0.0, 'from_reason': 'HEIGHT_CONTROL', 'outcome': 'other'}),
        ]
        s = analyze_records(rows)
        t = s['merge_opportunity_trace']
        self.assertEqual(t['turns'], 3)
        self.assertEqual(t['selected'], 1)
        self.assertEqual(t['kept_original'], 1)
        self.assertEqual(t['other'], 1)
        self.assertEqual(t['redirects'], 1, 'selected with a moved x counts as a redirect')

    def test_strategy_hashes_separate_pre_and_post_deploy_records(self):
        rows = [
            record(0, lane_pieces(), 5, 9, 2.5, top=-4.0, strategy_hash='old'),
            record(1, lane_pieces(), 5, 9, 2.5, top=-4.0, strategy_hash='new'),
        ]
        s = analyze_records(rows)
        self.assertEqual(s['strategy_hashes'], {'old': 1, 'new': 1})

    def test_records_without_pairs_are_skipped_from_rates(self):
        rows = [record(0, [piece(1, 9, 0)], 5, 9, 0.0, top=-1.66)]
        s = analyze_records(rows)
        self.assertEqual(s['pair_opportunity_turns'], 0)
        self.assertEqual(s['recorded_top_cover_burial_strict'], 0)


class LoadHistoryTests(unittest.TestCase):
    def test_load_history_tags_files_and_skips_malformed_lines(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'game_0001.jsonl'
            path.write_text(json.dumps(record(0, lane_pieces(), 5, 9, 0.0, top=-1.66)) + '\n{not json\n', encoding='utf-8')
            records = load_history(path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['file'], 'game_0001.jsonl')


if __name__ == '__main__':
    unittest.main()

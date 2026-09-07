"""Final executable choices must preserve available merge routes without bypassing safety."""
import os
import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import strategy
import strategy_runner as runner
from extract_decide_hash import compute_hash_from_source


def piece(i, t, x, y=-3.0, radius=0.6):
    return {'id': i, 'type': t, 'x': x, 'y': y, 'r': radius, 'rx': radius, 'ry': radius}


def candidate(x, y=-2.0, grade='NO', **extra):
    return dict(x=x, landing_y=y, merge_grade=grade, crosses_deadline=False,
                merge_result_crosses_deadline=False, wall_rotation_risk=False,
                risk_top_y_after_drop=y+0.3, top_y_after_drop=y+0.3,
                deadline_y=3.38, deadline_margin=3.38-y-0.3, **extra)


def state(pieces, nt=2, nn=9):
    return {'pieces': pieces, 'next': {'type': nt, 'r': .3, 'rx': .3, 'ry': .3},
            'nextNext': {'type': nn}, 'deadline_crossed': False}


def analysis(results):
    return {'results': results, 'reactor': {'deadline_crossed': False, 'deadline_margin': 3.0},
            'deadline': {'deadline_crossed': False, 'deadline_y':3.38, 'top_edge_y':-2.0}}


class MergeOpportunityTests(unittest.TestCase):
    def choose(self, gs, results, x=0):
        # Exercise the actual post-runtime consumer, including the existing safety pass.
        return runner.apply_strategy_final_decision(strategy, {'x': x, 'reason': 'HEIGHT_CONTROL'}, analysis(results), gs)

    def test_nextnext_target_is_not_capped_when_a_safe_side_exists(self):
        gs = state([piece(1,9,0)])
        self.assertEqual(self.choose(gs,[candidate(0),candidate(2,-3.5)])['x'],2)

    def test_wide_drop_offset_still_caps_target(self):
        gs=state([piece(1,9,0)]); gs['next'].update(r=.8,rx=.8,ry=.8)
        self.assertEqual(self.choose(gs,[candidate(.7),candidate(2,-3.5)],.7)['x'],2)

    def test_first_small_piece_does_not_fill_clear_large_pair_gap(self):
        gs=state([piece(1,9,-.8),piece(2,9,.8)],nn=5)
        self.assertEqual(self.choose(gs,[candidate(0,-3),candidate(2,-3.5)])['x'],2)

    def test_pair_gap_remains_protected_when_pair_type_is_nextnext(self):
        gs=state([piece(1,9,-.8),piece(2,9,.8)],nn=9)
        self.assertEqual(self.choose(gs,[candidate(0,-3),candidate(2,-3.5)])['x'],2)

    def test_small_pair_can_keep_a_vibration_placement(self):
        gs=state([piece(1,3,-.6),piece(2,3,.6)],nn=5)
        self.assertEqual(self.choose(gs,[candidate(0,-3),candidate(2,-3.5)])['x'],0)

    def test_available_large_direct_merge_beats_nonmerge_placement(self):
        gs=state([piece(1,9,1)],nt=9,nn=5)
        self.assertEqual(self.choose(gs,[candidate(0),candidate(1,-2,'DIRECT')])['x'],1)

    def test_existing_direct_merge_is_not_replaced_by_future_lane(self):
        gs=state([piece(1,9,0)],nt=2)
        self.assertEqual(self.choose(gs,[candidate(0,grade='DIRECT'),candidate(2,-3.5)])['x'],0)

    def test_only_unsafe_alternative_does_not_force_a_switch(self):
        gs=state([piece(1,9,0)])
        other=candidate(2,-3.5); other['crosses_deadline']=True
        self.assertEqual(self.choose(gs,[candidate(0),other])['x'],0)

    def test_no_alternative_keeps_original_choice(self):
        self.assertEqual(self.choose(state([piece(1,9,0)]),[candidate(0)])['x'],0)

    def test_already_buried_nextnext_piece_does_not_reserve_a_lane(self):
        gs=state([piece(1,9,0),piece(2,4,0,-1.5)],nn=9)
        self.assertEqual(self.choose(gs,[candidate(0,.0),candidate(2,-3.5)])['x'],0)

    def test_next_of_same_type_is_not_a_foreign_cap(self):
        gs=state([piece(1,9,0)],nt=9,nn=9)
        self.assertEqual(self.choose(gs,[candidate(0),candidate(2,-3.5)])['x'],0)

    def test_policy_exception_keeps_safe_choice(self):
        def broken(*args):
            raise RuntimeError('candidate failure')
        module = SimpleNamespace(merge_opportunity_alternatives=broken)
        original = {'x': 0, 'reason': 'safe'}
        self.assertEqual(runner.apply_merge_opportunity_policy(module, original, analysis([candidate(0)]), state([])), original)

    def test_recorded_geometry_rejection_cannot_be_bypassed_by_reason(self):
        fx=json.loads((ROOT/'tests/fixtures/merge_opportunities/turn59.json').read_text())
        module=SimpleNamespace(
            DEADLINE_ALLOW_DIRECT_CROSS=True,
            merge_opportunity_alternatives=lambda *args: [
                {'x':.55,'reason':'OPEN_TWIN_MERGE_DESPERATE'}])
        original={'x':-2.0,'reason':'safe'}
        result=runner.apply_merge_opportunity_policy(module,original,fx['analysis'],fx['game_state'])
        self.assertEqual(result,original)

    def test_rejected_first_candidate_does_not_hide_a_safe_second_choice(self):
        fx=json.loads((ROOT/'tests/fixtures/merge_opportunities/turn59.json').read_text())
        module=SimpleNamespace(merge_opportunity_alternatives=lambda *args: [
            {'x':.55,'reason':'first'}, {'x':-2.8,'reason':'second'}])
        result=runner.apply_merge_opportunity_policy(module,{'x':-2.,'reason':'safe'},fx['analysis'],fx['game_state'])
        self.assertEqual(result['x'],-2.8)

    def test_nonfinite_geometry_does_not_replace_safe_choice(self):
        gs=state([piece(1,9,0)]); gs['pieces'][0]['x']=float('nan')
        self.assertEqual(self.choose(gs,[candidate(0),candidate(2,-3.5)])['x'],0)

    def test_future_route_is_not_protected_during_emergency(self):
        gs=state([piece(1,9,0)]); gs['deadline_crossed']=True
        self.assertEqual(strategy.merge_opportunity_alternatives(gs,analysis([candidate(0),candidate(2,-3.5)]),{'x':0}),[])

    def test_separated_large_pieces_do_not_reserve_whole_board(self):
        gs=state([piece(1,9,-2),piece(2,9,2)],nn=5)
        self.assertEqual(self.choose(gs,[candidate(0,-3),candidate(2,-3.5)])['x'],0)

    def test_existing_exposed_twin_attempt_is_not_sacrificed_for_nextnext(self):
        gs=state([piece(1,9,0),piece(2,2,.3,-2.5,.2)],nt=2)
        result=runner.apply_strategy_final_decision(strategy,{'x':0,'reason':'OPEN_TWIN_MERGE'},
                                                   analysis([candidate(0),candidate(2,-3.5)]),gs)
        self.assertEqual(result['x'],0)

    def test_recorded_boards_keep_nextnext_lane_through_full_pipeline(self):
        settings={'V763_DIVERSITY_W':'1.0','V767_BUNDLE':'0',
                  'ANALYZE_BOARD_VERTICAL_LANE_DIRECT':'1','ANALYZE_BOARD_MERGE_TOP_MODEL':'2',
                  'ANALYZE_BOARD_WALL_CLAMP':'1','ANALYZE_BOARD_LANDING_ARC':'0'}
        for turn, expected in ((39,.6),(14,1.2),(36,1.52)):
            with self.subTest(turn=turn), patch.dict(os.environ,settings):
                gs=json.loads((ROOT/f'tests/fixtures/merge_opportunities/route_turn{turn}.json').read_text())['game_state']
                an=runner.build_analysis(gs)
                d=strategy.decide(gs,an)
                d=runner.enforce_deadline_safety(d,an,gs,strategy)
                d=runner.apply_strategy_final_decision(strategy,d,an,gs)
                self.assertAlmostEqual(d['x'],expected)

    def test_policy_hook_and_helper_change_rollback_identity(self):
        template="def decide(gs,an):\n return {'x':0}\ndef threshold():\n return %s\ndef merge_opportunity_alternatives(gs,an,d):\n return threshold()\n"
        self.assertNotEqual(compute_hash_from_source(template%'1'),compute_hash_from_source(template%'2'))


if __name__=='__main__': unittest.main()

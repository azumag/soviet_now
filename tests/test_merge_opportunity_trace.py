"""Saved history explains accepted and safety-rejected reroutes."""
import io
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from tests.test_merge_opportunity_preservation import state, piece, candidate, analysis
import strategy
import strategy_runner as runner

class MergeTraceTests(unittest.TestCase):
    def test_accepted_route_records_original_choice_without_mutating_it(self):
        original={'x':0.,'reason':'HEIGHT_CONTROL'}
        gs=state([piece(1,9,0)])
        an=analysis([candidate(0),candidate(2,-3.5)])
        result=runner.apply_merge_opportunity_policy(strategy,original,an,gs)
        self.assertEqual(result['x'],2.)
        trace=result.get('merge_opportunity_trace',{})
        self.assertEqual(trace.get('from_x'),0.)
        self.assertEqual(trace.get('from_reason'),'HEIGHT_CONTROL')
        self.assertEqual(trace.get('outcome'),'selected')
        self.assertEqual(trace.get('attempts'),[{'x':2.,'safe_x':2.,'accepted':True}])
        self.assertEqual(original,{'x':0.,'reason':'HEIGHT_CONTROL'})
        output=io.StringIO()
        runner.record_turn(output,1,gs,result,an,strategy_hash='test')
        self.assertEqual(json.loads(output.getvalue()).get('merge_opportunity_trace'),trace)

    def test_all_rejected_records_redirect_without_adopting_it(self):
        fx=json.loads((Path(__file__).parent/'fixtures/merge_opportunities/turn59.json').read_text())
        module=SimpleNamespace(merge_opportunity_alternatives=lambda *args:[{'x':.55}])
        result=runner.apply_merge_opportunity_policy(module,{'x':-2.,'reason':'safe'},fx['analysis'],fx['game_state'])
        self.assertEqual((result['x'],result['reason']),(-2.,'safe'))
        trace=result.get('merge_opportunity_trace',{})
        self.assertEqual(trace.get('outcome'),'kept_original')
        self.assertEqual(trace.get('attempts'),[{'x':.55,'safe_x':-2.,'accepted':False}])

    def test_no_alternative_does_not_expand_history(self):
        original={'x':0.,'reason':'safe'}
        self.assertEqual(runner.apply_merge_opportunity_policy(strategy,original,analysis([candidate(0)]),state([])),original)

if __name__=='__main__':unittest.main()

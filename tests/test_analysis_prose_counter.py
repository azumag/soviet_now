import hashlib
import json
import unittest
from tests.test_analysis_evidence_contract import load

class ProseCounterTests(unittest.TestCase):
    def check(self, prose='', count=None, mutate=None):
        e={'game_count':13,'founded_games':count,'games':[{'file':'game_history/g.jsonl','turns':[1]}], 'observed_next_types':[3]}
        c={'version':1,'decision':'implement','evidence_sha256':hashlib.sha256(load().encode(e)).hexdigest(),
           'game_count':13,'founded_games':count,
           'hypotheses':[{'id':'H','claim':'One cause','evidence':[{'file':'game_history/g.jsonl','turn':1}]}],
           'changes':[{'hypothesis_id':'H','target':'strategy.py.staging','mechanism':'One change','required_next_types':[3]}]}
        if mutate:mutate(c)
        return load().validate(prose+'\n```analysis_contract\n'+json.dumps(c)+'\n```\n',e)

    def test_unknown_cannot_be_numeric_in_body(self):
        self.assertIn('prose_founding_count_mismatch',self.check('soviet=0/13')['errors'])

    def test_known_count_and_denominator(self):
        for text in ('soviet=1/13','soviet=2/12','soviet=2/12.','soviet=2/13 then soviet=0/13'):
            with self.subTest(text=text):self.assertEqual(self.check(text,2)['decision'],'reject')

    def test_markdown_unicode_spacing_and_labels(self):
        for text in ('**SOVIET** = **0** / **13**','__soviet__=__0__/__13__','`soviet` : `0/13`','Ｓｏｖｉｅｔ＝０／１３',
                     'soviet =\n0 / 13','founded_games=0/13','soviet_counter=0/13','ソ連建国数: 0/13'):
            with self.subTest(text=text):self.assertEqual(self.check(text)['decision'],'reject')

    def test_contract_strings_and_escaped_unicode(self):
        for field in ('claim','mechanism'):
            def mutate(c):
                c['hypotheses' if field=='claim' else 'changes'][0][field]='ＳＯＶＩＥＴ＝０／１３'
            with self.subTest(field=field):self.assertEqual(self.check(mutate=mutate)['decision'],'reject')

    def test_hold_does_not_exempt_contradiction(self):
        def mutate(c):c.update(decision='hold',hypotheses=[],changes=[],reason='soviet=0/13')
        self.assertEqual(self.check(mutate=mutate)['decision'],'reject')

    def test_quotes_negation_and_previous_batch_are_not_exempt(self):
        for text in ('> soviet=0/13','not soviet=0/13','previous batch: soviet=0/13'):
            with self.subTest(text=text):self.assertEqual(self.check(text)['decision'],'reject')

    def test_unknown_and_other_metrics_are_allowed(self):
        for text in ('soviet=unknown','soviet=unknown/13','russia=0/13',
                     'known_founded_games=0 unknown_counter_games=13','known_founded_games=0/13'):
            with self.subTest(text=text):self.assertTrue(self.check(text)['ok'])
        self.assertTrue(self.check('soviet=2/13',2)['ok'])

if __name__=='__main__':unittest.main()

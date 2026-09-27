import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('radio_game_context', ROOT / 'lib/radio_game_context.py')
context = importlib.util.module_from_spec(spec)
spec.loader.exec_module(context)


class RadioGameContextTests(unittest.TestCase):
    def test_only_ready_active_identifies_current_game(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'switch.json'
            for state, expected in [
                ({'phase': 'ready', 'game': 'sorengame', 'active': {'game': 'hanjuku-hero'}, 'candidate': {'game': 'soren91'}}, 'hanjuku-hero'),
                ({'phase': 'starting', 'active': {'game': 'sorengame'}}, 'unknown'),
                ({'phase': 'ready', 'active': None}, 'unknown'),
                ({'phase': 'ready', 'active': {'game': 'bad\nname'}}, 'unknown'),
                ([], 'unknown')]:
                with self.subTest(state=state):
                    path.write_text(json.dumps(state))
                    self.assertEqual(context.active_game(path), expected)
            path.write_text('{')
            self.assertEqual(context.active_game(path), 'unknown')
            path.unlink()
            self.assertEqual(context.active_game(path), 'unknown')

    def test_hanjuku_removes_old_score_and_player_claim_keeps_topic(self):
        prompt = ('自分自身がソ連ゲームをプレイしているプレイヤーでもあります。\n'
                  '【状況】ゲーム500回目開始。前回スコア911点。\n最高スコア: 1200点。\n'
                  '【今回の脱線テーマ指定】\nソ連の歴史について話す\n【出力】\n本文')
        result = context.ground(prompt, 'hanjuku-hero')
        for stale in ('911', '1200', '500回目', '自分自身がソ連ゲームをプレイ'):
            self.assertNotIn(stale, result)
        self.assertIn('現在のメイン画面: 半熟英雄', result)
        self.assertIn('ソ連の歴史について話す', result)
        self.assertIn('【出力】\n本文', result)

    def test_soren_unchanged_unknown_and_headerless_grounded(self):
        prompt = '【状況】ゲーム5回目開始。前回スコア911点。\n【出力】本文'
        self.assertEqual(context.ground(prompt, 'sorengame'), prompt)
        self.assertNotIn('911', context.ground(prompt, 'unknown'))
        self.assertIn('現在のメイン画面: NetHack', context.ground('雑談テーマ', 'nethack'))

    def test_generation_guards_prepass_and_snapshot(self):
        source = (ROOT / 'broadcast/radio_engine.sh').read_text()
        self.assertLess(source.index('lib/radio_game_context.py'), source.index('prompt_snapshot=$(cat'))
        self.assertLess(source.index('lib/radio_game_context.py'), source.index('local _prepass_prompt_file'))

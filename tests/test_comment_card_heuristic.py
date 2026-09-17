"""Preserve the live card classifier fix when integrating reviewed model chains."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CardHeuristicTests(unittest.TestCase):
    def classify(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'comments.txt'
            path.write_text(text + '\n', encoding='utf-8')
            result = subprocess.run(
                ['bash', '-c', 'source broadcast/comment.sh; _classify_comments_heuristic "$1"',
                 'card-heuristic-test', str(path)], cwd=ROOT,
                env={**os.environ, 'AI_PRIORITY_NOW_EPOCH': '1790254800'},
                text=True, capture_output=True, check=True,
            )
            return json.loads(result.stdout)[0]

    def test_card_notification_formats(self):
        for text in (
            'dociai: alice が【カードA】赤いカードを獲得しました',
            'dociai: alice が [カードA] 赤いカードを獲得しました',
            'dociai: alice が10連ガチャで赤いカードを獲得しました',
            'alice が【素材: 金】カードを獲得しました',
        ):
            with self.subTest(text=text):
                self.assertEqual(self.classify(text)['category'], 'card_gacha')

    def test_non_acquisition_is_not_a_card_notification(self):
        for text in ('alice: カードの素材: 金は何ですか？', 'alice: こんにちは',
                     'alice: が【カードA】赤いカードを獲得できません'):
            with self.subTest(text=text):
                self.assertNotEqual(self.classify(text)['category'], 'card_gacha')

    def test_preserves_source_mapping(self):
        result = self.classify('dociai: alice が【カードA】素材: 赤を獲得しました')
        self.assertEqual(result['index'], 1)
        self.assertEqual(result['user'], 'dociai')
        self.assertEqual(result['comment'], 'alice が【カードA】素材: 赤を獲得しました')


if __name__ == '__main__':
    unittest.main()

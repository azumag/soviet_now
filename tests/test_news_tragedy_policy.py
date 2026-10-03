"""Grounded tragedy policy: unit, CLI, live shell filter and priority regressions."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'lib'))
from news_topic_filter import is_uncontextualized_tragedy
from news_priority import choose_news_block


DENIED = [
    ('子どもが交通事故で死亡、母親が涙', ''),
    ('幼児が亡くなった 最後の笑顔に悲しみ', '家族の思い出と葬儀の様子を紹介。'),
    ('小学生が事故に遭いけが、現場の映像', '衝撃の一部始終を公開。'),
    ('男児が川で溺れ死亡', '政治家も「悲しい」と反応した。'),
    ('首相が少女殺害に哀悼の意', '政府と警察が事件の経緯を説明。'),
    ('Child died in crash, family shares final photo', 'The president expressed grief.'),
    ('小学生が死亡、再発防止を願う', '安全が大切だという教訓。'),
    ('園児が死亡', '安全対策を見直すという情報は未確認。'),
    ('園児が死亡', '安全対策の改善は報じられていない。'),
    ('園児が死亡', '安全対策を見直す予定はない。'),
    ('Child killed', 'No evidence that safety standards were violated.'),
    ('園児が死亡 - 安全基準の改正を検討', ''),  # outlet suffix is not evidence
    ('園児が死亡', 'source: 安全基準を改正\nhttps://example.com/安全基準を改正'),
    ('園児が死亡', '【内部メタ情報・読み上げ禁止】媒体=安全対策の改善 / 公開日時=不明'),
]
ALLOWED = [
    ('園児が死亡、バスの置き去り防止装置を義務化', ''),
    ('男児が川で死亡', '自治体が河川の安全管理の不備を検証した。'),
    ('児童虐待で死亡', '第三者委員会が児童相談所の監督体制の不備を指摘した。'),
    ('子どもが交通事故で死亡', '通学路の道路構造を見直す。'),
    ('子どもが死亡', '事故原因を検証した報告が製品欠陥を指摘した。'),
    ('候補者が殺害された', '選挙妨害の違反を調査する。'),
    ('労働者が工場で死亡', '労働環境の安全対策の欠陥を調査した。'),
    ('Child died on school bus', 'Safety inspections failed, inquiry found.'),
    ('空爆で市民が死亡', '国際人道法の違反を調査する。'),
    ('地震で住民が死亡', '自治体が避難所を開設した。'),
    ('政府が予算案を提出', ''),
    ('与党が税制改正を提案', ''),
    ('野党が税制改正を提案', ''),
    ('首相と野党党首が国会で討論', ''),
    ('Parliament debates death penalty reform', ''),
]


class NewsTragedyPolicyTest(unittest.TestCase):
    def test_denied_without_grounded_benefit(self):
        for title, body in DENIED:
            with self.subTest(title=title, body=body):
                self.assertTrue(is_uncontextualized_tragedy(title, body))

    def test_keeps_grounded_public_benefit_and_ordinary_politics(self):
        for title, body in ALLOWED:
            with self.subTest(title=title):
                self.assertFalse(is_uncontextualized_tragedy(title, body))

    def run_filters(self, title, body):
        block = f'■ {title}\n{body}'
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tmp').mkdir()
            news = root / 'news.txt'
            news.write_text(block)
            # Politics feed is intentionally not an exemption.
            meta = root / 'tmp/news_meta.json'
            meta.write_text(json.dumps({title: {'source_key': 'nhk_politics'}}))
            paths = [root / name for name in ('titles', 'keys', 'topics', 'urls')]
            for path in paths:
                path.touch()
            cli = subprocess.run(
                [sys.executable, str(ROOT / 'lib/news_filter.py'), 'filter_unread',
                 str(paths[0]), str(paths[1]), str(news), str(paths[3]), str(meta)],
                text=True, capture_output=True, check=True, cwd=root)
            env = dict(os.environ, ELOOP_LIB_DIR=str(ROOT),
                       PAST_NEWS_READ=str(paths[0]), PAST_NEWS_READ_KEYS=str(paths[1]),
                       PAST_NEWS_TOPIC_KEYS=str(paths[2]), PAST_NEWS_URL_HASHES=str(paths[3]))
            shell = subprocess.run(
                ['bash', '-c', '. "$ELOOP_LIB_DIR/broadcast/radio_news.sh"; _filter_unread_news_blocks'],
                input=block, text=True, capture_output=True, check=True, cwd=root, env=env)
            return cli.stdout.strip(), shell.stdout.strip()

    def test_both_selection_filters_apply_policy(self):
        for title, body in DENIED + ALLOWED:
            with self.subTest(title=title, body=body):
                outputs = self.run_filters(title, body)
                expected = (title, body) in ALLOWED
                for output in outputs:
                    self.assertEqual(bool(output), expected)

    def test_priority_cannot_rescue_rejected_tragedy(self):
        tragedy = '首相が少女殺害に哀悼の意'
        eligible = '野党が税制改正を提案'
        meta = {tragedy: {'source_key': 'nhk_politics'}}
        blocks = f'■ {tragedy}\n遺族が涙\n\n■ {eligible}\n税制を審議'
        for share in (0, 0.67, 1):
            self.assertIn(eligible, choose_news_block(
                blocks, meta=meta, political_share=share, rng=random.Random(7)))
        self.assertEqual('', choose_news_block(f'■ {tragedy}', meta=meta))
        allowed = '園児が死亡、バスの置き去り防止装置を義務化'
        self.assertIn(allowed, choose_news_block(f'■ {allowed}'))

    def test_prompts_require_grounded_benefit(self):
        for name in ('radio_news.md', 'radio_jiji.md', 'radio_jiji_research.md'):
            text = (ROOT / 'prompts' / name).read_text()
            self.assertIn('素材にない公益性・政策との関係を捏造して通さない', text)
            self.assertIn('本文・要約が不足して公益的文脈を確認できない悲劇は無理に採用しない', text)
            self.assertIn('支持政党・政治的立場で差別しない', text)
        text = (ROOT / 'broadcast/radio_corners.sh').read_text()
        self.assertGreaterEqual(text.count('【ニュース選定方針】'), 2)


if __name__ == '__main__':
    unittest.main()

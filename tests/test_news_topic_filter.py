#!/usr/bin/env python3
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from news_topic_filter import is_low_value_news_title, is_public_interest_news_title
from sports_filter import is_sports_title


class NewsTopicFilterTest(unittest.TestCase):
    def test_excludes_entertainment_lifestyle_and_product_promotion(self):
        excluded = [
            "人気アイドルが結婚発表、新ドラマにも出演",
            "コンビニ新商品、期間限定スイーツを発売",
            "新型iPhoneを発売、実機レビュー",
            "Best new smartphone hands-on review",
            "映画の推し事 人気俳優が主演する話題作",
            "夏の夜空に1万5千発の花火",
        ]
        for title in excluded:
            with self.subTest(title=title):
                self.assertTrue(is_low_value_news_title(title))

    def test_keeps_public_affairs_and_business(self):
        allowed = [
            "政府が新たな労働政策を閣議決定",
            "停戦協議をめぐり各国外相が会談",
            "中央銀行が政策金利を据え置き",
            "半導体企業が国内工場へ5兆円を投資",
            "スマートフォン販売をめぐり独禁法調査を開始",
        ]
        for title in allowed:
            with self.subTest(title=title):
                self.assertFalse(is_low_value_news_title(title))

    def test_public_interest_positive_gate(self):
        self.assertTrue(is_public_interest_news_title("中央銀行が政策金利を据え置き"))
        self.assertTrue(is_public_interest_news_title("停戦協議をめぐり各国外相が会談"))
        self.assertFalse(is_public_interest_news_title("人気ブランドのリュックが話題"))

    def test_multilingual_world_cup_is_sports(self):
        self.assertTrue(is_sports_title("البرازيل والقميص رقم 24 خلال كأس العالم 2026"))


if __name__ == "__main__":
    unittest.main()

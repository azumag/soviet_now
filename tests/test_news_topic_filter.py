#!/usr/bin/env python3
import pathlib
import sys
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from news_topic_filter import (
    is_low_value_news_title,
    is_public_affairs_beyond_incident_title,
    is_public_interest_news_title,
    is_tabloid_news_title,
)
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
            "前衛芸術家の回顧展を開催",
            "今年の24時間テレビの出演者を発表",
            "SpaceXのStarshipが10回目の飛行試験に成功",
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

    def test_excludes_tabloid_outlets_and_lurid_headlines(self):
        # 2026-10 に時事コーナーで実際に放送された見出し
        excluded = [
            "《愛人会社に政治資金8000万円》麻生太郎が抱える「政治とカネと女」の重大問題 - 文春オンライン",
            "高市首相“利益誘導”自慢の呆れたセンス…国交省1兆円交付金「確保」335回もアピール - 日刊ゲンダイDIGITAL",
            "鮮魚売り場に並ぶサンマに“異変”、気づいた釣り師が店員に耳打ちした理由 「どうしてこんなに…」 - Sirabee",
            "吉原のソープランドを摘発 売春場所に店内個室を提供か 責任者ら9人を逮捕 - FNNプライムオンライン",
            "「数十回以上」総武線で女子高生にわいせつ疑い 児相職員を逮捕 - 毎日新聞",
            "「デブすぎ臭すぎ」患者映り込む動画を無断投稿 徳洲会・千葉西総合病院が謝罪 - Yahoo!ニュース",
            "元警察署長を万引き容疑で書類送検 - 読売新聞",
        ]
        for title in excluded:
            with self.subTest(title=title):
                self.assertTrue(is_tabloid_news_title(title))
                self.assertTrue(is_low_value_news_title(title))

    def test_tabloid_outlet_match_is_exact_source_suffix(self):
        self.assertFalse(is_tabloid_news_title("Flash flood warning issued after encounter with storm - Reuters"))
        self.assertFalse(is_tabloid_news_title("首相、旧敵国条項の削除へ働きかけ - 47NEWS"))

    def test_lurid_term_kept_when_headline_is_about_institutional_response(self):
        self.assertFalse(is_tabloid_news_title("盗撮を処罰する法改正、国会で審議入り - NHKニュース"))

    def test_jiji_gate_requires_signal_beyond_incident_words(self):
        incident_only = [
            "消防局職員2人が「助けて!」 酔っぱらい虚偽の110番 容疑で書類送検 明石|社会 - 神戸新聞",
            "スーパー銭湯ロッカーを合鍵で開け物色の疑い、中国籍の男を再逮捕 - 読売新聞",
            "週間天気予報 三連休はお出かけ日和 - ウェザーニュース",
        ]
        for title in incident_only:
            with self.subTest(title=title):
                self.assertFalse(is_public_affairs_beyond_incident_title(title))
        public_affairs = [
            "尖閣諸島沖の領海に中国海警局の船４隻が侵入 - 読売新聞",
            "栃木・那須町長選「当選有効」 県選管の採決取り消し 東京高裁 - 毎日新聞",
            "京都府、同志社国際高への私学助成金の半額返還求める - 産経ニュース",
            "富山市職員による人口水増し、国勢調査票を捏造 - 読売新聞",
        ]
        for title in public_affairs:
            with self.subTest(title=title):
                self.assertTrue(is_public_affairs_beyond_incident_title(title))

    def test_multilingual_world_cup_is_sports(self):
        self.assertTrue(is_sports_title("البرازيل والقميص رقم 24 خلال كأس العالم 2026"))


if __name__ == "__main__":
    unittest.main()

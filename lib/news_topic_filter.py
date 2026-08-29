#!/usr/bin/env python3
"""Filter low-public-interest news topics from the radio news pool.

The news corner is for public affairs: society, politics, international affairs,
economics and business.  This filter is intentionally limited to clearly
consumer/entertainment-oriented titles so policy or industry stories are not
discarded merely because they mention a company or technology.
"""
from __future__ import annotations

import re
import unicodedata


def _norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").strip().lower()


_ENTERTAINMENT_TERMS = (
    "芸能", "エンタメ", "アイドル", "タレント", "お笑い", "芸人", "声優",
    "俳優", "女優", "主演", "歌手", "映画", "音楽", "花火", "芸術家",
    "テレビ番組", "24時間テレビ",
    "熱愛", "不倫", "恋人", "結婚発表", "離婚発表", "交際発表", "写真集", "♥",
    "ドラマ", "映画公開", "映画『", "映画「", "アニメ", "漫画", "コミック",
    "ライブ開催", "コンサート", "新曲", "ニューアルバム", "視聴率",
    "celebrity", "actor", "actress", "singer", "idol", "tv show",
    "box office", "anime", "manga", "concert", "new album",
)

_LIFESTYLE_TERMS = (
    "レシピ", "スイーツ", "グルメ", "コーデ", "着こなし", "占い", "ダイエット",
    "お取り寄せ", "食べ放題", "期間限定メニュー", "コンビニ新商品",
    "recipe", "horoscope", "fashion tips", "weight loss",
)

_DIRECT_PRODUCT_TERMS = (
    "新製品", "新商品", "新モデル", "製品発表", "商品発表", "予約開始",
    "予約受付", "実機レビュー", "先行レビュー", "開封レビュー", "価格と発売日",
    "買ってみた", "使ってみた", "おすすめ商品", "セール情報",
    "new product", "new model", "product launch", "pre-order", "preorder",
    "hands-on review", "product review", "buying guide",
)

_CONSUMER_PRODUCTS = (
    "iphone", "ipad", "pixel", "galaxy", "xperia", "スマホ", "スマートフォン",
    "イヤホン", "ヘッドホン", "カメラ", "レンズ", "テレビ", "家電", "腕時計",
    "playstation", "nintendo switch", "xbox", "ゲームソフト", "ゲーミング",
    "smartphone", "headphones", "earbuds", "camera", "gaming pc", "video game",
)

_PRODUCT_ACTIONS = (
    "発売", "販売開始", "登場", "予約", "レビュー", "値下げ", "セール", "価格",
    "launch", "release", "released", "available", "review", "discount", "sale",
)

_LOW_VALUE_OUTLETS = (
    "4gamer", "gamespark", "game spark", "ファミ通", "ねとらぼ", "carview",
    "ギズモード", "スポニチ", "日刊スポーツ", "oricon", "モデルプレス",
    "選挙ドットコム",
)

# Google News search/topic feeds sometimes return weakly related consumer stories.
# For those feeds, require at least one explicit public-affairs or economy signal
# in the headline.  Other curated feeds (NHK politics / Global Voices) do not use
# this positive gate.
_PUBLIC_INTEREST_TERMS = (
    # society, law, disasters, public services
    "社会", "事件", "事故", "逮捕", "容疑", "起訴", "判決", "裁判", "司法", "警察",
    "災害", "豪雨", "洪水", "地震", "津波", "台風", "山火事", "土砂", "避難", "復旧",
    "医療", "病院", "感染", "福祉", "介護", "教育", "学校", "子育て", "少子", "人口",
    "労働", "雇用", "賃金", "給与", "パワハラ", "人権", "市民権", "移民", "難民",
    "農業", "農家", "食料", "コメ", "環境", "気候", "公害", "インフラ",
    # politics, government, diplomacy, security
    "政府", "国会", "首相", "大統領", "閣僚", "外相", "防衛相", "知事", "市長", "議会",
    "選挙", "政党", "与党", "野党", "自民", "立民", "公明", "維新", "国民民主", "中道",
    "法案", "法律", "政策", "規制", "行政", "自治体", "省庁", "官庁", "予算", "税制",
    "外交", "会談", "協議", "条約", "制裁", "停戦", "戦争", "軍", "防衛", "武器", "安全保障",
    "中国", "北朝鮮", "ロシア", "ウクライナ", "米国", "アメリカ", "欧州", "eu ",
    # economy and business
    "経済", "景気", "物価", "インフレ", "金利", "為替", "円相場", "株価", "株式", "市場",
    "決算", "業績", "投資", "買収", "合併", "企業", "会社", "経営", "倒産", "破綻", "工場",
    "関税", "貿易", "輸出", "輸入", "銀行", "資産", "優待", "半導体", "供給網", "エネルギー",
    # English public-affairs feeds
    "government", "parliament", "congress", "president", "prime minister", "minister", "election",
    "policy", "law ", "court", "police", "arrest", "crime", "disaster", "flood", "earthquake",
    "wildfire", "hospital", "health", "education", "labor", "worker", "wage", "human rights",
    "citizenship", "migrant", "refugee", "diplomacy", "sanction", "ceasefire", "war ", "military",
    "economy", "inflation", "interest rate", "trade", "tariff", "investment", "business", "company",
)


def is_low_value_news_title(title: str) -> bool:
    """Return True for clearly entertainment, lifestyle or product-promo titles."""
    norm = _norm(title)
    if not norm:
        return False
    if any(term in norm for term in _ENTERTAINMENT_TERMS):
        return True
    if any(term in norm for term in _LIFESTYLE_TERMS):
        return True
    if any(term in norm for term in _DIRECT_PRODUCT_TERMS):
        return True
    if any(term in norm for term in _LOW_VALUE_OUTLETS):
        return True
    # A generic word such as 「発売」 alone can occur in business/regulatory news.
    # Require both a consumer-product noun and a launch/review/sale action.
    return (
        any(term in norm for term in _CONSUMER_PRODUCTS)
        and any(term in norm for term in _PRODUCT_ACTIONS)
    )


def is_public_interest_news_title(title: str) -> bool:
    """Return True when a headline explicitly concerns public affairs/business."""
    norm = _norm(title)
    return bool(norm) and any(term in norm for term in _PUBLIC_INTEREST_TERMS)


FILTER_REASON_LOW_VALUE_TOPIC = "low_value_topic"
FILTER_REASON_OUTSIDE_PUBLIC_AFFAIRS = "outside_public_affairs"

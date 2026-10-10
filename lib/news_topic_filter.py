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
    "テレビ番組", "24時間テレビ", "キャラクター",
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

_OUTSIDE_SCOPE_SCIENCE_TERMS = (
    "spacex", "starship", "宇宙探査", "宇宙望遠鏡", "探査機", "ロケット打ち上げ",
    "ロケットの打ち上げ", "飛行試験", "天文学",
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

# Weekly-magazine / soft-news outlets.  Matched exactly against the trailing
# " - 媒体名" of a Google News title so short names never hit ordinary words.
_TABLOID_OUTLETS = frozenset(_norm(name) for name in (
    "文春オンライン", "週刊文春", "日刊ゲンダイDIGITAL", "日刊ゲンダイ", "デイリー新潮",
    "週刊新潮", "女性自身", "週刊女性PRIME", "NEWSポストセブン", "SmartFLASH",
    "FRIDAYデジタル", "東スポWEB", "東スポ", "夕刊フジ", "zakzak", "日刊SPA!",
    "ENCOUNT", "Sirabee", "オトナンサー", "まいどなニュース", "CREA WEB",
    "集英社オンライン", "アサ芸プラス", "よろず～ニュース", "J-CAST ニュース",
    "J-CASTニュース", "デイリースポーツ", "New York Post", "Daily Mail", "The Sun", "TMZ",
))

# Sex crimes, petty scandal and clickbait framing.  A headline that also names a
# concrete institutional response (law, ordinance, Diet debate) is kept: the
# story is then about the system, not about the lurid detail.
_SENSATIONAL_TERMS = (
    "ソープ", "売春", "買春", "わいせつ", "猥褻", "盗撮", "痴漢", "性的暴行",
    "下着", "風俗店", "パパ活", "不倫", "愛人", "万引き", "不適切動画",
    "不適切投稿", "迷惑動画", "《", "嗚咽", "呆れた", "素顔", "ワケ", "“異変”",
    "ネット騒然", "話題に", "無断撮影", "無断投稿", "金賞",
)
_INSTITUTIONAL_RESPONSE_TERMS = (
    "法改正", "改正法", "法案", "条例", "制度", "国会", "閣議", "審議会", "規制",
)


def _source_suffix(title: str) -> str:
    match = re.search(r"\s[-–—]\s([^-–—]{1,80})$", title or "")
    return _norm(match.group(1)) if match else ""


def is_tabloid_news_title(title: str) -> bool:
    """Return True for tabloid outlets or lurid/clickbait headlines."""
    if _source_suffix(title) in _TABLOID_OUTLETS:
        return True
    norm = _norm(title)
    return any(term in norm for term in _SENSATIONAL_TERMS) and not any(
        term in norm for term in _INSTITUTIONAL_RESPONSE_TERMS
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
    "選管", "最高裁", "高裁", "地裁", "当局", "入管", "不法就労", "補助金", "助成金",
    "サイバー", "ランサム", "個人情報",
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
    if any(term in norm for term in _OUTSIDE_SCOPE_SCIENCE_TERMS):
        return True
    if any(term in norm for term in _LOW_VALUE_OUTLETS):
        return True
    if is_tabloid_news_title(title):
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


# Words that only say "a crime/incident happened".  A local arrest story carries
# these and nothing else; the jiji corner needs a wider public-affairs signal.
_INCIDENT_ONLY_TERMS = frozenset((
    "社会", "事件", "事故", "逮捕", "容疑", "起訴", "警察",
    "police", "arrest", "crime",
))


def is_public_affairs_beyond_incident_title(title: str) -> bool:
    """Return True when a public-affairs signal other than crime words is present."""
    # A suspect's nationality (「中国籍の男」) is not an international-affairs story.
    norm = re.sub(r"[一-龥ァ-ヶー]{1,8}籍", "", _norm(title))
    return bool(norm) and any(
        term in norm for term in _PUBLIC_INTEREST_TERMS if term not in _INCIDENT_ONLY_TERMS
    )


FILTER_REASON_LOW_VALUE_TOPIC = "low_value_topic"
FILTER_REASON_OUTSIDE_PUBLIC_AFFAIRS = "outside_public_affairs"


# A candidate gate, not a semantic proof of public benefit.
# Concrete institutional consequences and ongoing/widespread public danger
# also retain candidates; an enumerated preventive measure is not required.
# Broad words (politics, police, school, accident) are deliberately insufficient.
_PERSONAL_TRAGEDY_RE = re.compile(
    r"死亡|亡くな|なくなった|命を落と|命を失|死傷|負傷|遺体|死去|殺害|殺人|虐待|重傷|溺れ|溺死|転落|ひき逃げ|刺され|刺殺|性被害|性的被害|自殺|誘拐|行方不明|"
    r"(?:子ども|子供|幼児|児童|小学生|男児|女児|少年|少女|男性|女性|[0-9]+歳)[^。！？\n]{0,20}(?:事故|けが|被害)|"
    r"\b(?:die|dies|died|dead|death|deaths|kill|kills|killed|murder(?:ed)?|drown(?:ed|ing)?|"
    r"abuse(?:d)?|suicide|kidnapped|missing child|seriously injured)\b"
)
# Require a concrete subject + institutional/safety action in the same sentence.
# These are grounded signals in the supplied article, never generated rationales.
_PUBLIC_BENEFIT_RE = re.compile(
    r"(?:安全基準|安全対策|安全管理|再発防止策|防止対策|防止装置|点検体制|監督体制|"
    r"避難指示|避難勧告|避難所|救援物資|救助活動|支援制度|補償制度|救済制度|"
    r"児童相談所|通学路|道路構造|労働環境|医療体制|事故原因|製品欠陥|熱中症対策|検証報告|調査報告|第三者委員会|"
    r"停戦案|停戦協定|人道支援|国際人道法|戦争犯罪|政治暴力|選挙妨害|言論弾圧)"
    r"[^。！？\n]{0,50}(?:義務化|改正|改定|見直|改善|導入|設置|開設|発令|検証|勧告|"
    r"不備|欠陥|怠|不足|違反|調査|検討|提言|実施|拡大|合意|審議)|"
    r"\b(?:safety standards?|safety measures?|safety inspections?|warning systems?|"
    r"child protection|death penalty|evacuation orders?|evacuation shelters?|humanitarian aid|"
    r"ceasefire agreement|war crimes?)\b[^.!?\n]{0,80}"
    r"\b(?:reform|review|investigat\w*|require\w*|mandat\w*|implement\w*|"
    r"fail\w*|violat\w*|abolish\w*|debate\w*|issued|opened|approved|agreed)\b"
)
# Separate retention boundaries for political consequences and public safety.
# Neither politicians mentioning victims nor a generic government response is
# enough. These patterns describe a reported change, a public warning, or the
# scale/progression of a hazard, rather than a hypothetical lesson to invent.
_INSTITUTIONAL_IMPACT_RE = re.compile(
    r"(?:非常事態|緊急事態)[^。！？\n]{0,20}(?:宣言|発令)|"
    r"(?:選挙|投票|議会|国会|政権|憲法|統治)[^。！？\n]{0,20}"
    r"(?:延期|中止|停止|解散|継承|移譲|移行|権限移管)|"
    r"(?:現職の?)?(?:首相|大統領|国家元首|党首|選挙候補者)"
    r"(?:(?:が|は)(?:選挙演説中に|演説中に|襲撃で|銃撃で)?"
    r"(?:暗殺され|殺害され|銃撃され)|を(?:暗殺|殺害))|"
    r"\b(?:state of emergency|elections?|voting|parliament|transfer of power)\b"
    r"[^.!?\n]{0,50}\b(?:declared|postponed|cancelled|canceled|suspended|dissolved|transferred)\b|"
    r"\b(?:prime minister|president|head of state|election candidate)\s+"
    r"(?:was\s+)?(?:assassinated|killed during (?:an? )?(?:election |campaign )?(?:speech|rally))\b"
)
_PUBLIC_DANGER_RE = re.compile(
    r"(?:津波警報|大雨特別警報|避難命令|緊急安全確保)[^。！？\n]{0,20}(?:発表|発令|継続)|"
    r"(?:堤防|ダム)[^。！？\n]{0,15}決壊|"
    r"(?:山火事|洪水|浸水|感染症|有害物質)[^。！？\n]{0,20}(?:拡大|拡散|流出)|"
    r"(?:広域|広範囲|県全域|複数の市町村|大規模)[^。！？\n]{0,20}"
    r"(?:地震|津波|洪水|浸水|山火事|停電|断水|避難|被害)|"
    r"(?:地震|津波|洪水|台風|山火事|土砂災害)[^。！？\n]{0,30}"
    r"(?:(?:[1-9][0-9]+|数十|数百|数千|多数)(?:人|名)(?:が|の)?(?:死亡|死傷|行方不明)|"
    r"死者(?:[1-9][0-9]+|数十|数百|数千|多数)(?:人|名))|"
    r"\b(?:tsunami warning|flood warning|evacuation order)\b[^.!?\n]{0,40}\b(?:issued|active|extended)\b|"
    r"\b(?:wildfire|flooding|toxic spill)\b[^.!?\n]{0,40}\b(?:spreading|expanding|widespread)\b|"
    r"\b(?:earthquake|tsunami|floods?|wildfire)\b[^.!?\n]{0,40}"
    r"\b(?:[1-9][0-9]+|dozens|hundreds|thousands) (?:people )?(?:dead|killed|missing)\b"
)
_NO_EVIDENCE_RE = re.compile(
    r"(?:確認できない|確認されていない|未確認|根拠がない|記載がない|記載なし|報じられていない|"
    r"(?:実施|検討|見直し|調査)しない|予定はない|事実はない|行わない|"
    r"(?:宣言|延期|中止|停止|解散|発令)(?:しない|していない|せず|されず|されていない|されなかった)|"
    r"という噂|との噂|するべき|すべき|仮に|もし|された場合)|"
    r"\b(?:no evidence|unconfirmed|not reported|not confirmed|did not|will not)\b"
)


def is_uncontextualized_tragedy(title: str, article_text: str = "") -> bool:
    """Exclude personal tragedy unless supplied text has concrete public benefit.

    Retain documented institutional impacts, public warnings and widespread
    hazards even when no preventive measure is yet reported. A disaster casualty
    count of ten or more is a scale signal, not an assertion of a policy lesson.
    Headline-only personal tragedy is withheld without one of these contexts.
    Metadata (outlet, feed, URL, timestamp) must not count as article evidence.
    Matching signals only retain a candidate for the grounded editorial prompt;
    the model must still reject voyeurism and must never invent a justification.
    """
    title = re.sub(r"\s+[-–—|]\s+[^-–—|]{1,80}$", "", title or "")
    lines = [title] + [
        line for line in (article_text or "").splitlines()
        if not re.match(r"\s*(?:https?://|【内部メタ|(?:source|source_key|url|published_at|媒体|出典)\s*[:=：])", line, re.I)
    ]
    text = _norm("\n".join(lines))
    if not _PERSONAL_TRAGEDY_RE.search(text):
        return False
    sentences = re.split(r"[。！？.!?\n]", text)
    return not any(
        any(pattern.search(sentence) for pattern in (
            _PUBLIC_BENEFIT_RE, _INSTITUTIONAL_IMPACT_RE, _PUBLIC_DANGER_RE,
        )) and not _NO_EVIDENCE_RE.search(sentence)
        for sentence in sentences
    )

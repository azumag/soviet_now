#!/usr/bin/env python3
"""同じ事件の別媒体・別見出しを既読扱いにする判定 (lib/news_filter.py) の回帰テスト。

2026-08-26 の実障害: jiji コーナーの既読判定はタイトル一致と URL 一致だけだったため、
Google News が並べる「同じ事件の別媒体見出し」がすべて未読として通り、
パキスタン病院火災を Reuters / Al Jazeera / NYT の見出しで 3 回、
ホルムズ海峡のイラン・オマーン合意を FT / CNBC / CBS で 3 回読み上げた。

フィクスチャ (tests/fixtures/) は当日の本番データそのもの:
  jiji_past_titles_20260826.txt   … tmp/history/.past_jiji_titles.txt (既読 100 件)
  google_headlines_20260826.txt   … tmp/google_headlines.txt (候補 41 件)
頻出語 (ukraine / iran) の判定はコーパス規模に依存するため、本番と同じ規模で検証する。
"""
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILTER_PATH = os.path.join(ROOT, "lib", "news_filter.py")
FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")

spec = importlib.util.spec_from_file_location("news_filter", FILTER_PATH)
nf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nf)


def load_lines(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip()]


PAST_TITLES = load_lines("jiji_past_titles_20260826.txt")
HEADLINES = [ln[2:].strip() for ln in load_lines("google_headlines_20260826.txt") if ln.startswith("■ ")]
CORPUS = PAST_TITLES[-60:] + HEADLINES

# 当日 3 回読み上げてしまったパキスタン病院火災 (媒体違い) と、候補に残っていた 4 本目
PAKISTAN = [
    "At least 15 infants killed in Islamabad hospital fire, local TV reports - Reuters",
    "At least 14 newborn babies killed in Pakistan hospital nursery fire - Al Jazeera",
    "14 Newborns Die in Hospital Fire in Pakistan’s Capital - The New York Times",
    "Fire in a hospital nursery kills 14 newborns in Pakistan’s capital - AP News",
]
HORMUZ = [
    "Iran and Oman edge towards deal on Strait of Hormuz - Financial Times",
    "Iran and Oman prepare Hormuz deal as U.S. holds back on secondary sanctions - CNBC",
    "Live Updates: Iran and Oman edge toward temporary Strait of Hormuz reopening plan, "
    "but Tehran insists U.S. lift blockade - CBS News",
]
NEPAL = [
    "Hundreds missing and at least 8 killed after avalanche triggers deadly flash floods in Nepal - AP News",
    "Hundreds missing, including 291 foreign tourists, after flash flood on Nepal-Tibet border - BBC",
]


def same(a, b, corpus=None):
    generic = nf.generic_tokens(CORPUS if corpus is None else corpus)
    return nf.same_event(nf.event_tokens(a), nf.event_tokens(b), generic)


class FixtureSanityTest(unittest.TestCase):
    def test_fixtures_reflect_the_incident(self):
        # 既読履歴に「同じ事件の別見出し」が実際に 3 本入っていたこと自体が障害の証拠
        self.assertEqual(sum(1 for t in PAST_TITLES if t in PAKISTAN), 3)
        self.assertIn(PAKISTAN[3], HEADLINES)  # 4 本目は候補として残っていた


class SameEventTest(unittest.TestCase):
    def test_pakistan_variants_are_the_same_event(self):
        self.assertTrue(same(PAKISTAN[0], PAKISTAN[1]))
        self.assertTrue(same(PAKISTAN[1], PAKISTAN[2]))
        self.assertTrue(same(PAKISTAN[1], PAKISTAN[3]))

    def test_hormuz_variants_are_the_same_event(self):
        self.assertTrue(same(HORMUZ[0], HORMUZ[1]))
        self.assertTrue(same(HORMUZ[0], HORMUZ[2]))

    def test_nepal_variants_are_the_same_event(self):
        self.assertTrue(same(NEPAL[0], NEPAL[1]))

    def test_distinct_stories_are_not_merged(self):
        pairs = [
            (NEPAL[0], "More than 50 kidnapped as violent gang attack in Haiti leaves 47 dead - NPR"),
            (PAKISTAN[0], "Six Chinese nationals among seven killed in Russian gas plant fire - Reuters"),
            # 共有語が頻出語 (ukraine) と一般語 (minister / war) だけの別事件
            ("Vatican foreign minister, in Moscow, says Ukraine war must end - Reuters",
             "EXCLUSIVE: Ukraine needs changes to avoid losing war, ousted defence minister says - Reuters"),
            # ホルムズでも「タンカー攻撃」は合意交渉とは別の展開
            (HORMUZ[0], "New Tanker Strike in Hormuz as Iran Vows Retaliation for U.S. Economic Offensive - WSJ"),
        ]
        for a, b in pairs:
            with self.subTest(a=a[:40], b=b[:40]):
                self.assertFalse(same(a, b))

    def test_generic_tokens_pick_up_the_periods_common_words(self):
        generic = nf.generic_tokens(CORPUS)
        self.assertIn("ukraine", generic)
        self.assertIn("iran", generic)
        # 事件固有の語は、同じ事件の見出しが複数並んでも頻出語にしない
        for specific in ("hormuz", "pakistan", "hospital", "nepal"):
            with self.subTest(token=specific):
                self.assertNotIn(specific, generic)

    def test_generic_tokens_need_a_large_enough_corpus(self):
        # 小さいコーパスでは頻出語と固有語を区別できないので判定しない
        self.assertEqual(nf.generic_tokens(HEADLINES[:10]), frozenset())

    def test_outlet_suffix_is_ignored(self):
        # 媒体名だけが違う同一見出しは同一と判定する
        self.assertTrue(same("Flood kills dozens in Nepal village - Reuters",
                             "Flood kills dozens in Nepal village - BBC"))
        # 媒体名を共有するだけでは同一にしない
        self.assertFalse(same("China prepares for more heavy rain from back-to-back storms - Reuters",
                              "Some oil companies to avoid ships on Iran blacklist, sources say - Reuters"))

    def test_japanese_headlines(self):
        self.assertTrue(same("イスラマバードの病院で新生児室が火災、15人死亡",
                             "パキスタンの病院で火災 新生児室の15人が死亡", corpus=[]))
        self.assertFalse(same("イスラマバードの病院で新生児室が火災、15人死亡",
                              "ネパールで鉄砲水、数百人が行方不明", corpus=[]))


class FilterUnreadTest(unittest.TestCase):
    """CLI 経由で、既読事件の別見出しが候補から落ちることを確認する。"""

    def run_filter(self, past_titles, headlines, env=None):
        with tempfile.TemporaryDirectory() as tmp:
            past = os.path.join(tmp, "past_titles.txt")
            keys = os.path.join(tmp, "past_keys.txt")
            news = os.path.join(tmp, "news.txt")
            with open(past, "w", encoding="utf-8") as f:
                f.write("\n".join(past_titles) + "\n")
            open(keys, "w", encoding="utf-8").close()
            with open(news, "w", encoding="utf-8") as f:
                f.write("\n".join("■ " + h for h in headlines) + "\n")
            run_env = dict(os.environ)
            run_env.update(env or {})
            out = subprocess.run(
                [sys.executable, FILTER_PATH, "filter_unread", past, keys, news],
                capture_output=True, text=True, env=run_env, check=True,
            ).stdout
            return [ln[2:].strip() for ln in out.splitlines() if ln.startswith("■ ")]

    def test_production_snapshot_drops_only_already_covered_events(self):
        survivors = self.run_filter(PAST_TITLES, HEADLINES)
        blocked = [h for h in HEADLINES if h not in survivors]
        # 当日の実データで落ちるべきもの: 既読事件の別見出しだけ
        self.assertIn(PAKISTAN[3], blocked)
        self.assertIn("Gulf ship traffic via Strait of Hormuz hovers below 10-day average, data shows - Reuters", blocked)
        # 別事件は残る
        self.assertIn("New Tanker Strike in Hormuz as Iran Vows Retaliation for U.S. Economic Offensive - WSJ", survivors)
        self.assertIn("Vatican foreign minister, in Moscow, says Ukraine war must end - Reuters", survivors)
        # 過剰に弾いていないこと (41 件中 8 件以内)
        self.assertLessEqual(len(blocked), 8, f"blocked too many: {blocked}")

    def test_batch_offers_one_headline_per_event(self):
        # 既読ゼロでも、同じ batch に並んだ 4 媒体の見出しからは 1 本しか通さない。
        # 言い回しが離れた見出し同士は、間に入る見出しを経由して同一事件と繋がる。
        survivors = self.run_filter([], PAKISTAN + NEPAL + HEADLINES)
        self.assertEqual([s for s in survivors if s in PAKISTAN], [PAKISTAN[0]])
        self.assertEqual([s for s in survivors if s in NEPAL], [NEPAL[0]])

    def test_kill_switch_restores_old_behaviour(self):
        survivors = self.run_filter([PAKISTAN[0]], PAKISTAN, env={"NEWS_EVENT_DEDUP": "0"})
        self.assertEqual(survivors, PAKISTAN[1:])


if __name__ == "__main__":
    unittest.main(verbosity=2)

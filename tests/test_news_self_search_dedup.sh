#!/usr/bin/env bash
# ニュース自主探索フォールバックが「読んだニュース」を既読台帳へ残すことを検証する。
#
# 修正前: RSS 候補が枯れると _news_self_search_fallback が毎回走り、AI が選んだ
# ニュースはどこにも記録されなかった。その結果、同じ大ニュース（実例: ドリー・パートン
# さん死去）を 1 日に 3 回読み上げた。
# 修正後: 生成結果の ===SELECTED_NEWS===（無ければ要約行）を RSS 記事と同じ
# 既読台帳へ記録し、次回のプロンプトへ「既に扱った話題」として渡す。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NEWS_SRC="$ROOT/broadcast/radio_news.sh"
CORNERS_SRC="$ROOT/broadcast/radio_corners.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source（修正後の実コードをそのまま使う） ---
sed -n '/^_news_title_key()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_title_key.sh"
sed -n '/^_news_topic_key()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_topic_key.sh"
sed -n '/^_append_news_read_source()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_read_source.sh"
sed -n '/^_append_news_read_url_hash()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_read_url_hash.sh"
sed -n '/^_append_news_read_entry()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_read_entry.sh"
sed -n '/^_recent_news_corner_topics_block()/,/^}/p' "$NEWS_SRC" >"$TMP/fn_recent_topics.sh"
sed -n '/^_news_self_search_fallback()/,/^}/p' "$CORNERS_SRC" >"$TMP/fn_fallback.sh"
for f in fn_title_key fn_topic_key fn_read_source fn_read_url_hash fn_read_entry fn_recent_topics fn_fallback; do
	[ -s "$TMP/$f.sh" ] || { not_ok "extract $f"; exit 1; }
	# shellcheck source=/dev/null
	. "$TMP/$f.sh"
done

# --- 履歴ファイル ---
PAST_NEWS_READ="$TMP/past_news_read.txt"
PAST_NEWS_READ_KEYS="$TMP/past_news_read_keys.txt"
PAST_NEWS_TOPIC_KEYS="$TMP/past_news_topic_keys.txt"
PAST_NEWS_READ_SOURCES="$TMP/past_news_read_sources.txt"
PAST_NEWS_URL_HASHES="$TMP/past_news_url_hashes.txt"
PAST_RADIO_TOPICS="$TMP/past_radio_topics.txt"
: >"$PAST_NEWS_READ"
: >"$PAST_NEWS_READ_KEYS"
: >"$PAST_NEWS_TOPIC_KEYS"
cat >"$PAST_RADIO_TOPICS" <<'TOPICS'
[13:26] Game#45755 1185pts [news]: ドリー・パートン,ナッシュビル,ジョリーン / ドリー・パートンさんの訃報と功績を語りました
[13:31] Game#45758 900pts [theme]: 洗濯機,家事労働 / 脱線テーマの雑談をしました
[15:26] Game#45780 1000pts [jiji]: 台風18号,奄美 / 時事ニュースの考察をしました
TOPICS

# --- 依存モック ---
LOG_FILE="$TMP/news.log"
log() { printf '%s\n' "$*" >>"$LOG_FILE"; }
_radio_time_context() { _rc_time_spoken="午後7時"; }
_radio_past_topics_block() { printf '%s\n' "直近ラジオの重複回避メモ:"; }
_radio_persona_block() { printf '%s\n' "(persona)"; }
_radio_output_rules() { printf '%s\n' "(output rules)"; }

MOCK_SELECTED=""
MOCK_SUMMARY=""
MOCK_RC=0
LAST_PROMPT="$TMP/last_prompt.txt"
_radio_generate_and_play() {
	cp "$1" "$LAST_PROMPT"
	if [ -n "${RADIO_GEN_RESULT_DIR:-}" ] && [ -d "${RADIO_GEN_RESULT_DIR}" ]; then
		printf '%s' "$MOCK_SELECTED" >"${RADIO_GEN_RESULT_DIR}/selected_news.txt"
		printf '%s' "$MOCK_SUMMARY" >"${RADIO_GEN_RESULT_DIR}/summary.txt"
	fi
	return "$MOCK_RC"
}

# === 0) 既定では未確認の自主探索を行わない ===
NEWS_SELF_SEARCH_FALLBACK=0
MOCK_SELECTED="読まれてはいけない自主探索ニュース"
MOCK_SUMMARY="自主探索 / 読まれてはいけません"
MOCK_RC=0
_news_self_search_fallback 45820 1000 "テスト: 未読候補なし"
[ ! -e "$LAST_PROMPT" ] && ok "self-search fallback is disabled by default" \
	|| not_ok "self-search fallback is disabled by default"
grep -qF "未確認の自主探索は行わずスキップ" "$LOG_FILE" \
	&& ok "disabled fallback logs a skip" || not_ok "disabled fallback logs a skip"

# 以降は、明示的に旧フォールバックを有効化した場合の互換挙動を検証する。
NEWS_SELF_SEARCH_FALLBACK=1

# === 1) 見出しが出た場合は既読台帳へ記録される ===
MOCK_SELECTED="ドリー・パートンさん死去、80歳"
MOCK_SUMMARY="ドリー・パートン,ナッシュビル / 訃報を語りました"
MOCK_RC=0
_news_self_search_fallback 45825 1504 "テスト: 未読候補なし"
rc=$?
[ "$rc" -eq 0 ] && ok "fallback returns generator rc (0)" || not_ok "fallback returns generator rc (got $rc)"
grep -qF "ドリー・パートンさん死去、80歳" "$PAST_NEWS_READ" \
	&& ok "selected headline appended to PAST_NEWS_READ" \
	|| not_ok "selected headline appended to PAST_NEWS_READ"
expected_key=$(_news_title_key "ドリー・パートンさん死去、80歳")
grep -qF "$expected_key" "$PAST_NEWS_READ_KEYS" \
	&& ok "normalized key appended to PAST_NEWS_READ_KEYS" \
	|| not_ok "normalized key appended to PAST_NEWS_READ_KEYS"
expected_topic=$(_news_topic_key "ドリー・パートンさん死去、80歳")
if [ -n "$expected_topic" ]; then
	grep -qF "$expected_topic" "$PAST_NEWS_TOPIC_KEYS" \
		&& ok "topic key appended to PAST_NEWS_TOPIC_KEYS" \
		|| not_ok "topic key appended to PAST_NEWS_TOPIC_KEYS"
else
	not_ok "topic key derivable from headline"
fi
grep -qF "[NEWS] 自主探索の既読記録: ドリー・パートンさん死去、80歳" "$LOG_FILE" \
	&& ok "records log line" || not_ok "records log line"
[ -z "${RADIO_GEN_RESULT_DIR:-}" ] && ok "RADIO_GEN_RESULT_DIR unset after call" \
	|| not_ok "RADIO_GEN_RESULT_DIR unset after call"

# === 2) プロンプトに実際のニュース話題と ===SELECTED_NEWS=== 要求が入る ===
grep -qF "ドリー・パートン,ナッシュビル,ジョリーン" "$LAST_PROMPT" \
	&& ok "prompt carries concrete past news-corner topics" \
	|| not_ok "prompt carries concrete past news-corner topics"
grep -qF "台風18号,奄美" "$LAST_PROMPT" \
	&& ok "prompt carries past jiji topics too" \
	|| not_ok "prompt carries past jiji topics too"
grep -qF "洗濯機,家事労働" "$LAST_PROMPT" \
	&& not_ok "prompt should not carry non-news corner topics" \
	|| ok "prompt excludes non-news corner topics"
grep -qF "===SELECTED_NEWS===" "$LAST_PROMPT" \
	&& ok "prompt requests SELECTED_NEWS marker" \
	|| not_ok "prompt requests SELECTED_NEWS marker"

# === 3) 2回目のプロンプトには1回目の見出しが既読として載る ===
MOCK_SELECTED="錦織圭、現役最後の全米オープン"
MOCK_SUMMARY="錦織圭,全米オープン / 引退を語りました"
_news_self_search_fallback 45830 1200 "テスト: 未読候補なし"
grep -qF "ドリー・パートンさん死去、80歳" "$LAST_PROMPT" \
	&& ok "second prompt lists the first self-search headline as already read" \
	|| not_ok "second prompt lists the first self-search headline as already read"
[ "$(grep -cF "錦織圭、現役最後の全米オープン" "$PAST_NEWS_READ")" -eq 1 ] \
	&& ok "second headline recorded once" || not_ok "second headline recorded once"

# === 4) 見出し行が無ければ要約行で代替記録する ===
MOCK_SELECTED=""
MOCK_SUMMARY="福岡40.4度,猛暑 / 記録的猛暑を語りました"
_news_self_search_fallback 45840 1100 "テスト: 未読候補なし"
grep -qF "福岡40.4度,猛暑 / 記録的猛暑を語りました" "$PAST_NEWS_READ" \
	&& ok "falls back to summary line when no headline marker" \
	|| not_ok "falls back to summary line when no headline marker"

# === 5) 生成失敗時は既読記録しない ===
before=$(wc -l <"$PAST_NEWS_READ")
MOCK_SELECTED="生成に失敗したニュース"
MOCK_SUMMARY="失敗 / 失敗"
MOCK_RC=1
_news_self_search_fallback 45850 1000 "テスト: 未読候補なし"
rc=$?
[ "$rc" -eq 1 ] && ok "propagates generator failure rc" || not_ok "propagates generator failure rc (got $rc)"
after=$(wc -l <"$PAST_NEWS_READ")
[ "$before" -eq "$after" ] && ok "nothing recorded on generation failure" \
	|| not_ok "nothing recorded on generation failure ($before -> $after)"

# === 6) 台帳追記ヘルパの単体挙動 ===
MOCK_RC=0
_append_news_read_entry "" && not_ok "empty title rejected" || ok "empty title rejected"
_append_news_read_entry "テスト記事" "wikinews" "deadbeef" && ok "entry with source/url accepted" \
	|| not_ok "entry with source/url accepted"
grep -qF "wikinews" "$PAST_NEWS_READ_SOURCES" && ok "source key recorded" || not_ok "source key recorded"
grep -qF "deadbeef" "$PAST_NEWS_URL_HASHES" && ok "url hash recorded" || not_ok "url hash recorded"

if [ "$FAIL" -eq 0 ]; then
	echo "ALL PASS"
else
	echo "FAILED"
fi
exit "$FAIL"

#!/usr/bin/env bash
# tests/test_speech_vocabulary_rule.sh
#
# 発話プロンプト（ラジオ全コーナー・建国祝賀・コメント返し・soren91実況・
# 戦略解説・アンケート・バッチ解説）が、語彙ルールの正本
# prompts/speech_vocabulary_rule.md を実際に注入していることを検証する。
#
# 背景: 「腹がすく」「食う」のような丁寧でない言い回しが読み上げに混ざるため、
# 語彙ルールを1か所の正本へ集約し、各プロンプト生成箇所から参照する方針にした。
# 正本を編集したのに一部の発話経路へ届かない、という退行をここで止める。

set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULE_FILE="$ROOT/prompts/speech_vocabulary_rule.md"

ok=0
fail=0
check() {
	local condition="$1" message="$2"
	if eval "$condition"; then
		printf 'ok - %s\n' "$message"
		ok=$((ok + 1))
	else
		printf 'not ok - %s\n' "$message"
		fail=$((fail + 1))
	fi
}

# --- 1. 正本そのもの ---
check '[ -f "$RULE_FILE" ]' '語彙ルールの正本が存在する'
check 'grep -qF "腹がすく" "$RULE_FILE" && grep -qF "お腹が空く" "$RULE_FILE"' '正本が「腹がすく→お腹が空く」を規定する'
check 'grep -qF "食う" "$RULE_FILE" && grep -qF "食べる" "$RULE_FILE"' '正本が「食う→食べる」を規定する'
check '! grep -qF "\${" "$RULE_FILE"' '正本に envsubst が壊れる変数参照がない'

# --- 2. ラジオ: 出力ルールへ実際に載る ---
radio_rules=$(ELOOP_LIB_DIR="$ROOT" bash -c \
	'source "$1"; _broadcast_host_mode() { printf "main"; }; _radio_output_rules 100 200' \
	_ "$ROOT/broadcast/radio_persona.sh" 2>/dev/null || true)
check 'printf "%s" "$radio_rules" | grep -qF "語彙・言い回しの共通ルール"' 'ラジオ出力ルールに語彙ルールが載る'
check 'printf "%s" "$radio_rules" | grep -qF "お腹が空く"' 'ラジオ出力ルールに具体例が載る'

# --- 3. 建国祝賀: ヘルパーが正本を返し、プロンプトが参照する ---
celeb_rule=$(ELOOP_LIB_DIR="$ROOT" bash -c \
	'source "$1"; _celebration_vocabulary_rule' \
	_ "$ROOT/broadcast/radio_celebration.sh" 2>/dev/null || true)
check 'printf "%s" "$celeb_rule" | grep -qF "腹がすく"' '建国祝賀ヘルパーが正本を返す'
check '[ "$(grep -c "_celebration_vocabulary_rule" "$ROOT/broadcast/radio_celebration.sh")" -ge 3 ]' '建国祝賀の両プロンプトがヘルパーを参照する'

# --- 4. コメント返し: 共通契約へ実際に載る ---
contract_tmp=$(mktemp)
ELOOP_LIB_DIR="$ROOT" bash -c 'source "$1"; _append_comment_reply_contract "$2"' \
	_ "$ROOT/broadcast/comment.sh" "$contract_tmp" 2>/dev/null || true
check 'grep -qF "語彙・言い回しの共通ルール" "$contract_tmp"' 'コメント返し契約に語彙ルールが載る'
check 'grep -qF "お腹が空く" "$contract_tmp"' 'コメント返し契約に具体例が載る'
rm -f "$contract_tmp"

# --- 5. soren91 実況 (JS) と戦略解説 (bash) ---
check 'grep -qF "speech_vocabulary_rule.md" "$ROOT/soren91/comment.mjs"' 'soren91実況が語彙ルールを読み込む'
check 'grep -qF "loadSpeechVocabularyRule()" "$ROOT/soren91/comment.mjs"' 'soren91実況が読み込んだ語彙ルールをプロンプトへ付ける'
check 'grep -qF "speech_vocabulary_rule.md" "$ROOT/soren91_control.sh"' 'soren91戦略解説が語彙ルールを読み込む'

# --- 6. アンケート / バッチ解説 ---
check 'grep -qF "speech_vocabulary_rule.md" "$ROOT/workers/poll_worker.sh"' 'アンケート文面が語彙ルールを読み込む'
check '[ "$(grep -c "_append_poll_vocabulary_rule" "$ROOT/workers/poll_worker.sh")" -ge 3 ]' 'アンケート質問と結果の両方へ注入する'
check 'grep -qF "speech_vocabulary_rule.md" "$ROOT/batch_commentary.sh"' 'バッチ解説が語彙ルールを読み込む'

# --- 7. 建国告知テンプレート（静的重複）が正本の主要例を持つ ---
check 'grep -qF "お腹が空く" "$ROOT/prompts/celebration.md"' '建国告知テンプレートに語彙ルールが載る'

# --- 8. 定型の「注目」導入句の連発を抑える ---
check 'grep -qF "導入句・つなぎ言葉" "$RULE_FILE"' '正本が導入句の連発を規定する'
check 'grep -qF "1回のトークで同じ導入句を2回以上使わない" "$RULE_FILE"' '正本が同一導入句の反復を禁止する'
check 'grep -qF "導入句も連発しない" "$ROOT/broadcast/radio_persona.sh"' 'ラジオ出力ルールが導入句の連発を禁じる'
check 'printf "%s" "$radio_rules" | grep -qF "導入句・つなぎ言葉"' 'ラジオ全コーナー共通の出力ルールに導入句ルールが載る'
check 'grep -qF "連発せず" "$ROOT/prompts/radio_jiji.md"' 'jijiコーナーが導入句の連発を禁じる'
# 注: prompts/radio_news.md は現在どのコードからも参照されないテンプレート。
# 実際の news コーナーは broadcast/radio_corners.sh の inline プロンプトで、
# 上の「ラジオ全コーナー共通の出力ルール」経由で同じ導入句ルールを受け取る。

banned_input='ここで面白いのは、Aです。ここで面白いのは、Bです。'
banned_output=$(ELOOP_LIB_DIR="$ROOT" bash -c \
	'source "$1"; printf "%s" "$2" | _normalize_radio_tone' \
	_ "$ROOT/broadcast/radio_engine.sh" "$banned_input" 2>/dev/null)
check '! printf "%s" "$banned_output" | grep -qF "ここで面白いのは"' '禁止の「ここで面白いのは」は文頭から全て外す'
check 'printf "%s" "$banned_output" | grep -qF "Aです" && printf "%s" "$banned_output" | grep -qF "Bです"' '禁止句を外しても本文は残る'

framing_input='ここで注目なのは、Aです。ここで注目なのは、Bです。ポイントになるのは、Cです。'
framing_output=$(ELOOP_LIB_DIR="$ROOT" bash -c \
	'source "$1"; printf "%s" "$2" | _normalize_radio_tone' \
	_ "$ROOT/broadcast/radio_engine.sh" "$framing_input" 2>/dev/null)
framing_kept=$(printf '%s' "$framing_output" | grep -o 'ここで注目なのは' | wc -l | tr -d ' ')
framing_dup=$(printf '%s' "$framing_output" | grep -o 'ポイントになるのは' | wc -l | tr -d ' ')
check '[ "$framing_kept" -eq 1 ]' '注目導入句は1トークにつき1回まで'
check '[ "$framing_dup" -eq 0 ]' '2つ目以降の導入句は外す'
check 'printf "%s" "$framing_output" | grep -qF "Bです" && printf "%s" "$framing_output" | grep -qF "Cです"' '導入句を外しても本文は残る'

printf '\n%d ok, %d failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]

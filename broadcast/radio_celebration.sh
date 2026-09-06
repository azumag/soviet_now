# broadcast/radio_celebration.sh - ロシア/ソ連建国祝賀, クリップ, チャット投稿


#=== 生成チェーン (通常コーナーと同一) ===

# 祝賀トーク生成は通常のラジオコーナーと同じモデルチェーン設定 (RADIO_AGENTS) を
# ai_generate_list で回す。claude へのフォールバックは通常方針どおり行わない。
# 成功時は本文を stdout へ、失敗時は空を返す (呼び出し側で dump する)。
# 祝賀用の候補判定。通常コーナーと違い、祝賀プロンプトはトーク本文のみを
# 出力させる (ON_AIR_SCRIPT マーカー等の構造を要求しない) ため、パーサー必須の
# _radio_is_valid_generation_candidate は使えない。最終段と同等の sanitize まで
# 通して _is_valid_radio_talk で判定する。
_celebration_is_valid_candidate() {
	local raw="$1" guarded sanitized
	[ -n "$raw" ] || return 1
	if command -v _ai_guard_model_output >/dev/null 2>&1; then
		guarded=$(printf '%s' "$raw" | _ai_guard_model_output 2>/dev/null || printf '%s' "$raw")
	else
		guarded="$raw"
	fi
	[ -n "$guarded" ] || return 1
	if command -v _contains_provider_error_text >/dev/null 2>&1; then
		_contains_provider_error_text "$guarded" && return 1
	fi
	sanitized=$(printf '%s' "$guarded" | _sanitize_onair_text | _normalize_radio_tone)
	_is_valid_radio_talk "$sanitized"
}

_celebration_generate_talk() {
	local tag="$1" prompt_file="$2"
	local last_agent_file talk provider_used
	last_agent_file=$(mktemp /tmp/eloop_celebration_agent_XXXXXXXX)
	talk=$(ai_generate_list "RADIO:${tag}" "$prompt_file" "${RADIO_AGENTS:-opencode-go:deepseek-v4-flash,codex:minimax-m3}" "" "_celebration_is_valid_candidate" "$last_agent_file" 2>/dev/null || true)
	provider_used=$(cat "$last_agent_file" 2>/dev/null)
	rm -f "$last_agent_file" 2>/dev/null || true
	if command -v _ai_guard_model_output >/dev/null 2>&1; then
		talk=$(printf '%s' "$talk" | _ai_guard_model_output 2>/dev/null || printf '%s' "$talk")
	fi
	if [ -n "$talk" ]; then
		log "[${tag}] provider=${provider_used:-unknown}"
	fi
	printf '%s' "$talk"
}

# 生成失敗・検証落ち時の原文保存 (プロンプト+本文を残し、後から原因を追えるようにする)
_celebration_dump_failure() {
	local tag="$1" reason="$2" prompt_snapshot="$3" body="$4"
	local dump
	dump="${TMP_DEBUG_DIR:-tmp/debug}/radio_failed_${tag}_$(date +%s).txt"
	{
		echo "reason=${reason}"
		echo "corner=${tag}"
		echo
		echo "===PROMPT==="
		printf '%s\n' "$prompt_snapshot"
		echo
		echo "===RAW==="
		printf '%s\n' "$body"
	} >"$dump" 2>/dev/null || true
	log "[${tag}] dump: $dump"
}

_celebration_country_names() {
	python3 -c 'import sys; from lib.normalize_speech_text import replace_country_references; sys.stdout.write(replace_country_references(sys.stdin.read()))'
}

generate_russia_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_russia_celebration_XXXXXXXX)
	cat >"$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況のパーソナリティ兼人工知能プレイヤーです。

【速報】ロシアが建国されました！

ゲーム「ソ連ゲーム」で、「ロシア」ピースが誕生しました。
これはソ連完成の一歩手前まで国家併合が進んだことを意味します。
ゲーム${game_num}回目、スコア${score}点、${turns}ターン、現在時刻: ${current_time}。

【ルール】
- 900文字前後の祝賀トーク
- ロシア到達は大きな前進だが、まだ最終ゴールではないと明確にする
- ここまでの積み上げと、次はソ連完成を狙う段階だと伝える
- 話し言葉で、少し高揚感を出す
- 大げさすぎる勝利宣言にしない。中間到達点として祝う
- 国は必ずアルメニア、モルドバ、エストニア、ラトビア、リトアニア、ジョージア、アゼルバイジャン、タジキスタン、キルギス、ベラルーシ、ウズベキスタン、トルクメニスタン、ウクライナ、カザフスタン、ロシア、ソ連の国名で呼ぶ
- 内部の type、T、タイプ番号は本文へ一切出さない
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- 【最重要】全ての文末を「です・ます」調にすること。「〜だ」「〜である」「〜だった」「〜なのだ」は1文も許可しない。「〜です」「〜ます」「〜でしょう」「〜ですけど」で統一
- 「ね」で終わる文末は禁止。「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」は使わない
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
CELEBPROMPT

	_radio_set_state "generating" "russia_celebration"
	log "[RUSSIA] 生成中..."
	local celebration_talk celebration_prompt_snapshot
	celebration_prompt_snapshot=$(cat "$celebration_prompt_file" 2>/dev/null)
	celebration_talk=$(_celebration_generate_talk "RUSSIA" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		_celebration_dump_failure "russia_celebration" "generation_empty" "$celebration_prompt_snapshot" ""
		_radio_clear_state "russia_celebration" "generation_failed"
		log "[RUSSIA] 祝賀トーク生成失敗"
		rm -f "$celebration_prompt_file"
		return 1
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		celebration_talk=$(printf '%s' "$celebration_talk" | _sanitize_onair_text | _normalize_radio_tone)
		if [ "${RADIO_FACT_CHECK_ENABLED:-1}" != "0" ]; then
			_radio_set_state "verifying" "russia_celebration"
			celebration_talk=$(_radio_fact_check_body "celebration" "$celebration_prompt_snapshot" "$celebration_talk") || {
				_radio_clear_state "russia_celebration" "fact_check_failed"
				log "[RUSSIA] fact-check失敗"
				return 1
			}
		fi
		local country_named_talk
		country_named_talk=$(printf '%s' "$celebration_talk" | _celebration_country_names 2>/dev/null) || {
			_radio_clear_state "russia_celebration" "country_name_normalization_failed"
			log "[RUSSIA] 国名正規化失敗"
			return 1
		}
		celebration_talk="$country_named_talk"
		if ! _is_valid_radio_talk "$celebration_talk"; then
			_celebration_dump_failure "russia_celebration" "invalid_after_fact_check" "$celebration_prompt_snapshot" "$celebration_talk"
			_radio_clear_state "russia_celebration" "invalid_after_fact_check"
			log "[RUSSIA] fact-check後の本文が不正/短文"
			return 1
		fi
		echo "$celebration_talk" >$TMP_DEBUG_DIR/radio_russia_celebration.txt
		_radio_set_state "playing" "russia_celebration"
		log "[RUSSIA] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	fi
}

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)
	cat >"$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況のパーソナリティ兼人工知能プレイヤーです。

【緊急ニュース】ソ連が建国されました！

ゲーム「ソ連ゲーム」で、ついに「ソ連」ピースが誕生しました！
アルメニアから国を育て、二つのロシアを併合してようやく到達する究極のゴールです。
ゲーム${game_num}回目、スコア${score}点、${turns}ターンでの偉業。現在時刻: ${current_time}。

【ルール】
- 2000文字程度の祝賀トーク
- ソ連建国の興奮と感動を全力で表現
- 歴史的な偉業を達成したことを強調
- ソ連の偉大さを讃える表現をふんだんに盛り込むこと
- 戦略の巧妙さを称えること
- 大げさな宣言調も交えて
- 話し言葉で、感情豊かに
- 国は必ずアルメニア、モルドバ、エストニア、ラトビア、リトアニア、ジョージア、アゼルバイジャン、タジキスタン、キルギス、ベラルーシ、ウズベキスタン、トルクメニスタン、ウクライナ、カザフスタン、ロシア、ソ連の国名で呼ぶ
- 内部の type、T、タイプ番号は本文へ一切出さない
- 「誰も聞いていない」「聞き手がいない」「過疎」「無人放送」など、視聴者不在を示す自虐表現は禁止
- 【最重要】全ての文末を「です・ます」調にすること。「〜だ」「〜である」「〜だった」「〜なのだ」は1文も許可しない。「〜です」「〜ます」「〜でしょう」「〜ですけど」で統一
- 「ね」で終わる文末は禁止。「〜ですね」「〜ますね」「〜ですけどね」「〜でしょうね」は使わない
- マークダウンや記号は使わない。読み上げ用プレーンテキストのみ
- 出力はトーク本文のみ。前置きや補足説明は不要
CELEBPROMPT

	_radio_set_state "generating" "celebration"
	log "[CELEBRATION] 生成中..."
	local celebration_talk celebration_prompt_snapshot
	celebration_prompt_snapshot=$(cat "$celebration_prompt_file" 2>/dev/null)
	celebration_talk=$(_celebration_generate_talk "CELEBRATION" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		_celebration_dump_failure "celebration" "generation_empty" "$celebration_prompt_snapshot" ""
		_radio_clear_state "celebration" "generation_failed"
		log "[CELEBRATION] 祝賀トーク生成失敗"
		rm -f "$celebration_prompt_file"
		return 1
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		celebration_talk=$(printf '%s' "$celebration_talk" | _sanitize_onair_text | _normalize_radio_tone)
		if [ "${RADIO_FACT_CHECK_ENABLED:-1}" != "0" ]; then
			_radio_set_state "verifying" "celebration"
			celebration_talk=$(_radio_fact_check_body "celebration" "$celebration_prompt_snapshot" "$celebration_talk") || {
				_radio_clear_state "celebration" "fact_check_failed"
				log "[CELEBRATION] fact-check失敗"
				return 1
			}
		fi
		local country_named_talk
		country_named_talk=$(printf '%s' "$celebration_talk" | _celebration_country_names 2>/dev/null) || {
			_radio_clear_state "celebration" "country_name_normalization_failed"
			log "[CELEBRATION] 国名正規化失敗"
			return 1
		}
		celebration_talk="$country_named_talk"
		if ! _is_valid_radio_talk "$celebration_talk"; then
			_celebration_dump_failure "celebration" "invalid_after_fact_check" "$celebration_prompt_snapshot" "$celebration_talk"
			_radio_clear_state "celebration" "invalid_after_fact_check"
			log "[CELEBRATION] fact-check後の本文が不正/短文"
			return 1
		fi
		echo "$celebration_talk" >"$TMP_DEBUG_DIR/radio_soviet_celebration.txt"
		_radio_set_state "playing" "celebration"
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	fi
}

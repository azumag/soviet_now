# broadcast/radio_celebration.sh - ロシア/ソ連建国祝賀, クリップ, チャット投稿


#=== ソ連祝賀トーク ===

generate_russia_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_russia_celebration_XXXXXXXX)
	cat >"$celebration_prompt_file" <<CELEBPROMPT
あなたはゲーム実況のパーソナリティ兼人工知能プレイヤーです。

【速報】ロシアが建国されました！

ゲーム「ソ連ゲーム」で、レベル14の「ロシア」ピースが誕生しました。
これはソ連完成の一歩手前まで国家併合が進んだことを意味します。
ゲーム${game_num}回目、スコア${score}点、${turns}ターン、現在時刻: ${current_time}。

【ルール】
- 900文字前後の祝賀トーク
- ロシア到達は大きな前進だが、まだ最終ゴールではないと明確にする
- ここまでの積み上げと、次はソ連完成を狙う段階だと伝える
- 話し言葉で、少し高揚感を出す
- 大げさすぎる勝利宣言にしない。中間到達点として祝う
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
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_claude_radio "$celebration_prompt_file")
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
		if ! _is_valid_radio_talk "$celebration_talk"; then
			_radio_clear_state "russia_celebration" "invalid_after_fact_check"
			log "[RUSSIA] fact-check後の本文が不正/短文"
			return 1
		fi
		echo "$celebration_talk" >$TMP_DEBUG_DIR/radio_russia_celebration.txt
		_radio_set_state "playing" "russia_celebration"
		log "[RUSSIA] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
		_radio_clear_state "russia_celebration" "generation_failed"
		log "[RUSSIA] 祝賀トーク生成失敗"
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

ゲーム「ソ連ゲーム」で、ついにレベル15の「ソ連」ピースが誕生しました！
アルメニアから始まりロシアまで14段階の併合を経てようやく到達する究極のゴールです。
ゲーム${game_num}回目、スコア${score}点、${turns}ターンでの偉業。現在時刻: ${current_time}。

【ルール】
- 2000文字程度の祝賀トーク
- ソ連建国の興奮と感動を全力で表現
- 歴史的な偉業を達成したことを強調
- ソ連の偉大さを讃える表現をふんだんに盛り込むこと
- 戦略の巧妙さを称えること
- 大げさな宣言調も交えて
- 話し言葉で、感情豊かに
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
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_claude_radio "$celebration_prompt_file")
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
		if ! _is_valid_radio_talk "$celebration_talk"; then
			_radio_clear_state "celebration" "invalid_after_fact_check"
			log "[CELEBRATION] fact-check後の本文が不正/短文"
			return 1
		fi
		echo "$celebration_talk" >tmp/radio_celebration.txt
		_radio_set_state "playing" "celebration"
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
		_radio_clear_state "celebration" "generation_failed"
		log "[CELEBRATION] 祝賀トーク生成失敗"
	fi
}

#!/usr/bin/env bash
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export COMMENT_SPOKEN_HISTORY_DIR="$TMP/spoken_history"
export COMMENT_SPOKEN_HISTORY_MAX_FILES=16
export COMMENT_SPOKEN_PROMPT_ITEMS=8
export COMMENT_SPOKEN_PROMPT_MAX_CHARS=2500
export COMMENT_SPOKEN_ITEM_MAX_CHARS=350
mkdir -p "$COMMENT_SPOKEN_HISTORY_DIR"

source "$ROOT/broadcast/comment.sh"

_clean_comment_talk() { cat; }
_sanitize_onair_text() { cat; }

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

check 'grep -q "一人称は「私」を使うこと。「僕」「俺」「自分」は使わない" "$ROOT/prompts/comment_persona_main.md"' 'コメントmainペルソナは一人称「私」を規定する'
check 'grep -q "一人称は「僕」を使うこと。「私」「俺」「自分」は使わない" "$ROOT/prompts/comment_persona_soren91.md"' 'コメントsoren91ペルソナは一人称「僕」を規定する'
check 'grep -q "一人称は「私」を使うこと。「僕」「俺」「自分」は使わない" "$ROOT/prompts/celebration.md"' '建国告知プロンプトは一人称「私」を規定する'
check 'grep -q "一人称は「私」を使い、「僕」「俺」「自分」は使わない" "$ROOT/batch_commentary.sh"' 'バッチ解説プロンプトは一人称「私」を規定する'

main_persona_block=$(awk "/cat <<'PERSONA'/{n++} n==2" "$ROOT/broadcast/radio_persona.sh")
check 'printf "%s" "$main_persona_block" | grep -q "一人称は「私」を使うこと。「僕」「俺」「自分」は使わない"' 'ラジオmainペルソナブロックは一人称「私」を規定する'
check '[ "$(printf "%s" "$main_persona_block" | grep -c "一人称は「僕」")" -eq 0 ]' 'ラジオmainペルソナブロックは一人称「僕」を規定しない'

_remember_comment_reply_text "メインの返信です。" main
_remember_comment_reply_text "サブの返信です。" soren91
_remember_comment_reply_text "デフォルトの返信です。"
_remember_comment_reply_text "不正モードの返信です。" unknown_mode

check '[ "$(find "$COMMENT_SPOKEN_HISTORY_DIR" -name "*_main.txt" | wc -l | tr -d " ")" -eq 3 ]' 'mainモードの履歴ファイルが3件作られる'
check '[ "$(find "$COMMENT_SPOKEN_HISTORY_DIR" -name "*_soren91.txt" | wc -l | tr -d " ")" -eq 1 ]' 'soren91モードの履歴ファイルが1件作られる'

legacy_file="$COMMENT_SPOKEN_HISTORY_DIR/20200101_000000_000_legacy.txt"
printf '%s\n' "旧形式の返信です。" >"$legacy_file"

recent_main=$(_build_recent_spoken_comment_context main)
recent_soren91=$(_build_recent_spoken_comment_context soren91)
recent_default=$(_build_recent_spoken_comment_context)

check 'printf "%s" "$recent_main" | grep -q "メインの返信です。"' 'mainモードの履歴はmain生成時に参照される'
check 'printf "%s" "$recent_main" | grep -q "旧形式の返信です。"' '旧形式の履歴はmain生成時に参照される'
check '[ "$(printf "%s" "$recent_main" | grep -c "サブの返信です。")" -eq 0 ]' 'soren91モードの履歴はmain生成時に参照されない'
check 'printf "%s" "$recent_soren91" | grep -q "サブの返信です。"' 'soren91モードの履歴はsoren91生成時に参照される'
check '[ "$(printf "%s" "$recent_soren91" | grep -c "メインの返信です。")" -eq 0 ]' 'mainモードの履歴はsoren91生成時に参照されない'
check '[ "$(printf "%s" "$recent_soren91" | grep -c "旧形式の返信です。")" -eq 0 ]' '旧形式の履歴はsoren91生成時に参照されない'
check 'printf "%s" "$recent_default" | grep -q "デフォルトの返信です。"' 'モード省略時はmain扱いで履歴を参照する'

mkdir -p "$TMP/tmp/.say_queue"
playing_dir="$TMP/tmp/.say_queue"
playing_file="$playing_dir/comment_playing_test.playing"
printf '%s\n' "再生中のサブ返信です。" >"$playing_file"
printf '%s\n' "soren91" >"${playing_file%.playing}.mode"
printf '%s\n' "seq|playing|$playing_file" >"$playing_dir/current_source"

cd "$TMP" || exit 1
recent_main_with_playing=$(_build_recent_spoken_comment_context main)
recent_soren91_with_playing=$(_build_recent_spoken_comment_context soren91)
cd "$ROOT" || exit 1

check '[ "$(printf "%s" "$recent_main_with_playing" | grep -c "再生中のサブ返信です。")" -eq 0 ]' 'soren91再生中の項目はmain生成時に「再生中」として出ない'
check 'printf "%s" "$recent_soren91_with_playing" | grep -q "再生中のサブ返信です。"' 'soren91再生中の項目はsoren91生成時に「再生中」として出る'

hints_main=$(_build_comment_followup_hints /dev/null main)
hints_soren91=$(_build_comment_followup_hints /dev/null soren91)
check '[ "$(printf "%s" "$hints_main" | grep -c "サブの返信です。")" -eq 0 ]' '追い反応ヒントもmain生成時にsoren91履歴を使わない'
check '[ "$(printf "%s" "$hints_soren91" | grep -c "メインの返信です。")" -eq 0 ]' '追い反応ヒントもsoren91生成時にmain履歴を使わない'

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# soren91(メリケンAI)モード中、コメント返しプロンプトの【現在のゲーム状態メモ】に
# 本編(ソレンゲーム)のスコア歴を入れないことを検証する。
#
# 背景(2026-09-14 実機報告): ソ連ゲーム91のコーナー中に、読み上げられるスコア歴が
# ソレンゲーム本編のものになっていた。原因は _build_comment_game_context の
# スコア/建国履歴メモが host_mode に関係なくテンプレートへ埋め込まれていたこと。
# soren91 ではスコアの概念がないため、順位で振り返る旨の差し替えメモに切り替える。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_comment_soren91_game_state_note()/,/^}/p' "$SRC" >"$TMP/fn_note.sh"
[ -s "$TMP/fn_note.sh" ] || { not_ok "extract _comment_soren91_game_state_note"; exit 1; }
# shellcheck source=/dev/null
. "$TMP/fn_note.sh"

# --- 1. soren91 用メモの内容 ---
note="$(_comment_soren91_game_state_note)"
case "$note" in *"ソ連ゲーム91"*) ok "soren91 と明記する" ;; *) not_ok "soren91 の記載なし: $note" ;; esac
case "$note" in *"スコアの概念がなく"*) ok "スコアの概念がないと明記する" ;; *) not_ok "スコア無概念の記載なし: $note" ;; esac
case "$note" in *"本編"*"読み上げない"*) ok "本編のスコアを読み上げないよう指示する" ;; *) not_ok "本編スコア抑止の記載なし: $note" ;; esac
# 本編の具体的な統計値(平均・件数・直近スコア等)を混ぜていないこと
case "$note" in
	*"平均"*|*"終了スコア"*|*"スコア履歴"*|*"最高"*) not_ok "本編の統計語が混入: $note" ;;
	*) ok "本編の統計値・語を含まない" ;;
esac

# --- 2. soren91 モードのときだけ差し替える配線になっていること ---
if grep -qE '_spoken_ctx_mode" = "soren91"' "$SRC" &&
	grep -qE 'game_state_context=\$\(_comment_soren91_game_state_note\)' "$SRC"; then
	ok "soren91 モードで note に差し替える配線がある"
else
	not_ok "soren91 モードの差し替え配線が見つからない"
fi
# main 側は本編のメモを維持していること
if grep -qE 'game_state_context=\$\(_build_comment_game_context' "$SRC"; then
	ok "main 側は本編の game_state_context を維持"
else
	not_ok "main 側の game_state_context 呼び出しが消えている"
fi

[ "$FAIL" -eq 0 ] && echo "PASS" || echo "FAIL"
exit "$FAIL"

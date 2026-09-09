#!/bin/bash
# 予想告知は定型文のchat投稿のみ。AI掛け声/リアクションの付加はしない。
# 開始・結果のchat投稿はdaemonが視聴者コメントとして取り込み、
# コメント生成AIの反応対象にする (ガチャと同方式)。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/twitch_predictions.sh"
DAEMON="$ROOT/twitch_chat_daemon.sh"

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok %d - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok - %s\n' "$1"; }

# --- AI付加機構の残骸がない ---
for fn in _generate_prediction_line _announce_prediction_start _announce_prediction_result _prediction_ensure_ai _prediction_comment_validator _prediction_persona_header; do
	if grep -Fq "$fn" "$SRC"; then
		not_ok "AI残骸あり: $fn"
	else
		ok "AI残骸なし: $fn"
	fi
done
if grep -Fq "TWITCH_PREDICTION_COMMENTARY_ENABLED" "$SRC" || grep -Fq "RADIO_PREDICTION_START" "$SRC" || grep -Fq "RADIO_PREDICTION_RESULT" "$SRC"; then
	not_ok "AI設定の残骸あり"
else
	ok "AI設定の残骸なし"
fi

# --- 告知はchatのみ・audioなし ---
if grep -Fq 'enqueue_audio_text' "$SRC"; then
	not_ok "audio送出が残っている"
else
	ok "audio送出なし"
fi
if [ "$(grep -c 'enqueue_chat_message' "$SRC")" = "7" ]; then
	ok "chat告知は7箇所 (開始1＋stale3＋resolve3)"
else
	not_ok "chat告知数が不正: $(grep -c 'enqueue_chat_message' "$SRC")"
fi
if grep -Fq 'enqueue_chat_message "チャネルポイント予想スタート' "$SRC"; then
	ok "開始定型文のchat投稿あり"
else
	not_ok "開始定型文のchat投稿なし"
fi

# --- daemon素通し ---
# shellcheck source=/dev/null
source <(sed -n '/^_is_card_gacha_result_message()/,/^}/p;/^_is_prediction_announce_message()/,/^}/p;/^_is_ignored_author()/,/^}/p' "$DAEMON")
export TWITCH_IGNORE_AUTHORS="${TWITCH_IGNORE_AUTHORS:-azumagdev azumagbanjo あずまぐ}"

if _is_prediction_announce_message 'チャネルポイント予想スタート！「次の48試合で建国できる？」投票受付中（8分）。'; then
	ok "開始告知を素通し対象に"
else
	not_ok "開始告知が素通し対象外"
fi
if _is_prediction_announce_message '予想結果：「ソ連建国」でした！'; then
	ok "結果告知を素通し対象に"
else
	not_ok "結果告知が素通し対象外"
fi
if _is_prediction_announce_message '予想結果：「粛清」！試していた新戦略が前より成績を落としたので、安定版に戻しました。理由：comp比率低下'; then
	ok "粛清結果を素通し対象に"
else
	not_ok "粛清結果が素通し対象外"
fi
if _is_prediction_announce_message 'こんにちは'; then
	not_ok "通常文を素通し"
else
	ok "通常文は素通し対象外"
fi
if _is_prediction_announce_message '見たか資本主義者ども！計画通りの建国だ！'; then
	not_ok "AI返信風を素通し (循環の恐れ)"
else
	ok "AI返信風は素通し対象外 (循環なし)"
fi
if grep -Fq '_is_prediction_announce_message "$msg"' "$DAEMON"; then
	ok "daemonのignore分岐に予想判定を配線"
else
	not_ok "daemonのignore分岐に予想判定なし"
fi
if grep -Fq 'trusted-prediction' "$DAEMON"; then
	ok "trusted-predictionフラグあり"
else
	not_ok "trusted-predictionフラグなし"
fi

printf 'pass=%d fail=%d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

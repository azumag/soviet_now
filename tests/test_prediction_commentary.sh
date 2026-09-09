#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/tmp"
cd "$TMP" || exit 1
SRC="$ROOT/twitch_predictions.sh"

{
	sed -n '/^_prediction_ensure_ai()/,/^}/p' "$SRC"
	sed -n '/^_prediction_comment_validator()/,/^}/p' "$SRC"
	sed -n '/^_prediction_persona_header()/,/^}/p' "$SRC"
	sed -n '/^_generate_prediction_line()/,/^}/p' "$SRC"
	sed -n '/^_announce_prediction_start()/,/^}/p' "$SRC"
	sed -n '/^_announce_prediction_result()/,/^}/p' "$SRC"
} >"$TMP/functions.sh"
# shellcheck source=/dev/null
. "$TMP/functions.sh"

export TWITCH_PREDICTION_AGENTS="test:agent"
export TWITCH_PREDICTION_AI_TIMEOUT=30
export TWITCH_PREDICTION_COMMENTARY_ENABLED=1
AI_MODE="${AI_MODE:-ok}"
AI_LOG="$TMP/ai_calls.log"
: >"$AI_LOG"
: >"$TMP/chat.log"
: >"$TMP/audio.log"

ai_generate_list() {
	printf '%s\n' "$1" >>"$AI_LOG"
	if [ "$AI_MODE" = "fail" ]; then return 1; fi
	printf '%s\n' "$AI_CANNED"
	return 0
}
enqueue_chat_message() { printf '%s\n' "$1" >>"$TMP/chat.log"; }
enqueue_audio_text() { printf '%s\n' "$1" >>"$TMP/audio.log"; }

pass=0
fail=0
ok() { pass=$((pass + 1)); printf 'ok %d - %s\n' "$pass" "$1"; }
not_ok() { fail=$((fail + 1)); printf 'not ok - %s\n' "$1"; }

# --- validator ---
if _prediction_comment_validator '同志諸君、賭けるなら建国に全ツッパだ！計画経済に裏切りなし！'; then
	ok "日本語の一言を許可"
else
	not_ok "正常な一言を拒否"
fi

if _prediction_comment_validator '建国に100票集まりました。'; then
	not_ok "票数再掲を許可"
else
	ok "票数再掲を拒否"
fi

long=$(python3 -c 'print("あ" * 141)')
if _prediction_comment_validator "$long"; then
	not_ok "141文字を許可"
else
	ok "141文字を拒否"
fi

if _prediction_comment_validator 'Hello comrades vote now'; then
	not_ok "日本語なしを許可"
else
	ok "日本語なしを拒否"
fi

if _prediction_comment_validator 'The output is not ready **結果**'; then
	not_ok "英語自己訂正・装飾を許可"
else
	ok "英語自己訂正・装飾を拒否"
fi

if _prediction_comment_validator '1行目
2行目'; then
	not_ok "改行入りを許可"
else
	ok "改行入りを拒否"
fi

# --- start announce ---
AI_CANNED='五カ年計画どころか五分で決める！賭けない奴は資本主義者だ！'
export AI_CANNED
_announce_prediction_start "5分" "48"
if grep -Fq "$AI_CANNED" "$TMP/chat.log" && grep -Fq "$AI_CANNED" "$TMP/audio.log"; then
	ok "開始時にAIの掛け声をchat＋audioへ"
else
	not_ok "開始時のAI掛け声が未配送"
fi
if grep -Fq "RADIO_PREDICTION_START" "$AI_LOG"; then
	ok "開始生成はRADIO_PREDICTION_STARTラベル"
else
	not_ok "開始ラベルが不正: $(cat "$AI_LOG")"
fi

# --- result announce ---
: >"$TMP/chat.log"
: >"$TMP/audio.log"
AI_CANNED='見たか資本主義者ども！計画通りの建国だ！'
export AI_CANNED
_announce_prediction_result "予想結果：「ソ連建国」でした！" "ソ連建国"
if grep -Fq "予想結果：「ソ連建国」でした！ $AI_CANNED" "$TMP/chat.log" && \
	grep -Fq "予想結果：「ソ連建国」でした！ $AI_CANNED" "$TMP/audio.log"; then
	ok "結果は定型＋AIリアクションを結合して配送"
else
	not_ok "結果の結合配送が不正: $(cat "$TMP/chat.log")"
fi
if grep -Fq "RADIO_PREDICTION_RESULT" "$AI_LOG"; then
	ok "結果生成はRADIO_PREDICTION_RESULTラベル"
else
	not_ok "結果ラベルが不正"
fi

# --- AI failure falls back to fixed text ---
: >"$TMP/chat.log"
: >"$TMP/audio.log"
AI_MODE=fail
export AI_MODE
_announce_prediction_result "予想結果：「粛清」でした！" "粛清"
if grep -Fq "予想結果：「粛清」でした！" "$TMP/chat.log"; then
	ok "AI失敗時は定型のみ送る"
else
	not_ok "AI失敗時のフォールバックが不正"
fi
if [ -s "$TMP/audio.log" ] && grep -Fq "予想結果" "$TMP/audio.log"; then
	ok "AI失敗時もaudioは定型で送る"
else
	not_ok "AI失敗時のaudio配送が不正"
fi
AI_MODE=ok
export AI_MODE

# --- disabled flag stays silent ---
: >"$TMP/chat.log"
: >"$TMP/audio.log"
TWITCH_PREDICTION_COMMENTARY_ENABLED=0
export TWITCH_PREDICTION_COMMENTARY_ENABLED
_announce_prediction_start "5分" "48"
if [ ! -s "$TMP/chat.log" ] && [ ! -s "$TMP/audio.log" ]; then
	ok "無効時は開始AI追加分を送らない"
else
	not_ok "無効時に開始AI送信が発生"
fi
_announce_prediction_result "予想結果：「建国なし」でした！" "建国なし"
if grep -Fq "予想結果：「建国なし」でした！" "$TMP/chat.log" && ! grep -Fq "$AI_CANNED" "$TMP/chat.log"; then
	ok "無効時は結果の定型のみ送る"
else
	not_ok "無効時の結果配送が不正"
fi
TWITCH_PREDICTION_COMMENTARY_ENABLED=1
export TWITCH_PREDICTION_COMMENTARY_ENABLED

# --- wiring: exactly one announce site per flow ---
if [ "$(grep -c "_announce_prediction_start" "$SRC")" = "2" ]; then
	ok "開始告知は定義＋create側1箇所"
else
	not_ok "開始告知の配線数が不正"
fi
if [ "$(grep -c "_announce_prediction_result" "$SRC")" = "7" ]; then
	ok "結果告知は定義＋stale3箇所＋resolve3箇所"
else
	not_ok "結果告知の配線数が不正: $(grep -c "_announce_prediction_result" "$SRC")"
fi

# --- standalone source: no missing deps (本番で発生した log/_contains_provider_error_text 不足の回帰) ---
(
	unset -f log _contains_provider_error_text ai_generate_list 2>/dev/null
	cd "$ROOT" || exit 1
	# 注: sourceの定義を残すため $() 内で実行しない (回帰 sprawl 防止)
	_prediction_ensure_ai 2>"$TMP/ensure.err" || true
	err=$(cat "$TMP/ensure.err" 2>/dev/null || true)
	# declare -F で関数定義を見る (同名バイナリの誤検出を避ける)。
	if declare -F ai_generate_list >/dev/null 2>&1 && \
		declare -F log >/dev/null 2>&1 && \
		declare -F _contains_provider_error_text >/dev/null 2>&1; then
		if printf '%s' "$err" | grep -Fq "command not found"; then
			printf 'not ok - standalone sourceでcommand not found: %s\n' "$err" >&2
			exit 1
		fi
		exit 0
	else
		echo "not ok - standalone sourceで依存が揃わない" >&2
		exit 1
	fi
)
if [ "$?" -eq 0 ]; then
	ok "standalone sourceでもlog/判定関数が揃う"
else
	not_ok "standalone sourceで依存不足"
fi

printf 'pass=%d fail=%d\n' "$pass" "$fail"
[ "$fail" -eq 0 ]

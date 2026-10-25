#!/usr/bin/env bash
# docich#2778: Helix 失敗時の IRC フォールバックは、トークン所有者 (dociai) と
# 一致する nick を使う。TWITCH_BOT_NICK 明示時はそれが優先される。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/twitch_chat.sh"
fail=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; fail=1; }

eval "$(sed -n '/^_send()/,/^}/p' "$SRC")"

# Helix 経路は失敗させ (rc=1 ではなく 2 = 設定なし扱い)、IRC フォールバックへ進ませる。
_send_api() { return 2; }
_log() { :; }
sleep() { :; }
NC_LOG=$(mktemp /tmp/test_send_nick_XXXXXXXX)
nc() { cat >"$NC_LOG"; printf ':tmi.twitch.tv 001 dociai :Welcome, GLHF!\r\n'; }

run_send() {
  : >"$NC_LOG"
  env -u TWITCH_BOT_NICK -u TWITCH_BOT_LOGIN "$@" \
    bash -c "$(declare -f _send _send_api _log sleep nc); NC_LOG='$NC_LOG' TWITCH_BOT_TOKEN=synthetic-token TWITCH_CHANNEL=testchannel _send 'hello' >/dev/null 2>&1"
  grep -o 'NICK [^[:space:]]*' "$NC_LOG" | head -1
}

# ① 未設定時の既定 nick はトークン所有者 dociai
nick_line=$(run_send)
[ "$nick_line" = "NICK dociai" ] \
  && ok "IRC fallback default nick is dociai" \
  || not_ok "IRC fallback default nick is dociai (got '$nick_line')"

# ② TWITCH_BOT_NICK 明示時はそれが優先される (既存環境の非影響)
nick_line=$(run_send TWITCH_BOT_NICK=customnick)
[ "$nick_line" = "NICK customnick" ] \
  && ok "explicit TWITCH_BOT_NICK takes precedence" \
  || not_ok "explicit TWITCH_BOT_NICK takes precedence (got '$nick_line')"

# ③ TWITCH_BOT_LOGIN は TWITCH_BOT_NICK 未設定時のフォールバックとして残る
nick_line=$(run_send TWITCH_BOT_LOGIN=loginnick)
[ "$nick_line" = "NICK loginnick" ] \
  && ok "TWITCH_BOT_LOGIN remains a fallback" \
  || not_ok "TWITCH_BOT_LOGIN remains a fallback (got '$nick_line')"

rm -f "$NC_LOG"
exit "$fail"

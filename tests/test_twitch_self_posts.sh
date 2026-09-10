#!/usr/bin/env bash
# 2026-09-11 owner request: read all dociai chat.
# Self-posts must no longer be dropped by the daemon/fetch; their "user: "
# prefix is stripped so the classified/spoken text is the message body.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/twitch_chat.sh"
DAEMON="$ROOT/twitch_chat_daemon.sh"
fail=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; fail=1; }

eval "$(sed -n '/^_is_ignored_comment_author_line()/,/^}/p' "$SRC")"
eval "$(sed -n '/^_sanitize_comment_line()/,/^}/p' "$SRC")"
export TWITCH_IGNORE_AUTHORS="dociai azumagdev"

out=$(_sanitize_comment_line "dociai: レトロゲームコーナーです。") || true
[ "$out" = "レトロゲームコーナーです。" ] \
  && ok "self post body kept without user prefix" \
  || not_ok "self post body kept (got '$out')"

out=$(_sanitize_comment_line "azumagdev: 旧botの投稿") || true
[ "$out" = "旧botの投稿" ] \
  && ok "legacy bot self post kept" \
  || not_ok "legacy bot self post kept (got '$out')"

out=$(_sanitize_comment_line "viewer: こんにちは") || true
[ "$out" = "viewer: こんにちは" ] \
  && ok "other viewer line keeps its prefix" \
  || not_ok "other viewer line keeps its prefix (got '$out')"

# The daemon no longer drops ignored authors.
if grep -Fq 'if ! _is_card_gacha_result_message "$msg" && ! _is_prediction_announce_message "$msg"; then' "$DAEMON"; then
  not_ok "daemon still drops self-posts"
else
  ok "daemon no longer drops self-posts"
fi
# Trusted card/prediction formatting must remain present.
grep -Fq 'trusted-card' "$DAEMON" && ok "trusted-card kept" || not_ok "trusted-card kept"
grep -Fq 'trusted-prediction' "$DAEMON" && ok "trusted-prediction kept" || not_ok "trusted-prediction kept"

exit "$fail"

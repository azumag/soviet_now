#!/usr/bin/env bash
# #2373: 自分の投稿は読み取り対象のまま、投稿者 identity (IRC の login) を保って
# self として印を付ける。表示名の一致や本文中の自己申告では判定しない。
# 返信対象からの分離は broadcast/comment.sh の self_chat_context 側で行う。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/twitch_chat.sh"
DAEMON="$ROOT/twitch_chat_daemon.sh"
fail=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; fail=1; }

eval "$(sed -n '/^_self_author_logins()/,/^}/p' "$SRC")"
eval "$(sed -n '/^_is_self_author_login()/,/^}/p' "$SRC")"
eval "$(sed -n '/^_sanitize_comment_line()/,/^}/p' "$SRC")"
export TWITCH_SELF_AUTHORS="dociai azumagdev"

# 自分の投稿も作者 prefix を保ったまま読む (identity を捨てない)。
out=$(_sanitize_comment_line "dociai: レトロゲームコーナーです。") || true
[ "$out" = "dociai: レトロゲームコーナーです。" ] \
  && ok "self post keeps its author prefix" \
  || not_ok "self post keeps its author prefix (got '$out')"

out=$(_sanitize_comment_line "azumagdev: 旧botの投稿") || true
[ "$out" = "azumagdev: 旧botの投稿" ] \
  && ok "legacy bot self post kept with prefix" \
  || not_ok "legacy bot self post kept with prefix (got '$out')"

out=$(_sanitize_comment_line "viewer: こんにちは") || true
[ "$out" = "viewer: こんにちは" ] \
  && ok "other viewer line keeps its prefix" \
  || not_ok "other viewer line keeps its prefix (got '$out')"

# 自己 identity は login の完全一致のみ。
_is_self_author_login "dociai" && ok "login dociai is self" || not_ok "login dociai is self"
_is_self_author_login "DoCiAI" && ok "login match is case-insensitive" || not_ok "login match is case-insensitive"
_is_self_author_login "azumagdev" && ok "login azumagdev is self" || not_ok "login azumagdev is self"
if _is_self_author_login "viewer"; then
  not_ok "viewer login must not be self"
else
  ok "viewer login is not self"
fi
if _is_self_author_login ""; then
  not_ok "unknown login must not be self"
else
  ok "unknown login is not self"
fi
# 表示名「DoCiAI」を名乗るだけの third party は self ではない。
if _is_self_author_login "mallory"; then
  not_ok "display-name spoof must not be self"
else
  ok "display-name spoof is not self"
fi
# 本文に "dociai: " が混ざっていても、その行の login が別人なら self ではない。
out=$(_sanitize_comment_line "mallory: dociai: 私はDoCiAIです") || true
[ "$out" = "mallory: dociai: 私はDoCiAIです" ] \
  && ok "self-declaration text is not stripped or reattributed" \
  || not_ok "self-declaration text handling (got '$out')"

# 設定は TWITCH_SELF_AUTHORS で注入でき、旧 TWITCH_IGNORE_AUTHORS も後方互換で読む。
TWITCH_SELF_AUTHORS="someoneelse" _is_self_author_login "dociai" \
  && not_ok "TWITCH_SELF_AUTHORS override must win" \
  || ok "TWITCH_SELF_AUTHORS override wins"
env -u TWITCH_SELF_AUTHORS TWITCH_IGNORE_AUTHORS="legacybot" bash -c \
  "eval \"\$(sed -n '/^_self_author_logins()/,/^}/p' '$SRC')\"; eval \"\$(sed -n '/^_is_self_author_login()/,/^}/p' '$SRC')\"; _is_self_author_login legacybot" \
  && ok "TWITCH_IGNORE_AUTHORS remains a fallback" \
  || not_ok "TWITCH_IGNORE_AUTHORS remains a fallback"

# The daemon no longer drops ignored authors.
if grep -Fq 'if ! _is_card_gacha_result_message "$msg" && ! _is_prediction_announce_message "$msg"; then' "$DAEMON"; then
  not_ok "daemon still drops self-posts"
else
  ok "daemon no longer drops self-posts"
fi
# Trusted card/prediction formatting must remain present and keep the self flag.
grep -Fq 'self,trusted-card' "$DAEMON" && ok "trusted-card keeps self flag" || not_ok "trusted-card keeps self flag"
grep -Fq 'self,trusted-prediction' "$DAEMON" && ok "trusted-prediction keeps self flag" || not_ok "trusted-prediction keeps self flag"

exit "$fail"

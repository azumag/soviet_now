#!/usr/bin/env bash
# lib/twitch_tls_transport.sh の単体テスト (docich issue #38)。
# openssl引数構築・ROOMSTATE抽出・channel identity一致判定・identity schema検証を
# 実ネットワーク接続なしで検証する。実TLS接続を伴うe2eテストは
# tests/test_twitch_chat_daemon_tls_e2e.sh を参照。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/twitch_tls_transport_test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

cd "$WORK"
source "$ROOT/lib/twitch_tls_transport.sh"

# --- 1) 既定値: 本番はirc.chat.twitch.tv:6697、CAfileはOSデフォルト(未設定) ---
[ "$TWITCH_IRC_TLS_HOST" = "irc.chat.twitch.tv" ] && ok || ng "default TLS host is irc.chat.twitch.tv, got: $TWITCH_IRC_TLS_HOST"
[ "$TWITCH_IRC_TLS_PORT" = "6697" ] && ok || ng "default TLS port is 6697, got: $TWITCH_IRC_TLS_PORT"
[ "$TWITCH_IRC_TLS_VERIFY_HOST" = "irc.chat.twitch.tv" ] && ok || ng "default verify host is irc.chat.twitch.tv"
[ -z "$TWITCH_IRC_TLS_CAFILE" ] && ok || ng "default CAfile must be empty (use OS trust store)"

# --- 2) 引数構築: 証明書検証・hostname検証が必須フラグとして含まれる ---
twitch_tls_build_args
args_joined=$(printf '%s\n' "${TWITCH_TLS_ARGS[@]}")
echo "$args_joined" | grep -qx -- "-verify_return_error" && ok || ng "-verify_return_error must be present (fail closed on cert error)"
echo "$args_joined" | grep -qx -- "-verify_hostname" && ok || ng "-verify_hostname must be present (hostname check)"
echo "$args_joined" | grep -qx -- "-quiet" && ok || ng "-quiet must be present (keep session/cert info out of the IRC line stream)"
echo "$args_joined" | grep -qx -- "irc.chat.twitch.tv:6697" && ok || ng "-connect target must default to irc.chat.twitch.tv:6697"
# CAfile未設定時は-CAfileを渡さない(OSデフォルト信頼ストアを使う)
echo "$args_joined" | grep -qx -- "-CAfile" && ng "must not pass -CAfile when TWITCH_IRC_TLS_CAFILE is unset" || ok

# --- 3) CAfile override時のみ-CAfileが付く(テスト専用差し替え口) ---
TWITCH_IRC_TLS_CAFILE="$WORK/dummy_ca.pem"
twitch_tls_build_args
args_joined2=$(printf '%s\n' "${TWITCH_TLS_ARGS[@]}")
echo "$args_joined2" | grep -qx -- "-CAfile" && ok || ng "-CAfile must be present once TWITCH_IRC_TLS_CAFILE is set"
echo "$args_joined2" | grep -qx -- "$WORK/dummy_ca.pem" && ok || ng "-CAfile value must be the configured path"
TWITCH_IRC_TLS_CAFILE=""

# --- 4) ホスト/ポートのオーバーライドが引数に反映される(テストがmockサーバへ向けるため) ---
TWITCH_IRC_TLS_HOST="127.0.0.1"
TWITCH_IRC_TLS_PORT="16700"
TWITCH_IRC_TLS_VERIFY_HOST="irc.chat.twitch.tv"
twitch_tls_build_args
args_joined3=$(printf '%s\n' "${TWITCH_TLS_ARGS[@]}")
echo "$args_joined3" | grep -qx -- "127.0.0.1:16700" && ok || ng "-connect must follow TWITCH_IRC_TLS_HOST/PORT override"
echo "$args_joined3" | grep -qx -- "irc.chat.twitch.tv" && ok || ng "-servername/-verify_hostname must stay pinned to TWITCH_IRC_TLS_VERIFY_HOST even when connect target is an IP"
TWITCH_IRC_TLS_HOST="irc.chat.twitch.tv"
TWITCH_IRC_TLS_PORT="6697"

# --- 5) ROOMSTATE行からのroom-id/channel抽出 ---
tags="emote-only=0;room-id=999000;subs-only=0"
[ "$(twitch_tls_room_id_from_tags "$tags")" = "999000" ] && ok || ng "room-id extraction"
[ -z "$(twitch_tls_room_id_from_tags "" 2>/dev/null)" ] && ok || ng "empty tags -> empty room-id"
[ -z "$(twitch_tls_room_id_from_tags "emote-only=0" 2>/dev/null)" ] && ok || ng "tags without room-id -> empty"

payload=":tmi.twitch.tv ROOMSTATE #azumagbanjo"
[ "$(twitch_tls_channel_from_payload "$payload")" = "azumagbanjo" ] && ok || ng "channel extraction from ROOMSTATE"
[ -z "$(twitch_tls_channel_from_payload ":tmi.twitch.tv PRIVMSG #azumagbanjo :hi" 2>/dev/null)" ] && ok || ng "non-ROOMSTATE payload -> empty channel"

# --- 6) channel identity一致判定 ---
# 6a: 正しく一致 -> confirmed, rc=0
out=$(twitch_tls_identity_confirmed "azumagbanjo" "999000" "azumagbanjo" "999000")
rc=$?
[ "$rc" -eq 0 ] && ok || ng "matching channel+room-id must confirm (rc=$rc)"
case "$out" in confirmed:*) ok ;; *) ng "expected confirmed: prefix, got: $out" ;; esac

# 6b: room-id不一致 -> reject, rc=1
twitch_tls_identity_confirmed "azumagbanjo" "999000" "azumagbanjo" "111111" >/dev/null
rc=$?
[ "$rc" -eq 1 ] && ok || ng "room-id mismatch must reject (rc=$rc)"

# 6c: channel名不一致(room-idは一致) -> reject
twitch_tls_identity_confirmed "azumagbanjo" "999000" "someoneelse" "999000" >/dev/null
rc=$?
[ "$rc" -eq 1 ] && ok || ng "channel name mismatch must reject (rc=$rc)"

# 6d: channel名の大文字小文字違いは一致とみなす(IRCの慣習)
twitch_tls_identity_confirmed "AzumagBanjo" "999000" "azumagbanjo" "999000" >/dev/null
rc=$?
[ "$rc" -eq 0 ] && ok || ng "channel name comparison must be case-insensitive (rc=$rc)"

# 6e: expected_room_id(TWITCH_BROADCASTER_ID)未設定 -> 常にreject(fail closed既定)
twitch_tls_identity_confirmed "azumagbanjo" "" "azumagbanjo" "999000" >/dev/null
rc=$?
[ "$rc" -eq 1 ] && ok || ng "missing expected broadcaster id must reject (fail closed default)"

# 6f: actual(受信側)が欠落 -> reject
twitch_tls_identity_confirmed "azumagbanjo" "999000" "" "" >/dev/null
rc=$?
[ "$rc" -eq 1 ] && ok || ng "missing actual identity must reject"

# 6g: expected_room_idが数字以外(設定ミス) -> reject
twitch_tls_identity_confirmed "azumagbanjo" "not-a-number" "azumagbanjo" "999000" >/dev/null
rc=$?
[ "$rc" -eq 1 ] && ok || ng "non-numeric expected broadcaster id must reject"

# --- 7) identity schema検証 ---
twitch_identity_valid_channel_id "999000" && ok || ng "numeric channel id valid"
twitch_identity_valid_channel_id "" && ng "empty channel id must be invalid" || ok
twitch_identity_valid_channel_id "abc123" && ng "non-numeric channel id must be invalid" || ok

twitch_identity_valid_user_id "1001" && ok || ng "numeric user id valid"
twitch_identity_valid_user_id "" && ng "empty user id must be invalid" || ok
twitch_identity_valid_user_id "-1" && ng "negative-looking user id must be invalid" || ok

twitch_identity_valid_badges "" && ok || ng "no badges (plain viewer) is valid"
twitch_identity_valid_badges "broadcaster/1" && ok || ng "single badge valid"
twitch_identity_valid_badges "broadcaster/1,subscriber/12" && ok || ng "multiple badges valid"
twitch_identity_valid_badges "broadcaster/1;rm -rf /" && ng "shell-metacharacter-laced badges must be invalid" || ok
twitch_identity_valid_badges "broadcaster" && ng "badges without /version must be invalid" || ok

echo "test_twitch_tls_transport: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# lib/twitch_command_registry.sh の単体テスト (docich issue #37)。
# deny-by-default gate: role=viewer以外は認証済みtransport(#38)完了まで常にdeny。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/twitch_cmd_registry_test.XXXXXX")
trap 'rm -rf "$WORK"' EXIT

pass=0; fail=0
ok() { pass=$((pass+1)); }
ng() { fail=$((fail+1)); echo "FAIL: $*"; }

cd "$WORK"
TWITCH_CHAT_DIR="$WORK/.twitch_chat"
export TWITCH_CHAT_DIR
source "$ROOT/lib/twitch_command_registry.sh"

# 1) スキーマ: 期待するcommand全てにrequired_role/side_effect/rate_limit/confirmation/
#    idempotency/auditの6フィールドが全て非空で登録されている。
expected_ids="clip stream_start audio_repair pitch tempo voice_style asmr ntrob doushi"
for id in $expected_ids; do
    twitch_cmd_registered "$id" && ok || ng "registered: $id"
    for field in role side_effect rate_limit confirmation idempotency audit; do
        val=$(twitch_cmd_field "$id" "$field")
        [ -n "$val" ] && ok || ng "schema $id.$field non-empty"
    done
done

# 2) 未登録commandはdeny (fail closed)
twitch_cmd_authorize "no_such_command" "12345" "true" && ng "unregistered command must deny" || ok

# 3) role=viewerは常にallow (未認証transportでもspoofed roleでも)
TWITCH_TRANSPORT_AUTHENTICATED=0
twitch_cmd_authorize "clip" "" "false" && ok || ng "viewer role allow (no identity)"
twitch_cmd_authorize "clip" "999" "true" && ok || ng "viewer role allow (even with mod flag)"

# 4) role!=viewer は未認証transportの間、broadcaster/mod badgeがあっても常にdeny
twitch_cmd_authorize "stream_start" "111" "true" && ng "stream_start must deny while transport unauthenticated (broadcaster)" || ok
twitch_cmd_authorize "audio_repair" "111" "true" && ng "audio_repair must deny while transport unauthenticated (broadcaster)" || ok
twitch_cmd_authorize "pitch" "111" "true" && ng "pitch must deny while transport unauthenticated (mod)" || ok
twitch_cmd_authorize "tempo" "111" "true" && ng "tempo must deny while transport unauthenticated (mod)" || ok

# 5) spoofed 表示名 / badge文字列を直接authorizeに渡しても関係ない
#    (authorizeはis_mod_or_broadcasterのbool以外、表示名を一切見ない)
twitch_cmd_authorize "stream_start" "111" "false" && ng "stream_start must deny for non-mod even if badges text says broadcaster elsewhere" || ok

# 6) 認証済みtransport (#38完了後の想定) では、正しいroleとstable user-idがあれば許可、
#    無ければ拒否する。
TWITCH_TRANSPORT_AUTHENTICATED=1
twitch_cmd_authorize "stream_start" "111" "true" && ok || ng "stream_start allow once transport authenticated + broadcaster"
twitch_cmd_authorize "stream_start" "111" "false" && ng "stream_start deny once transport authenticated but not broadcaster" || ok
twitch_cmd_authorize "stream_start" "" "true" && ng "stream_start deny without stable user-id even if authenticated" || ok
TWITCH_TRANSPORT_AUTHENTICATED=0

# 7) audit ログに全決定が記録される
[ -s "$TWITCH_CMD_AUDIT_LOG" ] && ok || ng "audit log written"
grep -q "cmd=no_such_command" "$TWITCH_CMD_AUDIT_LOG" && ok || ng "audit log contains deny for unregistered command"
grep -q "cmd=stream_start" "$TWITCH_CMD_AUDIT_LOG" && ok || ng "audit log contains stream_start decisions"

# 8) rate limit ヘルパー
cd_file="$WORK/cooldown"
twitch_cmd_rate_limited "$cd_file" 60 && ng "no cooldown file yet must not be rate-limited" || ok
twitch_cmd_mark_rate_limit "$cd_file"
twitch_cmd_rate_limited "$cd_file" 60 && ok || ng "just marked cooldown must be rate-limited"
twitch_cmd_rate_limited "$cd_file" 0 && ng "cooldown_sec=0 must never rate-limit" || ok

echo "test_twitch_command_registry: pass=$pass fail=$fail"
[ "$fail" -eq 0 ]

#!/usr/bin/env bash
# 2026-10-27 #2793: コメント英訳の失敗がどのログにも残らない事象を受け、
# 翻訳経路の失敗種別 (timeout / provider_error / empty_output /
# invalid_output) を専用ファイルへ必ず 1 行残す契約を固定する。
# 呼び出し元は `translation_text=$(...)` で stdout だけを翻訳文として
# 受け取るため、理由は専用ログへ直接書く必要がある。
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/broadcast/comment.sh"
fail=0
pass=0
ok() { pass=$((pass + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

eval "$(sed -n '/^_comment_translation_log()/,/^}$/p' "$SRC")"
eval "$(sed -n '/^_comment_generate_translation()/,/^}$/p' "$SRC")"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
COMMENT_TRANSLATION_LOG="$TMP/comment_translation.log"
export COMMENT_TRANSLATION_LOG

log() { :; }
_comment_guard_model_text() { printf '%s' "$1"; }
_comment_is_valid_translation_candidate() {
	local raw="$1"
	[ -n "$raw" ] || return 1
	printf '%s' "$raw" | grep -qi 'tool_call' && return 1
	return 0
}
_ai_backoff_check() { return 0; }

prompt="$TMP/prompt.txt"
printf 'prompt\n' >"$prompt"
last_agent="$TMP/last_agent"
: >"$last_agent"

reset_log() { : >"$COMMENT_TRANSLATION_LOG"; }
log_lines() { grep -c '\[COMMENT_TRANSLATION\]' "$COMMENT_TRANSLATION_LOG" 2>/dev/null || true; }
has() { grep -q "$1" "$COMMENT_TRANSLATION_LOG" 2>/dev/null; }

# 1. provider 失敗 (rc=1) は provider_error として agent ごとに残る
reset_log
_ai_dispatch() { return 1; }
out=$(_comment_generate_translation "$prompt" "alpha,beta" 5 "$last_agent" 2>/dev/null)
rc=$?
[ "$rc" -ne 0 ] && ok || not_ok "全 agent 失敗で非 0 を返す (rc=$rc)"
has 'event=failure kind=provider_error agent=alpha rc=1 attempt=1' && ok || not_ok "alpha の provider_error 記録"
has 'event=failure kind=provider_error agent=beta rc=1 attempt=2' && ok || not_ok "beta の provider_error 記録"
has 'event=exhausted kind=no_translation' && ok || not_ok "exhausted 記録"
[ -z "$out" ] && ok || not_ok "失敗時に stdout へ理由を混ぜない (got '$out')"

# 2. timeout (rc=124) は timeout として区別する
reset_log
_ai_dispatch() { return 124; }
_comment_generate_translation "$prompt" "alpha" 5 "$last_agent" >/dev/null 2>&1
has 'event=failure kind=timeout agent=alpha rc=124' && ok || not_ok "timeout の記録"

# 3. rc=0 でも空出力は empty_output
reset_log
_ai_dispatch() { return 0; }
_comment_generate_translation "$prompt" "alpha" 5 "$last_agent" >/dev/null 2>&1
has 'event=failure kind=empty_output agent=alpha rc=0' && ok || not_ok "empty_output の記録"

# 4. 推論ブロック混入などは invalid_output
reset_log
_ai_dispatch() { printf 'tool_call: {"name":"x"}\n'; return 0; }
_comment_generate_translation "$prompt" "alpha" 5 "$last_agent" >/dev/null 2>&1
has 'event=failure kind=invalid_output agent=alpha rc=0' && ok || not_ok "invalid_output の記録"

# 5. 成功時は success を残し、翻訳文だけを stdout へ返す
reset_log
_ai_dispatch() { printf 'Hello from the stream.\n'; return 0; }
out=$(_comment_generate_translation "$prompt" "alpha,beta" 5 "$last_agent" 2>/dev/null)
rc=$?
[ "$rc" -eq 0 ] && ok || not_ok "成功時は 0 を返す (rc=$rc)"
[ "$out" = "Hello from the stream." ] && ok || not_ok "成功時の stdout は翻訳文のみ (got '$out')"
[ "$(cat "$last_agent")" = "alpha" ] && ok || not_ok "成功 agent を last_agent_file へ書く"
has 'event=success kind=ok agent=alpha rc=0 attempt=1' && ok || not_ok "success の記録"

# 6. 3 件目は試行上限(2)で止まり、agent_limit を残す
reset_log
_ai_dispatch() { return 1; }
_comment_generate_translation "$prompt" "alpha,beta,gamma" 5 "$last_agent" >/dev/null 2>&1
has 'event=stop kind=agent_limit agent=gamma' && ok || not_ok "agent_limit の記録"
[ "$(log_lines)" -eq 4 ] && ok || not_ok "3 agent でも試行は 2 回 + stop + exhausted の 4 行 (got $(log_lines))"

# 7. backoff skip も理由が残る
reset_log
_ai_backoff_check() { return 1; }
_ai_dispatch() { printf 'Hello.\n'; return 0; }
_comment_generate_translation "$prompt" "alpha,beta" 5 "$last_agent" >/dev/null 2>&1
has 'event=skip kind=backoff agent=alpha' && ok || not_ok "backoff skip の記録"

# 8. 記録は stderr にも出る (既存の ai_stderr.log 経路を壊さない)
reset_log
stderr_out=$(_comment_translation_log "failure" "provider_error" "alpha" 1 1 2>&1 >/dev/null)
case "$stderr_out" in
*"[COMMENT_TRANSLATION] event=failure kind=provider_error agent=alpha rc=1 attempt=1"*) ok ;;
*) not_ok "stderr にも同じ 1 行を出す (got '$stderr_out')" ;;
esac

# 9. ログ先が書けない場合でも翻訳経路を止めない
_ai_backoff_check() { return 0; }
_ai_dispatch() { printf 'Hello.\n'; return 0; }
COMMENT_TRANSLATION_LOG="/proc/definitely-not-writable/x.log" \
	_comment_generate_translation "$prompt" "alpha" 5 "$last_agent" >/dev/null 2>&1
rc=$?
[ "$rc" -eq 0 ] && ok || not_ok "ログ失敗時も翻訳結果を返す (rc=$rc)"

echo "pass=$pass fail=$fail"
exit "$fail"

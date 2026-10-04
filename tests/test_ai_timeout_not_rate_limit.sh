#!/usr/bin/env bash
# タイムアウトを「明示的レート制限」と誤判定しないことの回帰テスト。
#
# 2026-10-02 本番実測の事象:
#   opencode 系は上流 429 を出したまま内部リトライでレーンのタイムアウト
#   (prepass 30秒 / COMMENT 240秒) を超えることがある。timeout 分岐が
#   stderr の 429 文言を根拠に AI_RATE_LIMIT_RC(79) を返すと、チェーン側が
#   そのモデルに 18000秒(5時間) の bench を課した。本番では COMMENT チェーンの
#   6モデル中4本が実測で 8秒以内(rc=0)に応答できるにもかかわらず、
#   最大20時間コメント返信生成が完全停止した。
#
# 契約:
#   - rc=124 (timeout) は常に通常失敗(rc=1)。bench の根拠にしない。
#   - rc!=0 で stderr に明示的なレート制限が出た場合のみ 79 を返す。
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export AI_STATS_DIR="$TMP/ai_stats"
export AI_BACKOFF_DIR="$TMP/ai_backoff"
export AI_FAIL_STREAK_DIR="$TMP/ai_fail_streak"
export AI_GENERATION_QUEUE_ENABLED=0
export AI_RADIO_IMPROVE_GATE=0
export OPENCODE_ABORT_RETRY_WAIT_SEC=0

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
source "$ROOT/core/helpers.sh"
source "$ROOT/lib/ai_generate.sh"

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

FAKE_BIN="$TMP/fakebin"
mkdir -p "$FAKE_BIN"
prompt_file="$TMP/prompt.txt"
printf 'テストプロンプト' >"$prompt_file"

# --- 1. opencode: timeout(rc=124) + stderr に429があっても 79 を返さない ---
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
# 上流429を出したまま内部リトライでハングする実象を再現する
printf 'Error: OpenCode upstream rate limit (429); stopped internal retry\n' >&2
sleep 30
EOF
chmod +x "$FAKE_BIN/opencode"
export OPENCODE_BIN="$FAKE_BIN/opencode"
unset OPENCODE_ABORT_RETRY

_ai_call_opencode_unqueued "TEST:timeout429" "opencode:x-timeout-f-free" "$prompt_file" 1 >/dev/null 2>&1
rc=$?
check '[ "$rc" -ne "$AI_RATE_LIMIT_RC" ]' 'opencode: timeout(rc=124)+stderr429 はレート制限として返さない'
check '[ "$rc" -ne 0 ]' 'opencode: timeout は通常失敗(rc!=0)として返す'

# チェーン経由でも長時間の bench が発生しないこと。ラベルのタイムアウトを
# 短くして 本番の prepass 30秒相当をローカルで再現する。
export COMMENT_CODEX_TIMEOUT=2
rm -f "$AI_BACKOFF_DIR"/* 2>/dev/null
chain_log=$(ai_generate_list "COMMENT:timeout_chain" "$prompt_file" "opencode:x-timeout-f-free" "" "" "" 2>&1 >/dev/null)
bf_file="$AI_BACKOFF_DIR/opencode_x-timeout-f-free"
rem=0
[ -f "$bf_file" ] && rem=$(( $(cat "$bf_file") - $(date +%s) ))
check '[ "$rem" -gt 0 ] && [ "$rem" -le 3600 ]' 'timeout は短時間の失敗バックオフ(3600秒以内)だけになる'
check '! printf "%s" "$chain_log" | grep -q "explicit rate limit"' 'timeout では explicit rate limit を記録しない'
check 'printf "%s" "$chain_log" | grep -q "provider failure → short backoff"' 'timeout は provider failure の短バックオフとして記録される'
check 'printf "%s" "$chain_log" | grep -q "COMMENT:timeout_chain] opencode timeout (2s"' 'タイムアウトとして記録される'

# --- 2. opencode: 明示的な429(rc!=0) は従来どおり bench する ---
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
printf 'Error: {"name":"ProviderError","message":"429 Too Many Requests"}\n' >&2
exit 1
EOF
chmod +x "$FAKE_BIN/opencode"
rm -f "$AI_BACKOFF_DIR"/* "$AI_FAIL_STREAK_DIR"/* 2>/dev/null

_ai_call_opencode_unqueued "TEST:real429" "opencode:x-real429-f" "$prompt_file" 30 >/dev/null 2>&1
rc=$?
check '[ "$rc" -eq "$AI_RATE_LIMIT_RC" ]' 'opencode: rc!=0 + 明示的429 は AI_RATE_LIMIT_RC を返す'

chain_log=$(ai_generate_list "COMMENT:real429_chain" "$prompt_file" "opencode:x-real429-f" "" "" "" 2>&1 >/dev/null)
check 'printf "%s" "$chain_log" | grep -q "explicit rate limit"' '明示的429 は長時間の bench を発火する'
expected_bench=$(_ai_backoff_sec_for_label "COMMENT:real429_chain")
rem=0
[ -f "$AI_BACKOFF_DIR/opencode_x-real429-f" ] && rem=$(( $(cat "$AI_BACKOFF_DIR/opencode_x-real429-f") - $(date +%s) ))
diff=$(( rem - expected_bench ))
check '[ "$diff" -ge -10 ] && [ "$diff" -le 10 ]' '明示的429 の bench は設定されたレート制限 bench 値になる'

# --- 3. retired Codex never executes, even if a fake binary would return 429 ---
cat >"$FAKE_BIN/codex" <<'EOF'
#!/usr/bin/env bash
touch "$CODEX_TEST_MARKER"
printf '429 rate limit exceeded\n' >&2
exit 79
EOF
chmod +x "$FAKE_BIN/codex"
export CODEX_BIN="$FAKE_BIN/codex" CODEX_TEST_MARKER="$TMP/codex-executed"
for label in COMMENT RADIO RADIO_RESEARCH; do
    _ai_call_codex_unqueued "$label" "codex:x-retired" "$prompt_file" 1 >/dev/null 2>&1
    rc=$?
    check '[ "$rc" -eq 1 ]' "codex: $label retired adapter fails closed"
    check '[ ! -e "$CODEX_TEST_MARKER" ]' "codex: $label never executes binary"
done

# --- 4. claude backend も同じ契約 (timeout は bench しない) ---
cat >"$FAKE_BIN/claude" <<'EOF'
#!/usr/bin/env bash
printf 'API Error: 429 rate limit reached\n' >&2
sleep 30
EOF
chmod +x "$FAKE_BIN/claude"
export PATH="$FAKE_BIN:$PATH"
_ai_call_claude_unqueued "TEST:claude_timeout" "$prompt_file" "test-model" 1 >/dev/null 2>&1
rc=$?
check '[ "$rc" -ne "$AI_RATE_LIMIT_RC" ]' 'claude: timeout(rc=124)+stderr429 はレート制限として返さない'
check '[ "$rc" -ne 0 ]' 'claude: timeout は通常失敗(rc!=0)として返す'

printf '\n%d/%d tests passed\n' "$ok" "$((ok + fail))"
[ "$fail" -eq 0 ]

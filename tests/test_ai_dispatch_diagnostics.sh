#!/usr/bin/env bash
# AI dispatch の失敗観測とサーキットブレーカの回帰テスト。
# - 失敗時に ai_stats JSONL へ error フィールドが記録される
# - 連続 provider 失敗でバックオフが段階延長される（streak）
# - 成功時 (_ai_dispatch ok) に streak が解除される
# - UnknownError / unexpected server error が provider error 判定に一致する
# - opencode CLI の一過性失敗 (rc!=0・空出力) は同一モデルで1回だけ再試行する
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export AI_STATS_DIR="$TMP/ai_stats"
export AI_BACKOFF_DIR="$TMP/ai_backoff"
export AI_FAIL_STREAK_DIR="$TMP/ai_fail_streak"
export AI_GENERATION_QUEUE_ENABLED=0
export OPENCODE_ABORT_RETRY_WAIT_SEC=0

# core/helpers.sh の依存が最小のため、log は先にスタブする
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

# --- 1. provider error 判定に UnknownError 系が含まれる ---
deepseek_error='Error: {
  "name": "UnknownError",
  "data": {
    "message": "Unexpected server error. Check server logs for details.",
    "ref": "err_9e579fcf"
  }
}'
check '_contains_provider_error_text "$deepseek_error"' 'UnknownError JSON が provider error 判定に一致する'
check '! _contains_provider_error_text "こんにちは、今日もいい天気ですね。"' '通常文は provider error 判定に一致しない'
legacy_agent_error='!  agent "minimax" not found. Falling back to default agent
Error: Free tier users do not have access to this model. Upgrade to paid credits.'
check '_contains_provider_error_text "$legacy_agent_error"' 'OpenCode agent欠落+free-tier拒否をprovider errorとして検出する'

# JIJI research はモデルspecを共通dispatchへ渡す。legacy --agent 経路へ誤配送しない。
printf '調査対象' >"$TMP/jiji_prompt.txt"
jiji_route=$(
	(
		source "$ROOT/broadcast/radio_corners.sh"
		_ai_dispatch() { printf 'dispatch:%s:%s' "$1" "$2"; }
		_ai_generation_queue_run() { printf 'legacy:%s' "$1"; }
		_run_opencode_jiji_research "opencode:muse-spark-test" "$TMP/jiji_prompt.txt"
	)
)
check '[ "$jiji_route" = "dispatch:RADIO:JIJI_RESEARCH:opencode:muse-spark-test" ]' 'JIJI research のモデルspecは共通AI dispatchへ送る'
jiji_primary_count=$(grep -F -c '_run_opencode_jiji_research "${RADIO_MAIN_PREPASS_AGENT}"' "$ROOT/broadcast/radio_corners.sh")
check '[ "$jiji_primary_count" -ge 1 ]' 'JIJI research primaryは有効なprepassモデルspecを使う'

# 長文stderrは先頭(バナー)と末尾(エラー本体)を両方残す
long_banner="Reading additional input from stdin... OpenAI Codex v0.147.0 -------- workdir: /home/ubuntu/soren model: amd-token-factory-deepseek-v4-flash provider: soren-litellm session id: 01a023c3 -------- stream error: provider returned 500 upstream failure"
preview=$(_ai_error_preview_from_text "$long_banner")
check 'printf %s "$preview" | grep -q "Reading additional input"' '長文previewにバナー先頭が残る'
check 'printf %s "$preview" | grep -q "500 upstream failure"' '長文previewに末尾のエラー本体が残る'
check '[ ${#preview} -le 220 ]' 'previewは概ね200字以内に収まる'

# CLIのstderrは切断位置で不正なUTF-8を含むことがある。診断欠落を防ぐ。
malformed_stderr=$(printf 'prefix \xff\xe3 suffix')
preview=$(_ai_error_preview_from_text "$malformed_stderr")
check '[ -n "$preview" ]' '不正UTF-8を含むstderrでもerror概要を生成する'
check '[ ${#preview} -le 220 ]' '不正UTF-8を含むerror概要も上限内に収める'

# --- 2. opencode 一過性失敗の1回再試行 ---
FAKE_BIN="$TMP/fakebin"
mkdir -p "$FAKE_BIN"
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
COUNT_FILE="${OC_CALL_COUNT:-/tmp/oc_call_count}"
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\n' "$n" >"$COUNT_FILE"
if [ "$n" -eq 1 ]; then
	echo "boom" >&2
	exit 1
fi
printf '再試行後の正常応答です。'
EOF
chmod +x "$FAKE_BIN/opencode"
export PATH="$FAKE_BIN:$PATH"
# /snap/bin/opencode が実在する環境 (Linux VM) でもスタブを優先させる
export OPENCODE_BIN="$FAKE_BIN/opencode"
unset OPENCODE_ABORT_RETRY
rm -f "${TMP}/oc_count"
export OC_CALL_COUNT="${TMP}/oc_count"

prompt_file="$TMP/prompt.txt"
printf 'テストプロンプト' >"$prompt_file"
out=$(_ai_call_opencode_unqueued "TEST" "opencode:x-preview-f-free" "$prompt_file" 30)
rc=$?
check '[ "$rc" -eq 0 ]' '一過性失敗からの再試行で成功する'
check '[ "$out" = "再試行後の正常応答です。" ]' '再試行の出力が透過する'
check '[ "$(cat "$OC_CALL_COUNT")" = "2" ]' 'CLI呼び出しが2回(初回+再試行1回)にとどまる'

# rc=0でもreasoning-only等はcleanup後に本文空になるため成功扱いしない。
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
COUNT_FILE="${OC_CALL_COUNT:-/tmp/oc_call_count}"
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\n' "$n" >"$COUNT_FILE"
if [ "$n" -eq 1 ]; then
	printf '<think>reasoning only</think>'
	exit 0
fi
printf '本文のみの応答です。'
EOF
chmod +x "$FAKE_BIN/opencode"
rm -f "$OC_CALL_COUNT"
out=$(_ai_call_opencode_unqueued "TEST:empty_cleanup" "opencode:x-preview-f-free" "$prompt_file" 30)
rc=$?
check '[ "$rc" -eq 0 ]' '本文空出力を検出して再試行する'
check '[ "$out" = "本文のみの応答です。" ]' 'cleanup後に有効な本文だけを返す'
check '[ "$(cat "$OC_CALL_COUNT")" = "2" ]' '本文空出力の再試行は1回にとどまる'

# 再試行無効化フラグ
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
COUNT_FILE="${OC_CALL_COUNT:-/tmp/oc_call_count}"
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\n' "$n" >"$COUNT_FILE"
echo "always fail" >&2
exit 1
EOF
chmod +x "$FAKE_BIN/opencode"
rm -f "$OC_CALL_COUNT"
OPENCODE_ABORT_RETRY=0 out=$(_ai_call_opencode_unqueued "TEST" "opencode:x-preview-f-free" "$prompt_file" 30)
rc=$?
check '[ "$rc" -ne 0 ]' '再試行無効時は失敗をそのまま返す'
check '[ "$(cat "$OC_CALL_COUNT")" = "1" ]' '再試行無効時はCLI呼び出し1回'

# タイムアウトは再試行しない
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
COUNT_FILE="${OC_CALL_COUNT:-/tmp/oc_call_count}"
n=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
n=$((n + 1))
printf '%s\n' "$n" >"$COUNT_FILE"
sleep 30
EOF
chmod +x "$FAKE_BIN/opencode"
rm -f "$OC_CALL_COUNT"
out=$(_ai_call_opencode_unqueued "TEST" "opencode:x-preview-f-free" "$prompt_file" 1)
rc=$?
check '[ "$(cat "$OC_CALL_COUNT")" = "1" ]' 'タイムアウト時は再試行しない'

# --- 3. ai_stats への error 記録 + streak サーキットブレーカ ---
# streak の加算・延長は chain 実行器 (ai_generate_list) が担うため
# 単一候補の chain で検証する。
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
echo "simulated upstream outage" >&2
exit 1
EOF
chmod +x "$FAKE_BIN/opencode"
rm -f "$OC_CALL_COUNT"

_stats_line() {
	tail -n 1 "$AI_STATS_DIR/$(date +%Y%m%d).jsonl" 2>/dev/null
}

ai_generate_list "TEST:streak" "$prompt_file" "opencode:x-preview-f-free" "" "" "" >/dev/null 2>&1
line=$(grep '"event":"fail"' "$AI_STATS_DIR/$(date +%Y%m%d).jsonl" | tail -n 1)
check 'printf %s "$line" | grep -q "\"event\":\"fail\""' 'dispatch失敗がstatsへ記録される'
check 'printf %s "$line" | grep -q "simulated upstream outage"' 'statsのerrorフィールドにstderr概要が入る'
check 'printf %s "$line" | python3 -c "import json,sys; json.loads(sys.stdin.read()); print(1)" | grep -q 1' 'error付きfailレコードは妥当なJSONである'
check '[ -f "$AI_FAIL_STREAK_DIR/opencode_x-preview-f-free" ]' '失敗streakファイルが作られる'
check '[ "$(cat "$AI_FAIL_STREAK_DIR/opencode_x-preview-f-free")" = "1" ]' '1回目の失敗でstreak=1'

# 2回目以降はバックオフ期限切れ後の再試行を模擬するため backoff を都度消してから実行する
rm -f "$AI_BACKOFF_DIR/opencode_x-preview-f-free"
ai_generate_list "TEST:streak" "$prompt_file" "opencode:x-preview-f-free" "" "" "" >/dev/null 2>&1
rm -f "$AI_BACKOFF_DIR/opencode_x-preview-f-free"
ai_generate_list "TEST:streak" "$prompt_file" "opencode:x-preview-f-free" "" "" "" >/dev/null 2>&1
rm -f "$AI_BACKOFF_DIR/opencode_x-preview-f-free"
check '[ "$(cat "$AI_FAIL_STREAK_DIR/opencode_x-preview-f-free")" = "3" ]' '連続失敗でstreakが加算される'
ai_generate_list "TEST:streak" "$prompt_file" "opencode:x-preview-f-free" "" "" "" >/dev/null 2>&1
bf_until=$(cat "$AI_BACKOFF_DIR/opencode_x-preview-f-free")
now=$(date +%s)
rem=$((bf_until - now))
check '[ "$rem" -gt 300 ] && [ "$rem" -le 3600 ]' 'streak>1でバックオフが300秒より延長され上限内に収まる'

# 成功で streak 解除（streak>=3 の解除は復旧ログを出す）
cat >"$FAKE_BIN/opencode" <<'EOF'
#!/usr/bin/env bash
printf '復旧済みの出力です。'
EOF
chmod +x "$FAKE_BIN/opencode"
recovery_log=$(_ai_dispatch "TEST:streak" "opencode:x-preview-f-free" "$prompt_file" 30 2>&1 >/dev/null)
check '[ ! -f "$AI_FAIL_STREAK_DIR/opencode_x-preview-f-free" ]' '成功時にstreakが解除される'
check 'printf %s "$recovery_log" | grep -q "recovered after 4 consecutive failures"' 'streak>=3の解除で復旧ログが出る'

# --- 4. バックオフ中は chain がスキップし、stats attempt を増やさない ---
mkdir -p "$AI_BACKOFF_DIR"
now=$(date +%s)
printf '%s\n' $((now + 900)) >"$AI_BACKOFF_DIR/codex_minimax-m3"
printf '候補テスト' >"$prompt_file"
before=$(wc -l <"$AI_STATS_DIR/$(date +%Y%m%d).jsonl")
ai_generate_list "TEST:chain" "$prompt_file" "codex:minimax-m3,local" "" "" "" >/dev/null 2>&1
after=$(wc -l <"$AI_STATS_DIR/$(date +%Y%m%d).jsonl")
check "[ $((after - before)) -lt 5 ]" 'バックオフ中候補はattemptを記録せずスキップされる'

printf '\n%d/%d tests passed\n' "$ok" "$((ok + fail))"
[ "$fail" -eq 0 ]

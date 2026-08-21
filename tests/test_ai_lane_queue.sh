#!/usr/bin/env bash
# AIレーン同時実行制御の回帰テスト。
# - 放送系 (RADIO/NEWS/JIJI/CELEBRATION) はモデル差に関係なく単一 "radio" レーンへ直列化
# - コメント返し (COMMENT*) も "comment" レーンへ直列化
# - 改善ジョブ稼働中 (improve_state.json running + PID生存) は新規放送系生成のみ待機
# - 改善開始前に始まった生成 (RADIO_GEN_STARTED_AT < improve started_at) はキャンセルされない
# - 待機上限 (AI_RADIO_IMPROVE_WAIT_MAX_SEC) 超過でその生成だけ諦める
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

export AI_STATS_DIR="$TMP/ai_stats"
export AI_BACKOFF_DIR="$TMP/ai_backoff"
export AI_FAIL_STREAK_DIR="$TMP/ai_fail_streak"
export AI_GENERATION_QUEUE_ENABLED=1
# 注意: AI_GENERATION_QUEUE_LOCK_DIR はスコープ接尾辞なしで使われる既存仕様のため、
# レーン分離を検証するここでは使わず一時workdirへcdして既定パスを使う。
unset AI_GENERATION_QUEUE_LOCK_DIR
WORK="$TMP/work"
mkdir -p "$WORK"
cd "$WORK" || exit 1
LOCK_BASE="$WORK/tmp/state/.ai_generation_locks"
export IMPROVE_STATE_FILE="$TMP/improve_state.json"
export AI_GENERATION_QUEUE_WAIT_SEC=1

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

write_improve_state() {
	local status="$1" pid="$2" started_at="$3"
	cat >"$IMPROVE_STATE_FILE" <<EOF
{"status": "$status", "pid": $pid, "started_at": $started_at, "phase": "test"}
EOF
}

# --- 1. レーンスコープの畳み込み ---
check '[ "$(_ai_queue_lock_scope "RADIO:news:prepass:remote:codex:modelA")" = "radio" ]' 'RADIO系ラベルは radio レーンに畳まれる'
check '[ "$(_ai_queue_lock_scope "JIJI:research:minimax")" = "radio" ]' 'JIJI系ラベルも radio レーンに畳まれる'
check '[ "$(_ai_queue_lock_scope "NEWS:x")" = "radio" ]' 'NEWS系ラベルも radio レーンに畳まれる'
check '[ "$(_ai_queue_lock_scope "COMMENT:opencode:muse")" = "comment" ]' 'COMMENT系ラベルは comment レーンに畳まれる'
check '[ "$(_ai_queue_lock_scope "COMMENT_TRANSLATION:x")" = "comment" ]' 'COMMENT_TRANSLATION も comment レーンに畳まれる'
scope_default=$(_ai_queue_lock_scope "RADIO:test:remote:codex:modelB")
check '[ "$scope_default" = "radio" ]' 'フラグ既定値では radio レーンが有効'

# 注意: bash では「代入のみの文への前置代入」が現在シェルに永続するため、
# フラグ切替は必ず明示的なサブシェル ( export ...; func ) 形式で行う。
scope_off=$( (export AI_RADIO_LANE_LOCK=0; _ai_queue_lock_scope "RADIO:test:remote:codex:modelB") )
check '[ "$scope_off" != "radio" ]' 'AI_RADIO_LANE_LOCK=0 で従来のモデル別スコープに戻る'
scope_coff=$( (export AI_COMMENT_LANE_LOCK=0; _ai_queue_lock_scope "COMMENT:test:remote:codex:m") )
check '[ "$scope_coff" != "comment" ]' 'AI_COMMENT_LANE_LOCK=0 で従来スコープに戻る'

# --- 2. 放送系の直列化 (異なるモデルでも待ち合わせる) ---
rm -rf "$LOCK_BASE"
mkdir -p "$LOCK_BASE/radio"
# 先行ホルダーを模擬: radio ロックを手動取得した状態で後続が待つこと
printf 'token=holder\npid=%s\nlabel=RADIO:holder\n' "$$" >"$LOCK_BASE/radio/owner"

(
	# 後続ジョブ: radio レーン取得を待ってから成功ファイルを作る
	sleep 0.2
	_ai_generation_queue_run "RADIO:follower:remote:codex:modelX" true
	echo done >"$TMP/follower_done"
) &
follower_pid=$!
sleep 1
check '[ ! -f "$TMP/follower_done" ]' 'radio ロック保持中は後続の放送系呼び出しが待機する'
_ai_generation_queue_leave holder "RADIO:holder"
wait "$follower_pid"
check '[ -f "$TMP/follower_done" ]' 'ロック解放後に後続の放送系呼び出しが完了する'

# --- 3. コメント返しのレーン化 ---
mkdir -p "$LOCK_BASE/comment"
printf 'token=holder\npid=%s\nlabel=COMMENT:holder\n' "$$" >"$LOCK_BASE/comment/owner"
(
	sleep 0.2
	_ai_generation_queue_run "COMMENT:follower:remote:codex:modelY" true
	echo done >"$TMP/comment_follower_done"
) &
comment_follower_pid=$!
sleep 1
check '[ ! -f "$TMP/comment_follower_done" ]' 'comment ロック保持中は後続のコメント返しが待機する'
_ai_generation_queue_leave holder "COMMENT:holder"
wait "$comment_follower_pid"
check '[ -f "$TMP/comment_follower_done" ]' 'ロック解放後にコメント返しの後続が完了する'

# --- 4. 改善ゲート ---
unset RADIO_GEN_STARTED_AT
rm -f "$IMPROVE_STATE_FILE"
started=$(_ai_radio_improve_gate "RADIO:test"); gate_rc=$?
check '[ "$gate_rc" -eq 0 ]' '改善stateが無ければゲートは即座に通過する'

# 稼働中 (running + 自分自身のPID = 生存) → 待機する
write_improve_state "running" "$$" "$(date +%s)"
(
	sleep 0.3
	write_improve_state "idle" 0 0
) &
flip_pid=$!
gate_start=$(date +%s)
_ai_radio_improve_gate "RADIO:test" >/dev/null 2>&1
gate_rc=$?
gate_elapsed=$(( $(date +%s) - gate_start ))
wait "$flip_pid"
check '[ "$gate_rc" -eq 0 ]' '改善完了後はゲートが通過する'
check '[ "$gate_elapsed" -ge 1 ]' '改善稼働中はゲートが実際に待機した'

# 改善開始前に始まった生成はキャンセルしない
write_improve_state "running" "$$" "$(date +%s)"
out=$( (export RADIO_GEN_STARTED_AT=$(( $(date +%s) - 60 )); _ai_radio_improve_gate "RADIO:test" 2>&1) )
gate_rc=$?
check '[ "$gate_rc" -eq 0 ]' '生成開始時刻が改善より前なら待機せず通過する (キャンセルしない)'
check 'printf %s "$out" | grep -q "proceed without cancel"' '通過理由がログに残る'
unset RADIO_GEN_STARTED_AT
rm -f "$IMPROVE_STATE_FILE"

# 死んだPIDのrunning stateは非活性扱い
write_improve_state "running" 999999999 0
_ai_radio_improve_gate "RADIO:test" >/dev/null 2>&1
check '[ "$?" -eq 0 ]' 'PIDが死んでいるrunning stateは非活性として即通過する'

# 待機上限超過で諦める
unset RADIO_GEN_STARTED_AT
write_improve_state "running" "$$" "$(date +%s)"
out=$( (export AI_RADIO_IMPROVE_WAIT_MAX_SEC=2; _ai_radio_improve_gate "RADIO:test" 2>&1) )
gate_rc=$?
check '[ "$gate_rc" -eq 1 ]' '待機上限超過時はその生成だけ諦める (rc=1)'
check 'printf %s "$out" | grep -q "give up"' '打ち切りがログに残る'
rm -f "$IMPROVE_STATE_FILE"

# ゲート無効化フラグ
write_improve_state "running" "$$" "$(date +%s)"
gate_bypass_rc=$( (export AI_RADIO_IMPROVE_GATE=0; _ai_radio_improve_gate "RADIO:test" >/dev/null 2>&1; echo $?) )
check '[ "$gate_bypass_rc" = "0" ]' 'AI_RADIO_IMPROVE_GATE=0 でゲートを無効化できる'
rm -f "$IMPROVE_STATE_FILE"

# --- 5. キュー無効化で全部バイパス ---
out5=$( (export AI_GENERATION_QUEUE_ENABLED=0; _ai_generation_queue_run "RADIO:bypass" printf bypassed) )
check '[ "$out5" = "bypassed" ]' 'キュー無効時はロックせず素通しする'

printf '\n%d ok, %d not ok\n' "$ok" "$fail"
[ "$fail" -eq 0 ]

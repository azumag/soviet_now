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

# --- 2. dead owner の generation lock は即時回収 ---
rm -rf "$LOCK_BASE"
mkdir -p "$LOCK_BASE/radio"
printf 'token=dead-holder\npid=999999999\nlabel=RADIO:dead-holder\n' >"$LOCK_BASE/radio/owner"
(
	_ai_generation_queue_run "RADIO:after-dead-owner" true
	echo done >"$TMP/dead_owner_done"
) &
dead_follower_pid=$!
sleep 2
check '[ -f "$TMP/dead_owner_done" ]' 'owner PIDが消滅したfresh generation lockはstale期限を待たず回収する'
if kill -0 "$dead_follower_pid" 2>/dev/null; then
	kill "$dead_follower_pid" 2>/dev/null || true
fi
wait "$dead_follower_pid" 2>/dev/null || true
rm -rf "$LOCK_BASE"

# --- 2b. dead-owner 回収は複数 follower でも新ownerを削除しない ---
# follower B を dead-owner 観測後・削除直前で止め、A が旧lockを回収して
# critical sectionへ入った後に B を再開する。check-then-delete 実装だと
# B が A の新lockまで削除し、critical section が重複する。
rm -rf "$LOCK_BASE"
mkdir -p "$LOCK_BASE/radio"
printf 'token=dead-holder\npid=999999999\nlabel=RADIO:dead-holder\n' >"$LOCK_BASE/radio/owner"
rm() {
	local last="${!#}"
	if [[ "$last" == */.ai_generation_locks/radio || "$last" = "tmp/state/.ai_generation_locks/radio" ]] && [ "${RACE_ROLE:-}" = "B" ] && [ ! -f "$TMP/release_b_rm" ]; then
		: >"$TMP/b_waiting_to_rm"
		while [ ! -f "$TMP/release_b_rm" ]; do sleep 0.02; done
	fi
	command rm "$@"
}
race_critical() {
	local role="$1"
	if ! mkdir "$TMP/critical_active" 2>/dev/null; then
		: >"$TMP/critical_overlap"
	else
		printf '%s\n' "$role" >"$TMP/critical_active/owner"
	fi
	sleep 1
	if [ -d "$TMP/critical_active" ] && [ "$(cat "$TMP/critical_active/owner" 2>/dev/null)" = "$role" ]; then
		command rm -rf "$TMP/critical_active"
	fi
}
(
	export RACE_ROLE=B
	_ai_generation_queue_run "RADIO:race-b" race_critical B
) &
race_b_pid=$!
for _ in $(seq 1 100); do
	[ -f "$TMP/b_waiting_to_rm" ] && break
	sleep 0.02
done
check '[ -f "$TMP/b_waiting_to_rm" ]' 'race follower B が dead-owner 削除直前まで到達する'
(
	export RACE_ROLE=A
	_ai_generation_queue_run "RADIO:race-a" race_critical A
) &
race_a_pid=$!
sleep 0.3
: >"$TMP/release_b_rm"
wait "$race_a_pid" 2>/dev/null || true
wait "$race_b_pid" 2>/dev/null || true
check '[ ! -f "$TMP/critical_overlap" ]' 'dead-owner回収中も generation critical section は重複しない'
unset -f rm
rm -rf "$LOCK_BASE"

# --- 3. 放送系の直列化 (異なるモデルでも待ち合わせる) ---
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
check '[ "$gate_rc" -eq "$AI_GATE_GIVEUP_RC" ]' '待機上限超過時は専用コード AI_GATE_GIVEUP_RC で諦める'
check 'printf %s "$out" | grep -q "give up"' '打ち切りがログに残る'
rm -f "$IMPROVE_STATE_FILE"

# ゲート無効化フラグ
write_improve_state "running" "$$" "$(date +%s)"
gate_bypass_rc=$( (export AI_RADIO_IMPROVE_GATE=0; _ai_radio_improve_gate "RADIO:test" >/dev/null 2>&1; echo $?) )
check '[ "$gate_bypass_rc" = "0" ]' 'AI_RADIO_IMPROVE_GATE=0 でゲートを無効化できる'
rm -f "$IMPROVE_STATE_FILE"

# dispatch スコープの二重待機防止フラグ
write_improve_state "running" "$$" "$(date +%s)"
flag_pass_rc=$( (export AI_GATE_PASSED_FOR_DISPATCH=1; _ai_radio_improve_gate "RADIO:test" >/dev/null 2>&1; echo $?) )
check '[ "$flag_pass_rc" = "0" ]' 'AI_GATE_PASSED_FOR_DISPATCH=1 なら改善稼働中でも即通過する (二重待機防止)'
rm -f "$IMPROVE_STATE_FILE"

# --- 4b. ゲート打ち切りは attempt/fail 統計に計上されない (gate_giveup 分離) ---
if command -v timeout >/dev/null 2>&1; then
	unset RADIO_GEN_STARTED_AT
	mkdir -p "$TMP/bin"
	# codex スタブ: -o <file> へ出力する (実 CLI と同じ契約)
	cat >"$TMP/bin/codex" <<'EOF'
#!/bin/sh
out=/dev/stdout
while [ $# -gt 0 ]; do
	case "$1" in
	-o) shift; out="$1" ;;
	esac
	shift
done
printf STUBBED > "$out"
EOF
	chmod +x "$TMP/bin/codex"
	printf 'テスト用プロンプトです。\n' >"$WORK/prompt.txt"
	# 改善稼働中 → 打ち切り。attempt/fail を記録せず gate_giveup のみ。
	write_improve_state "running" "$$" "$(date +%s)"
	(
		cd "$WORK" || exit 1
		PATH="$TMP/bin:$PATH"
		export PATH
		export AI_RADIO_IMPROVE_WAIT_MAX_SEC=2 AI_GENERATION_QUEUE_WAIT_SEC=1
		_ai_dispatch "RADIO:test:gated" "codex:stub-model" "$WORK/prompt.txt" >/dev/null 2>&1
	)
	giveup_dispatch_rc=$?
	check '[ "$giveup_dispatch_rc" -eq "$AI_GATE_GIVEUP_RC" ]' 'ゲート打ち切り時の _ai_dispatch は AI_GATE_GIVEUP_RC を返す'
	stats_file=$(ls "$TMP/ai_stats/"*.jsonl 2>/dev/null | head -1)
	if [ -n "$stats_file" ]; then
		check 'grep -q "\"event\":\"gate_giveup\"" "$stats_file"' '打ち切りは gate_giveup イベントとして記録される'
		check '! grep -q "\"event\":\"attempt\"" "$stats_file"' '打ち切り時に attempt を記録しない'
		check '! grep -q "\"event\":\"fail\"" "$stats_file"' '打ち切り時に fail を記録しない'
	else
		check 'false' '統計ファイルが作成されている'
	fi
	rm -f "$stats_file"

	# ai_generate_list でもゲート打ち切りはモデル非依存のため即時伝播し、
	# fallback候補の失敗streak/backoffを汚染しない。
	rm -f "$AI_BACKOFF_DIR/codex_gate-a" "$AI_BACKOFF_DIR/codex_gate-b"
	rm -f "$AI_FAIL_STREAK_DIR/codex_gate-a" "$AI_FAIL_STREAK_DIR/codex_gate-b"
	write_improve_state "running" "$$" "$(date +%s)"
	(
		cd "$WORK" || exit 1
		export AI_RADIO_IMPROVE_WAIT_MAX_SEC=1 AI_GENERATION_QUEUE_WAIT_SEC=1
		ai_generate_list "RADIO:test:list-gated" "$WORK/prompt.txt" "codex:gate-a,codex:gate-b" >/dev/null 2>&1
	)
	giveup_list_rc=$?
	check '[ "$giveup_list_rc" -eq "$AI_GATE_GIVEUP_RC" ]' 'ai_generate_list はゲート打ち切り専用コードをそのまま返す'
	check '[ ! -e "$AI_BACKOFF_DIR/codex_gate-a" ] && [ ! -e "$AI_BACKOFF_DIR/codex_gate-b" ]' 'ゲート打ち切りで候補モデルにbackoffを設定しない'
	check '[ ! -e "$AI_FAIL_STREAK_DIR/codex_gate-a" ] && [ ! -e "$AI_FAIL_STREAK_DIR/codex_gate-b" ]' 'ゲート打ち切りで候補モデルの失敗streakを増やさない'
	rm -f "$IMPROVE_STATE_FILE"

	# 改善なし → 通常どおり attempt/ok を記録する
	rm -f "$IMPROVE_STATE_FILE"
	stub_out=$(
		cd "$WORK" || exit 1
		PATH="$TMP/bin:$PATH"
		export PATH
		_ai_dispatch "RADIO:test:normal" "codex:stub-model" "$WORK/prompt.txt" 2>/dev/null
	)
	stub_rc=$?
	check '[ "$stub_rc" -eq 0 ]' '改善が無ければゲートを通過してモデル呼び出しまで進む'
	check '[ "$stub_out" = "STUBBED" ]' 'スタブ出力が透過する'
	stats_file=$(ls "$TMP/ai_stats/"*.jsonl 2>/dev/null | head -1)
	if [ -n "$stats_file" ]; then
		check 'grep -q "\"event\":\"attempt\"" "$stats_file"' '通常呼び出しは attempt を記録する'
		check 'grep -q "\"event\":\"ok\"" "$stats_file"' '通常呼び出しは ok を記録する'
	else
		check 'false' '通常呼び出しでも統計ファイルが作成される'
	fi
else
	printf 'skip - timeout コマンド不在のため gate_giveup 分離のe2e検証をスキップ\n'
fi

# --- 5. 生成キュー待ち上限 ---
rm -f "$IMPROVE_STATE_FILE"
rm -rf "$LOCK_BASE/radio"
mkdir -p "$LOCK_BASE/radio"
printf 'token=busy-holder\npid=%s\nlabel=RADIO:busy-holder\n' "$$" >"$LOCK_BASE/radio/owner"
rm -f "$TMP/queue_callback_ran"
queue_out=$(
	(
		export AI_GENERATION_QUEUE_MAX_WAIT_SEC=1 AI_GENERATION_QUEUE_WAIT_SEC=1
		_ai_generation_queue_run "NEWS:spam_check:remote:local:gemma4:12b" touch "$TMP/queue_callback_ran"
	) 2>&1
)
queue_rc=$?
check '[ "$queue_rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' '生成レーン混雑は専用コード AI_QUEUE_GIVEUP_RC で打ち切れる'
check '[ ! -e "$TMP/queue_callback_ran" ]' 'キュー打ち切り時はprovider本体を呼ばない'
check 'printf %s "$queue_out" | grep -q "generation slot wait exceeded 1s"' 'キュー待ち上限がログに残る'
check '[ -d "$LOCK_BASE/radio" ] && grep -q "token=busy-holder" "$LOCK_BASE/radio/owner"' 'キュー打ち切りで既存ownerを破壊しない'

# _ai_dispatch / ai_generate は queue_giveup をprovider失敗に変換せず、
# 同じ共有レーンへfallbackして待ち直すこともない。
printf 'queue timeout test\n' >"$WORK/queue_prompt.txt"
rm -f "$AI_STATS_DIR/"*.jsonl 2>/dev/null || true
queue_ai_out=$(
	(
		cd "$WORK" || exit 1
		export AI_GENERATION_QUEUE_MAX_WAIT_SEC=1 AI_GENERATION_QUEUE_WAIT_SEC=1
		ai_generate "NEWS:spam_check" "$WORK/queue_prompt.txt" "codex:queue-primary" "codex:queue-fallback" 1
	) 2>/dev/null
)
queue_ai_rc=$?
check '[ "$queue_ai_rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' 'ai_generate はqueue_giveupを即時伝播する'
stats_file=$(ls "$AI_STATS_DIR/"*.jsonl 2>/dev/null | head -1)
if [ -n "$stats_file" ]; then
	check 'grep -q "\"event\":\"queue_giveup\"" "$stats_file"' 'キュー打ち切りを queue_giveup として記録する'
	check '! grep -q "\"event\":\"fail\"" "$stats_file"' 'キュー打ち切りをprovider failとして記録しない'
	check '! grep -q "queue-fallback" "$stats_file"' '共有レーン混雑時はfallback候補へ待ち直さない'
else
	check 'false' 'キュー打ち切り統計ファイルが作成される'
fi

rm -f "$AI_BACKOFF_DIR/codex_queue-a" "$AI_BACKOFF_DIR/codex_queue-b"
rm -f "$AI_FAIL_STREAK_DIR/codex_queue-a" "$AI_FAIL_STREAK_DIR/codex_queue-b"
(
	cd "$WORK" || exit 1
	export AI_GENERATION_QUEUE_MAX_WAIT_SEC=1 AI_GENERATION_QUEUE_WAIT_SEC=1
	ai_generate_list "RADIO:test:list-queue" "$WORK/queue_prompt.txt" "codex:queue-a,codex:queue-b" >/dev/null 2>&1
)
queue_list_rc=$?
check '[ "$queue_list_rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' 'ai_generate_list もqueue_giveupを即時伝播する'
check '[ ! -e "$AI_BACKOFF_DIR/codex_queue-a" ] && [ ! -e "$AI_BACKOFF_DIR/codex_queue-b" ]' 'キュー打ち切りで候補モデルにbackoffを設定しない'
check '[ ! -e "$AI_FAIL_STREAK_DIR/codex_queue-a" ] && [ ! -e "$AI_FAIL_STREAK_DIR/codex_queue-b" ]' 'キュー打ち切りで候補モデルの失敗streakを増やさない'

# primary が通常失敗した後、fallback 側で queue_giveup した場合も専用RCを保持する。
# ここを generic rc=1 に潰すと caller が provider failure と queue congestion を区別できない。
queue_fallback_rc=$(
	(
		_ai_dispatch() {
			if [ "$2" = "codex:primary-fail" ]; then
				return 1
			fi
			return "$AI_QUEUE_GIVEUP_RC"
		}
		ai_generate "NEWS:spam_check" "$WORK/queue_prompt.txt" "codex:primary-fail" "codex:fallback-queue" >/dev/null 2>&1
		printf '%s' "$?"
	)
)
check '[ "$queue_fallback_rc" -eq "$AI_QUEUE_GIVEUP_RC" ]' 'fallback側のqueue_giveupもai_generateが専用コードで伝播する'

_ai_generation_queue_leave busy-holder "RADIO:busy-holder"

# --- 6. キュー無効化で全部バイパス ---
out5=$( (export AI_GENERATION_QUEUE_ENABLED=0; _ai_generation_queue_run "RADIO:bypass" printf bypassed) )
check '[ "$out5" = "bypassed" ]' 'キュー無効時はロックせず素通しする'

printf '\n%d ok, %d not ok\n' "$ok" "$fail"
[ "$fail" -eq 0 ]

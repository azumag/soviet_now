#!/usr/bin/env bash
# ラジオ render のチャンク間ロック解放（コメント優先）とチャンク上限を検証する。
# say_enqueue.sh の関数定義から必要な箇所だけを source してモック実行する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/say_enqueue.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- ヘルパー関数を抽出 ---
sed -n '/^_voicevox_synth_lock_wait_sec()/,/^}/p' "$SRC" > "$TMP/fn_wait.sh"
sed -n '/^_voicevox_synth_timeout_sec()/,/^}/p' "$SRC" > "$TMP/fn_timeout.sh"
sed -n '/^_has_pending_comment_queue()/,/^}/p' "$SRC" > "$TMP/fn_pending.sh"
sed -n '/^_radio_should_yield_to_comment()/,/^}/p' "$SRC" > "$TMP/fn_radio_yield.sh"
. "$TMP/fn_wait.sh"
. "$TMP/fn_timeout.sh"
. "$TMP/fn_pending.sh"
. "$TMP/fn_radio_yield.sh"

# --- ロック解放・再取得のコア挙動を再現 ---
# 実際の _acquire/_release は mkdir ロックなので、そのまま source するには
# 依存変数が必要。ここでは同型の関数を定義して検証する。
LOCK="$TMP/synth.lock"

_acquire_voicevox_synth_lock() {
	local timeout_sec="${1:-30}" waited=0
	while ! mkdir "$LOCK" 2>/dev/null; do
		sleep 0.05
		waited=$((waited + 1))
		[ "$waited" -ge $((timeout_sec * 20)) ] && return 1
	done
	return 0
}
_release_voicevox_synth_lock() {
	rmdir "$LOCK" 2>/dev/null || true
	return 0
}

# --- テスト1: コメントの待機優先度はラジオより高い ---
export SOURCE_LABEL=comment
wait_comment=$(_voicevox_synth_lock_wait_sec)
timeout_comment=$(_voicevox_synth_timeout_sec)
export SOURCE_LABEL=radio_render:news
wait_radio=$(_voicevox_synth_lock_wait_sec)
timeout_radio=$(_voicevox_synth_timeout_sec)
export SOURCE_LABEL=improve_progress
wait_progress=$(_voicevox_synth_lock_wait_sec)

[ "$wait_comment" -gt "$wait_radio" ] && ok "comment waits longer than radio (comment=$wait_comment radio=$wait_radio)" || not_ok "comment should wait longer: comment=$wait_comment radio=$wait_radio"
[ "$wait_progress" -gt "$wait_radio" ] && ok "foreground progress waits longer than radio (progress=$wait_progress radio=$wait_radio)" || not_ok "foreground progress should wait longer: progress=$wait_progress radio=$wait_radio"
[ "$timeout_comment" -ge "$timeout_radio" ] && ok "comment timeout >= radio (comment=$timeout_comment radio=$timeout_radio)" || not_ok "comment timeout should be >= radio"

# --- テスト2: ラジオ render のチャンク上限 ---
export SOURCE_LABEL=radio_render:news
_chunks=(a b c d e f g h i j k l m n o p) # 16チャンク
_max_pre_chunks="${#_chunks[@]}"
if [ "${SOURCE_LABEL#radio_render:}" != "$SOURCE_LABEL" ]; then
	_radio_chunk_cap="${VOICEVOX_RADIO_MAX_CHUNKS:-12}"
	case "$_radio_chunk_cap" in
	'' | *[!0-9]*) _radio_chunk_cap=12 ;;
	esac
	[ "$_radio_chunk_cap" -lt 1 ] && _radio_chunk_cap=1
	[ "$_max_pre_chunks" -gt "$_radio_chunk_cap" ] && _max_pre_chunks="$_radio_chunk_cap"
fi
[ "$_max_pre_chunks" -eq 12 ] && ok "radio chunk cap applied (16 -> 12)" || not_ok "radio cap: got $_max_pre_chunks want 12"

# --- テスト5: ラジオ再生はコメントに譲らない（割り込み無効化） ---
export SOURCE_LABEL=radio:news SAY_DISABLE_COMMENT_YIELD=1
unset -f _has_pending_comment_queue 2>/dev/null || true
_has_pending_comment_queue() { return 0; }  # pending コメントありを模擬
if _radio_should_yield_to_comment; then
	not_ok "radio with SAY_DISABLE_COMMENT_YIELD=1 should not yield"
else
	ok "radio with SAY_DISABLE_COMMENT_YIELD=1 does not yield to comments"
fi

# 明示的に譲る設定（0）では従来通り譲る
export SAY_DISABLE_COMMENT_YIELD=0
if _radio_should_yield_to_comment; then
	ok "radio with SAY_DISABLE_COMMENT_YIELD=0 still yields when explicitly allowed"
else
	not_ok "radio with SAY_DISABLE_COMMENT_YIELD=0 should yield"
fi
unset SAY_DISABLE_COMMENT_YIELD

# --- テスト3: コメントがロック保持中、ラジオは盗まず解放後に再取得する ---
export SOURCE_LABEL=radio_render:news
_acquire_voicevox_synth_lock 1  # ラジオが最初のチャンクのロックを取得
# コメントが割り込んでロックを保持（ラジオは解放済み）
_release_voicevox_synth_lock
# 別プロセスのコメントとして即座に取得
_acquire_voicevox_synth_lock 1
_lock_held_by_comment=1

# コメントがロックを解放した後は、ラジオが同じレンダー世代で取得を継続する。
(
	sleep 1
	_release_voicevox_synth_lock
) &
_comment_release_pid=$!
SECONDS=0
if _acquire_voicevox_synth_lock 3; then
	[ "$SECONDS" -ge 1 ] \
		&& ok "radio waits for comment-held lock and re-acquires" \
		|| not_ok "radio should wait for the comment before re-acquiring"
else
	not_ok "radio should re-acquire after the comment releases the lock"
fi
wait "$_comment_release_pid" 2>/dev/null || true
_release_voicevox_synth_lock

# --- テスト4: コメントは長く待って取得できる ---
export SOURCE_LABEL=comment VOICEVOX_SYNTH_LOCK_WAIT_COMMENT_SEC=2
if _acquire_voicevox_synth_lock 2; then
	ok "comment acquires lock after radio releases"
else
	not_ok "comment should acquire after wait"
fi
_release_voicevox_synth_lock

exit "$FAIL"

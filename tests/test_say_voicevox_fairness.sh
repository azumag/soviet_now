#!/usr/bin/env bash
# 背景ラジオ合成が、待機中の前景音声より先にロックを取り直さないことを検証する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/say_enqueue.sh"
TMP="$(mktemp -d)"
trap 'jobs -p | xargs kill 2>/dev/null || true; rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

for fn in \
	_file_mtime_epoch \
	_is_voicevox_synth_lock_owner \
	_release_voicevox_synth_lock \
	_voicevox_synth_is_background_render \
	_voicevox_gc_priority_waiters \
	_voicevox_priority_waiter_exists \
	_register_voicevox_priority_waiter \
	_touch_voicevox_priority_waiter \
	_unregister_voicevox_priority_waiter \
	_acquire_voicevox_synth_lock; do
	sed -n "/^${fn}()/,/^}/p" "$SRC" >>"$TMP/functions.sh"
done
. "$TMP/functions.sh"

QUEUE_DIR="$TMP/queue"
mkdir -p "$QUEUE_DIR"
VOICEVOX_SYNTH_LOCK="$QUEUE_DIR/.voicevox_synth_lock"
VOICEVOX_SYNTH_OWNER_FILE="$VOICEVOX_SYNTH_LOCK/owner_pid"
VOICEVOX_SYNTH_HEARTBEAT_FILE="$VOICEVOX_SYNTH_LOCK/heartbeat"
VOICEVOX_SYNTH_LOCK_STALE_SEC=180
VOICEVOX_SYNTH_LOCK_HELD=0
VOICEVOX_SYNTH_PRIORITY_WAIT_DIR="$QUEUE_DIR/.voicevox_synth_priority_waiters"
VOICEVOX_SYNTH_PRIORITY_WAIT_FILE=""
VOICEVOX_SYNTH_PRIORITY_WAIT_HELD=0
VOICEVOX_SYNTH_PRIORITY_WAIT_STALE_SEC=1
MY_TOKEN="test_${$}"
MY_OWNER="${$}:${MY_TOKEN}"
VOICEVOX_SYNTH_LOCK_BUSY_REASON=""
_log() { :; }

# 生きている前景 waiter がいる間、背景ラジオは即座に順番を譲る。
mkdir -p "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR"
sleep 10 &
waiter_pid=$!
printf '%s %s\n' "$waiter_pid" "$(date +%s)" >"$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR/foreground.wait"
SOURCE_LABEL=radio_render:news
SECONDS=0
if _acquire_voicevox_synth_lock 2; then
	not_ok "background radio should not acquire ahead of foreground waiter"
	_release_voicevox_synth_lock
else
	[ "$VOICEVOX_SYNTH_LOCK_BUSY_REASON" = "priority_waiter" ] \
		&& ok "background radio reports priority waiter" \
		|| not_ok "background radio should report priority_waiter"
	[ "$SECONDS" -lt 2 ] \
		&& ok "background radio yields without busy retry loop" \
		|| not_ok "background radio yield took too long"
fi
kill "$waiter_pid" 2>/dev/null || true
wait "$waiter_pid" 2>/dev/null || true
rm -f "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR/foreground.wait"
rmdir "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR" 2>/dev/null || true

# waiter が消えれば背景ラジオも通常どおり取得できる。
if _acquire_voicevox_synth_lock 1; then
	ok "background radio acquires after foreground waiter clears"
	_release_voicevox_synth_lock
else
	not_ok "background radio should acquire after waiter clears"
fi

# 前景音声は待機中だけ予約を公開し、取得後に確実に消す。
SOURCE_LABEL=improve_progress
_register_voicevox_priority_waiter
if [ -s "$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE" ]; then
	ok "foreground audio publishes priority waiter"
else
	not_ok "foreground audio should publish priority waiter"
fi
saved_wait_file="$VOICEVOX_SYNTH_PRIORITY_WAIT_FILE"
_unregister_voicevox_priority_waiter
if [ ! -e "$saved_wait_file" ]; then
	ok "foreground waiter is removed after acquisition/cancel"
else
	not_ok "foreground waiter should be removed"
fi

# 死んだ所有者の古い予約は回収し、ラジオを永久停止させない。
mkdir -p "$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR"
printf '999999 %s\n' "$(($(date +%s) - 10))" >"$VOICEVOX_SYNTH_PRIORITY_WAIT_DIR/stale.wait"
if _voicevox_priority_waiter_exists; then
	not_ok "stale foreground waiter should be collected"
else
	ok "stale foreground waiter is collected"
fi

exit "$FAIL"

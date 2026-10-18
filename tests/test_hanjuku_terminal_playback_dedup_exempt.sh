#!/usr/bin/env bash
# Regression for docich's per-run Hanjuku terminal receipt: identical recap
# text from different finished runs is a new event, while ordinary comments
# remain subject to the existing content-hash duplicate guard.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

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

cd "$TMP" || exit 1
export ELOOP_LIB_DIR="$ROOT"
export COMMENT_QUEUE_DIR="$TMP/comment_queue"
export COMMENT_VIEWER_MEMORY_ENABLED=0
export COMMENT_SPOKEN_HISTORY_DIR="$TMP/spoken_history"
export COMMENT_SPOKEN_HISTORY_MAX_FILES=10
mkdir -p "$COMMENT_QUEUE_DIR" "tmp/.say_queue" "tmp/.comment_queue"
_cp_my_pid="test"
RADIO_SAY_RATE=""

log() { :; }
_clean_comment_talk() { cat; }
_sanitize_onair_text() { cat; }
_broadcast_read_expected_mode() { :; }
_broadcast_host_mode() { printf '%s' main; }
_broadcast_clear_expected_mode() { :; }
_play_deferred_radio_queue_once() { :; }

cat >say_enqueue.sh <<'SH'
#!/usr/bin/env bash
exit 0
SH
chmod +x say_enqueue.sh

source "$ROOT/broadcast/comment.sh"
source "$ROOT/broadcast/comment_lib.sh"

check '[ "$(_comment_playback_context_label "x/comment_announce_1_ab_hanjuku_terminal.txt")" = "hanjuku_terminal" ]' \
	'hanjuku_terminal queue file has its own source label'
check '[ "$(_comment_playback_context_label "x/comment_announce_1_ab_hanjuku_terminal.playing")" = "hanjuku_terminal" ]' \
	'the source label remains explicit after claim'
check '[ "$(_comment_playback_overlay_title hanjuku_terminal)" = "半熟英雄終了結果 playback" ]' \
	'the playback overlay names the Hanjuku terminal result'

same_text='半熟英雄の終了時点の記録を振り返ります。'
printf '%s\n' "$same_text" >"$COMMENT_QUEUE_DIR/comment_announce_1_aaa_hanjuku_terminal.txt"
_play_comment_queue
printf '%s\n' "$same_text" >"$COMMENT_QUEUE_DIR/comment_announce_2_bbb_hanjuku_terminal.txt"
_play_comment_queue

check '! grep -q "重複スキップ" tmp/.say_queue/debug.log 2>/dev/null' \
	'separate terminal receipts play even when recap text is identical'
check '[ "$(grep -c "再生開始" tmp/.say_queue/debug.log 2>/dev/null)" -eq 2 ]' \
	'both terminal events reach the consumer playback path'
check '[ -z "$(find "$COMMENT_QUEUE_DIR" -maxdepth 1 -name "*hanjuku_terminal*" 2>/dev/null)" ]' \
	'both terminal queue files are consumed'

: >tmp/.say_queue/debug.log
printf '%s\n' "同じ内容のコメント返信テストです。" >"$COMMENT_QUEUE_DIR/comment_ordinary_1.txt"
_play_comment_queue
printf '%s\n' "同じ内容のコメント返信テストです。" >"$COMMENT_QUEUE_DIR/comment_ordinary_2.txt"
_play_comment_queue
check 'grep -q "重複スキップ" tmp/.say_queue/debug.log 2>/dev/null' \
	'ordinary comments still use the existing content-hash dedupe'

printf '%s passed, %s failed\n' "$ok" "$fail"
[ "$fail" -eq 0 ]

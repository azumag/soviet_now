#!/bin/bash
# soviet_now#144: outbound chat delivery seam の characterization test。
# `enqueue_chat_message` → pending/*.msg → `outbound_queue_consume_once`
# (claim→send→sent/pending) の現行動作を固定する。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

export ELOOP_LIB_DIR="$ROOT"
export OUTBOUND_CHAT_QUEUE_DIR="$TMP/q"
export OUTBOUND_CHAT_PAUSE_MARKER="$TMP/chat_worker.paused"
export TMP_DEBUG_DIR="$TMP/debug"

source lib/outbound_queue.sh
log() { :; }

# 送信 stub: ./twitch_chat.sh send は cwd から呼ばれる。挙動は $STUB_MODE で制御。
mkdir -p "$TMP/run" && cd "$TMP/run"
cat >twitch_chat.sh <<'EOF'
#!/bin/bash
printf '%s\n' "$1" >>"${STUB_LOG:-/dev/null}"
case "${STUB_MODE:-ok}" in
ok) exit 0 ;;
fail) echo "temporary send failure" >&2; exit 1 ;;
auth) echo "Invalid OAuth token" >&2; exit 1 ;;
*) exit 0 ;;
esac
EOF
chmod +x twitch_chat.sh
export STUB_LOG="$TMP/sent.log"
touch "$STUB_LOG"

# ==== enqueue: ファイル名形式・内容・atomic ====
enqueue_chat_message "テスト投稿一" twitch 3 runkey1
f=$(ls "$OUTBOUND_CHAT_PENDING_DIR" | head -1)
case "$f" in
*_twitch_3.msg) pass 'enqueue filename {ts}_{source}_{priority}.msg' ;;
*) not_ok "enqueue filename=$f" ;;
esac
[ "$(cat "$OUTBOUND_CHAT_PENDING_DIR/$f")" = "テスト投稿一" ] && pass 'msg file holds message text' || not_ok 'msg content mismatch'
ls "$OUTBOUND_CHAT_PENDING_DIR" | grep -q '^\.' && not_ok 'tmp file leaked into pending' || pass 'no .tmp residue in pending'

# ==== pause marker: キューに積まない no-op ====
touch "$OUTBOUND_CHAT_PAUSE_MARKER"
n_before=$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')
enqueue_chat_message "一時停止中" twitch 3
n_after=$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')
[ "$n_after" = "$n_before" ] && pass 'paused enqueue is no-op (not queued)' || not_ok 'paused enqueue queued'
rm -f "$OUTBOUND_CHAT_PAUSE_MARKER"

# ==== dedup claim: 同一 delivery_key + message は二重積みしない ====
enqueue_chat_message "テスト投稿一" twitch 3 runkey1
n=$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')
[ "$n" = "1" ] && pass 'same delivery_key+message deduped' || not_ok "dup enqueued (n=$n)"
# delivery_key 無し・同一文は別扱い（key 有りとの差分）
enqueue_chat_message "テスト投稿一" twitch 3
n=$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')
[ "$n" = "2" ] && pass 'no delivery_key enqueues separately' || not_ok "unexpected dedup (n=$n)"

# ==== consume: 成功 → sent/ へ移動、twitch_chat.sh send が本文を受け取る ====
export STUB_MODE=ok
# 古い順に処理: 先に積んだ runkey1 付きが先 (fifo)
outbound_queue_consume_once && pass 'consume_once success rc=0' || not_ok 'consume_once rc!=0'
[ "$(ls "$OUTBOUND_CHAT_SENT_DIR" | wc -l | tr -d ' ')" = "1" ] && pass 'sent file moved to sent/' || not_ok 'sent/ empty'
[ "$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')" = "1" ] && pass 'pending shrank by one' || not_ok 'pending count wrong'
[ "$(wc -l <"$STUB_LOG" | tr -d ' ')" = "1" ] && pass 'send received message' || not_ok 'send not invoked'

# ==== consume 失敗: pending に戻る（次回リトライ） ====
export STUB_MODE=fail
outbound_queue_consume_once && not_ok 'failing consume returned 0' || pass 'consume failure rc=1'
[ "$(ls "$OUTBOUND_CHAT_PENDING_DIR" | wc -l | tr -d ' ')" = "1" ] && pass 'failed msg restored to pending' || not_ok 'failed msg lost'
[ "$(ls "$OUTBOUND_CHAT_PROCESSING_DIR" | wc -l | tr -d ' ')" = "0" ] && pass 'processing/ left empty after failure' || not_ok 'processing residue'
grep -q "twitch send failed" "$TMP_DEBUG_DIR/outbound_chat_twitch.log" 2>/dev/null && pass 'failure logged' || not_ok 'failure not logged'

# ==== auth 失敗: backoff ファイルが立ち consume は拒否される ====
export STUB_MODE=auth
outbound_queue_consume_once || true
[ -f "$OUTBOUND_CHAT_QUEUE_DIR/twitch_backoff_until" ] && pass 'auth failure sets backoff' || not_ok 'no backoff file'
outbound_queue_consume_once && not_ok 'consume ran during backoff' || pass 'consume suppressed during backoff'
# backoff を手動解除すれば再試行できる
rm -f "$OUTBOUND_CHAT_QUEUE_DIR/twitch_backoff_until"
export STUB_MODE=ok
outbound_queue_consume_once && pass 'consume resumes after backoff cleared' || not_ok 'consume still blocked'

# ==== 空キュー: rc=1 ====
export STUB_MODE=ok
outbound_queue_consume_once || true  # 残り1件を消費
outbound_queue_consume_once && not_ok 'empty queue returned 0' || pass 'empty queue rc=1'

echo "== $ok passed, $fail failed =="
[ "$fail" = "0" ]

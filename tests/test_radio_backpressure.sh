#!/usr/bin/env bash
# issue #5: deferred radio queue 滞留時（> 5 件）の新規ラジオ生成抑止を検証する。
# 実コードをそのまま抽出して source する（radio_state.sh のカウント関数と
# scheduler.sh の抑止判定の実装をテスト対象にする）。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
SCHED_SRC="$ROOT/broadcast/scheduler.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_radio_deferred_queue_count()/,/^}/p' "$STATE_SRC" > "$TMP/fn_count.sh"
sed -n '/^_radio_generation_blocked_by_backpressure()/,/^}/p' "$SCHED_SRC" > "$TMP/fn_blocked.sh"
. "$TMP/fn_count.sh"

TMP_MARKERS_DIR="$TMP/markers"
RADIO_DEFERRED_QUEUE_DIR="$TMP/queue"
mkdir -p "$TMP_MARKERS_DIR"

log() { printf 'LOG: %s\n' "$*" >> "$TMP/log.txt"; }
. "$TMP/fn_blocked.sh"

mkqueue() {
	local n="$1"
	mkdir -p "$RADIO_DEFERRED_QUEUE_DIR"
	rm -f "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.txt "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.*
	local i
	for i in $(seq 1 "$n"); do
		: > "$RADIO_DEFERRED_QUEUE_DIR/radio_${i}_1_theme_${i}.txt"
	done
}

# --- カウント検証 ---
mkqueue 3
count=$(_radio_deferred_queue_count)
[ "$count" = "3" ] && ok "count: 3 text files" || not_ok "count: 3 text files (got $count)"

# サイドカーは本文として数えない
: > "$RADIO_DEFERRED_QUEUE_DIR/radio_9_9_news_9.txt.mode"
: > "$RADIO_DEFERRED_QUEUE_DIR/radio_9_9_news_9.ready.wav"
: > "$RADIO_DEFERRED_QUEUE_DIR/radio_9_9_news_9.history"
count=$(_radio_deferred_queue_count)
[ "$count" = "3" ] && ok "count: sidecars ignored" || not_ok "count: sidecars ignored (got $count)"

rm -f "$RADIO_DEFERRED_QUEUE_DIR"/radio_9_9_news_9.*

# --- 抑止検証: 6 件（MAX=5 を超過）→ ブロック + マーカー作成 + ログ 1 回 ---
mkqueue 6
rm -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" "$TMP/log.txt"
if _radio_generation_blocked_by_backpressure; then
	ok "case1: blocked at 6 items"
else
	not_ok "case1: blocked at 6 items"
fi
if [ -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" ]; then
	ok "case1: backpressure marker created"
else
	not_ok "case1: backpressure marker created"
fi
if [ "$(grep -c '抑制' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ]; then
	ok "case1: suppression logged once"
else
	not_ok "case1: suppression logged once"
fi

# --- 抑止継続: マーカーがあるまま再度呼んでもログは増えない ---
rm -f "$TMP/log.txt"
_radio_generation_blocked_by_backpressure
if [ ! -s "$TMP/log.txt" ]; then
	ok "case1b: no repeated log while staying blocked"
else
	not_ok "case1b: no repeated log while staying blocked"
fi

# --- 再開検証: 5 件以下に戻ると解除 + マーカー削除 + 再開ログ 1 回 ---
mkqueue 5
rm -f "$TMP/log.txt"
if _radio_generation_blocked_by_backpressure; then
	not_ok "case2: allowed at 5 items"
else
	ok "case2: allowed at 5 items"
fi
if [ ! -f "$TMP_MARKERS_DIR/.radio_queue_backpressure_active" ]; then
	ok "case2: marker cleared"
else
	not_ok "case2: marker cleared"
fi
if [ "$(grep -c '再開' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ]; then
	ok "case2: resume logged once"
else
	not_ok "case2: resume logged once"
fi

# --- 通常時: 0 件でマーカーが無ければ通知なしで許可 ---
mkqueue 0
rm -f "$TMP/MARKERS_DIR/.radio_queue_backpressure_active" "$TMP/log.txt"
if _radio_generation_blocked_by_backpressure; then
	not_ok "case3: allowed at 0 items"
else
	ok "case3: allowed at 0 items"
fi
if [ ! -s "$TMP/log.txt" ]; then
	ok "case3: no log when normal"
else
	not_ok "case3: no log when normal"
fi

# --- MAX を環境変数で変更できる ---
mkqueue 3
rm -f "$TMP/MARKERS_DIR/.radio_queue_backpressure_active"
if RADIO_DEFERRED_QUEUE_MAX=2 _radio_generation_blocked_by_backpressure; then
	ok "case4: MAX=2 blocks at 3 items"
else
	not_ok "case4: MAX=2 blocks at 3 items"
fi

# --- 統合検証: schedule_nonessential_audio_jobs がガードを先に効かせる ---
sed -n '/^schedule_nonessential_audio_jobs()/,/^}/p' "$SCHED_SRC" > "$TMP/fn_schedule.sh"
export TMP_DIR="$TMP"
cat > "$TMP/integration_env.sh" <<'ENV'
TMP_MARKERS_DIR="$TMP_DIR/markers"
RADIO_DEFERRED_QUEUE_DIR="$TMP_DIR/queue"
GAME_COUNT_FILE="$TMP_DIR/game_count.txt"
TMP_STATE_DIR="$TMP_DIR/state"
mkdir -p "$TMP_MARKERS_DIR" "$TMP_STATE_DIR"
echo "1" > "$GAME_COUNT_FILE"

log() { printf 'LOG: %s\n' "$*" >> "$TMP_DIR/integration.log"; }
get_comment_backlog_counts() { printf '0 0\n'; }
is_comment_backlog_high() { return 1; }
_last_score() { printf '0\n'; }
_try_game_corner() { return 1; }
fetch_and_play_news() { log "CALLED fetch_and_play_news"; return 0; }
_run_jiji_corner_guarded() { log "CALLED _run_jiji_corner_guarded"; return 0; }
start_random_radio_corner() { log "CALLED start_random_radio_corner"; return 0; }
start_radio_corner_news() { log "CALLED start_radio_corner_news"; return 0; }
# 時刻ベース window 外になるよう固定時刻を返す
date() {
	if [ "${1:-}" = "+%H" ]; then printf '12\n'; return 0; fi
	if [ "${1:-}" = "+%M" ]; then printf '34\n'; return 0; fi
	if [ "${1:-}" = "+%Y%m%d" ]; then printf '20260817\n'; return 0; fi
	if [ "${1:-}" = "+%s" ]; then printf '1786900000\n'; return 0; fi
	command date "$@"
}
rm -f "$TMP_DIR/integration.log"
ENV

# pending strategy radio: 処理されれば削除される
mkdir -p "$TMP/tmp/state"
cat > "$TMP/tmp/state/pending_strategy_radio.json" <<'JSON'
{"game_num": 1, "strategy_diff": "test-diff", "best_score": 10, "scores": "100"}
JSON

# 6 件滞留 → ガードで即 return し、pending は処理されず残る
mkqueue 6
(
	cd "$TMP"
	. "$TMP/integration_env.sh"
	. "$TMP/fn_count.sh"
	. "$TMP/fn_blocked.sh"
	. "$TMP/fn_schedule.sh"
	schedule_nonessential_audio_jobs 1 0 2>/dev/null
)
if [ -f "$TMP/tmp/state/pending_strategy_radio.json" ]; then
	ok "integration: pending strategy radio untouched while blocked (6 items)"
else
	not_ok "integration: pending strategy radio untouched while blocked (6 items)"
fi
if [ -f "$TMP/markers/.radio_queue_backpressure_active" ]; then
	ok "integration: backpressure marker active after schedule call"
else
	not_ok "integration: backpressure marker active after schedule call"
fi
if [ ! -s "$TMP/integration.log" ]; then
	ok "integration: no generation function called while blocked"
else
	not_ok "integration: no generation function called while blocked ($(cat "$TMP/integration.log"))"
fi

# 5 件以下に戻る → ガード解除され pending が処理される
mkqueue 5
rm -f "$TMP/markers/.radio_queue_backpressure_active"
rm -f "$TMP/integration.log"
(
	cd "$TMP"
	. "$TMP/integration_env.sh"
	. "$TMP/fn_count.sh"
	. "$TMP/fn_blocked.sh"
	. "$TMP/fn_schedule.sh"
	schedule_nonessential_audio_jobs 1 0 2>/dev/null
)
if [ ! -f "$TMP/tmp/state/pending_strategy_radio.json" ]; then
	ok "integration: pending strategy radio processed after resume (5 items)"
else
	not_ok "integration: pending strategy radio processed after resume (5 items)"
fi
if grep -q "CALLED" "$TMP/integration.log"; then
	ok "integration: generation function called after resume"
else
	not_ok "integration: generation function called after resume"
fi

exit "$FAIL"

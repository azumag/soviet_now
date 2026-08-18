#!/usr/bin/env bash
# ピーク時間帯限定のキューゲートを検証する: issue #5 のバックプレッシャー(MAX=5)とは
# 独立に、ピーク中はキューが完全に空(0件)になるまで新規ラジオ生成を止める。
# ピーク外はこのゲート自体が働かない。
# 実コードをそのまま抽出して source する。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPERS_SRC="$ROOT/core/helpers.sh"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
SCHED_SRC="$ROOT/broadcast/scheduler.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source ---
sed -n '/^_peak_hours_to_minutes()/,/^}/p'          "$HELPERS_SRC" >"$TMP/fn_min.sh"
sed -n '/^_is_peak_hours()/,/^}/p'                  "$HELPERS_SRC" >"$TMP/fn_peak.sh"
sed -n '/^_radio_deferred_queue_count()/,/^}/p'     "$STATE_SRC"   >"$TMP/fn_count.sh"
sed -n '/^_radio_generation_blocked_by_peak_hour_queue()/,/^}/p' "$SCHED_SRC" >"$TMP/fn_gate.sh"

TMP_MARKERS_DIR="$TMP/markers"
RADIO_DEFERRED_QUEUE_DIR="$TMP/queue"
mkdir -p "$TMP_MARKERS_DIR"

log() { printf 'LOG: %s\n' "$*" >>"$TMP/log.txt"; }
: >"$TMP/log.txt"
. "$TMP/fn_min.sh"
. "$TMP/fn_peak.sh"
. "$TMP/fn_count.sh"
. "$TMP/fn_gate.sh"

mkqueue() {
	local n="$1"
	mkdir -p "$RADIO_DEFERRED_QUEUE_DIR"
	rm -f "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.txt "$RADIO_DEFERRED_QUEUE_DIR"/radio_*.*
	local i
	# BSD seq (macOS) は `seq 1 0` で "1\n0" を返す(GNU seqは空)。n=0 はループ自体を
	# 回避してこの差異を吸収する（tests/test_radio_backpressure.sh の同名ヘルパーにも
	# 潜在する差異だが、issue #5 側は MAX=5 に隠れて表面化しないため未修整のまま）。
	[ "$n" -gt 0 ] || return 0
	for i in $(seq 1 "$n"); do
		: >"$RADIO_DEFERRED_QUEUE_DIR/radio_${i}_1_theme_${i}.txt"
	done
}

# ============================================================
# ピーク中: キュー1件でもブロック、0件で許可
# ============================================================
PEAK_HOURS_TEST_NOW=1100
unset PEAK_HOURS_WINDOWS PEAK_HOURS_QUEUE_GATE_ENABLED

mkqueue 1
rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" "$TMP/log.txt"
if _radio_generation_blocked_by_peak_hour_queue; then
	ok "peak: blocked at 1 item (stricter than issue #5's MAX=5)"
else
	not_ok "peak: blocked at 1 item (stricter than issue #5's MAX=5)"
fi
if [ -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
	ok "peak: gate marker created"
else
	not_ok "peak: gate marker created"
fi
if [ "$(grep -c '抑制' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ]; then
	ok "peak: suppression logged once"
else
	not_ok "peak: suppression logged once"
fi

# マーカーがある間は再度呼んでもログが増えない
rm -f "$TMP/log.txt"
_radio_generation_blocked_by_peak_hour_queue
if [ ! -s "$TMP/log.txt" ]; then
	ok "peak: no repeated log while staying blocked"
else
	not_ok "peak: no repeated log while staying blocked"
fi

mkqueue 5
rm -f "$TMP/log.txt"
if _radio_generation_blocked_by_peak_hour_queue; then
	ok "peak: blocked at 5 items (issue #5 alone would allow this)"
else
	not_ok "peak: blocked at 5 items (issue #5 alone would allow this)"
fi

mkqueue 0
rm -f "$TMP/log.txt"
if _radio_generation_blocked_by_peak_hour_queue; then
	not_ok "peak: allowed at 0 items"
else
	ok "peak: allowed at 0 items"
fi
if [ ! -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
	ok "peak: gate marker cleared on resume"
else
	not_ok "peak: gate marker cleared on resume"
fi
if [ "$(grep -c '再開' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ]; then
	ok "peak: resume logged once"
else
	not_ok "peak: resume logged once"
fi

# ============================================================
# オフピーク: キューが1件以上あってもこのゲートは常に許可
# ============================================================
PEAK_HOURS_TEST_NOW=1400
mkqueue 3
rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" "$TMP/log.txt"
if _radio_generation_blocked_by_peak_hour_queue; then
	not_ok "off-peak: never blocks regardless of queue size"
else
	ok "off-peak: never blocks regardless of queue size"
fi
if [ ! -s "$TMP/log.txt" ]; then
	ok "off-peak: silent (no log)"
else
	not_ok "off-peak: silent (no log)"
fi

# ============================================================
# マーカーのリーク回帰確認 (C1): ピーク終了時・無効化時にマーカーが残ると、
# 次にピークへ再突入した時に抑制開始ログが出ない(サイレントに見える)バグの再発防止。
# ============================================================
PEAK_HOURS_TEST_NOW=1100
mkqueue 1
rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" "$TMP/log.txt"
_radio_generation_blocked_by_peak_hour_queue >/dev/null # ピーク中+queue=1 -> マーカー作成
if [ -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
	ok "leak-check: marker set while blocked at peak"
else
	not_ok "leak-check: marker set while blocked at peak"
fi

# キューは1件のまま、オフピークへ抜ける -> マーカーは掃除されるべき
PEAK_HOURS_TEST_NOW=1400
_radio_generation_blocked_by_peak_hour_queue >/dev/null
if [ ! -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
	ok "leak-check: marker cleared on leaving peak (queue still >0)"
else
	not_ok "leak-check: marker cleared on leaving peak (queue still >0)"
fi

# 再びピークへ (キューは依然1件) -> 抑制開始ログがちゃんと再度出ること
PEAK_HOURS_TEST_NOW=1100
rm -f "$TMP/log.txt"
if _radio_generation_blocked_by_peak_hour_queue; then
	ok "leak-check: re-entering peak blocks again"
else
	not_ok "leak-check: re-entering peak blocks again"
fi
if [ "$(grep -c '抑制' "$TMP/log.txt" 2>/dev/null || echo 0)" = "1" ]; then
	ok "leak-check: suppression logged again after peak re-entry (not silently skipped)"
else
	not_ok "leak-check: suppression logged again after peak re-entry (not silently skipped)"
fi

# 同様に、無効化スイッチでもマーカーは掃除される
rm -f "$TMP/log.txt"
PEAK_HOURS_QUEUE_GATE_ENABLED=0
_radio_generation_blocked_by_peak_hour_queue >/dev/null
if [ ! -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active" ]; then
	ok "leak-check: marker cleared when gate disabled mid-block"
else
	not_ok "leak-check: marker cleared when gate disabled mid-block"
fi
unset PEAK_HOURS_QUEUE_GATE_ENABLED

# ============================================================
# 無効化スイッチ
# ============================================================
PEAK_HOURS_TEST_NOW=1100
mkqueue 1
rm -f "$TMP_MARKERS_DIR/.radio_peak_queue_gate_active"
PEAK_HOURS_QUEUE_GATE_ENABLED=0
if _radio_generation_blocked_by_peak_hour_queue; then
	not_ok "PEAK_HOURS_QUEUE_GATE_ENABLED=0 -> never blocks even at peak"
else
	ok "PEAK_HOURS_QUEUE_GATE_ENABLED=0 -> never blocks even at peak"
fi
unset PEAK_HOURS_QUEUE_GATE_ENABLED

unset PEAK_HOURS_TEST_NOW

# ============================================================
# 統合検証: schedule_nonessential_audio_jobs が issue #5 の後段でこのゲートも効かせる
# ============================================================
sed -n '/^schedule_nonessential_audio_jobs()/,/^}/p' "$SCHED_SRC" >"$TMP/fn_schedule.sh"
sed -n '/^_radio_generation_blocked_by_backpressure()/,/^}/p' "$SCHED_SRC" >"$TMP/fn_backpressure.sh"
export TMP_DIR="$TMP"
cat >"$TMP/integration_env.sh" <<'ENV'
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
# 時刻ベース window 外になるよう固定時刻を返す（schedule内部の時刻分岐用。
# ピーク判定は PEAK_HOURS_TEST_NOW で別途注入するため、ここでは干渉しない）
date() {
	if [ "${1:-}" = "+%H" ]; then printf '12\n'; return 0; fi
	if [ "${1:-}" = "+%M" ]; then printf '34\n'; return 0; fi
	if [ "${1:-}" = "+%Y%m%d" ]; then printf '20260817\n'; return 0; fi
	if [ "${1:-}" = "+%s" ]; then printf '1786900000\n'; return 0; fi
	command date "$@"
}
rm -f "$TMP_DIR/integration.log"
ENV

mkdir -p "$TMP/tmp/state"
cat >"$TMP/tmp/state/pending_strategy_radio.json" <<'JSON'
{"game_num": 1, "strategy_diff": "test-diff", "best_score": 10, "scores": "100"}
JSON

# ピーク中 + キュー1件 → issue #5(MAX=5)は許可する条件だが、新ゲートで止まる
mkqueue 1
(
	cd "$TMP"
	. "$TMP/integration_env.sh"
	. "$TMP/fn_min.sh"
	. "$TMP/fn_peak.sh"
	. "$TMP/fn_count.sh"
	. "$TMP/fn_backpressure.sh"
	. "$TMP/fn_gate.sh"
	. "$TMP/fn_schedule.sh"
	PEAK_HOURS_TEST_NOW=1100 schedule_nonessential_audio_jobs 1 0 2>/dev/null
)
if [ -f "$TMP/tmp/state/pending_strategy_radio.json" ]; then
	ok "integration: peak+1item blocks generation (pending untouched)"
else
	not_ok "integration: peak+1item blocks generation (pending untouched)"
fi
if [ -f "$TMP/markers/.radio_peak_queue_gate_active" ]; then
	ok "integration: peak queue gate marker active after schedule call"
else
	not_ok "integration: peak queue gate marker active after schedule call"
fi

# ピーク中 + キュー0件 → 生成が許可される
mkqueue 0
rm -f "$TMP/markers/.radio_peak_queue_gate_active" "$TMP/integration.log"
(
	cd "$TMP"
	. "$TMP/integration_env.sh"
	. "$TMP/fn_min.sh"
	. "$TMP/fn_peak.sh"
	. "$TMP/fn_count.sh"
	. "$TMP/fn_backpressure.sh"
	. "$TMP/fn_gate.sh"
	. "$TMP/fn_schedule.sh"
	PEAK_HOURS_TEST_NOW=1100 schedule_nonessential_audio_jobs 1 0 2>/dev/null
)
if [ ! -f "$TMP/tmp/state/pending_strategy_radio.json" ]; then
	ok "integration: peak+0item allows generation (pending processed)"
else
	not_ok "integration: peak+0item allows generation (pending processed)"
fi

# オフピーク + キュー3件 → issue #5(MAX=5)の範囲内なので生成が許可される
mkdir -p "$TMP/tmp/state"
cat >"$TMP/tmp/state/pending_strategy_radio.json" <<'JSON'
{"game_num": 1, "strategy_diff": "test-diff", "best_score": 10, "scores": "100"}
JSON
mkqueue 3
rm -f "$TMP/markers/.radio_peak_queue_gate_active" "$TMP/markers/.radio_queue_backpressure_active" "$TMP/integration.log"
(
	cd "$TMP"
	. "$TMP/integration_env.sh"
	. "$TMP/fn_min.sh"
	. "$TMP/fn_peak.sh"
	. "$TMP/fn_count.sh"
	. "$TMP/fn_backpressure.sh"
	. "$TMP/fn_gate.sh"
	. "$TMP/fn_schedule.sh"
	PEAK_HOURS_TEST_NOW=1400 schedule_nonessential_audio_jobs 1 0 2>/dev/null
)
if [ ! -f "$TMP/tmp/state/pending_strategy_radio.json" ]; then
	ok "integration: off-peak+3items allows generation (issue #5 alone governs)"
else
	not_ok "integration: off-peak+3items allows generation (issue #5 alone governs)"
fi

exit "$FAIL"

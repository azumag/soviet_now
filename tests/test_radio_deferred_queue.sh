#!/usr/bin/env bash
# deferred ラジオキューが mode 不一致でも破棄せず、生成順に全て再生することを検証する。
# 修正前は expected_mode != current_mode のキュー項目を削除していたが、
# 修正後は破棄せず再生し、同一 game+corner の二重生成も done マーカーで防ぐ。
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE_SRC="$ROOT/broadcast/radio_state.sh"
ENGINE_SRC="$ROOT/broadcast/radio_engine.sh"
PERSONA_SRC="$ROOT/broadcast/radio_persona.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

FAIL=0
ok() { echo "ok - $1"; }
not_ok() { echo "not ok - $1"; FAIL=1; }

# --- 実関数を抽出して source（修正後の実コードをそのまま使う） ---
sed -n '/^_radio_audio_base_path()/,/^}/p' "$STATE_SRC" > "$TMP/fn_base.sh"
sed -n '/^_radio_ready_wav_path()/,/^}/p' "$STATE_SRC" > "$TMP/fn_ready.sh"
sed -n '/^_radio_render_marker_path()/,/^}/p' "$STATE_SRC" > "$TMP/fn_marker.sh"
sed -n '/^_radio_render_retry_path()/,/^}/p' "$STATE_SRC" > "$TMP/fn_retry_path.sh"
sed -n '/^_radio_clear_deferred_render_retry()/,/^}/p' "$STATE_SRC" > "$TMP/fn_retry_clear.sh"
sed -n '/^_play_deferred_radio_queue_once()/,/^}/p' "$STATE_SRC" > "$TMP/fn_play.sh"
sed -n '/^_broadcast_mode_sidecar_path()/,/^}/p' "$PERSONA_SRC" > "$TMP/fn_sidecar.sh"
sed -n '/^_broadcast_clear_expected_mode()/,/^}/p' "$PERSONA_SRC" > "$TMP/fn_clear_mode.sh"
. "$TMP/fn_base.sh"
. "$TMP/fn_ready.sh"
. "$TMP/fn_marker.sh"
. "$TMP/fn_retry_path.sh"
. "$TMP/fn_retry_clear.sh"
. "$TMP/fn_sidecar.sh"
. "$TMP/fn_clear_mode.sh"
. "$TMP/fn_play.sh"

# --- 依存モック ---
LOG_FILE="$TMP/play.log"
SAY_LOG="$TMP/say.log"
QUEUE_DIR="$TMP/queue"
mkdir -p "$QUEUE_DIR"

log() { printf '%s\n' "$*" >> "$LOG_FILE"; }
get_comment_backlog_counts() { printf '0 0\n'; }
pgrep() { return 1; }
_radio_voicevox_speaker_override() { return 0; }
_radio_start_deferred_render_if_needed() { return 0; }
_refresh_radio_intro_for_playback_file() { return 0; }
_radio_generation_debug_summary() { return 0; }
_radio_commit_spoken_history_for_file() { return 0; }
_radio_clear_generation_meta() { return 0; }
_radio_clear_spoken_history_line() { return 0; }

# 偽 say_enqueue.sh: 呼び出しを記録して成功する
cat > "$TMP/say_enqueue.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$SAY_LOG"
exit 0
SH
chmod +x "$TMP/say_enqueue.sh"

export RADIO_DEFERRED_QUEUE_DIR="$QUEUE_DIR"
export RADIO_STATE_STALE_SEC=600
export RADIO_SAY_RATE=1.0

# --- 静的検証: 破棄コードが残っていないこと ---
if grep -q "mode不一致で破棄\|mode不一致で再生前破棄\|stale_mode_discarded\|-> discard" "$STATE_SRC" "$ENGINE_SRC"; then
	not_ok "mode mismatch discard code removed"
else
	ok "mode mismatch discard code removed"
fi

# --- 静的検証: 生成成功時に done マーカーを作成すること ---
if grep -q '_radio_mark_done "$done_marker"' "$ENGINE_SRC"; then
	ok "done marker created on successful enqueue"
else
	not_ok "done marker created on successful enqueue"
fi

# --- 挙動検証1: expected=soren91 / current=main でも破棄せず再生 ---
mkdir -p "$TMP/case1"
export RADIO_DEFERRED_QUEUE_DIR="$TMP/case1"
QUEUE_FILE="$TMP/case1/radio_1000_5_news_1.txt"
echo "テスト用ラジオ本文" > "$QUEUE_FILE"
echo "soren91" > "$TMP/case1/radio_1000_5_news_1.mode"
printf 'RIFF' > "$TMP/case1/radio_1000_5_news_1.ready.wav"
export SAY_LOG="$TMP/case1/say.log" LOG_FILE="$TMP/case1/play.log"
rm -f "$TMP/case1/play.log" "$TMP/case1/say.log"

(
	cd "$TMP"
	_play_deferred_radio_queue_once
)

if grep -q "再生開始" "$TMP/case1/play.log"; then
	ok "case1: radio played despite mode mismatch"
else
	not_ok "case1: radio played despite mode mismatch"
fi
if grep -q "破棄" "$TMP/case1/play.log"; then
	not_ok "case1: no discard log"
else
	ok "case1: no discard log"
fi
if [ -s "$TMP/case1/say.log" ]; then
	ok "case1: say_enqueue invoked"
else
	not_ok "case1: say_enqueue invoked"
fi
if [ ! -e "$TMP/case1/radio_1000_5_news_1.txt" ] && [ ! -e "$TMP/case1/radio_1000_5_news_1.playing" ]; then
	ok "case1: queue item consumed"
else
	not_ok "case1: queue item consumed"
fi

# --- 挙動検証2: 2項目を生成順（ファイル名順=FIFO）に全部再生 ---
mkdir -p "$TMP/case2"
export RADIO_DEFERRED_QUEUE_DIR="$TMP/case2"
echo "ラジオA" > "$TMP/case2/radio_1000_5_theme_1.txt"
printf 'RIFF' > "$TMP/case2/radio_1000_5_theme_1.ready.wav"
echo "ラジオB" > "$TMP/case2/radio_2000_6_theme_1.txt"
printf 'RIFF' > "$TMP/case2/radio_2000_6_theme_1.ready.wav"
export SAY_LOG="$TMP/case2/say.log" LOG_FILE="$TMP/case2/play.log"
rm -f "$TMP/case2/play.log" "$TMP/case2/say.log"

(
	cd "$TMP"
	_play_deferred_radio_queue_once
	_play_deferred_radio_queue_once
)

first=$(head -n1 "$TMP/case2/play.log")
if grep -q "radio_1000_5_theme_1" "$TMP/case2/play.log" && grep -q "radio_2000_6_theme_1" "$TMP/case2/play.log"; then
	ok "case2: both radios played"
else
	not_ok "case2: both radios played"
fi
if [ "$(grep -c "再生開始" "$TMP/case2/play.log")" -eq 2 ]; then
	ok "case2: exactly 2 playbacks"
else
	not_ok "case2: exactly 2 playbacks (got $(grep -c "再生開始" "$TMP/case2/play.log"))"
fi
if [ "$(ls -1 "$TMP/case2"/radio_*.txt 2>/dev/null | wc -l)" -eq 0 ]; then
	ok "case2: queue drained"
else
	not_ok "case2: queue drained"
fi

exit "$FAIL"

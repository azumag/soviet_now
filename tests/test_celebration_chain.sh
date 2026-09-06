#!/usr/bin/env bash
set -eo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

# 祝賀生成が通常コーナーと同じモデルチェーン設定を使うこと
grep -q 'ai_generate_list "RADIO:' broadcast/radio_celebration.sh
grep -q '${RADIO_AGENTS:-' broadcast/radio_celebration.sh
! grep -q '_run_claude_radio' broadcast/radio_celebration.sh
! grep -q '_run_opencode_radio' broadcast/radio_celebration.sh
echo "chain wiring static checks: OK"

# 失敗時の原文保存 (dump) の動作確認
log() { :; }
_radio_set_state() { :; }
_radio_clear_state() { :; }
_sanitize_onair_text() { cat; }
_normalize_radio_tone() { cat; }
AI_TEXT=""
ai_generate_list() { printf '%s' "$AI_TEXT"; }
_ai_guard_model_output() { cat; }

tmp_dir=$(mktemp -d)
trap 'rm -rf "$tmp_dir"' EXIT
export TMP_DEBUG_DIR="$tmp_dir/debug"
mkdir -p "$TMP_DEBUG_DIR"
export RADIO_FACT_CHECK_ENABLED=0

source broadcast/radio_celebration.sh

# 1. 生成空振り → generation_empty の dump
_is_valid_radio_talk() { return 0; }
AI_TEXT=""
if generate_russia_celebration 1234 45 67; then echo "FAIL: expected rc!=0"; exit 1; fi
dump=$(ls "$TMP_DEBUG_DIR"/radio_failed_russia_celebration_*.txt 2>/dev/null | head -n 1)
[ -n "$dump" ]
grep -q 'reason=generation_empty' "$dump"
grep -q '===PROMPT===' "$dump"
echo "empty-generation dump: OK ($dump)"

# 2. 検証落ち → invalid_after_fact_check の dump (本文付き)
rm -f "$TMP_DEBUG_DIR"/radio_failed_*.txt
AI_TEXT="これはダミー祝賀トークです。ソ連が建国されました。"
_is_valid_radio_talk() { return 1; }
if generate_soviet_celebration 5312 175 49106; then echo "FAIL: expected rc!=0"; exit 1; fi
dump=$(ls "$TMP_DEBUG_DIR"/radio_failed_celebration_*.txt 2>/dev/null | head -n 1)
[ -n "$dump" ]
grep -q 'reason=invalid_after_fact_check' "$dump"
grep -q 'ダミー祝賀トーク' "$dump"
echo "invalid-talk dump: OK ($dump)"

# 3. 正常系 → ファイル出力
rm -f "$TMP_DEBUG_DIR"/radio_failed_*.txt
_is_valid_radio_talk() { return 0; }
generate_soviet_celebration 5312 175 49106
[ -s "$TMP_DEBUG_DIR/radio_soviet_celebration.txt" ]
grep -q 'ダミー祝賀トーク' "$TMP_DEBUG_DIR/radio_soviet_celebration.txt"
echo "happy path: OK"

echo "celebration chain tests: OK"

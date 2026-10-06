#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
source "$ROOT/broadcast/radio_engine.sh"

fail() {
	echo "FAIL: $*" >&2
	exit 1
}

DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=0
DOCICH_RADIO_NATIVE_CORNERS="ai_knowledge,finance"
if _radio_native_consumer_enabled_for "ai_knowledge" "公開トピック"; then
	fail "disabled gate must stay off"
fi

DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1
DOCICH_RADIO_NATIVE_CORNERS="ai_knowledge, finance"
_radio_native_consumer_enabled_for "ai_knowledge" "公開トピック" || fail "allowlisted corner should be eligible"
_radio_native_consumer_enabled_for "finance" "公開トピック" || fail "whitespace-normalized allowlist should work"
if _radio_native_consumer_enabled_for "news" "公開トピック"; then
	fail "non-allowlisted corner must stay legacy"
fi
if _radio_native_consumer_enabled_for "ai_knowledge" ""; then
	fail "topic-less corner must stay legacy"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
DOCICH="$TMP/docich"
mkdir -p "$DOCICH/src/docich/radio"
: >"$DOCICH/src/docich/__init__.py"
: >"$DOCICH/src/docich/radio/__init__.py"
cat >"$DOCICH/src/docich/radio/consumer.py" <<'PY'
import json
import os
import sys
import time

request = json.load(sys.stdin)
assert set(request) == {"topic", "queries", "agents"}
assert request["queries"] == [request["topic"]]
assert request["agents"] == "openrouter-api:fixture"
mode = os.environ.get("FIXTURE_MODE", "ok")
if mode == "ok":
    payload = {
        "status": "ok",
        "scope": "web",
        "body": "公開素材に基づく日本語のテスト本文です。",
        "summary": "テスト要約です。",
        "selected_news": "",
    }
elif mode == "partial":
    payload = {
        "status": "partial",
        "scope": "web",
        "body": "公開素材が一部だけ得られた場合の日本語テスト本文です。",
        "summary": "部分取得のテスト要約です。",
        "selected_news": "",
    }
elif mode == "hold":
    payload = {"status": "low_confidence", "scope": "unknown"}
elif mode == "sleep":
    time.sleep(2)
    payload = {
        "status": "ok",
        "scope": "web",
        "body": "期限超過用テスト本文です。",
        "summary": "期限超過用テスト要約です。",
        "selected_news": "",
    }
else:
    raise RuntimeError("synthetic secret must not cross bridge")
print(json.dumps(payload, ensure_ascii=False))
PY

DOCICH_RADIO_NATIVE_ROOT="$DOCICH"
DOCICH_RADIO_NATIVE_AGENTS="openrouter-api:fixture"
BODY="$TMP/body.txt"
SUMMARY="$TMP/summary.txt"
meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY") || fail "successful bridge call failed"
[ "$meta" = "ok:web" ] || fail "unexpected success metadata: $meta"
grep -q "公開素材" "$BODY" || fail "body was not written"
grep -q "テスト要約" "$SUMMARY" || fail "summary was not written"

rm -f "$BODY" "$SUMMARY"
FIXTURE_MODE=hold
export FIXTURE_MODE
if meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY"); then
	fail "hold result must fail closed"
fi
[ "$meta" = "low_confidence:unknown" ] || fail "unexpected hold metadata: $meta"
[ ! -s "$BODY" ] || fail "hold must not produce body"
[ ! -s "$SUMMARY" ] || fail "hold must not produce summary"
unset FIXTURE_MODE

DOCICH_RADIO_NATIVE_ROOT="relative/path"
if meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY"); then
	fail "relative root must be rejected"
fi
[ "$meta" = "invalid_root" ] || fail "unexpected invalid-root metadata: $meta"

DOCICH_RADIO_NATIVE_ROOT="$DOCICH"
FIXTURE_MODE=partial
export FIXTURE_MODE
meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY") || fail "partial bridge result should be accepted"
[ "$meta" = "partial:web" ] || fail "unexpected partial metadata: $meta"
grep -q "一部だけ得られた" "$BODY" || fail "partial body was not written"
unset FIXTURE_MODE

rm -f "$BODY" "$SUMMARY"
DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC=1
FIXTURE_MODE=sleep
export DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC FIXTURE_MODE
if meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY"); then
	fail "hung bridge must hit the outer process deadline"
fi
[ "$meta" = "process_timeout" ] || fail "unexpected process-timeout metadata: $meta"
[ ! -s "$BODY" ] || fail "process timeout must not produce a body"
[ ! -s "$SUMMARY" ] || fail "process timeout must not produce a summary"
unset DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC FIXTURE_MODE

DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC=0
export DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC
if meta=$(_radio_native_generate_script "公開トピック" "$BODY" "$SUMMARY"); then
	fail "invalid process timeout must fail closed"
fi
[ "$meta" = "invalid_process_timeout" ] || fail "unexpected invalid-timeout metadata: $meta"
unset DOCICH_RADIO_NATIVE_PROCESS_TIMEOUT_SEC


# Exercise the real RADIO consumer boundary through the existing deferred queue
# while stubbing external generation and presentation dependencies only. The
# actual process bridge is kept in the path.
DOCICH_RADIO_NATIVE_ROOT="$DOCICH"
DOCICH_RADIO_NATIVE_AGENTS="openrouter-api:fixture"
DOCICH_RADIO_NATIVE_CONSUMER_ENABLED=1
DOCICH_RADIO_NATIVE_CORNERS="ai_knowledge"
TMP_MARKERS_DIR="$TMP/markers"
mkdir -p "$TMP_MARKERS_DIR"
HOST_MODE="main"
QUEUE_MODE="ok"

log() { :; }
_radio_peak_hour_should_defer() { return 1; }
_radio_set_state() { printf 'state:%s:%s\n' "$1" "$2" >>"$TMP/events"; }
_write_radio_corner_status() { printf '%s|%s|%s\n' "$1" "$3" "$2" >>"$TMP/statuses"; }
_radio_clear_state() { printf 'clear_state:%s:%s\n' "$1" "$2" >>"$TMP/events"; }
_broadcast_host_mode() { printf '%s' "$HOST_MODE"; }
_radio_build_overlay_detail() { printf '%s' "$1"; }
_ensure_corner_announce() {
	printf 'announce:%s\n' "$2" >>"$TMP/events"
	printf '[announce:%s] %s' "$2" "$1"
}
_normalize_radio_tone() {
	printf 'normalize\n' >>"$TMP/events"
	sed 's/公開素材/検証済み公開素材/g'
}
_radio_quality_check() { printf '%s' "OK"; }
_is_valid_radio_talk() { return 0; }
_radio_store_generation_meta() {
	printf '%s|%s|%s|%s\n' "$2" "$4" "$9" "${10}" >>"$TMP/generation_meta"
}
_radio_mark_done() {
	printf 'done:%s\n' "$(basename "$1")" >>"$TMP/events"
	[ -n "$1" ] && touch "$1"
}
_radio_clear_generation_meta() {
	printf '%s\n' "$1" >>"$TMP/cleared_meta"
}
_build_cc_attribution_text() { printf 'CC:%s' "$1"; }
_enqueue_deferred_radio_talk() {
	local talk_file="$1" game_num="$2" corner_name="$3" expected_mode="$4" history_line="$5"
	printf 'enqueue:%s:%s\n' "$game_num" "$corner_name" >>"$TMP/events"
	printf '%s' "$history_line" >"$TMP/history_${game_num}_${corner_name}.txt"
	printf '%s' "$expected_mode" >"$TMP/mode_${game_num}_${corner_name}.txt"
	if [ "${QUEUE_MODE:-ok}" = "fail" ]; then
		return 1
	fi
	local target="$TMP/queued_${game_num}_${corner_name}.txt"
	cp "$talk_file" "$target"
	printf '%s' "$target"
}
ai_generate_list() {
	: >"$TMP/legacy_called"
	return 1
}
_radio_fact_check_body() {
	: >"$TMP/factcheck_called"
	return 1
}

PROMPT="$TMP/prompt.txt"
printf '%s\n' "legacy prompt must not be consumed" >"$PROMPT"
_radio_generate_and_play "$PROMPT" 42 7 "ai_knowledge" --topic "公開トピック" || fail "native consumer integration failed"

[ -s "$TMP/queued_42_ai_knowledge.txt" ] || fail "native body was not handed to deferred queue"
grep -q '\[announce:ai_knowledge\]' "$TMP/queued_42_ai_knowledge.txt" || fail "corner announce was not preserved"
grep -q "検証済み公開素材" "$TMP/queued_42_ai_knowledge.txt" || fail "tone normalization was not applied"
grep -q 'Game#42 7pts \[ai_knowledge\]: テスト要約です。' "$TMP/history_42_ai_knowledge.txt" || fail "native summary was not handed to deferred history"
[ "$(cat "$TMP/mode_42_ai_knowledge.txt")" = "main" ] || fail "generated host mode was not handed to deferred queue"
[ -f "$TMP_MARKERS_DIR/.radio_done_42_ai_knowledge" ] || fail "done marker must follow successful enqueue"
[ ! -d "$TMP_MARKERS_DIR/.radio_inflight_42_ai_knowledge" ] || fail "successful enqueue must release inflight marker"
enqueue_line=$(grep -n '^enqueue:42:ai_knowledge$' "$TMP/events" | tail -1 | cut -d: -f1)
done_line=$(grep -n '^done:.radio_done_42_ai_knowledge$' "$TMP/events" | tail -1 | cut -d: -f1)
[ -n "$enqueue_line" ] && [ -n "$done_line" ] && [ "$enqueue_line" -lt "$done_line" ] || fail "done marker must be written only after queue acceptance"
[ ! -e "$TMP/legacy_called" ] || fail "native selection must not call legacy ai_generate_list"
[ ! -e "$TMP/factcheck_called" ] || fail "native selection must not call legacy AI fact-check"
[ ! -e "$PROMPT" ] || fail "native path must clean caller prompt"
grep -q '^queued|42|ai_knowledge$' "$TMP/statuses" || fail "queued status was not emitted"

# A partial evidence result is still an accepted native script and follows the
# same queue contract; it must not silently switch to legacy generation.
FIXTURE_MODE=partial
export FIXTURE_MODE
PROMPT="$TMP/prompt_partial.txt"
printf '%s\n' "legacy prompt must not be consumed" >"$PROMPT"
_radio_generate_and_play "$PROMPT" 43 8 "ai_knowledge" --topic "部分取得トピック" || fail "partial native result should queue"
grep -q "一部だけ得られた" "$TMP/queued_43_ai_knowledge.txt" || fail "partial native body was not queued"
grep -q '部分取得のテスト要約です。' "$TMP/history_43_ai_knowledge.txt" || fail "partial summary was not preserved"
[ -f "$TMP_MARKERS_DIR/.radio_done_43_ai_knowledge" ] || fail "partial success must be marked done after enqueue"
unset FIXTURE_MODE

# Queue rejection must fail closed: no done marker, no legacy fallback, no stale
# inflight marker, and generation metadata cleanup must run.
QUEUE_MODE=fail
PROMPT="$TMP/prompt_queue_fail.txt"
printf '%s\n' "legacy prompt must not be consumed" >"$PROMPT"
if _radio_generate_and_play "$PROMPT" 44 9 "ai_knowledge" --topic "queue failure"; then
	fail "deferred queue rejection must fail native delivery"
fi
[ ! -e "$TMP_MARKERS_DIR/.radio_done_44_ai_knowledge" ] || fail "queue failure must not create done marker"
[ ! -d "$TMP_MARKERS_DIR/.radio_inflight_44_ai_knowledge" ] || fail "queue failure must release inflight marker"
grep -q '^deferred_enqueue_failed|44|ai_knowledge$' "$TMP/statuses" || fail "queue failure status was not emitted"
[ -s "$TMP/cleared_meta" ] || fail "queue failure must clear generation metadata"
[ ! -e "$TMP/legacy_called" ] || fail "queue failure must not fall back to legacy generation"
QUEUE_MODE=ok

# News canary keeps caller-owned attribution metadata outside spoken text, and
# passes the generated host mode to the existing queue so voice selection stays
# owned by the deferred/audio layer.
DOCICH_RADIO_NATIVE_CORNERS="ai_knowledge,news"
HOST_MODE="soren91"
PROMPT="$TMP/prompt_news.txt"
printf '%s\n' "legacy prompt must not be consumed" >"$PROMPT"
_radio_generate_and_play "$PROMPT" 45 10 "news" --topic "海外ニュース" --selected-news "Global Voices: テスト見出し" || fail "native news integration failed"
[ "$(cat "$TMP/mode_45_news.txt")" = "soren91" ] || fail "native queue must preserve generation host mode for voice selection"
[ "$(cat "$TMP/queued_45_news.news_title")" = "Global Voices: テスト見出し" ] || fail "news title attribution sidecar was not preserved"
[ "$(cat "$TMP/queued_45_news.cc_text")" = "CC:Global Voices: テスト見出し" ] || fail "caption/chat attribution sidecar was not preserved"
grep -q 'news|docich-native|Global Voices: テスト見出し|docich-native' "$TMP/generation_meta" || fail "native generation metadata did not retain caller-owned news attribution"
[ -f "$TMP_MARKERS_DIR/.radio_done_45_news" ] || fail "native news must be marked done after enqueue"
[ ! -d "$TMP_MARKERS_DIR/.radio_inflight_45_news" ] || fail "native news success must release inflight marker"

echo "ok"

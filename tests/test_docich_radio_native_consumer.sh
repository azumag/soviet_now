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
elif mode == "hold":
    payload = {"status": "low_confidence", "scope": "unknown"}
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

echo "ok"

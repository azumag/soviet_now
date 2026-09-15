#!/bin/bash
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

source "$ROOT/core/helpers.sh"
source "$ROOT/broadcast/radio_engine.sh"
source "$ROOT/broadcast/comment_quality.sh"

ok=0
fail=0
pass() { echo "ok - $1"; ok=$((ok + 1)); }
not_ok() { echo "not ok - $1"; fail=$((fail + 1)); }

cat >"$TMP/docich" <<'SH'
#!/bin/bash
[ "${1:-}" = "speech-quality" ] || exit 2
[ "${2:-}" = "--profile" ] || exit 2
[ "${3:-}" = "comment" ] || exit 2
cat >/dev/null
exit "${FAKE_DOCICH_RC:-0}"
SH
chmod +x "$TMP/docich"

export DOCICH_BIN="$TMP/docich"
export FAKE_DOCICH_RC=0
if _is_valid_comment_talk 'これは通常の返信です。'; then
	pass 'docich quality validator success is accepted'
else
	not_ok 'docich quality validator success is accepted'
fi

export FAKE_DOCICH_RC=1
if _is_valid_comment_talk 'これは通常の返信です。'; then
	not_ok 'docich quality rejection blocks otherwise valid reply'
else
	pass 'docich quality rejection blocks otherwise valid reply'
fi

# rc=2 represents an older docich that does not yet expose speech-quality.
# The bridge must fall back instead of treating every reply as invalid.
export FAKE_DOCICH_RC=2
long_bad='同志A、これはかなり面白い動きなので次の展開がどう変わるのか落ち着いて最後まで見届けたいと思っています。'
if _is_valid_comment_talk "$long_bad"; then
	not_ok 'fallback rejects a >30-char Japanese run without a comma'
else
	pass 'fallback rejects a >30-char Japanese run without a comma'
fi

long_good='同志A、これはかなり面白い動きなので、次の展開がどう変わるのか落ち着いて見届けたいと思っています。'
if _is_valid_comment_talk "$long_good"; then
	pass 'fallback accepts long Japanese prose with breathing commas'
else
	not_ok 'fallback accepts long Japanese prose with breathing commas'
fi

if grep -q '一文が30文字を超える場合' "$ROOT/prompts/speech_vocabulary_rule.md" && \
   grep -q '読点から次の読点・文末までが30文字を超えない' "$ROOT/prompts/speech_vocabulary_rule.md"; then
	pass 'generation prompt contains the punctuation contract'
else
	not_ok 'generation prompt contains the punctuation contract'
fi

echo "1..$((ok + fail))"
[ "$fail" -eq 0 ]

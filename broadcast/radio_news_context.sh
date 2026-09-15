#!/bin/bash
# broadcast/radio_news_context.sh - keep news provenance as non-spoken model context
#
# radio_news.sh intentionally strips spoken attribution from viewer-facing output.
# The model still needs bounded provenance/freshness metadata to judge recency and
# source context.  Wrap the existing block preparation instead of duplicating its
# filtering/sorting policy, then append a clearly non-spoken metadata line.

if declare -F _prepare_news_prompt_blocks >/dev/null && \
   ! declare -F _prepare_news_prompt_blocks_without_internal_metadata >/dev/null; then
	eval "$(declare -f _prepare_news_prompt_blocks | sed '1s/_prepare_news_prompt_blocks/_prepare_news_prompt_blocks_without_internal_metadata/')"
fi

if declare -F _prepare_news_prompt_blocks_without_internal_metadata >/dev/null; then
	_prepare_news_prompt_blocks() {
		local blocks_text="$1" prepared rc
		prepared="$(_prepare_news_prompt_blocks_without_internal_metadata "$blocks_text")"
		rc=$?
		[ "$rc" -eq 0 ] || return "$rc"

		python3 - "$prepared" <<'PY'
import datetime
import json
import re
import sys

raw = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    with open("tmp/news_meta.json", encoding="utf-8") as handle:
        meta = json.load(handle)
except Exception:
    meta = {}
if not isinstance(meta, dict):
    meta = {}


def one_line(value, limit):
    text = str(value or "")
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def published_at(item):
    explicit = one_line(item.get("published_at"), 80)
    if explicit:
        return explicit
    try:
        stamp = int(item.get("published_ts", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    try:
        return datetime.datetime.fromtimestamp(
            stamp, tz=datetime.timezone.utc
        ).isoformat().replace("+00:00", "Z")
    except (OverflowError, OSError, ValueError):
        return ""

blocks = []
current = []
for line in raw.splitlines():
    if line.startswith("■ "):
        if current:
            blocks.append(current)
        current = [line]
    elif current:
        current.append(line)
if current:
    blocks.append(current)

out = []
for block in blocks:
    title = block[0][2:].strip() if block and block[0].startswith("■ ") else ""
    item = meta.get(title, {})
    if not isinstance(item, dict):
        item = {}
    source = one_line(item.get("source"), 120) or "不明"
    published = published_at(item) or "不明"
    block = list(block)
    block.append(
        f"【内部メタ情報・読み上げ禁止】媒体={source} / 公開日時={published}"
    )
    out.append("\n".join(block).rstrip())

print("\n\n".join(out))
PY
	}
fi

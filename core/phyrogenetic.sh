# core/phyrogenetic.sh - 進化系統樹の記録・投稿


refresh_phyrogenetic_tree() {
	local pending_args=("$@")
	if python3 generate_phyrogenetic_tree.py --output "$PHYROGENETIC_TREE_FILE" "${pending_args[@]}"; then
		log "[PHYLO] updated $PHYROGENETIC_TREE_FILE"
		return 0
	fi
	log "[PHYLO] failed to update $PHYROGENETIC_TREE_FILE"
	return 1
}

_summarize_strategy_diff_for_phylo() {
	python3 -c "$(cat <<'PY'
import re
import sys

diff_text = sys.stdin.read()
added = []
removed = []

def normalize(text: str) -> str:
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text[:180]

for raw in diff_text.splitlines():
    if raw.startswith(('+++', '---', '@@')):
        continue
    if not raw or raw[0] not in '+-':
        continue
    body = raw[1:].strip()
    if not body:
        continue
    if body.startswith('#'):
        body = re.sub(r'^#+\s*', '', body).strip()
    if body.startswith(('refs:', '===', 'diff --git')):
        continue
    if raw[0] == '+':
        added.append(normalize(body))
    else:
        removed.append(normalize(body))

lines = []
for item in added:
    if item and item not in lines:
        lines.append(item)
    if len(lines) >= 6:
        break

if len(lines) < 4:
    for item in removed:
        note = f"removed: {item}"
        if item and note not in lines:
            lines.append(note)
        if len(lines) >= 6:
            break

print("\n".join(lines[:6]), end="")
PY
)"
}

_extract_rollback_analysis_for_phylo() {
	local analysis_file="${1:-$ROLLBACK_ANALYSIS_FILE}"
	[ -f "$analysis_file" ] || return 0
	python3 - "$analysis_file" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="ignore")

sections = {
    "## Why Rollback Triggered": 4,
    "## Defeat Delta": 3,
    "## Next Improve Focus": 3,
}

out = []
for header, limit in sections.items():
    part = text.split(header, 1)
    if len(part) != 2:
        continue
    block = part[1].split("\n## ", 1)[0]
    count = 0
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        out.append(line[2:].strip())
        count += 1
        if count >= limit:
            break

print("\n".join(out[:8]), end="")
PY
}

_rollback_chat_reason_from_analysis() {
	local analysis_file="${1:-$ROLLBACK_ANALYSIS_FILE}"
	[ -f "$analysis_file" ] || return 0
	python3 - "$analysis_file" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="ignore")

trigger = ""
for raw in text.splitlines():
    if raw.startswith("- trigger:"):
        trigger = raw.split(":", 1)[1].strip()
        break

def pct(raw):
    try:
        return f"{float(raw) * 100:.1f}%"
    except Exception:
        return ""

if "stage_achievement_regression" in trigger:
    target = ""
    for pattern in (r"target_type=(\d+)", r"stage_type(\d+)_achievement_gate"):
        m = re.search(pattern, trigger)
        if m:
            target = m.group(1)
            break
    target_rate = ""
    min_rate = ""
    sample_n = ""
    m = re.search(r"target_rate=([0-9.]+)", trigger)
    if m:
        target_rate = pct(m.group(1))
    m = re.search(r"min_rate=([0-9.]+)", trigger)
    if m:
        min_rate = pct(m.group(1))
    m = re.search(r"sample_n=(\d+)", trigger)
    if m:
        sample_n = m.group(1)
    parts = []
    if target:
        parts.append(f"Type{target}到達ゲート未達")
    else:
        parts.append("段階到達ゲート未達")
    if target_rate:
        parts.append(f"直近到達率{target_rate}")
    if min_rate:
        parts.append(f"要求{min_rate}")
    if sample_n:
        parts.append(f"n={sample_n}")
    print(" / ".join(parts)[:160], end="")
    raise SystemExit

part = text.split("## Why Rollback Triggered", 1)
if len(part) == 2:
    block = part[1].split("\n## ", 1)[0]
    for raw in block.splitlines():
        line = raw.strip()
        if not line.startswith("- "):
            continue
        reason = line[2:].strip()
        if reason:
            print(reason[:160], end="")
            raise SystemExit

if trigger:
    print(trigger[:160], end="")
PY
}

append_phyrogenetic_event() {
	local event_type="$1" from_hash="$2" to_hash="$3" game_num="$4" scores="$5" summary_text="$6" analysis_text="$7"
	[ -n "$event_type" ] || return 0
	[ -n "$from_hash" ] || return 0
	[ -n "$to_hash" ] || return 0
	PHYLO_EVENT_SUMMARY="$summary_text" \
		PHYLO_EVENT_ANALYSIS="$analysis_text" \
		python3 - "$PHYROGENETIC_EVENTS_FILE" "$event_type" "$from_hash" "$to_hash" "$game_num" "$scores" <<'PY'
import json
import os
import sys
import time
from pathlib import Path

event_file, event_type, from_hash, to_hash, game_num, scores = sys.argv[1:7]
summary = [ln.strip() for ln in os.environ.get("PHYLO_EVENT_SUMMARY", "").splitlines() if ln.strip()]
analysis = [ln.strip() for ln in os.environ.get("PHYLO_EVENT_ANALYSIS", "").splitlines() if ln.strip()]

payload = {
    "recorded_at": int(time.time()),
    "event_type": event_type,
    "from_hash": from_hash,
    "to_hash": to_hash,
    "game_num": str(game_num or ""),
    "scores": str(scores or ""),
    "summary_lines": summary[:8],
    "analysis_lines": analysis[:10],
    "source": "runtime",
}

path = Path(event_file)
path.parent.mkdir(parents=True, exist_ok=True)
with path.open("a", encoding="utf-8") as f:
    json.dump(payload, f, ensure_ascii=False)
    f.write("\n")
PY
}

_extract_news_source_name() {
	local title="$1"
	[ -f "tmp/news_meta.json" ] || return 0
	python3 - "$title" <<'PY'
import json
import sys

title = sys.argv[1] if len(sys.argv) > 1 else ""
try:
    with open("tmp/news_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    raise SystemExit(0)

item = meta.get(title, {})
source = item.get("source", "")
if source:
    print(source)
PY
}

_build_cc_attribution_text() {
	local title="$1"
	local meta_path="${2:-tmp/news_meta.json}"
	[ -f "$meta_path" ] || return 0
	python3 - "$title" "$meta_path" <<'PY'
import json
import re
import sys
import unicodedata

title = sys.argv[1] if len(sys.argv) > 1 else ""
meta_path = sys.argv[2] if len(sys.argv) > 2 else "tmp/news_meta.json"
try:
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    raise SystemExit(0)

item = meta.get(title)
if not item:
    raise SystemExit(0)

license_name = item.get("license")
if not license_name:
    raise SystemExit(0)

parts = ["[NEWS] " + title]
author = (item.get("author") or "").strip()
source = (item.get("source") or "").strip()
source_key = (item.get("source_key") or "").strip()
normalized_author = unicodedata.normalize("NFKC", author or "")
normalized_author = re.sub(r"\s+", "", normalized_author)
if normalized_author in {"トモモ", "背後のトモモ"} and (
    source_key.startswith("wikinews") or source == "ウィキニュース" or source.startswith("Wikinews")
):
    author = ""
if author:
    parts.append("by " + author)
if source:
    parts.append(source)
url = (item.get("url") or "").strip()
if url:
    parts.append(url)
parts.append(f"({license_name})")
print(" | ".join(parts))
PY
}

_append_cc_post_log() {
	local status="$1" detail="$2" cc_text="$3"
	mkdir -p "$(dirname "$CC_POST_LOG_FILE")" 2>/dev/null || true
	{
		printf '[%s] %s' "$(date '+%Y-%m-%d %H:%M:%S')" "$status"
		[ -n "$detail" ] && printf ' %s' "$detail"
		printf ' | %s\n' "$cc_text"
	} >>"$CC_POST_LOG_FILE" 2>/dev/null || true
	if [ -f "$CC_POST_LOG_FILE" ] && [ "$(wc -l < "$CC_POST_LOG_FILE")" -gt 200 ]; then
		tail -200 "$CC_POST_LOG_FILE" >"${CC_POST_LOG_FILE}.tmp" && mv "${CC_POST_LOG_FILE}.tmp" "$CC_POST_LOG_FILE"
	fi
}

_append_phyrogenetic_chat_post_log() {
	local status="$1" detail="$2" chat_text="$3"
	mkdir -p "$(dirname "$PHYROGENETIC_CHAT_POST_LOG_FILE")" 2>/dev/null || true
	{
		printf '[%s] %s' "$(date '+%Y-%m-%d %H:%M:%S')" "$status"
		[ -n "$detail" ] && printf ' %s' "$detail"
		printf ' | %s\n' "$chat_text"
	} >>"$PHYROGENETIC_CHAT_POST_LOG_FILE" 2>/dev/null || true
	if [ -f "$PHYROGENETIC_CHAT_POST_LOG_FILE" ] && [ "$(wc -l < "$PHYROGENETIC_CHAT_POST_LOG_FILE")" -gt 200 ]; then
		tail -200 "$PHYROGENETIC_CHAT_POST_LOG_FILE" >"${PHYROGENETIC_CHAT_POST_LOG_FILE}.tmp" && mv "${PHYROGENETIC_CHAT_POST_LOG_FILE}.tmp" "$PHYROGENETIC_CHAT_POST_LOG_FILE"
	fi
}

_post_phyrogenetic_tree_link_to_chat() {
	local event_type="$1" before_hash="$2" after_hash="$3" commit_hash="${4:-}"
	[ -n "$PHYROGENETIC_TREE_URL" ] || return 0
	local head_commit last_commit action before_short after_short transition chat_text
	local target_hash detail_info detail_label detail_anchor tree_url rollback_reason reason_suffix
	head_commit="$commit_hash"
	[ -n "$head_commit" ] || head_commit=$(git rev-parse HEAD 2>/dev/null || true)
	[ -n "$head_commit" ] || return 0
	last_commit=$(cat "$LAST_PHYROGENETIC_CHAT_COMMIT_FILE" 2>/dev/null || true)
	if [ -n "$last_commit" ] && [ "$last_commit" = "$head_commit" ]; then
		return 0
	fi
	case "$event_type" in
	improve) action="戦略更新" ;;
	rollback) action="戦略ロールバック" ;;
	*) action="戦略切り替え" ;;
	esac
	before_short="${before_hash:0:8}"
	after_short="${after_hash:0:8}"
	transition=""
	if [ -n "$before_short" ] && [ -n "$after_short" ] && [ "$before_short" != "$after_short" ]; then
		transition=" ${before_short}->${after_short}"
	fi
	target_hash="${after_hash:-$before_hash}"
	tree_url="$PHYROGENETIC_TREE_URL"
	if [ -n "$target_hash" ]; then
		local timeout_bin="" timeout_sec="${PHYROGENETIC_DETAIL_ANCHOR_TIMEOUT_SEC:-5}"
		if command -v timeout >/dev/null 2>&1; then
			timeout_bin="timeout"
		elif command -v gtimeout >/dev/null 2>&1; then
			timeout_bin="gtimeout"
		fi
		case "$timeout_sec" in
		''|*[!0-9]*) timeout_sec=5 ;;
		esac
		if [ -n "$timeout_bin" ]; then
			detail_info=$("$timeout_bin" "$timeout_sec" python3 generate_phyrogenetic_tree.py --print-detail-anchor-for "$target_hash" 2>/dev/null || true)
		else
			detail_info=$(python3 generate_phyrogenetic_tree.py --print-detail-anchor-for "$target_hash" 2>/dev/null || true)
		fi
		if [ -n "$detail_info" ]; then
			IFS=$'\t' read -r detail_label detail_anchor <<EOF
$detail_info
EOF
			if [ -n "$detail_label" ] && [ -n "$detail_anchor" ]; then
				tree_url="${PHYROGENETIC_TREE_URL}#${detail_anchor}"
			fi
		fi
	fi
	if [ -n "$detail_label" ]; then
		chat_text="${action}${transition}。系統樹はこちら(${detail_label}): ${tree_url}"
	else
		chat_text="${action}${transition}。系統樹はこちら: ${tree_url}"
	fi
	if [ "$event_type" = "rollback" ]; then
		rollback_reason=$(_rollback_chat_reason_from_analysis "$ROLLBACK_ANALYSIS_FILE" 2>/dev/null || true)
		if [ -n "$rollback_reason" ]; then
			reason_suffix="理由: ${rollback_reason}。"
			chat_text="${action}${transition}。${reason_suffix}系統樹はこちら"
			if [ -n "$detail_label" ]; then
				chat_text="${chat_text}(${detail_label})"
			fi
			chat_text="${chat_text}: ${tree_url}"
		fi
	fi
	enqueue_chat_message "$chat_text" "phyrogenetic"
	mkdir -p "$(dirname "$LAST_PHYROGENETIC_CHAT_COMMIT_FILE")" 2>/dev/null || true
	printf '%s\n' "$head_commit" >"$LAST_PHYROGENETIC_CHAT_COMMIT_FILE"
	_append_phyrogenetic_chat_post_log "QUEUED" "commit=${head_commit:0:8} event=$event_type" "$chat_text"
}

_post_pending_phyrogenetic_tree_link_to_chat_if_any() {
	local latest_line latest_commit latest_subject event_type
	latest_line=$(git log --format='%H|%s' --grep='^eloop Improve after' --grep='^eloop Auto-revert:' -n 1 2>/dev/null | head -n 1)
	[ -n "$latest_line" ] || return 0
	latest_commit="${latest_line%%|*}"
	latest_subject="${latest_line#*|}"
	[ -n "$latest_commit" ] || return 0
	case "$latest_subject" in
	eloop\ Improve\ after*) event_type="improve" ;;
	*) event_type="rollback" ;;
	esac
	_post_phyrogenetic_tree_link_to_chat "$event_type" "" "" "$latest_commit"
}

_post_cc_text_to_chat() {
	local cc_text="$1"
	[ -n "$cc_text" ] || return 0
	enqueue_chat_message "$cc_text" "phyrogenetic_cc"
	_append_cc_post_log "QUEUED" "" "$cc_text"
}

_post_cc_attribution_to_chat() {
	local title="$1"
	local meta_path="${2:-tmp/news_meta.json}"
	local cc_text
	cc_text=$(_build_cc_attribution_text "$title" "$meta_path")
	[ -n "$cc_text" ] || return 0
	_post_cc_text_to_chat "$cc_text"
}

# broadcast/radio_news.sh - ニュース取得・フィルタ・再生


# --- AIスパム判定 (opencode glmflash) ---
_news_ai_spam_check() {
	local title="$1" block="$2"
	# タイトル+本文冒頭をAIに判定させる
	local body_excerpt
	body_excerpt=$(printf '%s' "$block" | head -n 5 | tail -n +2 | head -c 300)

	local verdict
	verdict=$(opencode run --agent=glmflash --format=json \
		"以下の記事がニュースとして紹介する価値があるか判定してください。
宣伝、広告、アフィリエイト、プロモーションコード紹介、商品レビュー偽装、SEOスパム、企業PR記事であれば SPAM と答えてください。
正当な報道・ニュース・時事であれば NEWS と答えてください。
SPAM か NEWS の1単語だけ答えてください。

タイトル: ${title}
本文冒頭: ${body_excerpt}" 2>/dev/null \
		| grep '"type":"text"' \
		| python3 -c "import json,sys;[print(json.loads(l).get('part',{}).get('text','')) for l in sys.stdin]" 2>/dev/null \
		| tr -d '[:space:]')

	if [ "$verdict" = "SPAM" ]; then
		log "[NEWS:SPAM] AI判定: SPAM → ${title}"
		return 0  # spam detected
	fi
	log "[NEWS:SPAM] AI判定: ${verdict:-UNKNOWN}(PASS) → ${title}"
	return 1  # not spam
}

_news_title_key() {
	local title="$1"
	python3 - "$title" <<'PY'
import re
import sys
import unicodedata

s = sys.argv[1] if len(sys.argv) > 1 else ""
s = unicodedata.normalize("NFKC", s).strip().lower()
s = re.sub(r'[\s\u3000]+', '', s)
s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
print(s[:240])
PY
}

_news_topic_key() {
	local title="$1"
	python3 - "$title" <<'PY'
import re
import sys
import unicodedata

title = sys.argv[1] if len(sys.argv) > 1 else ""
s = unicodedata.normalize("NFKC", title).strip().lower()
s = re.sub(r'【[^】]*】', ' ', s)
s = re.sub(r'\[[^\]]*\]', ' ', s)
parts = [p for p in re.split(r'[\s\u3000・／/|｜:：,，、。!！?？]+', s) if p]
head = parts[0] if parts else s
head = re.sub(r'^(速報|続報|解説|独自|動画|写真|社説|論説)', '', head)
head = re.sub(r'[0-9０-９]+', '', head)

mk = re.match(r'([ァ-ヶー]{3,})', head)
if mk:
    print(mk.group(1)[:32])
    raise SystemExit(0)

ma = re.match(r'([a-z]{3,})', head)
if ma:
    print(ma.group(1)[:32])
    raise SystemExit(0)

norm = unicodedata.normalize("NFKC", head)
norm = re.sub(r'[\s\u3000]+', '', norm)
norm = ''.join(ch for ch in norm if unicodedata.category(ch)[0] not in ('P', 'S'))
norm = norm.replace("yahooニュース", "").replace("yahoo!ニュース", "")
print(norm[:8])
PY
}

_filter_unread_news_blocks() {
	local news_tmp
	news_tmp=$(mktemp /tmp/eloop_news_blocks_XXXXXXXX)
	cat >"$news_tmp"
	python3 - "$PAST_NEWS_READ" "$PAST_NEWS_READ_KEYS" "$PAST_NEWS_TOPIC_KEYS" "$PAST_NEWS_URL_HASHES" "$news_tmp" <<'PY'
import hashlib
import json
import os
import re
import sys
import unicodedata

past_title_file = sys.argv[1]
past_key_file = sys.argv[2]
past_topic_key_file = sys.argv[3]
past_url_hash_file = sys.argv[4]
news_file = sys.argv[5]
news_text = ""
if os.path.exists(news_file):
    with open(news_file, encoding="utf-8", errors="ignore") as f:
        news_text = f.read()
try:
    with open("tmp/news_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    meta = {}

def key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]

def url_hash_for_title(title: str) -> str:
    item = meta.get(title, {}) if isinstance(meta, dict) else {}
    url = (item.get("url") or "").strip()
    if not url:
        return ""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()

def topic_key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'【[^】]*】', ' ', s)
    s = re.sub(r'\[[^\]]*\]', ' ', s)
    parts = [p for p in re.split(r'[\s\u3000・／/|｜:：,，、。!！?？]+', s) if p]
    head = parts[0] if parts else s
    head = re.sub(r'^(速報|続報|解説|独自|動画|写真|社説|論説)', '', head)
    head = re.sub(r'[0-9０-９]+', '', head)
    head = re.sub(r'^(高市総理は|岸田総理は|石破総理は|首相は|大統領は|президент)', '', head)

    m = re.match(r'([ァ-ヶー]{3,})', head)
    if m:
        return m.group(1)[:32]
    m = re.match(r'([a-z]{3,})', head)
    if m:
        return m.group(1)[:32]

    k = key(head)
    if len(k) < 6:
        return ''
    return k[:16]

past_keys = set()
if os.path.exists(past_title_file):
    for ln in open(past_title_file, encoding="utf-8", errors="ignore"):
        t = ln.strip()
        if not t:
            continue
        k = key(t)
        if k:
            past_keys.add(k)
if os.path.exists(past_key_file):
    for ln in open(past_key_file, encoding="utf-8", errors="ignore"):
        k = ln.strip()
        if k:
            past_keys.add(k)

past_topic_keys = set()
if os.path.exists(past_topic_key_file):
    for ln in open(past_topic_key_file, encoding="utf-8", errors="ignore"):
        k = ln.strip()
        if k:
            past_topic_keys.add(k)

past_url_hashes = set()
if os.path.exists(past_url_hash_file):
    for ln in open(past_url_hash_file, encoding="utf-8", errors="ignore"):
        k = ln.strip()
        if k:
            past_url_hashes.add(k)

blocks = []
current = []
for line in news_text.splitlines():
    if line.startswith("■ "):
        if current:
            blocks.append(current)
        current = [line]
    elif current:
        current.append(line)
if current:
    blocks.append(current)

seen = set()
seen_topics = set()
seen_url_hashes = set()
out = []
for b in blocks:
    title = b[0][2:].strip()
    k = key(title)
    tk = topic_key(title)
    uh = url_hash_for_title(title)
    if not k:
        continue
    if k in seen:
        continue
    if tk and tk in seen_topics:
        continue
    if uh and uh in seen_url_hashes:
        continue
    if k in past_keys:
        continue
    if tk and tk in past_topic_keys:
        continue
    if uh and uh in past_url_hashes:
        continue
    seen.add(k)
    if tk:
        seen_topics.add(tk)
    if uh:
        seen_url_hashes.add(uh)
    out.append("\n".join(b).rstrip())

print("\n\n".join(out))
PY
	rm -f "$news_tmp"
}

_resolve_selected_news_title() {
	local selected_title="$1" news_file="$2"
	python3 - "$selected_title" "$news_file" <<'PY'
import os
import re
import sys
import unicodedata

selected = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
news_file = sys.argv[2] if len(sys.argv) > 2 else ""

def key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]

titles = []
if os.path.exists(news_file):
    with open(news_file, encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("■ "):
                titles.append(line[2:].strip())

if not selected:
    print("")
    raise SystemExit(0)
if not titles:
    print(selected)
    raise SystemExit(0)

sel_key = key(selected)
for t in titles:
    if t.strip() == selected:
        print(t)
        raise SystemExit(0)
for t in titles:
    if key(t) == sel_key and sel_key:
        print(t)
        raise SystemExit(0)
for t in titles:
    tk = key(t)
    if sel_key and (sel_key in tk or tk in sel_key):
        print(t)
        raise SystemExit(0)

print(selected)
PY
}

_news_source_name_for_title() {
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
source = (item.get("source") or "").strip()
if source:
    print(source)
PY
}

_news_url_hash_for_title_meta() {
	local title="$1" meta_file="${2:-tmp/news_meta.json}"
	[ -f "$meta_file" ] || return 0
	python3 - "$title" "$meta_file" <<'PY'
import hashlib
import json
import sys

title = sys.argv[1] if len(sys.argv) > 1 else ""
meta_file = sys.argv[2] if len(sys.argv) > 2 else "tmp/news_meta.json"
try:
    with open(meta_file, encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    raise SystemExit(0)

item = meta.get(title, {})
url = (item.get("url") or "").strip()
if url:
    print(hashlib.sha1(url.encode("utf-8")).hexdigest())
PY
}

_news_url_hash_for_title() {
	_news_url_hash_for_title_meta "$1" "tmp/news_meta.json"
}

_news_source_key_from_name() {
	local name="$1"
	case "$name" in
		"ウィキニュース"|wikinews|Wikinews) echo "wikinews" ;;
		Wikinews\(*) echo "wikinews" ;;
		wikinews_*) echo "wikinews" ;;
		"Global Voices"|globalvoices|GlobalVoices) echo "globalvoices" ;;
		*) echo "" ;;
	esac
}

_append_news_read_source() {
	local source_key="$1"
	[ -n "$source_key" ] || return 0
	echo "$source_key" >>"$PAST_NEWS_READ_SOURCES"
	tail -400 "$PAST_NEWS_READ_SOURCES" >"${PAST_NEWS_READ_SOURCES}.tmp" && mv "${PAST_NEWS_READ_SOURCES}.tmp" "$PAST_NEWS_READ_SOURCES"
}

_append_news_read_url_hash() {
	local url_hash="$1"
	[ -n "$url_hash" ] || return 0
	echo "$url_hash" >>"$PAST_NEWS_URL_HASHES"
	tail -"${PAST_NEWS_URL_HASHES_KEEP:-500}" "$PAST_NEWS_URL_HASHES" >"${PAST_NEWS_URL_HASHES}.tmp" && \
		mv "${PAST_NEWS_URL_HASHES}.tmp" "$PAST_NEWS_URL_HASHES"
}

_prepare_news_prompt_blocks() {
	local blocks_text="$1"
	python3 - "$PAST_NEWS_READ_SOURCES" "$blocks_text" <<'PY'
import json
import os
import sys
from collections import Counter

source_hist_path = sys.argv[1]
raw = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    with open("tmp/news_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    meta = {}

pref_order = {"wikinews": 0, "globalvoices": 1}
def _name_to_key(name):
    if name == "ウィキニュース" or name.startswith("Wikinews"):
        return "wikinews"
    return {"Global Voices": "globalvoices"}.get(name, "")
display = {"wikinews": "ウィキニュース", "globalvoices": "Global Voices"}
lang_labels = {
    "ja": "", "en": " [英語]", "fr": " [フランス語]", "ru": " [ロシア語]",
    "de": " [ドイツ語]", "ar": " [アラビア語]", "cs": " [チェコ語]",
    "eo": " [エスペラント]", "fi": " [フィンランド語]", "he": " [ヘブライ語]",
    "pl": " [ポーランド語]", "uk": " [ウクライナ語]", "zh": " [中国語]",
}

hist = []
if os.path.exists(source_hist_path):
    with open(source_hist_path, encoding="utf-8", errors="ignore") as f:
        hist = [ln.strip() for ln in f if ln.strip()]
recent = hist[-12:]
counts = Counter(recent)

blocks = []
current = []
for line in raw.splitlines():
    if line.startswith("■ "):
        if current:
            blocks.append(current)
        current = [line.rstrip()]
    elif current:
        current.append(line.rstrip())
if current:
    blocks.append(current)

def block_title(block):
    return block[0][2:].strip() if block and block[0].startswith("■ ") else ""

def block_source_name(block):
    title = block_title(block)
    item = meta.get(title, {})
    return (item.get("source") or "").strip()

def block_source_key(block):
    return _name_to_key(block_source_name(block))

def block_published_ts(block):
    title = block_title(block)
    item = meta.get(title, {})
    try:
        return int(item.get("published_ts", 0) or 0)
    except Exception:
        return 0

def block_priority(block):
    key = block_source_key(block)
    return (-block_published_ts(block), counts.get(key, 0), pref_order.get(key, 99), block_title(block))

blocks.sort(key=block_priority)

out_blocks = []
for block in blocks:
    title = block_title(block)
    source_name = block_source_name(block)
    item_meta = meta.get(title, {})
    lang = item_meta.get("lang", "ja")
    lang_tag = lang_labels.get(lang, f" [{lang}]") if lang != "ja" else ""
    if source_name:
        out_blocks.append("\n".join([block[0], f"出典: {source_name}{lang_tag}", *block[1:]]).rstrip())
    else:
        out_blocks.append("\n".join(block).rstrip())

print("\n\n".join(out_blocks))
PY
}

_random_pick_news_block() {
	local blocks_text="$1"
	python3 - "$PAST_NEWS_READ_SOURCES" "$blocks_text" <<'PY'
import os
import random
import sys
from collections import Counter

source_hist_path = sys.argv[1]
raw = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    import json
    with open("tmp/news_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    meta = {}

def _name_to_key(name):
    if name == "ウィキニュース" or name.startswith("Wikinews"):
        return "wikinews"
    return {"Global Voices": "globalvoices"}.get(name, "")

# Parse blocks
blocks = []
current = []
for line in raw.splitlines():
    if line.startswith("■ "):
        if current:
            blocks.append(current)
        current = [line.rstrip()]
    elif current:
        current.append(line.rstrip())
if current:
    blocks.append(current)

if not blocks:
    sys.exit(0)

# Weight by inverse source frequency (prefer underrepresented sources)
hist = []
if os.path.exists(source_hist_path):
    with open(source_hist_path, encoding="utf-8", errors="ignore") as f:
        hist = [ln.strip() for ln in f if ln.strip()]
recent = hist[-12:]
counts = Counter(recent)
published_values = []
for block in blocks:
    title = block[0][2:].strip() if block[0].startswith("■ ") else ""
    item = meta.get(title, {})
    try:
        published_values.append(int(item.get("published_ts", 0) or 0))
    except Exception:
        published_values.append(0)
newest_ts = max(published_values) if published_values else 0

weights = []
for block in blocks:
    title = block[0][2:].strip() if block[0].startswith("■ ") else ""
    item = meta.get(title, {})
    source_name = (item.get("source") or "").strip()
    source_key = _name_to_key(source_name)
    freq = counts.get(source_key, 0) if source_key else 0
    try:
        published_ts = int(item.get("published_ts", 0) or 0)
    except Exception:
        published_ts = 0
    if newest_ts > 0 and published_ts > 0:
        age_hours = max(0.0, (newest_ts - published_ts) / 3600.0)
        recency_weight = 1.0 / (1.0 + age_hours / 12.0)
    elif published_ts > 0:
        recency_weight = 1.0
    else:
        recency_weight = 0.25
    source_weight = 1.0 / (1 + freq)
    weights.append((recency_weight * 6.0) + source_weight)

chosen = random.choices(blocks, weights=weights, k=1)[0]
print("\n".join(chosen))
PY
}

_news_source_balance_hint() {
	local blocks_text="$1"
	python3 - "$PAST_NEWS_READ_SOURCES" "$blocks_text" <<'PY'
import json
import os
import sys
from collections import Counter

source_hist_path = sys.argv[1]
raw = sys.argv[2] if len(sys.argv) > 2 else ""

try:
    with open("tmp/news_meta.json", encoding="utf-8") as f:
        meta = json.load(f)
except Exception:
    meta = {}

def _name_to_key(name):
    if name == "ウィキニュース" or name.startswith("Wikinews"):
        return "wikinews"
    return {"Global Voices": "globalvoices"}.get(name, "")
label = {"wikinews": "ウィキニュース", "globalvoices": "Global Voices"}

hist = []
if os.path.exists(source_hist_path):
    with open(source_hist_path, encoding="utf-8", errors="ignore") as f:
        hist = [ln.strip() for ln in f if ln.strip()]
recent = hist[-12:]
counts = Counter(recent)

seen = []
for line in raw.splitlines():
    if not line.startswith("■ "):
        continue
    title = line[2:].strip()
    source_name = (meta.get(title, {}) or {}).get("source", "").strip()
    key = _name_to_key(source_name)
    if key and key not in seen:
        seen.append(key)

if not seen:
    raise SystemExit(0)

parts = [f"{label.get(k, k)}:{counts.get(k, 0)}" for k in seen]
under = sorted(seen, key=lambda k: (counts.get(k, 0), {"wikinews": 0, "globalvoices": 1}.get(k, 99)))
prefer = label.get(under[0], under[0])
print(f"直近12回のニュース出典件数: {', '.join(parts)}。内容が同程度なら最近少ない出典を優先。特に今回は {prefer} をやや優先。")
PY
}

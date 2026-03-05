#!/bin/bash
# lib/eloop_radio.sh - ラジオトーク・オーディオ管理

#=== ANSIエスケープ除去 ===

_strip_ansi() {
	perl -pe 's/\e\[[0-9;]*[a-zA-Z]//g; s/[\x00-\x09\x0b-\x0d\x0e-\x1f]//g' | tr -d '\r'
}

#=== opencode run を疑似TTY付きで実行 ===

_run_opencode_radio() {
	local agent="$1" prompt_file="$2"
	local raw_file
	raw_file=$(mktemp /tmp/eloop_radio_raw_XXXXXXXX)
	timeout "${RADIO_OPENCODE_TIMEOUT}" \
		script -q "$raw_file" bash -c "LC_ALL=en_US.UTF-8 OPENCODE_PERMISSION='$RADIO_OPENCODE_PERMISSION' opencode run --agent \"$agent\" \"\$(cat '$prompt_file')\" 2>&1" >/dev/null 2>&1
	local rc=$?
	if [ $rc -eq 124 ]; then
		# command substitution に混ざらないよう stderr に出す
		log "[RADIO] opencode timeout (${RADIO_OPENCODE_TIMEOUT}s, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	if [ $rc -ne 0 ]; then
		# 非タイムアウト失敗も本文扱いせず fallback へ渡す
		log "[RADIO] opencode failed (rc=$rc, agent=$agent)" >&2
		rm -f "$raw_file"
		return 1
	fi
	cat "$raw_file" |
		_strip_ansi |
		grep -v '^>' |
		grep -v '^\^D' |
		grep -v '^Script started on ' |
		grep -v '^Script done on ' |
		grep -v '^/[^ ]*$' |
		grep -v '^[[:space:]]*/Users/' |
		sed -E 's#</?(arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>##g' |
		sed '/^[[:space:]]*$/d'
	rm -f "$raw_file"
}

_run_claude_radio() {
	local prompt_file="$1"
	local prompt
	prompt=$(cat "$prompt_file" 2>/dev/null)
	if [ -z "$prompt" ]; then
		return 1
	fi
	# command substitution に混ざらないよう stderr に出す
	log "[RADIO] claude fallback (model=$RADIO_CLAUDE_MODEL)" >&2
	claude -p "$prompt" --model "$RADIO_CLAUDE_MODEL" 2>/dev/null
}

_clean_comment_talk() {
	printf '%s\n' "$1" |
		grep -Eiv '^[[:space:]]*(assistant|analysis|final|tool_call|tool_result)[[:space:]]*$' |
		grep -Eiv '^[[:space:]]*(agent|model|provider)[[:space:]]*[:=].*$' |
		grep -Eiv '^[[:space:]]*(zai|glmflash|sonnet|claude|opencode)[[:space:]]*$' |
		sed '/^[[:space:]]*$/d'
}

_is_valid_comment_talk() {
	local talk="$1"
	local compact
	compact=$(printf '%s' "$talk" | tr -d '[:space:]')
	[ ${#compact} -ge 24 ] || return 1
	printf '%s' "$talk" | grep -Eq '[。！？]' || return 1
	return 0
}

#=== ラジオトーク: 共通ヘルパー ===

_radio_time_context() {
	_rc_hour=$(date '+%H')
	_rc_time=$(date '+%H:%M')
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 9 ]; then
		_rc_period="早朝"
		_rc_mood="早朝放送。静かな時間帯に合わせて、寝ぼけた頭で毒が鈍い分、たまに本音が漏れる"
	elif [ "$_rc_hour" -ge 9 ] && [ "$_rc_hour" -lt 12 ]; then
		_rc_period="午前"
		_rc_mood="午前中の放送。人工知能はいつでも全力"
	elif [ "$_rc_hour" -ge 12 ] && [ "$_rc_hour" -lt 14 ]; then
		_rc_period="昼"
		_rc_mood="昼の放送。昼食後の時間帯で、眠気と戦いながらゲームを回す感じ。"
	elif [ "$_rc_hour" -ge 14 ] && [ "$_rc_hour" -lt 17 ]; then
		_rc_period="午後"
		_rc_mood="午後の放送。眠くなる時間帯。"
	elif [ "$_rc_hour" -ge 17 ] && [ "$_rc_hour" -lt 20 ]; then
		_rc_period="夕方"
		_rc_mood="夕方の放送。ちょっと詩的に"
	elif [ "$_rc_hour" -ge 20 ] && [ "$_rc_hour" -lt 23 ]; then
		_rc_period="夜"
		_rc_mood="夜の放送。"
	elif [ "$_rc_hour" -ge 23 ] || [ "$_rc_hour" -lt 2 ]; then
		_rc_period="深夜"
		_rc_mood="深夜放送。やけに饒舌になる"
	else
		_rc_period="未明"
		_rc_mood="未明の放送。哲学的に"
	fi
}

_radio_persona_block() {
	cat "$ELOOP_LIB_DIR/prompts/radio_persona.md"
}

_radio_output_rules() {
	local min_chars="$1" max_chars="$2"
	export min_chars max_chars
	envsubst '${min_chars} ${max_chars}' < "$ELOOP_LIB_DIR/prompts/radio_rules.md"
}

_radio_parse_output_to_files() {
	local body_file="$1" summary_file="$2" selected_news_file="$3"
	local parser_file
	parser_file=$(mktemp /tmp/eloop_radio_parser_XXXXXXXX)
	cat >"$parser_file" <<'PY'
import re
import sys
from pathlib import Path

body_path, summary_path, selected_path = sys.argv[1:4]
raw = sys.stdin.read().replace("\r", "")

raw = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw)
raw = re.sub(
    r"</?(?:arg_name|arg_value|think|analysis|final|assistant_response|tool_call|tool_result)[^>]*>",
    "",
    raw,
    flags=re.IGNORECASE,
)

lines = [line.strip() for line in raw.splitlines()]
clean_lines = []
for line in lines:
    if not line:
        continue
    if line.startswith("```"):
        continue
    if line == "^D":
        continue
    if re.fullmatch(r"/[^ ]*", line):
        continue
    if line.startswith("/Users/"):
        continue
    if re.fullmatch(r"</?[^>]+>", line):
        continue
    clean_lines.append(line)

def marker_positions(marker):
    return [idx for idx, line in enumerate(clean_lines) if line == marker]

summary_pos = marker_positions("===SUMMARY===")
selected_pos = marker_positions("===SELECTED_NEWS===")
main_lines = clean_lines[: selected_pos[0]] if selected_pos else clean_lines

selected_news = ""
if selected_pos:
    for line in clean_lines[selected_pos[0] + 1 :]:
        if not line or line.startswith("==="):
            continue
        selected_news = line
        break
selected_news = re.sub(r"</?[A-Za-z_][^>]*>", "", selected_news).strip()
selected_news = re.sub(r"\s+", " ", selected_news)[:240]

summary = ""
if summary_pos:
    summary_lines = []
    for line in main_lines[summary_pos[0] + 1 :]:
        if line.startswith("==="):
            break
        if not line:
            continue
        summary_lines.append(line)
        if len(summary_lines) >= 2:
            break
    if summary_lines:
        summary = " / ".join(summary_lines)
summary = re.sub(r"</?[A-Za-z_][^>]*>", "", summary).strip()
summary = re.sub(r"\s+", " ", summary)[:220]

segments = []
start = 0
for idx, line in enumerate(main_lines):
    if line == "===SUMMARY===":
        segments.append(main_lines[start:idx])
        start = idx + 1
segments.append(main_lines[start:])

def score_segment(seg):
    txt = " ".join(seg).strip()
    if not txt:
        return -1
    punct = len(re.findall(r"[。.!?！？]", txt))
    return len(txt) + punct * 80

body_lines = []
if segments:
    best = max(segments, key=score_segment)
    body_lines = [line for line in best if line and not line.startswith("===")]

if body_lines:
    head = body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        body_lines = body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(body_lines) >= 2:
        body_lines = body_lines[1:]

body = "\n".join(body_lines).strip()
body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

if len(body) < 100:
    used_before_summary = False
    if summary_pos and summary_pos[0] < len(main_lines):
        before_summary = [line for line in main_lines[: summary_pos[0]] if not line.startswith("===")]
        if before_summary:
            body = "\n".join(before_summary).strip()
            used_before_summary = True
    if len(body) < 100 and not used_before_summary:
        fallback_lines = [line for line in main_lines if not line.startswith("===")]
        body = "\n".join(fallback_lines).strip()
    body = re.sub(r"</?[A-Za-z_][^>]*>", "", body).strip()

clean_body_lines = [line.strip() for line in body.splitlines() if line.strip()]
if clean_body_lines:
    head = clean_body_lines[0]
    if ("," in head or "、" in head) and not re.search(r"[。.!?！？]", head):
        clean_body_lines = clean_body_lines[1:]
    elif head.count(",") + head.count("、") >= 4 and len(head) <= 180 and len(clean_body_lines) >= 2:
        clean_body_lines = clean_body_lines[1:]
body = "\n".join(clean_body_lines).strip()

body = re.sub(r"\n{3,}", "\n\n", body)
if len(body) > 12000:
    body = body[:12000]

Path(body_path).write_text(body, encoding="utf-8")
Path(summary_path).write_text(summary, encoding="utf-8")
Path(selected_path).write_text(selected_news, encoding="utf-8")
PY
	python3 "$parser_file" "$body_file" "$summary_file" "$selected_news_file"
	local rc=$?
	rm -f "$parser_file"
	return $rc
}

_radio_past_topics_block() {
	local past_topics=""
	if [ -f "$PAST_RADIO_TOPICS" ]; then
		past_topics=$(grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null | tail -80)
	fi
	echo "${past_topics:-まだ過去のトークはありません。自由に話してください。}"
}

_radio_dedup_text() {
	python3 -c "
import sys
text = sys.stdin.read()
lines = text.split('\n')
seen_repeat = 0
cut_at = len(lines)
for i in range(1, len(lines)):
    if lines[i].strip() and lines[i] == lines[i-1]:
        seen_repeat += 1
        if seen_repeat >= 3:
            cut_at = i - 2
            break
    else:
        seen_repeat = 0
from collections import Counter
chunk_size = 20
chunks = [text[i:i+chunk_size] for i in range(0, len(text)-chunk_size)]
freq = Counter(chunks)
repeat_phrase = None
for phrase, count in freq.most_common(1):
    if count >= 5 and len(phrase.strip()) > 5:
        repeat_phrase = phrase
        break
result = '\n'.join(lines[:cut_at])
if repeat_phrase:
    idx = 0
    for _ in range(3):
        idx = result.find(repeat_phrase, idx)
        if idx == -1:
            break
        idx += len(repeat_phrase)
    if idx > 0:
        result = result[:idx]
if len(result) > 10000:
    result = result[:10000]
print(result, end='')
	"
}

_sanitize_onair_text() {
	python3 -c "$(cat <<'PY'
import re
import sys

text = sys.stdin.read()
patterns = [
    (r'誰も(聞いて|見て)い(?:ない|ません)', 'みなさんに届くように'),
    (r'聞き手(?:が|は)?い(?:ない|ません)', '聞き手に届くように'),
    (r'リスナー(?:が|は)?い(?:ない|ません)', 'リスナーに届くように'),
    (r'視聴者(?:が|は)?い(?:ない|ません)', '視聴者に届くように'),
    (r'誰に向けてやってるのか', 'みなさんに向けて'),
    (r'過疎(?:配信|放送)?', 'この配信'),
    (r'無人(?:配信|放送)', '配信'),
    (r'誰もいない', 'みなさんがいる'),
]
out = text
for pat, repl in patterns:
    out = re.sub(pat, repl, out, flags=re.IGNORECASE)
sys.stdout.write(out)
PY
)"
}

_normalize_radio_tone() {
	python3 -c "
import re
import sys

text = sys.stdin.read()
out = text

rules = [
    (r'なんですよね(?=\\s|$|[。！？、])', 'なんです'),
    (r'なんですよ(?=\\s|$|[。！？、])', 'なんです'),
    (r'ですよね(?=\\s|$|[。！？、])', 'です'),
    (r'ですよ(?=\\s|$|[。！？、])', 'です'),
    (r'ますよね(?=\\s|$|[。！？、])', 'ます'),
    (r'ますね(?=\\s|$|[。！？、])', 'ます'),
    (r'ですね(?=\\s|$|[。！？、])', 'です'),
    (r'ですけどね(?=\\s|$|[。！？、])', 'ですけど'),
    (r'ますけどね(?=\\s|$|[。！？、])', 'ますけど'),
    (r'なんですけどね(?=\\s|$|[。！？、])', 'なんですけど'),
    (r'でしょうね(?=\\s|$|[。！？、])', 'でしょう'),
]
for pat, repl in rules:
    out = re.sub(pat, repl, out)
sys.stdout.write(out)
		"
}

_ensure_radio_intro() {
	local text="$1" corner_name="${2:-}"
	[ -z "$text" ] && return 1

	_radio_time_context
	local greet
	if [ "$_rc_hour" -ge 5 ] && [ "$_rc_hour" -lt 11 ]; then
		greet="おはようございます"
	elif [ "$_rc_hour" -ge 17 ] || [ "$_rc_hour" -lt 2 ]; then
		greet="こんばんは"
	else
		greet="こんにちは"
	fi

	local head
	head=$(printf '%s\n' "$text" | head -n 3)
	if printf '%s\n' "$head" | grep -Eq '現在時刻|[0-2][0-9]:[0-5][0-9]|おはよう|こんにちは|こんばんは'; then
		printf '%s' "$text"
		return 0
	fi

	local intro_line
	intro_line="${greet}、${_rc_period}の放送です。現在時刻は${_rc_time}です。"

	# ニュースはタイトル行を先頭に維持し、その直後に挨拶を補完
	if [ "$corner_name" = "news" ] && printf '%s\n' "$text" | head -n 1 | grep -Fq '今回取り上げるニュースタイトルは'; then
		local first_line rest
		first_line=$(printf '%s\n' "$text" | head -n 1)
		rest=$(printf '%s\n' "$text" | tail -n +2)
		printf '%s\n%s\n%s' "$first_line" "$intro_line" "$rest"
	else
		printf '%s\n%s' "$intro_line" "$text"
	fi
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

_filter_unread_news_blocks() {
	local news_tmp
	news_tmp=$(mktemp /tmp/eloop_news_blocks_XXXXXXXX)
	cat >"$news_tmp"
	python3 - "$PAST_NEWS_READ" "$PAST_NEWS_READ_KEYS" "$news_tmp" <<'PY'
import os
import re
import sys
import unicodedata

past_title_file = sys.argv[1]
past_key_file = sys.argv[2]
news_file = sys.argv[3]
news_text = ""
if os.path.exists(news_file):
    with open(news_file, encoding="utf-8", errors="ignore") as f:
        news_text = f.read()

def key(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).strip().lower()
    s = re.sub(r'[\s\u3000]+', '', s)
    s = ''.join(ch for ch in s if unicodedata.category(ch)[0] not in ('P', 'S'))
    s = s.replace("yahooニュース", "").replace("yahoo!ニュース", "")
    return s[:240]

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
out = []
for b in blocks:
    title = b[0][2:].strip()
    k = key(title)
    if not k:
        continue
    if k in seen:
        continue
    if k in past_keys:
        continue
    seen.add(k)
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

# 自分のコーナーの状態ファイルだけ安全に削除 (並列実行の競合防止)
_radio_clear_state() {
	local my_corner="$1"
	local current
	current=$(cat tmp/.radio_state 2>/dev/null) || return 0
	case "$current" in *":${my_corner}:"*) rm -f tmp/.radio_state ;; esac
}

_radio_generate_and_play() {
	local prompt_file="$1" game_num="$2" score="$3" corner_name="$4"
	shift 4
	local no_preempt=true
	while [ $# -gt 0 ]; do
		case "$1" in
		--no-preempt) no_preempt=true ;;
		esac
		shift
	done

	# 同一 game_num + corner の二重生成/二重再生を防止
	local done_marker="tmp/.radio_done_${game_num}_${corner_name}"
	if [ -f "$done_marker" ]; then
		log "[RADIO:${corner_name}] duplicate skip: already done for game=${game_num}"
		return 0
	fi
	local inflight_dir="tmp/.radio_inflight_${game_num}_${corner_name}"
	if ! mkdir "$inflight_dir" 2>/dev/null; then
		log "[RADIO:${corner_name}] duplicate skip: in-flight for game=${game_num}"
		return 0
	fi

	echo "generating:${corner_name}:$(date +%s)" > tmp/.radio_state
	log "[RADIO:${corner_name}] トーク生成中..."
	local talk
	talk=$(_run_opencode_radio "$RADIO_AGENT" "$prompt_file")
	if [ -z "$talk" ]; then
		talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$prompt_file")
	fi
	if [ -z "$talk" ]; then
		talk=$(_run_claude_radio "$prompt_file")
	fi
	rm -f "$prompt_file"

	if [ -z "$talk" ]; then
		log "[RADIO:${corner_name}] トーク生成失敗"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	local talk_body talk_summary selected_news parse_dir
	parse_dir=$(mktemp -d /tmp/eloop_radio_parse_XXXXXXXX)
	printf '%s' "$talk" | _radio_parse_output_to_files "$parse_dir/body.txt" "$parse_dir/summary.txt" "$parse_dir/selected_news.txt"
	talk_body=$(cat "$parse_dir/body.txt" 2>/dev/null)
	talk_summary=$(cat "$parse_dir/summary.txt" 2>/dev/null)
	selected_news=$(cat "$parse_dir/selected_news.txt" 2>/dev/null)
	rm -rf "$parse_dir"
	[ -z "$talk_summary" ] && talk_summary="(要約なし)"

	# ニュースコーナーの場合、選んだニュースを既読リストに記録
	if [ "$corner_name" = "news" ]; then
		if [ -n "$selected_news" ]; then
			local selected_key
			selected_news=$(_resolve_selected_news_title "$selected_news" "tmp/news.txt")
			selected_key=$(_news_title_key "$selected_news")
			if [ -z "$selected_key" ]; then
				log "[RADIO:news] 既読記録スキップ: タイトル解決失敗"
			elif grep -qxF "$selected_news" "$PAST_NEWS_READ" 2>/dev/null || grep -qxF "$selected_key" "$PAST_NEWS_READ_KEYS" 2>/dev/null; then
				log "[RADIO:news] 重複ニュース検出 → スキップ: ${selected_news}"
				_radio_clear_state "$corner_name"
				rmdir "$inflight_dir" 2>/dev/null || true
				return 1
			else
				echo "$selected_news" >>"$PAST_NEWS_READ"
				echo "$selected_key" >>"$PAST_NEWS_READ_KEYS"
				tail -60 "$PAST_NEWS_READ" >"${PAST_NEWS_READ}.tmp" && mv "${PAST_NEWS_READ}.tmp" "$PAST_NEWS_READ"
				tail -120 "$PAST_NEWS_READ_KEYS" >"${PAST_NEWS_READ_KEYS}.tmp" && mv "${PAST_NEWS_READ_KEYS}.tmp" "$PAST_NEWS_READ_KEYS"
				log "[RADIO:news] 既読記録: ${selected_news}"
			fi
		fi
	fi

	{
		[ -f "$PAST_RADIO_TOPICS" ] && grep -E '^\[[0-9]{2}:[0-9]{2}\] Game#[0-9]+ ' "$PAST_RADIO_TOPICS" 2>/dev/null || true
		echo "[$(date '+%H:%M')] Game#${game_num} ${score}pts [${corner_name}]: ${talk_summary}"
	} | tail -100 >"${PAST_RADIO_TOPICS}.tmp" && mv "${PAST_RADIO_TOPICS}.tmp" "$PAST_RADIO_TOPICS"

	# ニュースは選択タイトルを必ず先頭で読み上げる
	if [ "$corner_name" = "news" ] && [ -n "$selected_news" ]; then
		local title_line
		title_line="今回取り上げるニュースタイトルは「${selected_news}」です。"
		if ! printf '%s\n' "$talk_body" | head -n 2 | grep -Fq "$selected_news"; then
			talk_body="${title_line}
${talk_body}"
		fi
	fi

	local talk_body_parsed talk_body_sanitized talk_body_dedup
	talk_body_parsed="$talk_body"
	talk_body_sanitized=$(printf '%s' "$talk_body_parsed" | _sanitize_onair_text)
	talk_body_dedup=$(printf '%s' "$talk_body_sanitized" | _radio_dedup_text)

	# dedup が過剰に効いて短文化した場合は、まず非dedup本文に戻す
	if [ ${#talk_body_dedup} -lt 100 ] && [ ${#talk_body_sanitized} -ge 100 ]; then
		log "[RADIO:${corner_name}] dedup短縮が過剰 (${#talk_body_sanitized} -> ${#talk_body_dedup}字) → 非dedup本文を採用"
		talk_body="$talk_body_sanitized"
	else
		talk_body="$talk_body_dedup"
	fi

	# パーサ結果が短い場合は、生の出力から本文を再抽出して救済
	if [ ${#talk_body} -lt 100 ]; then
		local fallback_body
		fallback_body=$(printf '%s\n' "$talk" | sed '/^===SUMMARY===/,$d' | sed '/^===SELECTED_NEWS===/,$d')
		fallback_body=$(printf '%s' "$fallback_body" | _sanitize_onair_text)
		if [ ${#fallback_body} -ge 100 ]; then
			log "[RADIO:${corner_name}] 本文再抽出フォールバック採用 (${#fallback_body}字)"
			talk_body="$fallback_body"
		fi
	fi

	# 挨拶・時刻言及が抜けた出力を補完（ニュースはタイトル行を先頭維持）
	local talk_with_intro
	talk_with_intro=$(_ensure_radio_intro "$talk_body" "$corner_name")
	[ -n "$talk_with_intro" ] && talk_body="$talk_with_intro"
	talk_body=$(printf '%s' "$talk_body" | _normalize_radio_tone)

	if [ ${#talk_body} -lt 100 ]; then
		local debug_dump
		debug_dump="tmp/radio_short_${corner_name}_$(date +%s).txt"
		{
			echo "===RAW==="
			printf '%s\n' "$talk"
			echo
			echo "===PARSED==="
			printf '%s\n' "$talk_body_parsed"
			echo
			echo "===SANITIZED==="
			printf '%s\n' "$talk_body_sanitized"
			echo
			echo "===DEDUP==="
			printf '%s\n' "$talk_body_dedup"
		} >"$debug_dump"
		log "[RADIO:${corner_name}] WARNING: 本文が短すぎる(${#talk_body}字) → スキップ (dump: $debug_dump)"
		_radio_clear_state "$corner_name"
		rmdir "$inflight_dir" 2>/dev/null || true
		return 1
	fi

	# say待ちは say_enqueue.sh 内で行われるため、ここでは不要

	local talk_file
	talk_file=$(mktemp /tmp/eloop_radio_talk_XXXXXXXX)
	echo "$talk_body" >"$talk_file"
	echo "playing:${corner_name}:$(date +%s)" > tmp/.radio_state
	log "[RADIO:${corner_name}] ${#talk_body}字"
	if [ "$no_preempt" = true ]; then
		./say_enqueue.sh --no-preempt "$talk_file" "$RADIO_SAY_RATE" 0
	else
		./say_enqueue.sh "$talk_file" "$RADIO_SAY_RATE" 0
	fi
	rm -f "$talk_file"
	touch "$done_marker"
	# 古い重複防止マーカーを掃除（最新200件だけ保持）
	local old_markers
	old_markers=$(ls -1t tmp/.radio_done_* 2>/dev/null | tail -n +201 || true)
	if [ -n "$old_markers" ]; then
		echo "$old_markers" | xargs rm -f 2>/dev/null || true
	fi
	_radio_clear_state "$corner_name"
	rmdir "$inflight_dir" 2>/dev/null || true
	log "[RADIO:${corner_name}] トーク終了"
}

#=== ラジオトーク: テーマ選択 ===

_pick_radio_theme() {
	mapfile -t themes < "$ELOOP_LIB_DIR/data/radio_themes.txt"
	local past_themes_file="tmp/.past_radio_themes.txt"
	local available_themes=()
	local past_theme_list=""
	[ -f "$past_themes_file" ] && past_theme_list=$(cat "$past_themes_file")
	for t in "${themes[@]}"; do
		[ -z "$t" ] && continue
		local t_key="${t%%。*}"
		if ! echo "$past_theme_list" | grep -qF "$t_key"; then
			available_themes+=("$t")
		fi
	done
	if [ ${#available_themes[@]} -eq 0 ]; then
		available_themes=("${themes[@]}")
		>"$past_themes_file"
	fi
	local theme="${available_themes[$((RANDOM % ${#available_themes[@]}))]}"
	echo "${theme%%。*}" >>"$past_themes_file"
	tail -100 "$past_themes_file" >"${past_themes_file}.tmp" && mv "${past_themes_file}.tmp" "$past_themes_file"
	echo "$theme"
}

_pick_soviet_theme() {
	mapfile -t soviet_themes < "$ELOOP_LIB_DIR/data/radio_soviet_themes.txt"
	local past_soviet_file="tmp/.past_soviet_themes.txt"
	local available_soviet=()
	local past_soviet_list=""
	[ -f "$past_soviet_file" ] && past_soviet_list=$(cat "$past_soviet_file")
	for st in "${soviet_themes[@]}"; do
		[ -z "$st" ] && continue
		local st_key="${st%%。*}"
		[ "$st_key" = "$st" ] && st_key="${st%%を深掘り*}"
		if ! echo "$past_soviet_list" | grep -qF "$st_key"; then
			available_soviet+=("$st")
		fi
	done
	if [ ${#available_soviet[@]} -eq 0 ]; then
		available_soviet=("${soviet_themes[@]}")
		>"$past_soviet_file"
	fi
	local soviet_theme="${available_soviet[$((RANDOM % ${#available_soviet[@]}))]}"
	local soviet_key="${soviet_theme%%。*}"
	[ "$soviet_key" = "$soviet_theme" ] && soviet_key="${soviet_theme%%を深掘り*}"
	echo "$soviet_key" >>"$past_soviet_file"
	tail -60 "$past_soviet_file" >"${past_soviet_file}.tmp" && mv "${past_soviet_file}.tmp" "$past_soviet_file"
	echo "$soviet_theme"
}

#=== ラジオトーク: 5つのコーナー ===

start_radio_corner_theme() {
	local game_num="$1" score="$2"
	_radio_time_context
	local theme
	theme=$(_pick_radio_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood theme past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_theme.md" > "$prompt_file"

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "theme"
}

start_radio_corner_soviet() {
	local game_num="$1" score="$2"
	_radio_time_context
	local soviet_theme
	soviet_theme=$(_pick_soviet_theme)
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood soviet_theme past_topics game_num score
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_soviet.md" > "$prompt_file"

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "soviet"
}

start_radio_corner_news() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local news_headlines=""
	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		news_headlines=$(cat "tmp/news.txt")
	fi
	[ -z "$news_headlines" ] && return 1

	# 過去に読んだニュース見出しリスト
	local past_news_read=""
	[ -f "$PAST_NEWS_READ" ] && past_news_read=$(cat "$PAST_NEWS_READ")

	# 正規化キーで未読のみ抽出（表記揺れを吸収）
	local unread_news_headlines=""
	unread_news_headlines=$(printf '%s\n' "$news_headlines" | _filter_unread_news_blocks)
	if [ -z "$unread_news_headlines" ]; then
		log "[NEWS] 全ニュースが既読 → 既読履歴をリセットして再読モードに切替"
		: > "$PAST_NEWS_READ"
		: > "$PAST_NEWS_READ_KEYS"
		past_news_read=""
		unread_news_headlines="$news_headlines"
	fi

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood unread_news_headlines past_news_read past_topics game_num score
	# Default for empty past_news_read
	[ -z "$past_news_read" ] && export past_news_read="（なし）"
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_news.md" > "$prompt_file"

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "news"
}

start_radio_corner_recap() {
	local game_num="$1" score="$2"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local best_score
	best_score=$(cat best_score.txt 2>/dev/null || echo 0)
	local recent_scores=""
	[ -f "score_history.txt" ] && recent_scores=$(tail -10 score_history.txt 2>/dev/null)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood past_topics game_num score best_score
	export recent_scores="${recent_scores:-まだ履歴がありません}"
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_recap.md" > "$prompt_file"

	_radio_generate_and_play "$prompt_file" "$game_num" "$score" "recap"
}

start_radio_corner_strategy() {
	local strategy_diff="$1" scores="$2" game_num="$3" best_score="$4"
	_radio_time_context
	local past_topics
	past_topics=$(_radio_past_topics_block)

	local prompt_file
	prompt_file=$(mktemp /tmp/eloop_radio_prompt_XXXXXXXX)

	export persona_block
	persona_block=$(_radio_persona_block)
	export output_rules
	output_rules=$(_radio_output_rules 1000 2000)
	export _rc_time _rc_period _rc_mood past_topics game_num scores best_score strategy_diff
	envsubst < "$ELOOP_LIB_DIR/prompts/radio_strategy.md" > "$prompt_file"

	_radio_generate_and_play "$prompt_file" "$game_num" "${best_score}" "strategy"
}

#=== ニュース: 毎ゲーム取得 & 再生 ===

fetch_and_play_news() {
	local game_num="$1" score="$2"
	# 旧呼び出し（引数なし）でも、起動時点の値を固定して後段に渡す
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null || echo 0)

	log "[NEWS] ニュース取得..."
	./fetch_news.sh 2>/dev/null

	if [ -f "tmp/news.txt" ] && [ -s "tmp/news.txt" ]; then
		start_radio_corner_news "$game_num" "$score"
	else
		log "[NEWS] ニュースなし、スキップ"
	fi
}

#=== ラジオトーク: ディスパッチャー ===

start_random_radio_corner() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null || echo 0)

	# ニュースは毎ゲーム別途実行するので、ここでは除外
	local candidates=("theme" "soviet" "recap")

	local pick="${candidates[$((RANDOM % ${#candidates[@]}))]}"
	log "[RADIO] コーナー選択: ${pick}"

	case "$pick" in
	theme)   start_radio_corner_theme "$game_num" "$score" ;;
	soviet)  start_radio_corner_soviet "$game_num" "$score" ;;
	recap)   start_radio_corner_recap "$game_num" "$score" ;;
	esac
}

schedule_nonessential_audio_jobs() {
	local game_num="$1" score="$2"
	[ -z "$game_num" ] && game_num=$(cat "$GAME_COUNT_FILE" 2>/dev/null || echo 0)
	[ -z "$score" ] && score=$(tail -1 score_history.txt 2>/dev/null || echo 0)

	# 配信演出の頻度 (変更しても毎ループ source で即反映)
	local news_interval_day=4
	local news_interval_night=8
	local news_night_start_hour=2
	local news_night_end_hour=5
	local news_phase=1
	local radio_interval=5
	local radio_phase=0
	local comment_backlog_skip_threshold=4

	local comment_queued=0 comment_playing=0 comment_total=0
	local skip_nonessential_radio=false
	read -r comment_queued comment_playing <<<"$(get_comment_backlog_counts)"
	comment_queued=${comment_queued:-0}
	comment_playing=${comment_playing:-0}
	comment_total=$((comment_queued + comment_playing))
	if is_comment_backlog_high "$comment_backlog_skip_threshold"; then
		skip_nonessential_radio=true
	fi

	local current_hour current_news_interval current_news_mode
	current_hour=$(date +%H)
	if (( 10#$current_hour >= news_night_start_hour && 10#$current_hour < news_night_end_hour )); then
		current_news_interval="$news_interval_night"
		current_news_mode="night"
	else
		current_news_interval="$news_interval_day"
		current_news_mode="day"
	fi

	if [ "$current_news_mode" != "${LAST_NEWS_MODE:-}" ]; then
		log "[NEWS] schedule mode=${current_news_mode} interval=${current_news_interval} (night: ${news_night_start_hour}:00-${news_night_end_hour}:00)"
		LAST_NEWS_MODE="$current_news_mode"
	fi

	if (( game_num % current_news_interval == news_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[NEWS] skip: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			fetch_and_play_news "$game_num" "$score" &
		fi
	fi

	if (( game_num % radio_interval == radio_phase )); then
		if [ "$skip_nonessential_radio" = true ]; then
			log "[RADIO] skip random corner: comment backlog=${comment_total} (queued=${comment_queued}, playing=${comment_playing}, threshold=${comment_backlog_skip_threshold})"
		else
			start_random_radio_corner "$game_num" "$score" &
		fi
	fi
}

#=== ソ連祝賀トーク ===

generate_soviet_celebration() {
	local score="$1" turns="$2" game_num="$3"
	local current_time
	current_time=$(date '+%H:%M')

	local celebration_prompt_file
	celebration_prompt_file=$(mktemp /tmp/eloop_celebration_XXXXXXXX)

	export score turns game_num current_time
	envsubst < "$ELOOP_LIB_DIR/prompts/celebration.md" > "$celebration_prompt_file"

	echo "generating:celebration:$(date +%s)" > tmp/.radio_state
	log "[CELEBRATION] 生成中..."
	local celebration_talk
	celebration_talk=$(_run_opencode_radio "$RADIO_AGENT" "$celebration_prompt_file")
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_opencode_radio "$RADIO_FALLBACK" "$celebration_prompt_file")
	fi
	if [ -z "$celebration_talk" ]; then
		celebration_talk=$(_run_claude_radio "$celebration_prompt_file")
	fi
	rm -f "$celebration_prompt_file"

	if [ -n "$celebration_talk" ]; then
		echo "$celebration_talk" >tmp/radio_celebration.txt
		echo "playing:celebration:$(date +%s)" > tmp/.radio_state
		log "[CELEBRATION] ${#celebration_talk}字 生成完了（再生は呼び出し側で）"
	else
		_radio_clear_state "celebration"
		log "[CELEBRATION] 祝賀トーク生成失敗"
	fi
}

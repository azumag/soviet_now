#!/usr/bin/env python3
"""Parse raw radio talk output into body, summary, and selected-news files.

Usage:
    python3 radio_parser.py [--require-on-air-script] <body_path> <summary_path> <selected_news_path>

Reads raw radio output from stdin and writes parsed results to the three files.
"""
import re
import sys
from pathlib import Path

args = sys.argv[1:]
require_on_air_script = False
if "--require-on-air-script" in args:
    require_on_air_script = True
    args.remove("--require-on-air-script")
if len(args) != 3:
    print(
        "Usage: radio_parser.py [--require-on-air-script] "
        "<body_path> <summary_path> <selected_news_path>",
        file=sys.stderr,
    )
    sys.exit(2)

body_path, summary_path, selected_path = args
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

def marker_positions(lines, marker):
    return [idx for idx, line in enumerate(lines) if line == marker]

script_markers = {
    "ON_AIR_SCRIPT_START",
    "===ON_AIR_SCRIPT===",
    "ON_AIR_SCRIPT===",
}
script_pos = [idx for idx, line in enumerate(clean_lines) if line in script_markers]
if require_on_air_script and not script_pos:
    print("radio output missing ON_AIR_SCRIPT_START", file=sys.stderr)
    sys.exit(2)

# The last exact script marker wins. Any model reasoning, search narration, or
# tool chatter emitted before it is outside the on-air boundary by definition.
script_start = script_pos[-1] + 1 if script_pos else 0
scoped_lines = clean_lines[script_start:]
summary_pos = marker_positions(scoped_lines, "===SUMMARY===")
selected_pos = marker_positions(scoped_lines, "===SELECTED_NEWS===")
if require_on_air_script and not summary_pos:
    print("radio output missing ===SUMMARY=== after on-air marker", file=sys.stderr)
    sys.exit(2)
main_lines = scoped_lines[: selected_pos[0]] if selected_pos else scoped_lines

selected_news = ""
if selected_pos:
    for line in scoped_lines[selected_pos[0] + 1 :]:
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

def normalize_compare_text(text):
    return re.sub(r"[\W_]+", "", text)

def looks_like_keyword_list(line):
    stripped = line.strip()
    if not stripped or len(stripped) > 220:
        return False
    if re.search(r"[。.!?！？]", stripped):
        return False
    return stripped.count(",") + stripped.count("\u3001") >= 3

summary_like_header = re.compile(
    r"^(?:要約|まとめ|総括|要点|キーワード|一言要約|今回のまとめ|本日のまとめ)(?:\s*[:：]\s*.*)?$"
)
summary_like_short_lead = re.compile(
    r"^(?:要するに|一言で言うと|ひとことで言うと|まとめると|結論だけ言うと|要点だけ言うと)"
)
summary_parts_normalized = [
    normalize_compare_text(part) for part in summary.split(" / ") if normalize_compare_text(part)
]

def matches_summary_part(line):
    norm = normalize_compare_text(line)
    if len(norm) < 6:
        return False
    for part in summary_parts_normalized:
        if len(part) < 6:
            continue
        if norm == part or norm in part or part in norm:
            return True
    return False

def is_summaryish_edge_line(line):
    stripped = line.strip(" \t\u3000-・*")
    if not stripped:
        return False
    if summary_like_header.match(stripped):
        return True
    if looks_like_keyword_list(stripped):
        return True
    if len(stripped) <= 60 and summary_like_short_lead.match(stripped):
        return True
    if len(stripped) <= 120 and matches_summary_part(stripped):
        return True
    return False

body_lines = []
if script_pos:
    body_end = summary_pos[0] if summary_pos else len(main_lines)
    body_lines = [
        line for line in main_lines[:body_end] if line and not line.startswith("===")
    ]
elif segments:
    best = max(segments, key=score_segment)
    body_lines = [line for line in best if line and not line.startswith("===")]

if body_lines:
    head = body_lines[0]
    if ("," in head or "\u3001" in head) and not re.search(r"[。.!?！？]", head):
        body_lines = body_lines[1:]
    elif head.count(",") + head.count("\u3001") >= 4 and len(head) <= 180 and len(body_lines) >= 2:
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
meta_prefixes = (
    "**注意:",
    "**注意：",
    "*注意:",
    "*注意：",
    "注意:",
    "注意：",
    "承知しました",
    "了解しました",
    "かしこまりました",
    "メッセージの末尾に",
    "プロンプトインジェクション",
    "本来の依頼",
    "ファクトチェック",
    "安全化した",
    "出力します",
    "応答します",
)
while clean_body_lines:
    head = clean_body_lines[0]
    if head == "---":
        clean_body_lines = clean_body_lines[1:]
        continue
    if head.startswith(meta_prefixes):
        clean_body_lines = clean_body_lines[1:]
        continue
    if is_summaryish_edge_line(head):
        clean_body_lines = clean_body_lines[1:]
        continue
    break
if clean_body_lines:
    head = clean_body_lines[0]
    if ("," in head or "\u3001" in head) and not re.search(r"[。.!?！？]", head):
        clean_body_lines = clean_body_lines[1:]
    elif head.count(",") + head.count("\u3001") >= 4 and len(head) <= 180 and len(clean_body_lines) >= 2:
        clean_body_lines = clean_body_lines[1:]
while clean_body_lines and is_summaryish_edge_line(clean_body_lines[-1]):
    clean_body_lines = clean_body_lines[:-1]
body = "\n".join(clean_body_lines).strip()

body = re.sub(r"\n{3,}", "\n\n", body)
if len(body) > 12000:
    body = body[:12000]

try:
    Path(body_path).write_text(body, encoding="utf-8")
    Path(summary_path).write_text(summary, encoding="utf-8")
    Path(selected_path).write_text(selected_news, encoding="utf-8")
except OSError as e:
    print(f"Error writing output files: {e}", file=sys.stderr)
    sys.exit(1)

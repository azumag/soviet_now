#!/usr/bin/env python3
"""Radio text utility subcommands: dedup, sanitize, normalize_tone.

Usage:
    python3 radio_text_utils.py dedup            (reads stdin)
    python3 radio_text_utils.py sanitize         (reads stdin)
    python3 radio_text_utils.py normalize_tone   (reads stdin)
"""
import re
import sys
from collections import Counter


def cmd_dedup():
    """Remove repeated lines and repeated phrases from radio text."""
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


def cmd_sanitize():
    """Replace self-deprecating broadcast phrases with positive alternatives."""
    text = sys.stdin.read()
    drop_line_patterns = [
        r'現在.*(問題|不具合|障害).*(読み上げ|放送|案内).*(できません|できない)',
        r'現在.*(読み上げ|放送|案内).*(できません|できない)',
        r'検索(が|は)?できません',
        r'調査(が|は)?できません',
        r'情報(が|は)?取得できません',
        r'うまく読み上げできません',
        r'読み上げられません',
    ]
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
    filtered_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line and any(re.search(pat, line, flags=re.IGNORECASE) for pat in drop_line_patterns):
            continue
        filtered_lines.append(raw_line)
    out = "\n".join(filtered_lines)
    for pat, repl in patterns:
        out = re.sub(pat, repl, out, flags=re.IGNORECASE)
    sys.stdout.write(out)


def cmd_normalize_tone():
    """Normalize ですます tone by removing unnecessary suffixes."""
    text = sys.stdin.read()
    out = text

    rules = [
        (r'なんですよね(?=\s|$|[。！？、])', 'なんです'),
        (r'なんですよ(?=\s|$|[。！？、])', 'なんです'),
        (r'ですよね(?=\s|$|[。！？、])', 'です'),
        (r'ですよ(?=\s|$|[。！？、])', 'です'),
        (r'ますよね(?=\s|$|[。！？、])', 'ます'),
        (r'ますね(?=\s|$|[。！？、])', 'ます'),
        (r'ですね(?=\s|$|[。！？、])', 'です'),
        (r'ですけどね(?=\s|$|[。！？、])', 'ですけど'),
        (r'ますけどね(?=\s|$|[。！？、])', 'ますけど'),
        (r'なんですけどね(?=\s|$|[。！？、])', 'なんですけど'),
        (r'でしょうね(?=\s|$|[。！？、])', 'でしょう'),
    ]
    for pat, repl in rules:
        out = re.sub(pat, repl, out)
    sys.stdout.write(out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: radio_text_utils.py {dedup|sanitize|normalize_tone}", file=sys.stderr)
        sys.exit(1)

    subcmd = sys.argv[1]
    if subcmd == "dedup":
        cmd_dedup()
    elif subcmd == "sanitize":
        cmd_sanitize()
    elif subcmd == "normalize_tone":
        cmd_normalize_tone()
    else:
        print(f"Unknown subcommand: {subcmd}", file=sys.stderr)
        sys.exit(1)

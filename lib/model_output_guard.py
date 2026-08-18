#!/usr/bin/env python3
"""Extract only a model's final, speakable text from mixed CLI output.

Agent CLIs can interleave reasoning, web/tool progress, and the final answer on
the same stream.  This parser is intentionally conservative: reasoning
containers (<think>/<thinking>/<analysis>) are stripped in place so the body
that follows can still be spoken, but genuine internal-protocol leaks
(tool_call/invoke/function_calls/...) remain fatal — an output containing
those, or an obvious untagged work-note lead, is discarded entirely so that
callers can use their normal fallback instead of speaking it on air.
"""

from __future__ import annotations

import re
import sys


ANSI_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
EXPLICIT_FINAL_RE = re.compile(
    r"<(?:final|assistant_response)(?:\s[^>]*)?>(.*?)"
    r"</(?:final|assistant_response)\s*>",
    re.IGNORECASE | re.DOTALL,
)

# --- 推論タグ（除去対象）: タグと中身ごと落として本文を活かす ---
REASONING_TAGS = r"analysis|thinking|think"
# 対応ペア。非貪欲 + DOTALL + 後方参照で <think>…</think> を中身ごと除去する。
REASONING_BLOCK_RE = re.compile(
    rf"<(?P<tag>{REASONING_TAGS})\b[^>]*>.*?</(?P=tag)\s*>",
    re.IGNORECASE | re.DOTALL,
)
# 閉じタグの無い開始タグ = 推論の途中でストリームが切れた。以降を全部捨てる。
REASONING_OPEN_TAIL_RE = re.compile(
    rf"<(?:{REASONING_TAGS})\b[^>]*>.*\Z",
    re.IGNORECASE | re.DOTALL,
)
# 開始タグの無い閉じタグ = 開始タグが別チャネルへ出た。手前(最後の閉じタグまで)を全部捨てる。
REASONING_CLOSE_HEAD_RE = re.compile(
    rf"\A.*</(?:{REASONING_TAGS})\s*>",
    re.IGNORECASE | re.DOTALL,
)
# 除去後の残留検出用（防御ネット）。
REASONING_TAG_RE = re.compile(rf"</?(?:{REASONING_TAGS})(?:\s|>|$)", re.IGNORECASE)

# --- 内部プロトコル（拒否対象）: 従来どおり出力全体を破棄する ---
PROTOCOL_REJECT_RE = re.compile(
    r"</?(?:tool_call|tool_result|function_calls?|"
    r"invoke|parameter|arg_name|arg_value|search_query|tool)(?:\s|>|$)|"
    r"\]\s*<\]\s*[A-Za-z0-9_.:-]+\s*\[>\[",
    re.IGNORECASE,
)

# 従来名は「機械的ノイズ全般の検出子」として維持する（拒否パターン + 推論タグ残留）。
# SEPARATOR 分岐の「前置きはゴミか？」判定と、推論除去後の残留チェックの両方で使う。
UNSAFE_PROTOCOL_RE = re.compile(
    "(?:" + PROTOCOL_REJECT_RE.pattern + ")|(?:" + REASONING_TAG_RE.pattern + ")",
    re.IGNORECASE,
)

WORK_NOTE_RE = re.compile(
    r"(?:WebFetch|WebSearch|search_query)|"
    r"(?:材料|情報|出力|内容|候補).{0,16}(?:確認|検討|整理)(?:します|します。|してみます)|"
    r"(?:検索|調査|確認).{0,16}(?:します|してみます|を試します|できない|使えない)|"
    r"自分の知識で|確実性が高いのは|以下のあたり|"
    r"(?:^|\n)\s*(?:I need to|We need to|Let's|I will|I'll|Analyzing)\b",
    re.IGNORECASE,
)
SEPARATOR_RE = re.compile(r"(?m)^\s*---+\s*$")
FINAL_CHANNEL_RE = re.compile(r"(?im)^\s*(?:final|assistant_response)\s*$")


def _strip_reasoning_blocks(value: str) -> str:
    """対応ペアの推論ブロックを中身ごと除去する（複数出現・入れ子もどきにも対応）。"""
    previous = None
    while previous != value:
        previous = value
        value = REASONING_BLOCK_RE.sub("", value)
    return value


def _drop_reasoning(value: str) -> str:
    """推論ブロックに加え、片側しか無い推論タグの断片も落とす。"""
    value = _strip_reasoning_blocks(value)
    value = REASONING_CLOSE_HEAD_RE.sub("", value)  # 孤立した閉じタグ: 手前を捨てる
    value = REASONING_OPEN_TAIL_RE.sub("", value)  # 孤立した開始タグ: 以降を捨てる
    return value.strip()


def _trim_cli_noise(value: str) -> str:
    clean: list[str] = []
    for raw in value.replace("\r", "").splitlines():
        line = raw.strip()
        if not line:
            clean.append("")
            continue
        if (
            line == "^D"
            or line.startswith("Script started on ")
            or line.startswith("Script done on ")
            or re.fullmatch(r"/[^ ]*", line)
            or line.startswith("/Users/")
            or line.startswith("⚙")
            or line.startswith(">")
        ):
            continue
        clean.append(raw.rstrip())
    return "\n".join(clean).strip()


def _safe_candidate(value: str) -> str:
    # trim を先に行う: CLI エコー行 (例 "> <think>...") はここで丸ごと落ちるため、
    # そこに推論タグの開始/終了だけが乗っていても後段の _drop_reasoning を
    # 誤爆させない。逆に drop を先にすると、エコー行に未対応の開始タグが
    # あるだけで以降の本文まで巻き添えで捨てられる（REASONING_CLOSE_HEAD_RE は
    # 孤立した閉じタグの手前しか捨てないため、trim 後に drop してもこの順序で
    # 安全に本文へ到達できる）。
    value = _drop_reasoning(_trim_cli_noise(value))
    if not value or UNSAFE_PROTOCOL_RE.search(value):
        return ""
    head_lines = [line for line in value.splitlines() if line.strip()][:6]
    head = "\n".join(head_lines)[:800]
    if WORK_NOTE_RE.search(head):
        return ""
    return value


def extract_final_text(raw: str) -> str:
    text = ANSI_RE.sub("", raw).replace("\x00", "")

    # Fact-check output has its own strict downstream parser.  Preserve the
    # issue section, but discard any reasoning before its required envelope.
    lines = text.replace("\r", "").splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "===SAFE_SCRIPT===":
            script = []
            for script_line in lines[index + 1 :]:
                if script_line.strip() in {
                    "===ISSUES===",
                    "===SUMMARY===",
                    "===SELECTED_NEWS===",
                }:
                    break
                script.append(script_line)
            if not _safe_candidate("\n".join(script)):
                return ""
            # マーカー行(===SAFE_SCRIPT===)自体は保持したまま、以降の本文から
            # 対応ペアの推論ブロックだけを除去する。断片除去(_drop_reasoning)は
            # ここでは使わない: 片側だけの推論タグが構造マーカーをまたいでいる
            # 場合、貪欲な前方/後方削除が ===ISSUES=== 等ごと巻き込む恐れが
            # ペア除去よりさらに大きいため。なお対応ペアの除去自体も、ペアが
            # 構造マーカーをまたいで存在する場合はマーカーごと消え得る
            # （軽微: ISSUES等はログ/メタ用途で、本文(script)は上の
            # _safe_candidate 判定を独立して通過済み）。断片が残る envelope は
            # 従来どおり拒否して呼び出し元のフォールバックに任せる。
            envelope = _trim_cli_noise(
                lines[index] + "\n" + _strip_reasoning_blocks("\n".join(lines[index + 1 :]))
            )
            return "" if UNSAFE_PROTOCOL_RE.search(envelope) else envelope

    # Prefer explicit final-channel containers when the backend supplies them.
    explicit = list(EXPLICIT_FINAL_RE.finditer(text))
    if explicit:
        return _safe_candidate(explicit[-1].group(1))
    channel_markers = list(FINAL_CHANNEL_RE.finditer(text))
    if channel_markers:
        return _safe_candidate(text[channel_markers[-1].end() :])

    # Some routed models print untagged work notes, then a Markdown divider,
    # then the requested answer.  Recover only when the prefix is demonstrably
    # a work note; otherwise preserve legitimate dividers in clean prose.
    separators = list(SEPARATOR_RE.finditer(text))
    for separator in reversed(separators):
        prefix = text[: separator.start()]
        candidate = text[separator.end() :]
        if (UNSAFE_PROTOCOL_RE.search(prefix) or WORK_NOTE_RE.search(prefix)) and re.search(
            r"[。！？.!?]", candidate
        ):
            return _safe_candidate(candidate)

    cleaned = _safe_candidate(text)
    return cleaned


def main() -> int:
    result = extract_final_text(sys.stdin.read())
    if result:
        sys.stdout.write(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

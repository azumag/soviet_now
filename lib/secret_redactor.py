#!/usr/bin/env python3
"""lib/secret_redactor.py - docich#39

Coding Agent / AI生成code 用の prompt・output・debug artifact から、
現在の環境変数に載っている credential 相当の値を保存前に取り除く。

対象: 環境変数「名」が credential らしい (TOKEN/SECRET/API_KEY/PASSWORD/
OAUTH/COOKIE/WEBHOOK/CLIENT_ID/CLIENT_SECRET/ACCESS_KEY/PRIVATE_KEY/
REFRESH_TOKEN 等) と判定できるものの「値」。値そのものの中身は読むが、
このスクリプト自身は値をログ/stdoutへ出さない (置換先は固定文字列
`[REDACTED:NAME]` のみ)。

使い方:
    python3 lib/secret_redactor.py < raw.txt > redacted.txt

環境変数 SECRET_REDACTOR_EXTRA_NAMES (カンマ区切り) で、名前パターンに
一致しない追加の変数名を明示的に対象へ加えられる。
"""
import os
import re
import sys

_NAME_PATTERN = re.compile(
    r"(TOKEN|SECRET|PASSWORD|PASSWD|API[_-]?KEY|APIKEY|OAUTH|COOKIE|"
    r"WEBHOOK|CLIENT[_-]?ID|CLIENT[_-]?SECRET|ACCESS[_-]?KEY|PRIVATE[_-]?KEY|"
    r"REFRESH[_-]?TOKEN|CREDENTIAL)",
    re.IGNORECASE,
)

# 値が短すぎる/汎用的すぎるものは誤爆(意図しない一般語の置換)を避けるため対象外にする。
_MIN_VALUE_LEN = 6


def _candidate_secrets():
    extra = {
        name.strip()
        for name in os.environ.get("SECRET_REDACTOR_EXTRA_NAMES", "").split(",")
        if name.strip()
    }
    out = {}
    for name, value in os.environ.items():
        if not value or len(value) < _MIN_VALUE_LEN:
            continue
        if name in extra or _NAME_PATTERN.search(name):
            out[name] = value
    return out


def redact(text, secrets=None):
    if secrets is None:
        secrets = _candidate_secrets()
    # 長い値から先に置換する (短い値が長い値の部分文字列になっているケースで
    # 二重置換/中途半端な置換を避けるため)。
    for name, value in sorted(secrets.items(), key=lambda kv: len(kv[1]), reverse=True):
        if value in text:
            text = text.replace(value, f"[REDACTED:{name}]")
    return text


def main():
    data = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    sys.stdout.write(redact(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

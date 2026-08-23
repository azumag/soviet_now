#!/usr/bin/env python3
"""読み上げ直前のテキスト正規化。

適用する変換（この順）:
  1. ``--country-names`` 指定時だけ、ゲーム内のピースtype参照
     （T15 / type 15 / Type-15 / タイプ15 等）を国名へ置換する。
  2. アルファベットを全て小文字へ（日本語・カタカナ・漢字は不変）。

国名変換はゲーム由来の本文だけが明示的に有効化する。汎用読み上げで
T-34 や Type 2 diabetes のような別分野の表記を誤変換してはならない。

使い方:
    normalize_speech_text.py [--country-names] <file>  # その場で書き換え
    normalize_speech_text.py [--country-names]         # stdin -> stdout
"""
import re
import sys

try:
    from lib.country_names import COUNTRY_NAMES
except ModuleNotFoundError:  # direct execution: python3 lib/normalize_speech_text.py
    from country_names import COUNTRY_NAMES

# Unity の Republic prefab / asset 名で確認した type→国名。
# ゲーム由来として明示された本文は、低段階を含めて必ず国名に直す。
TYPE_COUNTRY = COUNTRY_NAMES

# "T15" / "type 15" / "Type-15" / "タイプ15" 等にマッチ。
#   (?<![A-Za-z]) : 直前が英字なら除外（prototype15 等の誤爆防止）
#   (?!\d|[A-Za-z_]|\.[\dA-Za-z_]): 小数・ASCII識別子の先頭だけを誤置換しない
_TYPE_RE = re.compile(
    r'(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})'
    r'(?!\d|[A-Za-z_]|\.[\dA-Za-z_])'
)
_TYPE_PAIR_RE = re.compile(
    r'(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})'
    r'\s*/\s*(\d{1,2})(?!\d|[A-Za-z_]|\.[\dA-Za-z_])'
)
_TYPE_COUNT_RE = re.compile(
    r'(?<![A-Za-z])(?:[Tt]ype|[Tt]|タイプ)\s*-?\s*(\d{1,2})'
    r'\s*(?:[xX×*])\s*(\d+)(?!\d|[A-Za-z_])'
)
_BEST_TYPE_KEY_RE = re.compile(
    r'(?<![A-Za-z])(?:max_piece|max|best_max|best|source_best)_type'
    r'\s*[:=]\s*(\d{1,2})(?!\d|[A-Za-z_])',
    re.IGNORECASE,
)
_HIGH_TYPE_COUNTS_LABEL_RE = re.compile(
    r'(?<![A-Za-z])high_type_counts\s*[:=]\s*', re.IGNORECASE
)


def _replace_type(m):
    # 17以降はゲームの国段階ではない。戦車名などを壊さないよう原文を保つ。
    return TYPE_COUNTRY.get(int(m.group(1)), m.group(0))


def _replace_type_pair(m):
    left = TYPE_COUNTRY.get(int(m.group(1)))
    right = TYPE_COUNTRY.get(int(m.group(2)))
    return f"{left}・{right}" if left and right else m.group(0)


def _replace_type_count(m):
    name = TYPE_COUNTRY.get(int(m.group(1)))
    return f"{name}{m.group(2)}個" if name else m.group(0)


def _replace_best_type_key(m):
    name = TYPE_COUNTRY.get(int(m.group(1)))
    return f"最高国={name}" if name else m.group(0)


def replace_country_references(text):
    """Replace internal country-stage references without changing other text."""
    text = str(text or "")
    text = _BEST_TYPE_KEY_RE.sub(_replace_best_type_key, text)
    text = _HIGH_TYPE_COUNTS_LABEL_RE.sub("終盤の国別個数=", text)
    text = _TYPE_PAIR_RE.sub(_replace_type_pair, text)
    text = _TYPE_COUNT_RE.sub(_replace_type_count, text)
    return _TYPE_RE.sub(_replace_type, text)


def normalize(text, *, replace_countries=False):
    if replace_countries:
        text = replace_country_references(text)
    # アルファベットは全て小文字に（日本語は str.lower() で不変）
    text = text.lower()
    return text


def main():
    args = sys.argv[1:]
    replace_countries = False
    if args and args[0] == "--country-names":
        replace_countries = True
        args = args[1:]
    if len(args) > 1:
        raise SystemExit(
            "usage: normalize_speech_text.py [--country-names] [file]"
        )
    if args:
        path = args[0]
        with open(path, encoding='utf-8') as f:
            text = f.read()
        out = normalize(text, replace_countries=replace_countries)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
    else:
        sys.stdout.write(
            normalize(sys.stdin.read(), replace_countries=replace_countries)
        )


if __name__ == '__main__':
    main()

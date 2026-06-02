#!/usr/bin/env python3
"""読み上げ直前のテキスト正規化。

適用する変換（この順）:
  1. ピースtype参照（T15 / type 15 / Type-15 / タイプ15 等）を対応する
     ソ連構成共和国・国名へ置換する。
  2. アルファベットを全て小文字へ（日本語・カタカナ・漢字は不変）。

使い方:
    normalize_speech_text.py <file>   # ファイルをその場で書き換え
    normalize_speech_text.py          # stdin -> stdout
"""
import re
import sys

# 確定している type→国名 のみ置換する（未確定の type には触れない）。
# 出典:
#   - dashboard_data.py STAGE_TYPES: 11=トルクメニスタン,12=ベラルーシ,13=ウクライナ,14=カザフスタン,15=ロシア
#   - soren91/comment.mjs / prompts: 同上の確認
#   - prompts/celebration.md: 「アルメニアから始まり…ロシアまで14段階」→ type1=アルメニア
#   - type16=ソ連（type15×2でソ連建国）
# type 2..10 は本リポジトリ内に確定名が無いため、誤読を避けて置換対象外とする。
TYPE_COUNTRY = {
    1: "アルメニア",
    11: "トルクメニスタン",
    12: "ベラルーシ",
    13: "ウクライナ",
    14: "カザフスタン",
    15: "ロシア",
    16: "ソ連",
}

# "T15" / "type 15" / "Type-15" / "タイプ15" 等にマッチ。
#   (?<![A-Za-z]) : 直前が英字なら除外（prototype15 等の誤爆防止）
#   (?!\d)        : 数字の直後がさらに数字なら除外（T1000 等の誤爆防止）
_TYPE_RE = re.compile(r'(?<![A-Za-z])(?:[Tt]ype|T|タイプ)\s*-?\s*(\d{1,2})(?!\d)')


def _replace_type(m):
    name = TYPE_COUNTRY.get(int(m.group(1)))
    return name if name else m.group(0)


def normalize(text):
    text = _TYPE_RE.sub(_replace_type, text)
    # アルファベットは全て小文字に（日本語は str.lower() で不変）
    text = text.lower()
    return text


def main():
    if len(sys.argv) >= 2:
        path = sys.argv[1]
        with open(path, encoding='utf-8') as f:
            text = f.read()
        out = normalize(text)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(out)
    else:
        sys.stdout.write(normalize(sys.stdin.read()))


if __name__ == '__main__':
    main()

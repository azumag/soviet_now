#!/usr/bin/env python3
"""strategy.py の最新バージョンに [BEST:スコア] タグを付与する。

Usage: python3 tag_best_changelog.py [strategy.py] [score]
  - 最新の "# vNNN:" 行に [BEST:score] タグを付与
  - 既に [BEST:] タグがある場合はスキップ
"""

import re
import sys


def tag_best(filepath: str, score: int) -> bool:
    with open(filepath, "r") as f:
        content = f.read()

    # 最新の "# vNNN:" 行を探す（[BEST:]タグなし）
    pattern = r"^(# )(v\d+:.*)$"
    match = re.search(pattern, content, re.MULTILINE)
    if not match:
        return False

    # 既に BEST タグがあればスキップ
    if f"[BEST:{score}]" in content:
        return False

    # タグ付与
    old_line = match.group(0)
    new_line = f"# [BEST:{score}] {match.group(2)}"
    content = content.replace(old_line, new_line, 1)

    with open(filepath, "w") as f:
        f.write(content)

    print(f"Tagged: {new_line.strip()}")
    return True


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "strategy.py"
    score = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if score > 0:
        tag_best(filepath, score)
    else:
        print("Usage: python3 tag_best_changelog.py <file> <score>")

#!/usr/bin/env python3
"""strategy.py の変更履歴を直近N個にトリミングする。

Usage: python3 trim_changelog.py [strategy.py] [keep_count]
  - keep_count: 保持するバージョン数 (デフォルト: 3)
  - ハイスコア行 (# [BEST:...]) は常に保持
"""

import re
import sys


def trim_changelog(filepath: str, keep: int = 3) -> bool:
    with open(filepath, "r") as f:
        lines = f.readlines()

    # 変更履歴セクションの開始・終了を検出
    changelog_start = None
    changelog_end = None
    for i, line in enumerate(lines):
        if "--- 変更履歴 ---" in line:
            changelog_start = i
        elif changelog_start is not None and changelog_end is None:
            # 変更履歴の終了: 空でないコード行 (コメントでない行)
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                changelog_end = i
                break

    if changelog_start is None or changelog_end is None:
        return False

    # 変更履歴セクション内のバージョンブロックを解析
    changelog_lines = lines[changelog_start:changelog_end]
    blocks = []  # [(start_idx, end_idx, is_best)]
    current_block_start = None
    current_is_best = False

    for i, line in enumerate(changelog_lines):
        # バージョンヘッダの検出: "# vNNN:" または "# [BEST:..."
        if re.match(r"^# (\[BEST:\d+\] )?v\d+:", line):
            if current_block_start is not None:
                blocks.append((current_block_start, i, current_is_best))
            current_block_start = i
            current_is_best = "[BEST:" in line
        elif line.strip() == "#" and current_block_start is not None:
            # 空コメント行はブロック区切り
            blocks.append((current_block_start, i, current_is_best))
            current_block_start = None

    # 最後のブロック
    if current_block_start is not None:
        blocks.append((current_block_start, len(changelog_lines), current_is_best))

    if not blocks:
        return False

    # 保持するブロックを選択
    best_blocks = [b for b in blocks if b[2]]  # BEST タグ付き
    normal_blocks = [b for b in blocks if not b[2]]  # 通常

    # 通常ブロックは直近 keep 個のみ
    kept_normal = normal_blocks[-keep:] if len(normal_blocks) > keep else normal_blocks

    # 保持するブロックのインデックスセット
    kept_blocks = best_blocks + kept_normal
    kept_blocks.sort(key=lambda b: b[0])

    # 新しい変更履歴セクションを構築
    new_changelog = [changelog_lines[0]]  # "# --- 変更履歴 ---" 行
    for start, end, _ in kept_blocks:
        new_changelog.extend(changelog_lines[start:end])

    # ファイルを再構成
    new_lines = lines[:changelog_start] + new_changelog + lines[changelog_end:]

    with open(filepath, "w") as f:
        f.writelines(new_lines)

    trimmed = len(blocks) - len(kept_blocks)
    if trimmed > 0:
        print(f"Trimmed {trimmed} old changelog entries, kept {len(kept_blocks)}")
    return trimmed > 0


if __name__ == "__main__":
    filepath = sys.argv[1] if len(sys.argv) > 1 else "strategy.py"
    keep = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    trim_changelog(filepath, keep)

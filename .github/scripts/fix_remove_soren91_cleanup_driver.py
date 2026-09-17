from pathlib import Path

path = Path('.github/scripts/remove_soren91_improvement_remnants.py')
text = path.read_text(encoding='utf-8')
old = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?\\n  \\}\\n(?=\\nasync function waitForRankingCommentContext)",\n    "\\n}",'
new = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?(?=\\n}\\n\\nasync function waitForRankingCommentContext)",\n    "",'
if old not in text:
    raise SystemExit('cleanup driver boundary pattern not found')
path.write_text(text.replace(old, new, 1), encoding='utf-8')

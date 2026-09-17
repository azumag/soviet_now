from pathlib import Path

path = Path('.github/scripts/remove_soren91_improvement_remnants.py')
text = path.read_text(encoding='utf-8')

old = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?\\n  \\}\\n(?=\\nasync function waitForRankingCommentContext)",\n    "\\n}",'
new = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?(?=\\n}\\n\\nasync function waitForRankingCommentContext)",\n    "",'
if old not in text:
    raise SystemExit('cleanup driver boundary pattern not found')
text = text.replace(old, new, 1)

old = '''replace(
    "soren91_control.sh",
    " SOREN91_EXTERNAL_IMPROVE='${_ext_improve}' IMPROVEMENT_INTERVAL_GAMES='${_improve_interval:-}'",
    "",
)
'''
new = '''text = load("soren91_control.sh")
for token in (
    " SOREN91_EXTERNAL_IMPROVE='$_ext_improve'",
    " IMPROVEMENT_INTERVAL_GAMES='${_improve_interval:-}'",
):
    text = text.replace(token, "")
save("soren91_control.sh", text)
'''
if old not in text:
    raise SystemExit('cleanup driver tmux env block not found')
text = text.replace(old, new, 1)

path.write_text(text, encoding='utf-8')

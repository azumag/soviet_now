from pathlib import Path

path = Path('.github/scripts/remove_soren91_improvement_remnants.py')
text = path.read_text(encoding='utf-8')

old_boundary = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?\\n  \\}\\n(?=\\nasync function waitForRankingCommentContext)",\n    "\\n}",'
new_boundary = 'r"\\n  // 外部制御モード: 内蔵改善をスキップ.*?(?=\\n}\\n\\nasync function waitForRankingCommentContext)",\n    "",'
if old_boundary in text:
    text = text.replace(old_boundary, new_boundary, 1)
# A later reviewed fix may already have replaced this exact boundary. In that
# case this compatibility helper must not turn an already-correct driver into
# a failure loop.

old_env = '''replace(
    "soren91_control.sh",
    " SOREN91_EXTERNAL_IMPROVE='${_ext_improve}' IMPROVEMENT_INTERVAL_GAMES='${_improve_interval:-}'",
    "",
)
'''
new_env = '''text = load("soren91_control.sh")
for token in (
    " SOREN91_EXTERNAL_IMPROVE='$_ext_improve'",
    " IMPROVEMENT_INTERVAL_GAMES='${_improve_interval:-}'",
):
    text = text.replace(token, "")
save("soren91_control.sh", text)
'''
if old_env in text:
    text = text.replace(old_env, new_env, 1)
elif new_env not in text:
    raise SystemExit('cleanup driver tmux env block not found')

path.write_text(text, encoding='utf-8')

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

# The physical retirement deletes soren91/improve.mjs. Remove the one stale
# play-contract assertion that still opens that tombstone; the shared behavior
# validator itself remains covered by the adjacent generated-strategy tests.
test_path = Path('tests/test_soren91_play.mjs')
test_text = test_path.read_text(encoding='utf-8')
stale_test = '''test('both improvement paths use the shared validator before adoption', () => {
  const source = readFileSync(new URL('../soren91/improve.mjs', import.meta.url), 'utf8');
  assert.ok(source.includes('const behavior = validateStrategyBehavior(module.decide);'));
  assert.ok(source.includes('if (!behavior.valid) return behavior;'));
  assert.equal((source.match(/let validationResult = await validateStrategy\\(newStrategy\\);/g) || []).length, 2);
  assert.ok(source.includes('${STRATEGY_CONTRACT}'));
});
'''
if stale_test in test_text:
    test_path.write_text(test_text.replace(stale_test, '', 1), encoding='utf-8')
elif "../soren91/improve.mjs" in test_text:
    raise SystemExit('unexpected remaining Soren91 improve.mjs test reference')

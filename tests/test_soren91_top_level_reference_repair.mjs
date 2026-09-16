import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { validateAndRepairCandidate } from '../soren91/daily_candidate_repair.mjs';

function withBaseline(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-top-level-repair-'));
  const previous = process.cwd();
  writeFileSync(
    join(dir, 'strategy.mjs'),
    'const helper = 1;\nfunction search() { return { x: 0 }; }\nexport function decide(boardState) { const normal = search(boardState); return { x: normal.x, hold: false, reason: "baseline" }; }\n',
  );
  process.chdir(dir);
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      process.chdir(previous);
      rmSync(dir, { recursive: true, force: true });
    });
}

test('top-level reference failure repairs only decide and preserves reviewed module prefix', async () => {
  await withBaseline(async () => {
    let validationCalls = 0;
    let repairCalls = 0;
    let repairPrompt = '';
    const expected = 'const helper = 1;\nfunction search() { return { x: 0 }; }\nexport function decide(boardState) { const normal = search(boardState); return { x: normal.x + 0.25, hold: false, reason: "repaired" }; }\n';
    const improveModule = {
      async validateStrategy(candidate) {
        validationCalls += 1;
        if (validationCalls === 1) {
          return { valid: false, error: 'Code error: ReferenceError: boardState is not defined' };
        }
        assert.equal(candidate, expected);
        assert.doesNotMatch(candidate, /leaked|boardState\.score/);
        return { valid: true, error: null };
      },
      async callStrategyModelWithFallback(prompt, screenshots, tag) {
        repairCalls += 1;
        repairPrompt = prompt;
        assert.deepEqual(screenshots, []);
        assert.equal(tag, 'improve_daily_fix');
        // Even if the model ignores the narrow contract and returns a whole
        // module with a bad top-level reference, only decide() may be adopted.
        return [
          'const leaked = boardState.score;',
          'export function decide(boardState) { const normal = search(boardState); return { x: normal.x + 0.25, hold: false, reason: "repaired" }; }',
        ].join('\n');
      },
    };

    const initial = [
      'const helper = 1;',
      'function search() { return { x: 0 }; }',
      'const leaked = boardState.score;',
      'export function decide(boardState) { const normal = search(boardState); return { x: normal.x + 0.1, hold: false, reason: "bad" }; }',
    ].join('\n');
    const result = await validateAndRepairCandidate(improveModule, initial);

    assert.equal(result.initialCategory, 'code_error_top_level_reference');
    assert.equal(result.repairs, 1);
    assert.equal(repairCalls, 1);
    assert.equal(result.finalCategory, null);
    assert.deepEqual(result.validation, { valid: true, error: null });
    assert.equal(result.candidate, expected);
    assert.match(repairPrompt, /code_error_top_level_reference/);
    assert.match(repairPrompt, /reviewed module prefix is immutable/);
    assert.match(repairPrompt, /replace ONLY its final decide\(\) function/);
    assert.match(repairPrompt, /All boardState\/current-turn logic must stay inside decide\(\)/);
  });
});

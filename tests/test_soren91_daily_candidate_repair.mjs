import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  buildDailyCandidateRepairPrompt,
  classifyCandidateValidation,
  validateAndRepairCandidate,
} from '../soren91/daily_candidate_repair.mjs';

function withBaseline(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-daily-repair-test-'));
  const previous = process.cwd();
  writeFileSync(
    join(dir, 'strategy.mjs'),
    'const helper = 1;\nexport function decide(boardState) { return { x: 0, reason: "baseline" }; }\n',
  );
  process.chdir(dir);
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      process.chdir(previous);
      rmSync(dir, { recursive: true, force: true });
    });
}

test('daily candidate repair fixes one invalid candidate from the reviewed complete baseline', async () => {
  await withBaseline(async () => {
    const validations = [];
    const fixes = [];
    const oldTotal = process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
    const oldPerModel = process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
    process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = '240';
    process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = '240';
    const improveModule = {
      async validateStrategy(candidate) {
        validations.push(candidate);
        return candidate === 'fixed'
          ? { valid: true }
          : { valid: false, error: 'no decide() function found in output. You must include "export function decide(boardState)" in the code.' };
      },
      async callStrategyModelWithFallback(prompt, screenshots, tag) {
        fixes.push({
          prompt,
          screenshots,
          tag,
          totalTimeout: process.env.SOREN91_TEXT_OPENCODE_TIMEOUT,
          perModelTimeout: process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT,
        });
        return 'fixed';
      },
    };

    try {
      const result = await validateAndRepairCandidate(improveModule, 'const helperOnly = true;');

      assert.equal(result.candidate, 'fixed');
      assert.deepEqual(result.validation, { valid: true });
      assert.equal(result.repairs, 1);
      assert.equal(result.initialCategory, 'missing_decide');
      assert.equal(result.finalCategory, null);
      assert.deepEqual(validations, ['const helperOnly = true;', 'fixed']);
      assert.equal(fixes.length, 1);
      assert.equal(fixes[0].tag, 'improve_daily_fix');
      assert.deepEqual(fixes[0].screenshots, []);
      assert.equal(fixes[0].totalTimeout, '420');
      assert.equal(fixes[0].perModelTimeout, '420');
      assert.match(fixes[0].prompt, /EXACTLY ONE JavaScript code block/);
      assert.match(fixes[0].prompt, /COMPLETE replacement strategy\.mjs module/);
      assert.match(fixes[0].prompt, /export function decide\(boardState\)/);
      assert.match(fixes[0].prompt, /reason: "baseline"/);
      assert.match(fixes[0].prompt, /helperOnly/);
      assert.equal(process.env.SOREN91_TEXT_OPENCODE_TIMEOUT, '240');
      assert.equal(process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT, '240');
    } finally {
      if (oldTotal == null) delete process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
      else process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = oldTotal;
      if (oldPerModel == null) delete process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
      else process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = oldPerModel;
    }
  });
});

test('daily candidate repair restores timeout env even when repair model fails', async () => {
  await withBaseline(async () => {
    const oldTotal = process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
    const oldPerModel = process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
    process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = '240';
    process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = '240';
    const improveModule = {
      async validateStrategy() {
        return { valid: false, error: 'no decide() function found in output' };
      },
      async callStrategyModelWithFallback() {
        assert.equal(process.env.SOREN91_TEXT_OPENCODE_TIMEOUT, '420');
        assert.equal(process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT, '420');
        throw new Error('fixed-private-failure');
      },
    };
    try {
      await assert.rejects(
        validateAndRepairCandidate(improveModule, 'invalid'),
        /fixed-private-failure/,
      );
      assert.equal(process.env.SOREN91_TEXT_OPENCODE_TIMEOUT, '240');
      assert.equal(process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT, '240');
    } finally {
      if (oldTotal == null) delete process.env.SOREN91_TEXT_OPENCODE_TIMEOUT;
      else process.env.SOREN91_TEXT_OPENCODE_TIMEOUT = oldTotal;
      if (oldPerModel == null) delete process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT;
      else process.env.SOREN91_TEXT_OPENCODE_MODEL_TIMEOUT = oldPerModel;
    }
  });
});

test('daily candidate repair stops after the single reviewed retry budget', async () => {
  await withBaseline(async () => {
    let validationCalls = 0;
    let fixCalls = 0;
    const improveModule = {
      async validateStrategy() {
        validationCalls += 1;
        return { valid: false, error: 'no decide() function found in output' };
      },
      async callStrategyModelWithFallback() {
        fixCalls += 1;
        return 'still-invalid';
      },
    };

    const result = await validateAndRepairCandidate(improveModule, 'invalid');

    assert.equal(result.candidate, 'still-invalid');
    assert.deepEqual(result.validation, { valid: false, error: 'no decide() function found in output' });
    assert.equal(result.repairs, 1);
    assert.equal(result.initialCategory, 'missing_decide');
    assert.equal(result.finalCategory, 'missing_decide');
    assert.equal(validationCalls, 2);
    assert.equal(fixCalls, 1);
  });
});

test('daily repair prompt tells the model to reconstruct from baseline instead of returning a snippet', () => {
  const prompt = buildDailyCandidateRepairPrompt(
    'function helper() {}',
    'no decide() function found in output',
    'export function decide(boardState) { return { x: 0, reason: "safe" }; }',
  );
  assert.match(prompt, /start from the reviewed baseline/);
  assert.match(prompt, /not a patch, snippet, helper, or explanation/);
  assert.match(prompt, /one block MUST be the COMPLETE replacement/);
  assert.equal(classifyCandidateValidation('no decide() function found in output'), 'missing_decide');
});

test('code errors use a fixed subtype and syntax-safe baseline reconstruction rules', () => {
  assert.equal(
    classifyCandidateValidation('Code error: SyntaxError: Unexpected end of input'),
    'code_error_truncated_or_unterminated',
  );
  assert.equal(
    classifyCandidateValidation("Code error: SyntaxError: Identifier 'score' has already been declared"),
    'code_error_duplicate_declaration',
  );
  assert.equal(
    classifyCandidateValidation("Code error: Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'foo'"),
    'code_error_module_dependency',
  );
  assert.equal(
    classifyCandidateValidation('Code error: ReferenceError: boardState is not defined'),
    'code_error_top_level_reference',
  );
  assert.equal(
    classifyCandidateValidation("Code error: SyntaxError: Unexpected token '}'"),
    'code_error_syntax',
  );

  const prompt = buildDailyCandidateRepairPrompt(
    'export function decide(boardState) { return { x: 0, reason: "bad" };',
    'Code error: SyntaxError: Unexpected end of input',
    'const helper = 1;\nexport function decide(boardState) { return { x: 0, reason: "safe" }; }',
  );
  assert.match(prompt, /code_error_truncated_or_unterminated/);
  assert.match(prompt, /reviewed baseline below is known-good source structure and is authoritative/);
  assert.match(prompt, /Do NOT copy broken\/truncated structure/);
  assert.match(prompt, /Preserve the complete tail of the reviewed baseline/);
  assert.match(prompt, /valid ESM/);
  assert.match(prompt, /non-authoritative hint only/);
});

test('daily candidate repair refuses retry budgets above one', async () => {
  const improveModule = {
    async validateStrategy() { return { valid: true }; },
    async callStrategyModelWithFallback() { return 'unused'; },
  };

  await assert.rejects(
    validateAndRepairCandidate(improveModule, 'candidate', { maxRepairs: 2 }),
    /candidate_repair_budget_invalid/,
  );
});

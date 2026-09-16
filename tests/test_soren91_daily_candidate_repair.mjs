import test from 'node:test';
import assert from 'node:assert/strict';

import { validateAndRepairCandidate } from '../soren91/daily_candidate_repair.mjs';


test('daily candidate repair fixes one invalid candidate then revalidates', async () => {
  const validations = [];
  const fixes = [];
  const improveModule = {
    async validateStrategy(candidate) {
      validations.push(candidate);
      return candidate === 'fixed'
        ? { valid: true }
        : { valid: false, error: 'decide contract failed' };
    },
    async callClaudeToFix(candidate, error, screenshots) {
      fixes.push({ candidate, error, screenshots });
      return 'fixed';
    },
  };

  const result = await validateAndRepairCandidate(improveModule, 'invalid');

  assert.equal(result.candidate, 'fixed');
  assert.deepEqual(result.validation, { valid: true });
  assert.equal(result.repairs, 1);
  assert.deepEqual(validations, ['invalid', 'fixed']);
  assert.deepEqual(fixes, [{
    candidate: 'invalid',
    error: 'decide contract failed',
    screenshots: [],
  }]);
});

test('daily candidate repair stops after the single reviewed retry budget', async () => {
  let validationCalls = 0;
  let fixCalls = 0;
  const improveModule = {
    async validateStrategy() {
      validationCalls += 1;
      return { valid: false, error: 'still invalid' };
    },
    async callClaudeToFix() {
      fixCalls += 1;
      return 'still-invalid';
    },
  };

  const result = await validateAndRepairCandidate(improveModule, 'invalid');

  assert.equal(result.candidate, 'still-invalid');
  assert.deepEqual(result.validation, { valid: false, error: 'still invalid' });
  assert.equal(result.repairs, 1);
  assert.equal(validationCalls, 2);
  assert.equal(fixCalls, 1);
});

test('daily candidate repair refuses retry budgets above one', async () => {
  const improveModule = {
    async validateStrategy() { return { valid: true }; },
    async callClaudeToFix() { return 'unused'; },
  };

  await assert.rejects(
    validateAndRepairCandidate(improveModule, 'candidate', { maxRepairs: 2 }),
    /candidate_repair_budget_invalid/,
  );
});

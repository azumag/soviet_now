import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  buildDailyCandidateRepairPrompt,
  validateAndRepairCandidate,
} from '../soren91/daily_candidate_repair.mjs';

function withBaseline(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-full-search-repair-'));
  const previous = process.cwd();
  const baseline = [
    'function evaluate() { return 0; }',
    'function search() { return { x: 0 }; }',
    'export function decide(boardState) {',
    '  const normal = search(boardState);',
    '  return { x: normal.x, hold: false, reason: "baseline" };',
    '}',
    '',
  ].join('\n');
  writeFileSync(join(dir, 'strategy.mjs'), baseline);
  process.chdir(dir);
  return Promise.resolve()
    .then(() => fn(baseline))
    .finally(() => {
      process.chdir(previous);
      rmSync(dir, { recursive: true, force: true });
    });
}

test('search-consistency repair may change reviewed search helpers but keeps decide search-based', async () => {
  await withBaseline(async baseline => {
    const invalid = [
      'function evaluate() { return 0; }',
      'function search() { return { x: 0 }; }',
      'function candidates() { return [{ x: 0.25 }]; }',
      'export function decide(boardState) {',
      '  const normal = search(boardState);',
      '  const immediate = candidates(boardState);',
      '  return { x: immediate[0].x, hold: false, reason: "bad-root-override" };',
      '}',
    ].join('\n');
    const repaired = [
      'function evaluate() { return 1; }',
      'function search() { return { x: 0.25 }; }',
      'export function decide(boardState) {',
      '  const normal = search(boardState);',
      '  return { x: normal.x, hold: false, reason: "beam-owned-root" };',
      '}',
      '',
    ].join('\n');

    let calls = 0;
    let promptSeen = '';
    const improveModule = {
      async validateStrategy(candidate) {
        assert.match(candidate, /export function decide\(boardState\)/);
        return { valid: true, error: null };
      },
      async callStrategyModelWithFallback(prompt, screenshots, tag) {
        calls += 1;
        promptSeen = prompt;
        assert.deepEqual(screenshots, []);
        assert.equal(tag, 'improve_daily_fix');
        return repaired;
      },
    };

    const result = await validateAndRepairCandidate(improveModule, invalid);

    assert.equal(result.initialCategory, 'source_search_consistency');
    assert.equal(result.repairs, 1);
    assert.equal(calls, 1);
    assert.deepEqual(result.validation, { valid: true, error: null });
    assert.equal(result.finalCategory, null);
    assert.equal(result.candidate, repaired);
    assert.notEqual(result.candidate, baseline);
    assert.match(result.candidate, /function evaluate\(\) \{ return 1; \}/);
    assert.match(result.candidate, /function search\(\) \{ return \{ x: 0\.25 \}; \}/);
    assert.doesNotMatch(result.candidate, /bad-root-override/);
    assert.match(promptSeen, /COMPLETE replacement strategy\.mjs module/);
    assert.match(promptSeen, /implement it inside evaluate\(\), compareMove\(\), comparePath\(\), or search\(\)/);
    assert.match(promptSeen, /SAME search path/);
    assert.doesNotMatch(promptSeen, /replace ONLY its final decide\(\) function/);
  });
});

test('search-consistency repair remains bounded to one model retry', async () => {
  await withBaseline(async () => {
    const invalid = [
      'function search() { return { x: 0 }; }',
      'function candidates() { return [{ x: 0.25 }]; }',
      'export function decide(boardState) {',
      '  const normal = search(boardState);',
      '  return { x: candidates(boardState)[0].x, hold: false, reason: "bad" };',
      '}',
    ].join('\n');
    let calls = 0;
    const improveModule = {
      async validateStrategy() { return { valid: true, error: null }; },
      async callStrategyModelWithFallback() {
        calls += 1;
        return invalid;
      },
    };

    const result = await validateAndRepairCandidate(improveModule, invalid);
    assert.equal(calls, 1);
    assert.equal(result.repairs, 1);
    assert.equal(result.initialCategory, 'source_search_consistency');
    assert.equal(result.finalCategory, 'source_search_consistency');
    assert.equal(result.validation.valid, false);
  });
});

test('search-consistency full repair prompt keeps retained state targets and full-module rules', () => {
  const prompt = buildDailyCandidateRepairPrompt(
    'export function decide(boardState) { return { x: 0, reason: "bad" }; }',
    'Strategy contract: source search consistency; decide bypasses multi-ply search',
    'function search() { return { x: 0 }; }\nexport function decide(boardState) { const normal = search(boardState); return { x: normal.x, reason: "base" }; }',
    [{ pieces: [], next: { type: 1 }, nextPieces: [], hold: null, canHold: true, garbage: { ratio: 0, gauge: 0 } }],
  );

  assert.match(prompt, /Retained-match behavior replay targets/);
  assert.match(prompt, /COMPLETE replacement strategy\.mjs module/);
  assert.match(prompt, /height\/root\/merge\/risk ranking/);
  assert.match(prompt, /preserve multi-ply look-ahead/);
});

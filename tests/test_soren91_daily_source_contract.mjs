import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  classifyCandidateValidation,
  validateAndRepairCandidate,
  validateStrategySourceContracts,
} from '../soren91/daily_candidate_repair.mjs';

const REAL_STRATEGY = new URL('../soren91/strategy.mjs', import.meta.url);

const REVIEWED_BASELINE = `
function normalizePiece(piece) { return { ...piece }; }
function search(board, piece, queue, garbage) { return { x: 0, pathRisk: 0, minClearance: 3, value: 1 }; }
export function decide(boardState) {
  const current = normalizePiece(boardState.next);
  const normal = search(boardState.pieces, current, boardState.nextPieces, boardState.garbage);
  return { x: normal.x, hold: false, reason: 'baseline' };
}
`.trim() + '\n';

function withBaseline(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-source-contract-'));
  const previous = process.cwd();
  writeFileSync(join(dir, 'strategy.mjs'), REVIEWED_BASELINE);
  process.chdir(dir);
  return Promise.resolve()
    .then(fn)
    .finally(() => {
      process.chdir(previous);
      rmSync(dir, { recursive: true, force: true });
    });
}

test('reviewed production strategy satisfies pre-PR source contracts', () => {
  const source = readFileSync(REAL_STRATEGY, 'utf8');
  assert.deepEqual(validateStrategySourceContracts(source), { valid: true, error: null });
});

test('pre-PR validator rejects #393-style immediate-root override', () => {
  const candidate = `
function normalizePiece(piece) { return { ...piece }; }
function search() { return { x: 0, pathRisk: 0, minClearance: 3, depth: 2 }; }
function candidates() { return [0.25]; }
function evaluate() { return { x: 0.25, risk: 0 }; }
export function decide(boardState) {
  const normal = search(boardState.pieces, boardState.next, boardState.nextPieces, boardState.garbage);
  let chosen = normal;
  const guard = candidates(boardState.pieces, boardState.next).map(x => evaluate(boardState.pieces, boardState.next, x))[0];
  if (guard) chosen = { ...normal, ...guard, pathRisk: normal.pathRisk, minClearance: normal.minClearance };
  return { x: chosen.x, hold: false, reason: 'guard' };
}
`;
  const result = validateStrategySourceContracts(candidate);
  assert.equal(result.valid, false);
  assert.match(result.error, /source search consistency/);
  assert.equal(classifyCandidateValidation(result.error), 'source_search_consistency');
});

test('pre-PR validator rejects #390-style coordinates on unpositioned current piece', () => {
  const candidate = `
function normalizePiece(piece) { return { ...piece }; }
function search() { return { x: 0, pathRisk: 0, minClearance: 3 }; }
export function decide(boardState) {
  const current = normalizePiece(boardState.next);
  const normal = search(boardState.pieces, current, boardState.nextPieces, boardState.garbage);
  const currentPartner = boardState.pieces.some(p => Math.abs(p.x - current.x) < 1);
  return { x: normal.x, hold: !currentPartner, reason: 'partner' };
}
`;
  const result = validateStrategySourceContracts(candidate);
  assert.equal(result.valid, false);
  assert.match(result.error, /source unpositioned piece/);
  assert.equal(classifyCandidateValidation(result.error), 'source_unpositioned_piece');
});

test('source-contract failure is repaired once before normal validation and PR creation', async () => {
  await withBaseline(async () => {
    let normalValidationCalls = 0;
    let repairCalls = 0;
    let promptSeen = '';
    const invalid = `
function normalizePiece(piece) { return { ...piece }; }
function search() { return { x: 0, pathRisk: 0, minClearance: 3 }; }
function candidates() { return [0.25]; }
function evaluate() { return { x: 0.25 }; }
export function decide(boardState) {
  const normal = search(boardState.pieces, boardState.next, boardState.nextPieces, boardState.garbage);
  const pick = candidates().map(x => evaluate(x))[0];
  return { x: pick.x, hold: false, reason: 'bad-root-override' };
}
`;
    const repaired = `
function normalizePiece(piece) { return { ...piece }; }
function search(board, piece, queue, garbage) { return { x: 0.25, pathRisk: 0, minClearance: 3, value: 1 }; }
export function decide(boardState) {
  const current = normalizePiece(boardState.next);
  const normal = search(boardState.pieces, current, boardState.nextPieces, boardState.garbage);
  return { x: normal.x, hold: false, reason: 'safe-search-change' };
}
`.trim() + '\n';
    const improveModule = {
      async validateStrategy() {
        normalValidationCalls += 1;
        return { valid: true, error: null };
      },
      async callStrategyModelWithFallback(prompt, screenshots, tag) {
        repairCalls += 1;
        promptSeen = prompt;
        assert.deepEqual(screenshots, []);
        assert.equal(tag, 'improve_daily_fix');
        return repaired;
      },
    };

    const result = await validateAndRepairCandidate(improveModule, invalid);
    assert.equal(result.initialCategory, 'source_search_consistency');
    assert.equal(result.repairs, 1);
    assert.equal(repairCalls, 1);
    // The invalid source never reaches dynamic import/smoke validation. Only
    // the repaired, source-contract-safe candidate does.
    assert.equal(normalValidationCalls, 1);
    assert.equal(result.validation.valid, true);
    assert.equal(result.finalCategory, null);
    assert.equal(result.candidate, repaired);
    assert.match(promptSeen, /source_search_consistency/);
    assert.match(promptSeen, /Search-consistency repair rules/);
    assert.match(promptSeen, /COMPLETE replacement strategy\.mjs module/);
    assert.match(promptSeen, /implement it inside evaluate\(\), compareMove\(\), comparePath\(\), or search\(\)/);
    assert.doesNotMatch(promptSeen, /replace ONLY its final decide\(\) function/);
    assert.deepEqual(validateStrategySourceContracts(result.candidate), { valid: true, error: null });
  });
});

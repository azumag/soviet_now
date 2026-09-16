import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  compareCandidateBehavior,
  loadObservedBehaviorReplayProbes,
  validateAndRepairCandidate,
} from '../soren91/daily_candidate_repair.mjs';

function observedState({ score, nextType = 1, x = 0 } = {}) {
  return {
    state: 'MOVE',
    pieces: [
      { type: 1, x: -1 + x, y: -4.4, r: 0.207, confidence: 0.95 },
      { type: 2, x: 0.7 + x, y: -4.25, r: 0.259, confidence: 0.92 },
    ],
    next: { type: nextType, r: nextType === 1 ? 0.207 : 0.259, confidence: 0.95 },
    nextPieces: [
      { type: nextType, r: nextType === 1 ? 0.207 : 0.259, confidence: 0.95 },
      { type: 3, r: 0.316, confidence: 0.9 },
    ],
    hold: { type: 3, r: 0.316, confidence: 0.95 },
    holdKnownEmpty: false,
    canHold: true,
    score,
    confidence: 0.9,
    garbage: { ratio: 0.1, height: -3.2, pixelCount: 12, gauge: 0.3, columns: [] },
  };
}

function historyLine(turn, state) {
  return JSON.stringify({
    turn,
    timestamp: '2026-09-16T00:00:00.000Z',
    state,
    decision: { x: 0, hold: false, reason: 'baseline' },
  });
}

function withObservedFixture(fn) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-observed-replay-'));
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  const previous = process.cwd();
  process.chdir(dir);
  return Promise.resolve()
    .then(() => fn(dir))
    .finally(() => {
      process.chdir(previous);
      rmSync(dir, { recursive: true, force: true });
    });
}

function writeCompletedGame(dir, game, states, { latest = false } = {}) {
  const token = String(game).padStart(4, '0');
  writeFileSync(join(dir, 'tmp', 'summaries', `game_${token}.json`), JSON.stringify({ rank: 50, turns: states.length }));
  const name = `${latest ? 'latest' : 'game'}_${token}.jsonl`;
  writeFileSync(join(dir, 'game_history', name), states.map((state, index) => historyLine(index, state)).join('\n') + '\n');
}

test('observed replay selects newest completed games and ignores live history without a summary', async () => {
  await withObservedFixture(async dir => {
    writeCompletedGame(dir, 6, [observedState({ score: 600, nextType: 1 })]);
    writeCompletedGame(dir, 7, [observedState({ score: 700, nextType: 2 })]);
    writeCompletedGame(dir, 8, [observedState({ score: 800, nextType: 1 })], { latest: true });
    writeCompletedGame(dir, 9, [observedState({ score: 900, nextType: 2 })]);
    writeFileSync(
      join(dir, 'game_history', 'latest_0010.jsonl'),
      historyLine(0, observedState({ score: 1000, nextType: 1 })) + '\n',
    );

    const probes = loadObservedBehaviorReplayProbes({ cwd: dir, maxGames: 3, maxProbes: 10 });
    assert.deepEqual(probes.map(state => state.score), [900, 800, 700]);
    assert.equal(probes.some(state => state.score === 600), false);
    assert.equal(probes.some(state => state.score === 1000), false);
  });
});

test('observed replay deduplicates states and remains bounded', async () => {
  await withObservedFixture(async dir => {
    const repeated = observedState({ score: 900, nextType: 1 });
    writeCompletedGame(dir, 9, [repeated, repeated, repeated, observedState({ score: 901, nextType: 2 })]);
    const probes = loadObservedBehaviorReplayProbes({ cwd: dir, maxGames: 1, maxProbes: 2 });
    assert.equal(probes.length, 2);
    assert.deepEqual(probes.map(state => state.score), [900, 901]);
    assert.throws(
      () => loadObservedBehaviorReplayProbes({ cwd: dir, maxGames: 9 }),
      /candidate_behavior_probe_budget_invalid/,
    );
  });
});

test('candidate may be novel on retained match states even when synthetic probes are unchanged', async () => {
  const baseline = `export function decide(boardState) {
    return { x: 0, hold: false, reason: 'baseline' };
  }`;
  const candidate = `export function decide(boardState) {
    return { x: boardState.score === 913 ? 0.3 : 0, hold: false, reason: 'observed-only' };
  }`;
  const observed = [observedState({ score: 913 }), observedState({ score: 914 }), observedState({ score: 915 })];

  const syntheticReplay = await compareCandidateBehavior(baseline, candidate);
  assert.equal(syntheticReplay.changed, false);
  const observedReplay = await compareCandidateBehavior(baseline, candidate, { probes: observed });
  assert.equal(observedReplay.changed, true);
  assert.equal(observedReplay.changedCount, 1);
});

test('synthetic-only novelty is rejected when completed retained match states exist', async () => {
  await withObservedFixture(async dir => {
    const baseline = `function search() { return { x: 0 }; }
export function decide(boardState) {
      const normal = search(boardState);
      return { x: normal.x, hold: false, reason: 'baseline' };
    }`;
    writeFileSync(join(dir, 'strategy.mjs'), baseline);
    writeCompletedGame(dir, 9, [
      observedState({ score: 913 }),
      observedState({ score: 914, nextType: 2 }),
      observedState({ score: 915, x: 0.1 }),
    ]);
    const syntheticOnly = `function search() { return { x: 0 }; }
export function decide(boardState) {
      const normal = search(boardState);
      return { x: boardState.score === 250 ? normal.x + 0.3 : normal.x, hold: false, reason: 'synthetic-only' };
    }`;
    const improveModule = {
      async validateStrategy() { return { valid: true, error: null }; },
      async callStrategyModelWithFallback() { throw new Error('repair must not run'); },
    };

    const result = await validateAndRepairCandidate(improveModule, syntheticOnly, { maxRepairs: 0 });
    assert.equal(result.validation.valid, false);
    assert.match(result.validation.error, /retained-match replay probes/);
    assert.equal(result.initialCategory, 'behavior_contract');
    assert.equal(result.repairs, 0);
  });
});

test('behavior-contract repair receives retained match targets and must change an observed action', async () => {
  await withObservedFixture(async dir => {
    const baseline = `function search() { return { x: 0 }; }
export function decide(boardState) {
      const normal = search(boardState);
      return { x: normal.x, hold: false, reason: 'baseline' };
    }`;
    writeFileSync(join(dir, 'strategy.mjs'), baseline);
    writeCompletedGame(dir, 9, [
      observedState({ score: 913 }),
      observedState({ score: 914, nextType: 2 }),
      observedState({ score: 915, x: 0.1 }),
    ]);

    let promptSeen = '';
    let repairCalls = 0;
    const improveModule = {
      async validateStrategy() { return { valid: true, error: null }; },
      async callStrategyModelWithFallback(prompt, screenshots, tag) {
        repairCalls += 1;
        promptSeen = prompt;
        assert.deepEqual(screenshots, []);
        assert.equal(tag, 'improve_daily_fix');
        return `function search() { return { x: 0 }; }
export function decide(boardState) {
          const normal = search(boardState);
          return { x: boardState.score === 914 ? normal.x + 0.25 : normal.x, hold: false, reason: 'observed-change' };
        }`;
      },
    };
    const noOp = `function search() { return { x: 0 }; }
export function decide(boardState) {
      const normal = search(boardState);
      return { x: normal.x, hold: false, reason: 'words-only' };
    }`;
    const result = await validateAndRepairCandidate(improveModule, noOp);

    assert.equal(result.initialCategory, 'behavior_contract');
    assert.equal(result.repairs, 1);
    assert.equal(repairCalls, 1);
    assert.equal(result.validation.valid, true);
    assert.equal(result.finalCategory, null);
    assert.match(promptSeen, /Retained-match behavior replay targets/);
    assert.match(promptSeen, /"garbageRatio"/); // compact state context is private model input only
    assert.match(promptSeen, /actual retained states/);
  });
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  analyzeHistoryText,
  buildDailyEvidence,
  contiguousFreshGames,
  selectFocusGames,
  selectSnapshotPaths,
} from '../soren91/daily_evidence.mjs';

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-daily-evidence-'));
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'strategy_snapshots'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'game_screenshots'), { recursive: true });
  return dir;
}

function writeGame(dir, game, summary, { history = true, historyRecords = null, strategy = true, shots = 0 } = {}) {
  const token = String(game).padStart(4, '0');
  writeFileSync(join(dir, 'tmp', 'summaries', `game_${token}.json`), JSON.stringify({ gameNumber: game, ...summary }));
  if (history) {
    const records = historyRecords || [{ turn: 1 }];
    writeFileSync(join(dir, 'game_history', `game_${token}.jsonl`), records.map(record => JSON.stringify(record)).join('\n') + '\n');
  }
  if (strategy) writeFileSync(join(dir, 'tmp', 'strategy_snapshots', `game_${token}_strategy.mjs`), 'export const version = 1;\n');
  if (shots > 0) {
    const shotDir = join(dir, 'tmp', 'game_screenshots', `game_${token}`);
    mkdirSync(shotDir, { recursive: true });
    for (let i = 1; i <= shots; i += 1) writeFileSync(join(shotDir, `turn_${i}.png`), 'png');
  }
}

test('contiguousFreshGames never jumps over a delayed mirror gap', () => {
  assert.deepEqual(contiguousFreshGames([1, 2, 3, 7], 3), {
    games: [], expected: 4, blockedByGap: true, missingGame: 4,
    nextAvailableGame: 7, deferredGames: [7],
  });
  assert.deepEqual(contiguousFreshGames([4, 5, 7, 8], 3), {
    games: [4, 5], expected: 4, blockedByGap: true, missingGame: 6,
    nextAvailableGame: 7, deferredGames: [7, 8],
  });
});

test('focus games cover worst, best and latest without duplicates', () => {
  const focus = selectFocusGames([
    { game: 4, rank: 80, turns: 10 },
    { game: 5, rank: 20, turns: 25 },
    { game: 6, rank: 20, turns: 40 },
  ]);
  assert.equal(focus.worst, 4);
  assert.equal(focus.best, 6);
  assert.equal(focus.latest, 6);
  assert.deepEqual(focus.games, [4, 6]);
});

test('snapshot selection samples early, middle and late frames', () => {
  const dir = fixture();
  try {
    writeGame(dir, 4, { rank: 30, turns: 20 }, { shots: 7 });
    assert.deepEqual(
      selectSnapshotPaths(dir, 4, 3).map(path => path.split('/').at(-1)),
      ['turn_1.png', 'turn_4.png', 'turn_7.png'],
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('history analysis surfaces low-confidence and dangerous turns', () => {
  const history = [
    { turn: 1, state: { confidence: 0.92, perception: { reason: 'stable' } }, decision: { hold: false, diagnostics: { risk: 0, pathRisk: 0, clearance: 1.4, minFutureClearance: 1.1, merges: 0 } } },
    { turn: 2, state: { confidence: 0.42, perception: { reason: 'stable-temporal-next' } }, decision: { hold: false, diagnostics: { risk: 0, pathRisk: 0, clearance: 1.0, minFutureClearance: 0.9, merges: 0 } } },
    { turn: 3, state: { confidence: 0.81, perception: { reason: 'stable' } }, decision: { hold: true, diagnostics: { risk: 1, pathRisk: 1, clearance: 0.38, minFutureClearance: 0.21, merges: 1 } } },
    { turn: 4, state: { confidence: 0.88, perception: { reason: 'stable-hold-empty' } }, decision: { hold: false, diagnostics: { risk: 2, pathRisk: 2, clearance: 0.07, minFutureClearance: 0.03, merges: 0 } } },
  ];
  const analyzed = analyzeHistoryText(history.map(record => JSON.stringify(record)).join('\n'));
  assert.equal(analyzed.ok, true);
  assert.equal(analyzed.metrics.lowConfidenceTurns, 1);
  assert.equal(analyzed.metrics.riskyDecisions, 2);
  assert.equal(analyzed.metrics.fatalDecisions, 1);
  assert.equal(analyzed.metrics.holdDecisions, 1);
  assert.equal(analyzed.metrics.merges, 1);
  assert.equal(analyzed.metrics.temporalNextObservations, 1);
  assert.equal(analyzed.metrics.knownEmptyHoldObservations, 1);
  assert.equal(analyzed.metrics.minConfidence, 0.42);
  assert.equal(analyzed.metrics.minFutureClearance, 0.03);
  assert.deepEqual(analyzed.criticalTurns, [4, 2]);
});

test('daily evidence prefers critical-turn screenshots and aggregates history metrics', () => {
  const dir = fixture();
  try {
    const historyRecords = [
      { turn: 1, state: { confidence: 0.92, perception: { reason: 'stable' } }, decision: { hold: false, diagnostics: { risk: 0, pathRisk: 0, clearance: 1.4, minFutureClearance: 1.1, merges: 0 } } },
      { turn: 2, state: { confidence: 0.42, perception: { reason: 'stable-temporal-next' } }, decision: { hold: false, diagnostics: { risk: 0, pathRisk: 0, clearance: 1.0, minFutureClearance: 0.9, merges: 0 } } },
      { turn: 3, state: { confidence: 0.81, perception: { reason: 'stable' } }, decision: { hold: true, diagnostics: { risk: 1, pathRisk: 1, clearance: 0.38, minFutureClearance: 0.21, merges: 1 } } },
      { turn: 4, state: { confidence: 0.88, perception: { reason: 'stable-hold-empty' } }, decision: { hold: false, diagnostics: { risk: 2, pathRisk: 2, clearance: 0.07, minFutureClearance: 0.03, merges: 0 } } },
      { turn: 5, state: { confidence: 0.91, perception: { reason: 'stable' } }, decision: { hold: false, diagnostics: { risk: 0, pathRisk: 0, clearance: 0.8, minFutureClearance: 0.6, merges: 0 } } },
    ];
    writeGame(dir, 1, { rank: 70, turns: 5 }, { historyRecords, shots: 6 });
    const bundle = buildDailyEvidence(dir, 0);
    const entry = bundle.entries[0];
    assert.equal(bundle.status, 'ready');
    assert.equal(bundle.schemaVersion, 2);
    assert.equal(bundle.metrics.lowConfidenceTurns, 1);
    assert.equal(bundle.metrics.riskyDecisions, 2);
    assert.equal(bundle.metrics.fatalDecisions, 1);
    assert.equal(bundle.metrics.minConfidence, 0.42);
    assert.deepEqual(entry.criticalTurns, [4, 2, 5]);
    const names = entry.screenshots.map(path => path.split('/').at(-1));
    assert.ok(names.includes('turn_4.png'));
    assert.ok(names.includes('turn_2.png'));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('daily evidence exposes rank metrics and concrete visual evidence paths', () => {
  const dir = fixture();
  try {
    writeGame(dir, 4, { rank: 80, turns: 10, piecesAtEnd: 12, strategyHash: 'aaaa' }, { shots: 5 });
    writeGame(dir, 5, { rank: 20, turns: 25, piecesAtEnd: 9, strategyHash: 'bbbb' }, { shots: 4 });
    writeGame(dir, 6, { rank: 10, turns: 40, piecesAtEnd: 7, strategyHash: 'cccc' }, { shots: 6 });
    const bundle = buildDailyEvidence(dir, 3);
    assert.equal(bundle.status, 'ready');
    assert.deepEqual(bundle.range.games, [4, 5, 6]);
    assert.equal(bundle.focus.worst, 4);
    assert.equal(bundle.focus.best, 6);
    assert.equal(bundle.focus.latest, 6);
    assert.equal(bundle.metrics.meanRank, 110 / 3);
    assert.equal(bundle.metrics.medianRank, 20);
    assert.equal(bundle.metrics.meanTurns, 25);
    assert.equal(bundle.entries.find(entry => entry.game === 4).screenshots.length, 3);
    assert.equal(bundle.entries.find(entry => entry.game === 5).screenshots.length, 0);
    assert.equal(bundle.entries.find(entry => entry.game === 6).screenshots.length, 3);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('missing history blocks automatic mutation but missing screenshots are warnings only', () => {
  const dir = fixture();
  try {
    writeGame(dir, 1, { rank: 50, turns: 11 }, { history: false, shots: 0 });
    const bundle = buildDailyEvidence(dir, 0);
    assert.equal(bundle.status, 'blocked');
    assert.match(bundle.warnings.join('\n'), /missing histories: 1/);
    assert.match(bundle.warnings.join('\n'), /focus games without screenshots: 1/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('malformed history blocks automatic mutation instead of silently trusting existence', () => {
  const dir = fixture();
  try {
    writeGame(dir, 1, { rank: 50, turns: 11 }, { shots: 1 });
    writeFileSync(join(dir, 'game_history', 'game_0001.jsonl'), '{"turn":1}\nnot-json\n');
    const bundle = buildDailyEvidence(dir, 0);
    assert.equal(bundle.status, 'blocked');
    assert.match(bundle.warnings.join('\n'), /invalid histories: 1/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('a gap after a safe contiguous prefix defers later games without losing the prefix', () => {
  const dir = fixture();
  try {
    writeGame(dir, 4, { rank: 40, turns: 12 }, { shots: 1 });
    writeGame(dir, 5, { rank: 35, turns: 18 }, { shots: 1 });
    writeGame(dir, 7, { rank: 90, turns: 4 }, { shots: 1 });
    const bundle = buildDailyEvidence(dir, 3);
    assert.equal(bundle.status, 'ready');
    assert.deepEqual(bundle.range.games, [4, 5]);
    assert.equal(bundle.range.missingGame, 6);
    assert.deepEqual(bundle.range.deferredGames, [7]);
    assert.match(bundle.warnings.join('\n'), /deferred after gap: missing #6/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

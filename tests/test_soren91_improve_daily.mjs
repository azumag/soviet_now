import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const {
  scanGameRange,
  pendingRange,
  planImprovementEvidence,
  formatPrMarker,
  parsePrMarker,
} = await import('../soren91/improve_daily.mjs');

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-improve-daily-'));
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  for (const n of [1, 2, 3, 7]) {
    writeFileSync(join(dir, 'tmp', 'summaries', `game_${String(n).padStart(4, '0')}.json`), '{}');
  }
  return dir;
}

function evidenceFixture(games) {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-improve-evidence-'));
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'strategy_snapshots'), { recursive: true });
  for (const n of games) {
    const token = String(n).padStart(4, '0');
    writeFileSync(
      join(dir, 'tmp', 'summaries', `game_${token}.json`),
      JSON.stringify({ gameNumber: n, rank: 50 - n, turns: 10 + n, piecesAtEnd: 5 }),
    );
    writeFileSync(join(dir, 'game_history', `game_${token}.jsonl`), '{"turn":1}\n');
    writeFileSync(join(dir, 'tmp', 'strategy_snapshots', `game_${token}_strategy.mjs`), 'export const version = 1;\n');
  }
  return dir;
}

test('scanGameRange reads the available summary range', () => {
  const dir = fixture();
  try {
    const r = scanGameRange(dir);
    assert.deepEqual(r.games, [1, 2, 3, 7]);
    assert.equal(r.from, 1);
    assert.equal(r.to, 7);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('pendingRange only counts games after lastConsumedGame', () => {
  const dir = fixture();
  try {
    const r = pendingRange(dir, 3);
    assert.deepEqual(r.fresh, [7]);
    assert.equal(r.hasFresh, true);
    assert.equal(pendingRange(dir, 7).hasFresh, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('daily improvement only consumes the contiguous prefix before a mirror gap', () => {
  const dir = evidenceFixture([4, 5, 7]);
  try {
    const evidence = planImprovementEvidence(dir, 3);
    assert.equal(evidence.status, 'ready');
    assert.deepEqual(evidence.range.games, [4, 5]);
    assert.deepEqual(evidence.range.deferredGames, [7]);
    assert.equal(evidence.range.missingGame, 6);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('daily improvement fails closed when the first expected mirrored game is missing', () => {
  const dir = evidenceFixture([7]);
  try {
    const evidence = planImprovementEvidence(dir, 3);
    assert.equal(evidence.status, 'blocked');
    assert.deepEqual(evidence.range.games, []);
    assert.equal(evidence.range.missingGame, 4);
    assert.equal(evidence.range.nextAvailableGame, 7);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('PR marker round-trips', () => {
  const marker = formatPrMarker(12, 34);
  assert.deepEqual(parsePrMarker(`body\n${marker}\nmore`), { fromGame: 12, toGame: 34 });
  assert.equal(parsePrMarker('no marker'), null);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const {
  scanGameRange,
  pendingRange,
  buildClearManifest,
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

test('clear manifest covers the consumed span and the runtime paths', () => {
  const m = buildClearManifest(4, 6);
  assert.deepEqual(m.games, [4, 5, 6]);
  assert.ok(m.targets.includes('tmp/summaries/game_*.json'));
  assert.ok(m.targets.some(t => t.startsWith('game_history/')));
});

test('PR marker round-trips', () => {
  const marker = formatPrMarker(12, 34);
  assert.deepEqual(parsePrMarker(`body\n${marker}\nmore`), { fromGame: 12, toGame: 34 });
  assert.equal(parsePrMarker('no marker'), null);
});

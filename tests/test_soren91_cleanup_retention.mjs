import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync, utimesSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const { cleanupRetention } = await import('../soren91/cleanup_retention.mjs');

const DAY = 24 * 60 * 60 * 1000;

function writeState(dir, state) {
  mkdirSync(join(dir, 'tmp', 'state'), { recursive: true });
  writeFileSync(join(dir, 'tmp', 'state', 'improve_daily.json'), JSON.stringify(state));
}

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-retention-'));
  const now = Date.now();
  const old = new Date(now - 5 * DAY);
  const fresh = new Date(now - 1 * DAY);
  writeState(dir, { lastConsumedGame: 1, pendingPr: null });

  // 消費済みかつ古い game #1: 削除対象。
  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  writeFileSync(join(dir, 'tmp', 'summaries', 'game_0001.json'), '{}');
  writeFileSync(join(dir, 'tmp', 'summaries', 'ranking_0001.png'), 'x');
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  writeFileSync(join(dir, 'game_history', 'game_0001.jsonl'), '{}');
  mkdirSync(join(dir, 'tmp', 'game_screenshots', 'game_0001'), { recursive: true });
  writeFileSync(join(dir, 'tmp', 'game_screenshots', 'game_0001', 'a.png'), 'x');
  for (const p of [
    join(dir, 'tmp', 'summaries', 'game_0001.json'),
    join(dir, 'tmp', 'summaries', 'ranking_0001.png'),
    join(dir, 'game_history', 'game_0001.jsonl'),
    join(dir, 'tmp', 'game_screenshots', 'game_0001'),
  ]) {
    utimesSync(p, old, old);
  }

  // 未消費 game #2: 日付に関係なく保持。ここでは新しい入力。
  writeFileSync(join(dir, 'tmp', 'summaries', 'game_0002.json'), '{}');
  utimesSync(join(dir, 'tmp', 'summaries', 'game_0002.json'), fresh, fresh);
  return { dir, now, old };
}

test('cleanupRetention deletes only old consumed game artifacts', () => {
  const { dir, now } = fixture();
  try {
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(r.removed, 4);
    assert.ok(!existsSync(join(dir, 'tmp', 'summaries', 'game_0001.json')));
    assert.ok(!existsSync(join(dir, 'game_history', 'game_0001.jsonl')));
    assert.ok(!existsSync(join(dir, 'tmp', 'game_screenshots', 'game_0001')));
    assert.ok(existsSync(join(dir, 'tmp', 'summaries', 'game_0002.json')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention keeps old unconsumed inputs when external improvement is delayed', () => {
  const { dir, now, old } = fixture();
  try {
    const pendingSummary = join(dir, 'tmp', 'summaries', 'game_0002.json');
    const pendingHistory = join(dir, 'game_history', 'game_0002.jsonl');
    writeFileSync(pendingHistory, '{}');
    utimesSync(pendingSummary, old, old);
    utimesSync(pendingHistory, old, old);

    const r = cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(r.removed, 4);
    assert.ok(existsSync(pendingSummary));
    assert.ok(existsSync(pendingHistory));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention protects the explicit pending PR range even if state is inconsistent', () => {
  const { dir, now, old } = fixture();
  try {
    writeState(dir, { lastConsumedGame: 2, pendingPr: { fromGame: 1, toGame: 2 } });
    const path = join(dir, 'tmp', 'summaries', 'game_0001.json');
    utimesSync(path, old, old);
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(r.removed, 0);
    assert.ok(existsSync(path));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention fails closed when consumption state is missing', () => {
  const { dir, now } = fixture();
  try {
    rmSync(join(dir, 'tmp', 'state', 'improve_daily.json'));
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(r.removed, 0);
    assert.equal(r.errors, 1);
    assert.ok(existsSync(join(dir, 'tmp', 'summaries', 'game_0001.json')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention dry-run keeps everything', () => {
  const { dir, now } = fixture();
  try {
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now, dryRun: true });
    assert.equal(r.removed, 4);
    assert.equal(readdirSync(join(dir, 'tmp', 'summaries')).length, 3);
    assert.ok(existsSync(join(dir, 'game_history', 'game_0001.jsonl')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention never touches strategy.mjs or state', () => {
  const { dir, now } = fixture();
  try {
    writeFileSync(join(dir, 'strategy.mjs'), 'export function decide(){}');
    const old = new Date(now - 30 * DAY);
    utimesSync(join(dir, 'strategy.mjs'), old, old);
    cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.ok(existsSync(join(dir, 'strategy.mjs')));
    assert.ok(existsSync(join(dir, 'tmp', 'state', 'improve_daily.json')));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

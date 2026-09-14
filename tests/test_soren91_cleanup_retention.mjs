import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync, utimesSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const { cleanupRetention } = await import('../soren91/cleanup_retention.mjs');

const DAY = 24 * 60 * 60 * 1000;

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-retention-'));
  const now = Date.now();
  const old = new Date(now - 5 * DAY);
  const fresh = new Date(now - 1 * DAY);
  // 旧 (5日前): 消える
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
  // 新 (1日前): 残る
  writeFileSync(join(dir, 'tmp', 'summaries', 'game_0002.json'), '{}');
  utimesSync(join(dir, 'tmp', 'summaries', 'game_0002.json'), fresh, fresh);
  return { dir, now };
}

test('cleanupRetention deletes only entries older than N days', () => {
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
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

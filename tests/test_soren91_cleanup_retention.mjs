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

  mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
  mkdirSync(join(dir, 'game_history'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'game_screenshots', 'game_0001'), { recursive: true });
  mkdirSync(join(dir, 'tmp', 'state'), { recursive: true });

  const oldArtifacts = [
    join(dir, 'tmp', 'summaries', 'game_0001.json'),
    join(dir, 'tmp', 'summaries', 'ranking_0001.png'),
    join(dir, 'game_history', 'game_0001.jsonl'),
  ];
  for (const path of oldArtifacts) {
    writeFileSync(path, 'x');
    utimesSync(path, old, old);
  }
  writeFileSync(join(dir, 'tmp', 'game_screenshots', 'game_0001', 'a.png'), 'x');
  utimesSync(join(dir, 'tmp', 'game_screenshots', 'game_0001'), old, old);

  const freshSummary = join(dir, 'tmp', 'summaries', 'game_0002.json');
  writeFileSync(freshSummary, '{}');
  utimesSync(freshSummary, fresh, fresh);

  writeFileSync(join(dir, 'tmp', 'state', 'manual-review.json'), '{}');
  return { dir, now, oldArtifacts, freshSummary };
}

test('cleanupRetention removes old managed evidence by age without improve_daily state', () => {
  const { dir, now, oldArtifacts, freshSummary } = fixture();
  try {
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(r.removed, 4);
    for (const path of oldArtifacts) assert.equal(existsSync(path), false);
    assert.equal(existsSync(join(dir, 'tmp', 'game_screenshots', 'game_0001')), false);
    assert.equal(existsSync(freshSummary), true);
    assert.equal(r.errors, 0);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention dry-run reports removals but keeps files', () => {
  const { dir, now } = fixture();
  try {
    const r = cleanupRetention({ runtimeDir: dir, days: 3, now, dryRun: true });
    assert.equal(r.removed, 4);
    assert.equal(readdirSync(join(dir, 'tmp', 'summaries')).length, 3);
    assert.equal(existsSync(join(dir, 'game_history', 'game_0001.jsonl')), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('cleanupRetention ignores unknown files and never touches strategy/state', () => {
  const { dir, now } = fixture();
  try {
    const strategy = join(dir, 'strategy.mjs');
    const unknown = join(dir, 'tmp', 'summaries', 'notes.txt');
    writeFileSync(strategy, 'export function decide(){}');
    writeFileSync(unknown, 'keep');
    const old = new Date(now - 30 * DAY);
    utimesSync(strategy, old, old);
    utimesSync(unknown, old, old);
    cleanupRetention({ runtimeDir: dir, days: 3, now });
    assert.equal(existsSync(strategy), true);
    assert.equal(existsSync(unknown), true);
    assert.equal(existsSync(join(dir, 'tmp', 'state', 'manual-review.json')), true);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

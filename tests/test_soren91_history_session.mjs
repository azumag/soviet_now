import test from 'node:test';
import assert from 'node:assert/strict';
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { archivePartialHistories } from '../soren91/archive_partial_history.mjs';

function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-history-session-'));
  const historyDir = join(dir, 'game_history');
  mkdirSync(historyDir, { recursive: true });
  return { dir, historyDir };
}

test('archives a pre-restart latest history without changing completed evidence', () => {
  const { dir, historyDir } = fixture();
  try {
    const latest = join(historyDir, 'latest_0007.jsonl');
    const completed = join(historyDir, 'game_0006.jsonl');
    writeFileSync(latest, '{"turn":0}\n{"turn":1}\n');
    writeFileSync(completed, '{"turn":9}\n');

    const result = archivePartialHistories({ historyDir, now: 1789700000000 });
    assert.deepEqual(result, { archived: 1 });
    assert.equal(existsSync(latest), false);
    assert.equal(readFileSync(completed, 'utf8'), '{"turn":9}\n');

    const names = readdirSync(historyDir).sort();
    assert.deepEqual(names, [
      'abandoned_0007_1789700000000_0.jsonl',
      'game_0006.jsonl',
    ]);
    assert.equal(
      readFileSync(join(historyDir, 'abandoned_0007_1789700000000_0.jsonl'), 'utf8'),
      '{"turn":0}\n{"turn":1}\n',
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('archives every prior child session and avoids archive-name collisions', () => {
  const { dir, historyDir } = fixture();
  try {
    writeFileSync(join(historyDir, 'latest_0007.jsonl'), 'seven');
    writeFileSync(join(historyDir, 'latest_0012.jsonl'), 'twelve');
    writeFileSync(join(historyDir, 'abandoned_0007_1789700000000_0.jsonl'), 'older');

    const result = archivePartialHistories({ historyDir, now: 1789700000000 });
    assert.deepEqual(result, { archived: 2 });
    assert.equal(
      readFileSync(join(historyDir, 'abandoned_0007_1789700000000_1.jsonl'), 'utf8'),
      'seven',
    );
    assert.equal(
      readFileSync(join(historyDir, 'abandoned_0012_1789700000000_0.jsonl'), 'utf8'),
      'twelve',
    );
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('runner archives partial evidence before every main.mjs child launch', () => {
  const source = readFileSync(new URL('../soren91/run_player_loop.sh', import.meta.url), 'utf8');
  const archiveCall = source.indexOf('node archive_partial_history.mjs');
  const mainLaunch = source.indexOf('node main.mjs');
  assert.ok(archiveCall >= 0, 'archive helper must be invoked');
  assert.ok(mainLaunch > archiveCall, 'archive helper must run before main child launch');
  assert.match(source, /if ! node archive_partial_history\.mjs/);
  assert.match(source, /partial history archival failed; refusing child launch/);
});

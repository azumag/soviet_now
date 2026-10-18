import test from 'node:test';
import assert from 'node:assert/strict';
import {
  appendFileSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  utimesSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';

import { archivePartialHistories, archiveLatestHistory } from '../soren91/archive_partial_history.mjs';
import { archiveCriticalTurnScreenshots } from '../soren91/critical_turn_screenshots.mjs';

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

function historyLines(turns) {
  return turns.map(turn => JSON.stringify({ turn })).join('\n') + '\n';
}

function readTurns(path) {
  return readFileSync(path, 'utf8')
    .split('\n')
    .filter(line => line.trim().length > 0)
    .map(line => JSON.parse(line).turn);
}

function assertContiguousFromZero(turns) {
  assert.ok(turns.length > 0, 'completed history must not be empty');
  assert.deepEqual(turns, turns.map((_, index) => index));
}

// Mirror the runtime lifecycle: each game snapshots its strategy before capture.
function makeGameBoundary(root, outputDir) {
  const dir = join(root, 'strategy_snapshots');
  mkdirSync(dir, { recursive: true });
  const path = join(dir, `${basename(outputDir)}_strategy.mjs`);
  writeFileSync(path, '// fixed game strategy\n');
  const beforeCapture = (Date.now() - 60_000) / 1000;
  utimesSync(path, beforeCapture, beforeCapture);
}

test('archiveLatestHistory isolates one game session and keeps other evidence', () => {
  const { dir, historyDir } = fixture();
  try {
    writeFileSync(join(historyDir, 'latest_0007.jsonl'), historyLines([0, 1, 2]));
    writeFileSync(join(historyDir, 'latest_0012.jsonl'), historyLines([0]));
    writeFileSync(join(historyDir, 'game_0006.jsonl'), historyLines([0, 1]));

    const archived = archiveLatestHistory(historyDir, 7, { now: 1789700000000 });
    assert.equal(archived, join(historyDir, 'abandoned_0007_1789700000000_0.jsonl'));
    assert.equal(existsSync(join(historyDir, 'latest_0007.jsonl')), false);
    assert.equal(readFileSync(archived, 'utf8'), historyLines([0, 1, 2]));
    // Untouched: other in-flight session and completed evidence.
    assert.equal(readFileSync(join(historyDir, 'latest_0012.jsonl'), 'utf8'), historyLines([0]));
    assert.equal(readFileSync(join(historyDir, 'game_0006.jsonl'), 'utf8'), historyLines([0, 1]));

    assert.equal(archiveLatestHistory(historyDir, 7, { now: 1789700000000 }), null);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('restart then completion keeps a single contiguous session (issue #438)', () => {
  const { dir, historyDir } = fixture();
  try {
    // Pre-restart partial session for the same game number (production
    // game_0007 shape: turns 0..10 written before the restart).
    writeFileSync(join(historyDir, 'latest_0007.jsonl'), historyLines([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]));

    // gameLoop session start on process restart: separate, never append onto.
    const archived = archiveLatestHistory(historyDir, 7, { now: 1789700000000 });
    assert.ok(archived);

    // New logical session writes turn=0.. onto a fresh file, then game over
    // renames latest_N to the completed game_N (mirrors handleGameOver).
    const latest = join(historyDir, 'latest_0007.jsonl');
    const completed = join(historyDir, 'game_0007.jsonl');
    for (let turn = 0; turn <= 11; turn += 1) {
      appendFileSync(latest, JSON.stringify({ turn }) + '\n');
    }
    renameSync(latest, completed);

    // Completed history is exactly the post-restart session: contiguous,
    // with no ...N -> 0 turn reset mixed in.
    assertContiguousFromZero(readTurns(completed));
    // The abandoned pre-restart session is preserved separately, byte intact.
    assert.equal(readTurns(archived).length, 11);
    assertContiguousFromZero(readTurns(archived));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('evidence consumer uses only the completed single-session history', () => {
  const root = mkdtempSync(join(tmpdir(), 'soren91-history-session-evidence-'));
  try {
    const screenshotDir = join(root, 'screenshots');
    const outputDir = join(root, 'game_0007');
    mkdirSync(screenshotDir, { recursive: true });
    makeGameBoundary(root, outputDir);
    for (let turn = 0; turn <= 11; turn += 1) {
      writeFileSync(join(screenshotDir, `turn_${String(turn).padStart(4, '0')}.png`), `frame-${turn}`);
    }

    // Pre-fix shape (production game_0007): old partial + completed session
    // concatenated. The consumer must refuse to attribute either session.
    const mixed = join(root, 'mixed_0007.jsonl');
    writeFileSync(mixed, historyLines([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]));
    const refused = archiveCriticalTurnScreenshots({ screenshotDir, outputDir, historyFile: mixed });
    assert.equal(refused.historyStatus, 'discontinuous');
    assert.deepEqual(refused.names, []);

    // Post-fix completed history: the single contiguous session only.
    const completed = join(root, 'game_0007.jsonl');
    writeFileSync(completed, historyLines([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]));
    const accepted = archiveCriticalTurnScreenshots({ screenshotDir, outputDir, historyFile: completed });
    assert.equal(accepted.historyStatus, 'ok');
    assert.ok(accepted.names.length > 0);
    assert.deepEqual(readdirSync(outputDir).sort(), accepted.names.slice().sort());
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('gameLoop separates pre-existing latest history instead of appending onto it', () => {
  const source = readFileSync(new URL('../soren91/main.mjs', import.meta.url), 'utf8');
  const gameLoopStart = source.indexOf('async function gameLoop(');
  assert.ok(gameLoopStart >= 0, 'gameLoop must exist');
  const afterStart = source.slice(gameLoopStart);
  assert.match(afterStart, /archiveLatestHistory\(HISTORY_DIR, gameNumber\)/);
  // Abandoned partials are preserved, never silently deleted.
  assert.ok(!source.includes('unlinkSync(historyFile)'), 'latest history must be archived, not unlinked');
});

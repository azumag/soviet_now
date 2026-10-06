import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, utimesSync, symlinkSync, truncateSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { archiveCriticalTurnScreenshots, selectCriticalSnapshotNames } from '../soren91/critical_turn_screenshots.mjs';

function fixture(t, { marker = true } = {}) {
  const root = mkdtempSync(join(tmpdir(), 'soren91-session-shots-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const screenshotDir = join(root, 'tmp/screenshots');
  const outputDir = join(root, 'tmp/game_screenshots/game_0104');
  const historyFile = join(root, 'game_history/latest_0104.jsonl');
  const markerPath = join(root, 'tmp/strategy_snapshots/game_0104_strategy.mjs');
  const startedAt = Date.now() - 60_000;
  mkdirSync(screenshotDir, { recursive: true });
  mkdirSync(join(root, 'game_history'), { recursive: true });
  mkdirSync(join(root, 'tmp/strategy_snapshots'), { recursive: true });
  function timedFile(path, body, time) {
    writeFileSync(path, body);
    utimesSync(path, time / 1000, time / 1000);
    return path;
  }
  if (marker) timedFile(markerPath, '// game strategy snapshot\n', startedAt);
  const shot = (name, body = name, offsetMs = 10_000) => timedFile(join(screenshotDir, name), body, startedAt + offsetMs);
  const archive = () => archiveCriticalTurnScreenshots({ screenshotDir, outputDir, historyFile });
  return { root, screenshotDir, outputDir, historyFile, markerPath, startedAt, timedFile, shot, archive };
}

test('name-only selection reserves one slot per numeric turn', () => {
  const names = ['turn_0001.jpg', 'turn_1.png', 'turn_2.jpg', 'turn_2.png', 'turn_3.jpg', 'turn_3.png'];
  const selected = selectCriticalSnapshotNames(names, 3, []);
  assert.equal(new Set(selected.map(name => Number(name.match(/turn_(\d+)/)[1]))).size, 3);
  assert.deepEqual(selected, selectCriticalSnapshotNames(names.reverse(), 3, []));
});

test('old PNG and old high-turn frames never enter current JPEG archive', t => {
  const f = fixture(t);
  for (const turn of [1, 2, 3]) {
    f.shot(`turn_${turn}.png`, 'previous game', -10_000);
    f.shot(`turn_${turn}.jpg`, `current ${turn}`, 10_000 + turn);
  }
  f.shot('turn_99.png', 'old high turn', -5000);
  const result = f.archive();
  assert.deepEqual(result.names, ['turn_1.jpg', 'turn_2.jpg', 'turn_3.jpg']);
  assert.equal(result.sessionStatus, 'ok');
  assert.deepEqual(readdirSync(f.outputDir).sort(), result.names);
});

test('PNG kill switch chooses newest current-game format for each turn', t => {
  const f = fixture(t);
  f.shot('turn_0003.jpg', 'older current', 10_000);
  f.shot('turn_3.png', 'newer current', 20_000);
  assert.deepEqual(f.archive().names, ['turn_3.png']);
  assert.equal(readFileSync(join(f.outputDir, 'turn_3.png'), 'utf8'), 'newer current');
});

test('missing marker suppresses images instead of guessing session', t => {
  const f = fixture(t, { marker: false });
  f.shot('turn_0.jpg');
  const result = f.archive();
  assert.equal(result.archived, 0);
  assert.equal(result.sessionStatus, 'missing-or-invalid');
});

test('future marker, future capture, and same-tick boundary fail closed', t => {
  const f = fixture(t);
  f.shot('turn_0.png', 'boundary', 0);
  f.shot('turn_1.jpg', 'future', 120_000);
  assert.equal(f.archive().archived, 0);
  f.timedFile(f.markerPath, 'future marker', Date.now() + 120_000);
  assert.equal(f.archive().archived, 0);
});

test('symlink marker cannot establish a game boundary', t => {
  const f = fixture(t, { marker: false });
  const external = f.shot('turn_0.jpg');
  symlinkSync(external, f.markerPath);
  assert.equal(f.archive().archived, 0);
});

test('symlink, directory, empty and oversized aliases cannot displace safe image', t => {
  const f = fixture(t);
  const safe = f.shot('turn_3.jpg', 'safe');
  symlinkSync(safe, join(f.screenshotDir, 'turn_3.png'));
  mkdirSync(join(f.screenshotDir, 'turn_3_dir.jpeg'));
  f.shot('turn_3_empty.jpeg', '', 20_000);
  const large = f.shot('turn_3_large.png', 'large', 20_000);
  truncateSync(large, 8 * 1024 * 1024 + 1);
  assert.deepEqual(f.archive().names, ['turn_3.jpg']);
});

test('re-archiving removes only previously managed turn images', t => {
  const f = fixture(t);
  mkdirSync(f.outputDir, { recursive: true });
  writeFileSync(join(f.outputDir, 'turn_3.png'), 'stale output');
  writeFileSync(join(f.outputDir, 'turn_99.jpg'), 'stale output');
  writeFileSync(join(f.outputDir, 'notes.txt'), 'keep');
  f.shot('turn_3.jpg', 'current');
  assert.deepEqual(f.archive().names, ['turn_3.jpg']);
  assert.deepEqual(readdirSync(f.outputDir).sort(), ['notes.txt', 'turn_3.jpg']);
});

test('output symlink is not followed or overwritten', t => {
  const f = fixture(t);
  const external = f.shot('turn_1.jpg', 'external original');
  mkdirSync(join(f.root, 'tmp/game_screenshots'), { recursive: true });
  symlinkSync(f.screenshotDir, f.outputDir, 'dir');
  assert.throws(f.archive, /symlink|directory/i);
  assert.equal(readFileSync(external, 'utf8'), 'external original');
});

test('malformed history still samples only frames within the established game', t => {
  const f = fixture(t);
  writeFileSync(f.historyFile, '{broken}\n');
  for (let turn = 0; turn <= 5; turn++) f.shot(`turn_${turn}.png`);
  f.shot('turn_88.png', 'old', -5000);
  const result = f.archive();
  assert.equal(result.historyStatus, 'invalid');
  assert.deepEqual(result.names, ['turn_2.png', 'turn_3.png', 'turn_5.png']);
});

test('discontinuous history remains suppressed even with a valid game boundary', t => {
  const f = fixture(t);
  writeFileSync(f.historyFile, [0, 1, 0, 1].map(turn => JSON.stringify({ turn })).join('\n'));
  f.shot('turn_0.jpg');
  assert.equal(f.archive().historyStatus, 'discontinuous');
  assert.equal(f.archive().archived, 0);
});

test('first-turn evidence survives when game boundary is valid', t => {
  const f = fixture(t);
  f.shot('turn_0000.jpg', 'first current turn');
  writeFileSync(f.historyFile, '{"turn":0}\n');
  assert.deepEqual(f.archive().names, ['turn_0000.jpg']);
});

test('mismatched history and output game identity is rejected', t => {
  const f = fixture(t);
  f.shot('turn_0.jpg');
  const result = archiveCriticalTurnScreenshots({ screenshotDir: f.screenshotDir, outputDir: f.outputDir,
    historyFile: join(f.root, 'game_history/latest_0105.jsonl') });
  assert.equal(result.archived, 0);
});

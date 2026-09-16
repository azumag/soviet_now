import test from 'node:test';
import assert from 'node:assert/strict';
import {
  mkdtempSync,
  mkdirSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  archiveCriticalTurnScreenshots,
  selectCriticalSnapshotNames,
} from '../soren91/critical_turn_screenshots.mjs';

function makeTurnNames(lastTurn) {
  return Array.from({ length: lastTurn + 1 }, (_, turn) => `turn_${String(turn).padStart(4, '0')}.png`);
}

function historyRecord(turn, { confidence = 0.9, risk = 0, clearance = 1 } = {}) {
  return {
    turn,
    state: { confidence },
    decision: {
      hold: false,
      diagnostics: {
        risk,
        pathRisk: risk,
        clearance,
        minFutureClearance: clearance,
        merges: 0,
      },
    },
  };
}

test('critical turns take priority over the old early/middle/late sample', () => {
  assert.deepEqual(
    selectCriticalSnapshotNames(makeTurnNames(9), 3, [8, 2, 6]),
    ['turn_0002.png', 'turn_0006.png', 'turn_0008.png'],
  );
});

test('completed-game archive copies frames nearest the critical history turns', () => {
  const root = mkdtempSync(join(tmpdir(), 'soren91-critical-shots-'));
  try {
    const screenshotDir = join(root, 'screenshots');
    const outputDir = join(root, 'game_0001');
    const historyFile = join(root, 'latest_0001.jsonl');
    mkdirSync(screenshotDir, { recursive: true });
    for (const name of makeTurnNames(7)) writeFileSync(join(screenshotDir, name), name);

    const history = [
      historyRecord(0),
      historyRecord(1),
      historyRecord(2, { confidence: 0.4 }),
      historyRecord(3),
      historyRecord(4, { risk: 1, clearance: 0.3 }),
      historyRecord(5),
      historyRecord(6, { risk: 2, clearance: 0.05 }),
      historyRecord(7),
    ];
    writeFileSync(historyFile, history.map(record => JSON.stringify(record)).join('\n') + '\n');

    const result = archiveCriticalTurnScreenshots({ screenshotDir, outputDir, historyFile });
    assert.equal(result.historyStatus, 'ok');
    assert.deepEqual(result.preferredTurns, [6, 2, 7]);
    assert.deepEqual(result.names, ['turn_0002.png', 'turn_0006.png', 'turn_0007.png']);
    assert.deepEqual(readdirSync(outputDir).sort(), result.names);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test('malformed history keeps bounded legacy sampling instead of dropping evidence', () => {
  const root = mkdtempSync(join(tmpdir(), 'soren91-critical-shots-fallback-'));
  try {
    const screenshotDir = join(root, 'screenshots');
    const outputDir = join(root, 'game_0002');
    const historyFile = join(root, 'latest_0002.jsonl');
    mkdirSync(screenshotDir, { recursive: true });
    for (const name of makeTurnNames(5)) writeFileSync(join(screenshotDir, name), name);
    writeFileSync(historyFile, '{not-json}\n');

    const result = archiveCriticalTurnScreenshots({ screenshotDir, outputDir, historyFile });
    assert.equal(result.historyStatus, 'invalid');
    assert.deepEqual(result.preferredTurns, []);
    assert.deepEqual(result.names, ['turn_0002.png', 'turn_0003.png', 'turn_0005.png']);
    assert.deepEqual(readdirSync(outputDir).sort(), result.names);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { buildDailyEvidence } from '../soren91/daily_evidence.mjs';

test('unknown rank/turns stay null and are excluded from aggregates', () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-daily-evidence-null-'));
  try {
    mkdirSync(join(dir, 'tmp', 'summaries'), { recursive: true });
    mkdirSync(join(dir, 'game_history'), { recursive: true });
    writeFileSync(join(dir, 'tmp', 'summaries', 'game_0001.json'), JSON.stringify({
      gameNumber: 1,
      rank: null,
      turns: null,
      piecesAtEnd: null,
    }));
    writeFileSync(join(dir, 'game_history', 'game_0001.jsonl'), '{"turn":1}\n');

    const bundle = buildDailyEvidence(dir, 0);
    assert.equal(bundle.status, 'ready');
    assert.equal(bundle.entries[0].rank, null);
    assert.equal(bundle.entries[0].turns, null);
    assert.equal(bundle.metrics.meanRank, null);
    assert.equal(bundle.metrics.medianRank, null);
    assert.equal(bundle.metrics.meanTurns, null);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

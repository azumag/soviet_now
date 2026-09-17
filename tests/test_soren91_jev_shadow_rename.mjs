import test from 'node:test';
import assert from 'node:assert/strict';
import {
  appendFileSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { loadJevShadowConfig, runJevShadowFollower } from '../soren91/jev_shadow.mjs';

function record(turn) {
  return {
    turn,
    state: {
      pieces: [{ type: 2, x: 0, y: -4, r: 0.259, confidence: 0.95 }],
      next: { type: 2, confidence: 0.95 },
      nextPieces: [{ type: 2, confidence: 0.95 }, { type: 3, confidence: 0.95 }],
      canHold: true,
      garbage: { ratio: 0, gauge: 0, columns: [] },
      confidence: 0.95,
    },
    decision: {
      x: 0,
      hold: false,
      diagnostics: { risk: 0, pathRisk: 0, clearance: 2, reservationStatus: 'none' },
    },
  };
}

function payload() {
  return {
    model: 'jev-latest',
    answers: {
      decision_quality: {
        type: 'choice', choice: 'accept', confidence: 0.9,
        probabilities: { accept: 0.9, review: 0.08, reject: 0.02 },
      },
      destroys_near_term_merge: { type: 'noul', noul: 0.1 },
      survival_override_justified: { type: 'noul', noul: 0.9 },
      hold_preferred: { type: 'noul', noul: 0.1 },
      strategic_risk: {
        type: 'score', score: 0.3, confidence: 0.9,
        legend: { 0: 'ok', 1: 'minor', 2: 'review', 3: 'high', 4: 'clear' },
        probabilities: { 0: 0.8, 1: 0.15, 2: 0.04, 3: 0.01, 4: 0 },
      },
    },
    usage: { input_tokens: 100, output_tokens: 0 },
  };
}

test('follower drains final turns after latest history is renamed at round end', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-jev-rename-'));
  const historyDir = join(dir, 'game_history');
  mkdirSync(historyDir, { recursive: true });
  const latest = join(historyDir, 'latest_0007.jsonl');
  const finalized = join(historyDir, 'game_0007.jsonl');
  writeFileSync(latest, `${JSON.stringify(record(1))}\n`);

  const config = {
    ...loadJevShadowConfig({
      SOREN91_JEV_SHADOW_ENABLED: '1',
      TYPESAFE_API_KEY: 'test-key',
      SOREN91_JEV_POLL_MS: '20',
      SOREN91_JEV_TIMEOUT_MS: '100',
    }),
    backfill: false,
  };
  const fetchImpl = async () => ({ ok: true, status: 200, json: async () => payload() });
  let stop = false;

  try {
    const follower = runJevShadowFollower({
      runtimeDir: dir,
      config,
      fetchImpl,
      log: () => {},
      stopSignal: () => stop,
    });

    // Let startup establish the current offset, then append the last turn and
    // rename immediately, reproducing main.mjs round-finalization timing.
    await new Promise(resolve => setTimeout(resolve, 5));
    appendFileSync(latest, `${JSON.stringify(record(2))}\n`);
    renameSync(latest, finalized);

    await new Promise(resolve => setTimeout(resolve, 80));
    stop = true;
    await follower;

    const ledger = join(dir, 'tmp', 'jev_shadow', 'game_0007.jsonl');
    const rows = readFileSync(ledger, 'utf8').trim().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(rows.map(row => row.turn), [2]);
  } finally {
    stop = true;
    rmSync(dir, { recursive: true, force: true });
  }
});

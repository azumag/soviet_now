import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  buildJevRequest,
  buildJevState,
  evaluateJevTurn,
  JevShadowQueue,
  loadJevShadowConfig,
  runJevShadowFollower,
  shouldSample,
  validateJevResponse,
} from '../soren91/jev_shadow.mjs';

function record(turn = 7) {
  return {
    turn,
    state: {
      pieces: [
        { type: 3, x: -1.23456, y: -3.45678, r: 0.316, confidence: 0.94 },
      ],
      next: { type: 2, confidence: 0.98 },
      nextPieces: [
        { type: 2, confidence: 0.98 },
        { type: 3, confidence: 0.91 },
      ],
      hold: null,
      holdKnownEmpty: true,
      canHold: true,
      confidence: 0.93,
      garbage: {
        ratio: 0.1,
        gauge: 0.2,
        columns: [{ left: -2, right: -1.5, top: -4.1 }],
      },
      rawScreenshotPath: '/secret/path/turn.png',
      viewerComment: 'do not send me',
    },
    decision: {
      x: 0.75123,
      hold: false,
      reason: 'private free-form reason should not be sent',
      diagnostics: {
        version: 'beam-v2',
        risk: 0,
        pathRisk: 0,
        clearance: 1.23456,
        merges: 0,
        knownMergeReservations: 1,
        preservedReservations: 0,
        lostReservations: 1,
        reservationStatus: 'destroyed',
      },
    },
  };
}

function validPayload() {
  return {
    model: 'jev-latest',
    answers: {
      decision_quality: {
        type: 'choice',
        choice: 'review',
        confidence: 0.8,
        probabilities: { accept: 0.15, review: 0.8, reject: 0.05 },
      },
      destroys_near_term_merge: { type: 'noul', noul: 0.91 },
      survival_override_justified: { type: 'noul', noul: 0.2 },
      hold_preferred: { type: 'noul', noul: 0.6 },
      strategic_risk: {
        type: 'score',
        score: 3.2,
        confidence: 0.77,
        legend: { 0: 'ok', 1: 'minor', 2: 'review', 3: 'high', 4: 'clear' },
        probabilities: { 0: 0.02, 1: 0.04, 2: 0.18, 3: 0.46, 4: 0.3 },
      },
    },
    usage: { input_tokens: 432, output_tokens: 0 },
  };
}

function enabledConfig(overrides = {}) {
  return {
    ...loadJevShadowConfig({
      SOREN91_JEV_SHADOW_ENABLED: '1',
      TYPESAFE_API_KEY: 'ts-secret-test-key',
      SOREN91_JEV_TIMEOUT_MS: '50',
      SOREN91_JEV_MAX_QUEUE: '4',
      SOREN91_JEV_MAX_INFLIGHT: '1',
      SOREN91_JEV_SAMPLE_RATE: '1',
    }),
    ...overrides,
  };
}

test('Jev shadow is disabled by default and keeps Node-side configuration bounded', () => {
  const config = loadJevShadowConfig({});
  assert.equal(config.enabled, false);
  assert.equal(config.model, 'jev-latest');
  assert.equal(config.maxInflight, 1);
  assert.equal(config.maxQueue, 4);
  assert.equal(config.sampleRate, 1);
});

test('buildJevState sends compact structured gameplay state, not free-form/log-only fields', () => {
  const state = buildJevState(record(), 12);
  const serialized = JSON.stringify(state);
  assert.equal(state.game, 12);
  assert.equal(state.turn, 7);
  assert.equal(state.pieces[0].x, -1.235);
  assert.equal(state.strategyDecision.x, 0.751);
  assert.equal(state.strategyDecision.diagnostics.reservationStatus, 'destroyed');
  assert.doesNotMatch(serialized, /rawScreenshotPath|secret\/path|viewerComment|private free-form reason/);
});

test('buildJevRequest asks only for typed review signals', () => {
  const request = buildJevRequest(record(), 12, 'jev-latest');
  assert.equal(request.model, 'jev-latest');
  assert.equal(request.questions.decision_quality.type, 'choice');
  assert.equal(request.questions.destroys_near_term_merge.type, 'noul');
  assert.equal(request.questions.strategic_risk.type, 'score');
  assert.deepEqual(Object.keys(request.questions.decision_quality.criteria), ['accept', 'review', 'reject']);
  assert.deepEqual(Object.keys(request.questions).sort(), [
    'decision_quality',
    'destroys_near_term_merge',
    'hold_preferred',
    'strategic_risk',
    'survival_override_justified',
  ]);
});

test('validateJevResponse accepts the documented System One response shape', () => {
  assert.equal(validateJevResponse(validPayload()), true);
  const invalid = validPayload();
  invalid.answers.decision_quality.confidence = 1.5;
  assert.equal(validateJevResponse(invalid), false);
});

test('evaluateJevTurn returns typed data without exposing API key or response body on success', async () => {
  let captured;
  const fetchImpl = async (url, init) => {
    captured = { url, init };
    return { ok: true, status: 200, json: async () => validPayload() };
  };
  const result = await evaluateJevTurn({
    record: record(),
    gameNumber: 12,
    config: enabledConfig(),
    fetchImpl,
  });
  assert.equal(result.status, 'ok');
  assert.equal(result.answers.destroys_near_term_merge.noul, 0.91);
  assert.equal(result.usage.input_tokens, 432);
  assert.equal(captured.url, 'https://api.typesafe.ai/v1/systemone');
  assert.equal(captured.init.headers.Authorization, 'Bearer ts-secret-test-key');
  assert.doesNotMatch(JSON.stringify(result), /ts-secret-test-key/);
});

test('evaluateJevTurn maps auth, rate-limit and malformed responses without persisting provider bodies', async () => {
  for (const [status, expected] of [[401, 'auth_error'], [429, 'rate_limited'], [503, 'provider_error']]) {
    const result = await evaluateJevTurn({
      record: record(),
      gameNumber: 1,
      config: enabledConfig(),
      fetchImpl: async () => ({
        ok: false,
        status,
        json: async () => ({ secret_provider_body: 'must-not-leak' }),
      }),
    });
    assert.equal(result.status, expected);
    assert.doesNotMatch(JSON.stringify(result), /secret_provider_body|must-not-leak/);
  }

  const malformed = await evaluateJevTurn({
    record: record(),
    gameNumber: 1,
    config: enabledConfig(),
    fetchImpl: async () => ({ ok: true, status: 200, json: async () => ({ model: 'jev-latest' }) }),
  });
  assert.equal(malformed.status, 'invalid_response');
});

test('evaluateJevTurn times out with AbortController rather than blocking indefinitely', async () => {
  const fetchImpl = (_url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => {
      const error = new Error('aborted');
      error.name = 'AbortError';
      reject(error);
    }, { once: true });
  });
  const result = await evaluateJevTurn({
    record: record(),
    gameNumber: 1,
    config: enabledConfig({ timeoutMs: 5 }),
    fetchImpl,
  });
  assert.equal(result.status, 'timeout');
});

test('sampling can disable all requests deterministically', () => {
  assert.equal(shouldSample(0, () => 0), false);
  assert.equal(shouldSample(1, () => 0.999), true);
  assert.equal(shouldSample(0.5, () => 0.49), true);
  assert.equal(shouldSample(0.5, () => 0.51), false);
});

test('JevShadowQueue is bounded and does not apply backpressure to the caller', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-jev-queue-'));
  let release;
  const gate = new Promise(resolve => { release = resolve; });
  const fetchImpl = async () => {
    await gate;
    return { ok: true, status: 200, json: async () => validPayload() };
  };
  try {
    const queue = new JevShadowQueue({
      config: enabledConfig({ maxQueue: 2, maxInflight: 1 }),
      outputDir: dir,
      fetchImpl,
      random: () => 0,
    });
    assert.equal(queue.enqueue({ gameNumber: 1, record: record(1) }), true); // inflight
    assert.equal(queue.enqueue({ gameNumber: 1, record: record(2) }), true); // queued
    assert.equal(queue.enqueue({ gameNumber: 1, record: record(3) }), true); // queued
    assert.equal(queue.enqueue({ gameNumber: 1, record: record(4) }), false); // dropped immediately
    assert.equal(queue.dropped, 1);
    release();
    await new Promise(resolve => setTimeout(resolve, 30));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('follower skips pre-existing history by default and evaluates only newly appended turns', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-jev-follow-'));
  const historyDir = join(dir, 'game_history');
  mkdirSync(historyDir, { recursive: true });
  const history = join(historyDir, 'latest_0007.jsonl');
  writeFileSync(history, `${JSON.stringify(record(1))}\n`);
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    return { ok: true, status: 200, json: async () => validPayload() };
  };
  let stop = false;
  try {
    const follower = runJevShadowFollower({
      runtimeDir: dir,
      config: enabledConfig({ pollMs: 5, backfill: false }),
      fetchImpl,
      log: () => {},
      stopSignal: () => stop,
    });
    await new Promise(resolve => setTimeout(resolve, 15));
    assert.equal(calls, 0);
    writeFileSync(history, `${JSON.stringify(record(1))}\n${JSON.stringify(record(2))}\n`);
    await new Promise(resolve => setTimeout(resolve, 30));
    stop = true;
    await follower;
    assert.equal(calls, 1);
    const out = readFileSync(join(dir, 'tmp', 'jev_shadow', 'game_0007.jsonl'), 'utf8').trim().split('\n');
    assert.equal(out.length, 1);
    assert.equal(JSON.parse(out[0]).turn, 2);
  } finally {
    stop = true;
    rmSync(dir, { recursive: true, force: true });
  }
});

test('runner supervises Jev in a separate process and stops it with runner lifecycle', () => {
  const runner = readFileSync(new URL('../soren91/run_player_loop.sh', import.meta.url), 'utf8');
  assert.match(runner, /node "\$SCRIPT_DIR\/jev_shadow\.mjs" --runtime-dir "\$SCRIPT_DIR"/);
  assert.match(runner, /_stop_jev_shadow/);
  assert.match(runner, /_ensure_jev_shadow/);
  assert.match(runner, /SOREN91_JEV_SHADOW_ENABLED/);
  assert.doesNotMatch(runner, /node main\.mjs[^\n]*jev_shadow/);
});

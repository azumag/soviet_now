import test from 'node:test';
import assert from 'node:assert/strict';
import {
  createConnectionErrorProbe,
  isUnrecognizedObservation,
  unknownFrameLimit,
  DEFAULT_UNKNOWN_FRAME_LIMIT,
  MAX_UNKNOWN_FRAME_LIMIT,
  MIN_UNKNOWN_FRAME_LIMIT,
} from '../soren91/observation_guard.mjs';

test('unrecognized observations are the not-ready or unknown-current frames', () => {
  assert.equal(isUnrecognizedObservation({ ready: false, reason: 'settling' }), true);
  assert.equal(isUnrecognizedObservation({ ready: true, reason: 'unknown-current' }), true);
  assert.equal(isUnrecognizedObservation({ ready: true, reason: 'stable' }), false);
  assert.equal(isUnrecognizedObservation(undefined), false);
});

test('probe fires only after the bounded streak and outside waiting states', () => {
  const probe = createConnectionErrorProbe({ limit: 5 });
  for (let i = 0; i < 4; i += 1) {
    assert.equal(probe.observe({ ready: false, reason: 'unknown-current' }), i + 1);
    assert.equal(probe.shouldProbe('DROP'), false);
  }
  assert.equal(probe.observe({ ready: false, reason: 'unknown-current' }), 5);
  assert.equal(probe.shouldProbe('DROP'), true);
  assert.equal(probe.shouldProbe('MOVE'), true);
  // The waiting branch already probes connection errors on every frame.
  assert.equal(probe.shouldProbe('WAITING'), false);
  assert.equal(probe.shouldProbe('GAMEOVER'), false);
});

test('a recognized frame resets the streak and reset() clears it', () => {
  const probe = createConnectionErrorProbe({ limit: 5 });
  for (let i = 0; i < 6; i += 1) probe.observe({ ready: false, reason: 'unknown-current' });
  assert.equal(probe.streak, 6);
  assert.equal(probe.shouldProbe('DROP'), true);
  assert.equal(probe.observe({ ready: true, reason: 'stable' }), 0);
  assert.equal(probe.shouldProbe('DROP'), false);
  probe.observe({ ready: false, reason: 'unknown-current' });
  probe.reset();
  assert.equal(probe.streak, 0);
  assert.equal(probe.shouldProbe('DROP'), false);
});

test('limit parsing keeps the default for invalid or out-of-range values', () => {
  assert.equal(unknownFrameLimit({}), DEFAULT_UNKNOWN_FRAME_LIMIT);
  assert.equal(unknownFrameLimit({ SOREN91_CONNECTION_ERROR_UNKNOWN_FRAMES: '12' }), 12);
  for (const raw of ['4', '201', 'abc', '1.5', '', '0', '-3']) {
    assert.equal(
      unknownFrameLimit({ SOREN91_CONNECTION_ERROR_UNKNOWN_FRAMES: raw }),
      DEFAULT_UNKNOWN_FRAME_LIMIT,
      raw,
    );
  }
  assert.deepEqual([MIN_UNKNOWN_FRAME_LIMIT, MAX_UNKNOWN_FRAME_LIMIT], [5, 200]);
});

test('an out-of-range constructor limit falls back to the default', () => {
  assert.equal(createConnectionErrorProbe({ limit: 2 }).threshold, DEFAULT_UNKNOWN_FRAME_LIMIT);
  assert.equal(createConnectionErrorProbe({ limit: '9' }).threshold, DEFAULT_UNKNOWN_FRAME_LIMIT);
  assert.equal(createConnectionErrorProbe({ limit: 30 }).threshold, 30);
});

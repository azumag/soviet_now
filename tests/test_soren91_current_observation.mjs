import test from 'node:test';
import assert from 'node:assert/strict';
import { gateObservation, markDropSent, queueAdvanceEvidence } from '../soren91/observation_guard.mjs';

const calibration = () => ({
  coordinateSchema: 2,
  screen: { width: 1280, height: 720 },
  confidence: 0.82,
  method: 'deadline-floor',
  board: { left: 450, right: 800, top: 220, bottom: 636, width: 350, height: 416 },
  arena: { left: 450, right: 800, top: 130, bottom: 636, width: 350, height: 506 },
  hud: { top: 0, bottom: 130 },
});
const preview = (type, extra = {}) => ({ type, r: 0.207, confidence: 0.9, ...extra });
const queue = types => types.map(type => type == null ? null : preview(type));
const observation = (types = [1, 1, 1], x = 0, extra = {}) => {
  const nextPieces = queue(types);
  return {
    state: 'MOVE',
    pieces: [{ type: 1, x, y: -4, r: 0.207, confidence: 0.9 }],
    next: nextPieces[0],
    nextPieces,
    hold: null,
    holdObservedEmpty: false,
    garbage: { columns: [] },
    ...extra,
  };
};

// Make every motion case eligible for the remote fast path. A false advance
// must not hide behind a disabled path, a short gap, or an unchanged board.
function withRemoteCadence(fn) {
  const values = {
    SOREN91_REMOTE_CDP_URL: 'http://remote-cdp.invalid:9222',
    SOREN91_SINGLE_FRAME_ADVANCE: '1',
    SOREN91_SINGLE_FRAME_ADVANCE_MS: '1200',
    SOREN91_ADVANCE_SINGLE_EVIDENCE: '1',
    SOREN91_OBSERVATION_MAX_STALE_MS: '15000',
  };
  const saved = Object.fromEntries(Object.keys(values).map(key => [key, process.env[key]]));
  Object.assign(process.env, values);
  try { return fn(); }
  finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function assertBlocked(result, reason) {
  assert.equal(result.state, 'DROP');
  assert.equal(result.perception.ready, false);
  assert.equal(result.perception.reason, reason);
}

for (const [label, types] of [
  ['unchanged future previews', [null, 2, 3]],
  ['advanced future previews', [null, 3, 4]],
]) {
  test(`a missing current piece stays unknown with ${label}`, () => withRemoteCadence(() => {
    const cal = calibration();
    gateObservation(observation([1, 2, 3]), cal, 1000);
    const missing = gateObservation(observation(types, 1), cal, 4000);
    assertBlocked(missing, 'unknown-current');
    assert.equal(missing.next, null);
    assert.equal(missing.nextPieces[0], null);
  }));
}

test('unchanged observed slots provide no queue-advance evidence', () => {
  for (const types of [[1, 1, 1], [1, 1, 2], [1, 2, 3], [1, null, null]]) {
    assert.equal(queueAdvanceEvidence(queue(types), queue(types)).evidence, 0, JSON.stringify(types));
  }
});

test('repeated identical previews cannot bypass motion, then a still board becomes ready', () => withRemoteCadence(() => {
  const cal = calibration();
  assertBlocked(gateObservation(observation([1, 1, 1], 0), cal, 1000), 'confirm-frame');
  const moving = gateObservation(observation([1, 1, 1], 1), cal, 4000);
  assertBlocked(moving, 'board-moving');
  assert.equal(moving.perception.queueTransition, 'same');
  const settled = gateObservation(observation([1, 1, 1], 1), cal, 7000);
  assert.equal(settled.state, 'MOVE');
  assert.equal(settled.perception.reason, 'stable');
}));

test('a sent drop can use a settled first post-drop board delta instead of a second remote capture', () => withRemoteCadence(() => {
  const cal = calibration();
  assertBlocked(gateObservation(observation([1, 1, 1], 0), cal, 1000), 'confirm-frame');
  assert.equal(markDropSent(cal, 1500), true);
  const advanced = gateObservation(observation([1, 1, 1], 1), cal, 5000);
  assert.equal(advanced.state, 'MOVE');
  assert.equal(advanced.perception.reason, 'stable-slow-advance-postdrop');
}));

test('post-drop board delta still waits when the first frame arrives before the conservative settle floor', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 1, 1], 0), cal, 1000);
  assert.equal(markDropSent(cal, 1500), true);
  assertBlocked(gateObservation(observation([1, 1, 1], 1), cal, 3500), 'board-moving');
  const settled = gateObservation(observation([1, 1, 1], 1), cal, 5000);
  assert.equal(settled.state, 'MOVE');
  assert.equal(settled.perception.reason, 'stable');
}));

test('a sent drop can accept a changed current after the normal time floor even when noisy future slots conflict', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 2, 3], 0), cal, 1000);
  assert.equal(markDropSent(cal, 1500), true);
  const advanced = gateObservation(observation([4, 9, 8], 1), cal, 3000);
  assert.equal(advanced.state, 'MOVE');
  assert.equal(advanced.perception.reason, 'stable-slow-advance-postdrop');
  assert.notEqual(advanced.perception.queueTransition, 'advanced');
}));

test('a changed current without a physical board delta is still treated as unconfirmed', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 2, 3], 0), cal, 1000);
  assert.equal(markDropSent(cal, 1500), true);
  assertBlocked(gateObservation(observation([4, 9, 8], 0), cal, 5000), 'preview-changed');
}));

test('post-drop fast path is one-shot and never bypasses same-type vertical motion', () => withRemoteCadence(() => {
  const cal = calibration();
  const positioned = (types, y, x = 0) => {
    const state = observation(types, x);
    state.next.y = y;
    return state;
  };
  gateObservation(positioned([1, 1, 2], 4.32), cal, 1000);
  assert.equal(markDropSent(cal, 1500), true);
  assertBlocked(gateObservation(positioned([1, 2, 3], 4.02, 1), cal, 5000), 'board-moving');
  const settled = gateObservation(positioned([1, 2, 3], 4.02, 1), cal, 8000);
  assert.equal(settled.state, 'MOVE');
  assert.equal(settled.perception.reason, 'stable');
}));

test('post-drop single-frame optimization has an explicit kill switch', () => withRemoteCadence(() => {
  const saved = process.env.SOREN91_POSTDROP_SINGLE_FRAME;
  process.env.SOREN91_POSTDROP_SINGLE_FRAME = '0';
  try {
    const cal = calibration();
    gateObservation(observation([1, 1, 1], 0), cal, 1000);
    assert.equal(markDropSent(cal, 1500), true);
    assertBlocked(gateObservation(observation([1, 1, 1], 1), cal, 5000), 'board-moving');
  } finally {
    if (saved === undefined) delete process.env.SOREN91_POSTDROP_SINGLE_FRAME;
    else process.env.SOREN91_POSTDROP_SINGLE_FRAME = saved;
  }
}));

test('fresh future-slot changes can prove an advance even when the current type repeats', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 1, 2], 0), cal, 1000);
  const advanced = gateObservation(observation([1, 2, 3], 1), cal, 4000);
  assert.equal(advanced.state, 'MOVE');
  assert.equal(advanced.perception.queueTransition, 'advanced');
  assert.equal(advanced.perception.reason, 'stable-slow-advance');
}));

test('temporally filled future slots cannot become raw advance evidence on the next frame', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 1, 2], 0), cal, 1000);
  const filled = gateObservation(observation([1, null, null], 0), cal, 4000);
  assert.equal(filled.state, 'MOVE');
  assert.deepEqual(filled.nextPieces.map(piece => piece?.type ?? null), [1, 1, 2]);
  assert.deepEqual(filled.nextPieces.slice(1).map(piece => piece.temporalSource), ['same-turn', 'same-turn']);

  const changed = gateObservation(observation([1, 2, 3], 1), cal, 7000);
  assertBlocked(changed, 'board-moving');
  assert.notEqual(changed.perception.queueTransition, 'advanced');
  const settled = gateObservation(observation([1, 2, 3], 1), cal, 10000);
  assert.equal(settled.state, 'MOVE');
  assert.equal(settled.perception.reason, 'stable');
}));

test('a current piece returning after a miss needs a fresh confirmation before MOVE', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 2, 3]), cal, 1000);
  assertBlocked(gateObservation(observation([null, 2, 3]), cal, 4000), 'unknown-current');
  assertBlocked(gateObservation(observation([2, 3, 4], 1), cal, 7000), 'confirm-frame');
  const confirmed = gateObservation(observation([2, 3, 4], 1), cal, 10000);
  assert.equal(confirmed.state, 'MOVE');
  assert.equal(confirmed.perception.reason, 'stable');
}));

for (const [label, invalid] of [
  ['type zero', preview(0)],
  ['type above the terminal type', preview(16)],
  ['fractional type', preview(1.5)],
  ['string type', preview('1')],
  ['low confidence', preview(1, { confidence: 0.57 })],
  ['fallback recognition', preview(1, { fallback: true })],
]) {
  test(`an invalid current (${label}) is never restored from a previous observation`, () => withRemoteCadence(() => {
    const cal = calibration();
    gateObservation(observation([1, 1, 1]), cal, 1000);
    const result = gateObservation(observation([1, 1, 1], 1, {
      next: invalid, nextPieces: [invalid, preview(1), preview(1)],
    }), cal, 4000);
    assertBlocked(result, 'unknown-current');
    assert.equal(result.next, null);
    assert.equal(result.nextPieces[0], null);
  }));
}

for (const target of ['input queue', 'returned stabilized queue']) {
  test(`mutating the ${target} cannot rewrite the prior raw observation`, () => withRemoteCadence(() => {
    const cal = calibration();
    const input = observation([1, 1, 2], 0);
    const first = gateObservation(input, cal, 1000);
    const mutated = target === 'input queue' ? input.nextPieces : first.nextPieces;
    mutated[1].type = 2;
    mutated[2].type = 3;

    const advanced = gateObservation(observation([1, 2, 3], 1), cal, 4000);
    assert.equal(advanced.state, 'MOVE');
    assert.equal(advanced.perception.queueTransition, 'advanced');
    assert.equal(advanced.perception.reason, 'stable-slow-advance');
  }));
}

test('mutating a caller-owned board piece cannot hide later motion', () => withRemoteCadence(() => {
  const cal = calibration();
  const input = observation([1, 1, 1], 0);
  gateObservation(input, cal, 1000);
  input.pieces[0].x = 1;
  assertBlocked(gateObservation(observation([1, 1, 1], 1), cal, 4000), 'board-moving');
}));

for (const [label, change] of [
  ['arena and matching HUD boundary', cal => {
    cal.arena.top = 140;
    cal.arena.height = 496;
    cal.hud.bottom = 140;
  }],
  ['HUD top alone', cal => { cal.hud.top = 10; }],
]) {
  test(`changing the ${label} requires fresh geometry confirmation`, () => withRemoteCadence(() => {
    const cal = calibration();
    gateObservation(observation([1, 1, 2], 0), cal, 1000);
    change(cal);
    // A genuine raw advance must not bypass a changed coordinate contract.
    assertBlocked(gateObservation(observation([1, 2, 3], 1), cal, 4000), 'confirm-frame');
    assert.equal(gateObservation(observation([1, 2, 3], 1), cal, 7000).state, 'MOVE');
  }));
}

test('unobserved HOLD never becomes known-empty across otherwise stable frames', () => withRemoteCadence(() => {
  const cal = calibration();
  for (const now of [1000, 4000, 7000]) {
    const result = gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: false }), cal, now);
    assert.equal(result.holdKnownEmpty, false);
    assert.equal(result.hold, null);
  }
}));

test('two explicit empty HOLD observations preserve the existing known-empty behavior', () => withRemoteCadence(() => {
  const cal = calibration();
  const first = gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: true }), cal, 1000);
  assert.equal(first.holdKnownEmpty, false);
  const second = gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: true }), cal, 4000);
  assert.equal(second.state, 'MOVE');
  assert.equal(second.holdKnownEmpty, true);
  assert.equal(second.perception.reason, 'stable-hold-empty');
}));

test('an unknown HOLD observation interrupts consecutive empty evidence', () => withRemoteCadence(() => {
  const cal = calibration();
  gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: true }), cal, 1000);
  assert.equal(gateObservation(observation(), cal, 4000).holdKnownEmpty, false);
  assert.equal(gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: true }), cal, 7000).holdKnownEmpty, false);
  assert.equal(gateObservation(observation([1, 1, 1], 0, { holdObservedEmpty: true }), cal, 10000).holdKnownEmpty, true);
}));

test('a same-type candidate falling through the spawn band cannot bypass vertical-motion confirmation', () => withRemoteCadence(() => {
  const cal = calibration();
  const positioned = (types, y) => {
    const state = observation(types);
    state.next.y = y;
    return state;
  };
  gateObservation(positioned([1, 1, 2], 4.32), cal, 1000);
  // Future slots shifted, but the same-type candidate moved vertically. Its
  // position cannot be discarded merely because the physical stack is still.
  assertBlocked(gateObservation(positioned([1, 2, 3], 4.02), cal, 4000), 'board-moving');
  assert.equal(gateObservation(positioned([1, 2, 3], 4.02), cal, 7000).state, 'MOVE');
}));

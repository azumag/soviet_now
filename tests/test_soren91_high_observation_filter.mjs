import test from 'node:test';
import assert from 'node:assert/strict';
import { decide, TYPE_RADII } from '../soren91/strategy.mjs';

const piece = (type, x = 0, y = -5 + TYPE_RADII[type], extra = {}) => ({
  type,
  r: TYPE_RADII[type],
  x,
  y,
  confidence: 0.9,
  ...extra,
});

test('isolated unsupported high observation remains risk-bearing', () => {
  const columns = [{ left: -3.5, right: 3.5, top: -2.512 }];
  const supportedLow = piece(4, -1.2, -2.12, { confidence: 0.9 });
  const isolatedHigh = piece(1, 0, 2.68, { confidence: 0.84 });
  const current = piece(2);

  const decision = decide({
    state: 'MOVE',
    pieces: [supportedLow, isolatedHigh],
    next: current,
    nextPieces: [current],
    garbage: { ratio: 0.265, height: -2.512, gauge: 0, columns },
  });

  assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(decision));
  assert.ok(decision.diagnostics.risk >= 1, JSON.stringify(decision));
});

for (const { name, y, confidence, risk } of [
  { name: 'warning', y: 2.68, confidence: 0.84, risk: 1 },
  { name: 'fatal at certainty boundary', y: 3.1, confidence: 0.6, risk: 2 },
]) {
  test(`mixed-confidence overlap retains the ${name} observation in either order`, () => {
    const supportedLow = piece(4, -1.2);
    const high = piece(1, 0, y, { confidence });
    const duplicate = piece(1, 0.1, y, { confidence: 0.599 });
    const current = piece(2);
    const state = { state: 'MOVE', next: current, nextPieces: [current] };
    const retained = decide({ ...state, pieces: [supportedLow, high] });
    assert.equal(retained.diagnostics.risk, risk, JSON.stringify(retained));

    for (const pair of [[high, duplicate], [duplicate, high]]) {
      const pieces = [supportedLow, ...pair];
      const before = structuredClone(pieces);
      const decision = decide({ ...state, pieces });
      assert.equal(decision.diagnostics.risk, risk, JSON.stringify(decision));
      assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 1, JSON.stringify(decision));
      assert.equal(decision.x, retained.x);
      assert.equal(decision.diagnostics.clearance, retained.diagnostics.clearance);
      assert.deepEqual(pieces, before, 'filter must not mutate the observation');
    }
  });
}

test('two uncertain overlaps cannot remove their shared certain fatal observation', () => {
  const supportedLow = piece(4, -1.2);
  const high = piece(1, 0, 3.1, { confidence: 0.9 });
  const left = piece(1, -0.1, 3.1, { confidence: 0.45 });
  const right = piece(1, 0.1, 3.1, { confidence: 0.5 });
  for (const cluster of [[high, left, right], [left, right, high], [right, high, left]]) {
    const decision = decide({ state: 'MOVE', pieces: [supportedLow, ...cluster], next: piece(2) });
    assert.equal(decision.diagnostics.risk, 2, JSON.stringify(decision));
    assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 2, JSON.stringify(decision));
  }
});

test('overlap without mixed certainty retains both high observations', () => {
  for (const confidence of [0.45, 0.6, 0.9]) {
    const pieces = [piece(4, -1.2), piece(1, 0, 3.1, { confidence }), piece(1, 0.1, 3.1, { confidence })];
    const decision = decide({ state: 'MOVE', pieces, next: piece(2) });
    assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(decision));
    assert.equal(decision.diagnostics.risk, 2, JSON.stringify(decision));
  }
});

// Distilled from the 2026-11-27 battle log (game 5 turn 4). The type 13 circle
// floats at y=4.11 with no contact; before the detached-above-deadline rule it
// made every candidate fatal and flipped the drop from x=1.25 to x=-2.921.
function retainedGame5Turn4() {
  const columns = Array.from({ length: 28 }, (_, i) => ({
    left: -3.5 + i * 0.25, right: -3.25 + i * 0.25, top: -3.0088888888888885,
  }));
  const pieces = [
    piece(13, 1.8, 4.11, { r: 1.207, confidence: 0.45 }),
    piece(9, 2.4, -2.29, { r: 0.746, confidence: 0.8145235864135864 }),
    piece(5, 0.13, -2.37, { r: 0.414, confidence: 0.45 }),
    piece(7, -0.77, -2.17, { r: 0.559, confidence: 0.45 }),
    piece(3, -2.12, -2.13, { r: 0.316, confidence: 0.45 }),
    piece(3, -2.33, -2.54, { r: 0.316, confidence: 0.8120103829623222 }),
    piece(1, -0.06, -2.56, { r: 0.207, confidence: 0.7351476454928141 }),
    piece(1, -1.45, -2.62, { r: 0.207, confidence: 0.8074652327074261 }),
    piece(1, -1.72, -2.32, { r: 0.207, confidence: 0.6797106785309902 }),
  ];
  const current = piece(7, 0, 4.362569472056551, { r: 0.559, confidence: 0.79 });
  return {
    state: 'MOVE', pieces, next: current,
    nextPieces: [current, piece(4, 0, 4.36, { confidence: 0.82 }), piece(3, 0, 4.36, { confidence: 0.81 })],
    hold: null, holdKnownEmpty: false, canHold: false,
    garbage: { ratio: 1 / 6, gauge: 0, columns },
  };
}

test('detached low-certainty observation above the deadline cannot fabricate fatal risk', () => {
  const state = retainedGame5Turn4();
  const decision = decide(state);
  assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 1, JSON.stringify(decision));
  assert.equal(decision.diagnostics.risk, 0, JSON.stringify(decision));
  assert.equal(decision.diagnostics.pathRisk, 0, JSON.stringify(decision));
  assert.equal(decision.x, 1.25, JSON.stringify(decision));

  const clean = decide({ ...state, pieces: state.pieces.slice(1) });
  assert.equal(clean.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(clean));
  assert.equal(decision.x, clean.x);
  assert.equal(decision.hold, clean.hold);
  assert.equal(decision.diagnostics.clearance, clean.diagnostics.clearance);
});

test('an above-deadline observation touching the stack stays risk-bearing', () => {
  const state = retainedGame5Turn4();
  const support = piece(9, 1.8, 2.2, { confidence: 0.82 });
  const decision = decide({ ...state, pieces: [...state.pieces, support] });
  assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(decision));
  assert.equal(decision.diagnostics.risk, 2, JSON.stringify(decision));
});

test('an isolated low-certainty observation below the deadline stays risk-bearing', () => {
  const pieces = [piece(4, -1.2), piece(1, 0, 2.68, { confidence: 0.45 })];
  const decision = decide({ state: 'MOVE', pieces, next: piece(2) });
  assert.equal(decision.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(decision));
  assert.equal(decision.diagnostics.risk, 1, JSON.stringify(decision));
});

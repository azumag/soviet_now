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

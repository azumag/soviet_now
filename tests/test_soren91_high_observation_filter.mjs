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

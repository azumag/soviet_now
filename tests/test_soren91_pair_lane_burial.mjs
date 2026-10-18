/**
 * Regression for docich#605 on the Soren91 runtime path.
 *
 * The runtime decision comes from soren91/strategy.mjs (main.mjs loads it), not
 * from the Python strategy, so the burial rule has to hold here too: a piece of
 * a different type must not be wedged into the gap of an exposed same-type pair
 * (a merge lane) while an equal-risk landing that keeps the entry open exists.
 *
 * The boards below are the ones where the pre-change search actually buried a
 * lane (x=0 between the two pieces); the remaining cases pin the gates that must
 * not become burial: a lane the current piece itself merges into, and pairs that
 * are too far apart to be a lane at all.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { decide, TYPE_RADII, landingAt } from '../soren91/strategy.mjs';

const piece = (type, x = 0, y = -3.0, extra = {}) => ({
  type, r: TYPE_RADII[type], x, y, confidence: 0.9, ...extra,
});
const board = (pieces = [], next = piece(1), extra = {}) => ({
  state: 'MOVE', pieces, next, nextPieces: [next], hold: null,
  garbage: { ratio: 0, height: -5, gauge: 0, columns: [] }, ...extra,
});

// Same lane rule as strategy.mjs: an exposed same-type pair within reach.
function lane(pieces, type) {
  const group = pieces.filter(p => p.type === type);
  if (group.length < 2) return null;
  const lo = Math.min(group[0].x, group[1].x);
  const hi = Math.max(group[0].x, group[1].x);
  const reach = TYPE_RADII[type] * 2 + 1.0;
  if (hi - lo <= 0 || hi - lo > reach) return null;
  return { type, lo, hi, top: Math.min(...group.map(p => p.y + p.r)) };
}

function buriesLane(pieces, next, x) {
  const l = lane(pieces, pieces[0].type);
  if (!l) return false;
  if (l.type === next.type) return false;
  if (!(l.lo + 0.25 < x && x < l.hi - 0.25)) return false;
  return landingAt(pieces, next, x, []) + next.r >= l.top - 0.25;
}

test('type-8 pair lane is not buried by a type-5 piece', () => {
  const pieces = [piece(8, -1.0), piece(8, 1.0)];
  const next = piece(5);
  const decision = decide(board(pieces, next));
  assert.equal(buriesLane(pieces, next, decision.x), false, `buried lane at x=${decision.x}`);
  assert.equal(decision.diagnostics.pairLaneBurials, 0);
  assert.equal(decision.diagnostics.pairLaneBurialAvoided, true);
});

test('type-9 pair lane is not buried by a type-3 piece', () => {
  const pieces = [piece(9, -1.0), piece(9, 1.0)];
  const next = piece(3);
  const decision = decide(board(pieces, next));
  assert.equal(buriesLane(pieces, next, decision.x), false, `buried lane at x=${decision.x}`);
  assert.equal(decision.diagnostics.pairLaneBurials, 0);
  assert.equal(decision.diagnostics.pairLaneBurialAvoided, true);
});

test('a piece of the lane type still takes the lane (merge is not a burial)', () => {
  const pieces = [piece(9, -1.0), piece(9, 1.0)];
  const next = piece(9);
  const decision = decide(board(pieces, next));
  assert.equal(buriesLane(pieces, next, decision.x), false);
  assert.equal(decision.diagnostics.pairLaneBurials, 0);
  assert.equal(decision.diagnostics.merges > 0, true, 'expected the lane merge to be taken');
});

test('a pair too far apart is not a lane and no burial is claimed', () => {
  const pieces = [piece(9, -3.0), piece(9, 3.0)];
  const next = piece(3);
  const decision = decide(board(pieces, next));
  assert.equal(lane(pieces, 9), null);
  assert.equal(decision.diagnostics.pairLaneBurials, 0);
  assert.equal(decision.diagnostics.pairLaneBurialAvoided, false);
});

test('ordinary boards keep deciding without a burial claim', () => {
  const decision = decide(board([piece(1, -1.0), piece(3, 1.0)], piece(2)));
  assert.ok(Number.isFinite(decision.x));
  assert.equal(decision.diagnostics.pairLaneBurials, 0);
  assert.equal(decision.diagnostics.pairLaneBurialAvoided, false);
});

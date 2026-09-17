import test from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import {
  decide,
  TYPE_RADII,
} from '../soren91/strategy.mjs';
import {
  gateObservation,
  DEFAULT_SINGLE_FRAME_ADVANCE_MS,
  slowCadenceFastPathEnabled,
} from '../soren91/observation_guard.mjs';

const piece = (type, x = 0, y = -5 + TYPE_RADII[type], extra = {}) => ({
  type, r: TYPE_RADII[type], x, y, confidence: 0.9, ...extra,
});
const board = (pieces = [], next = piece(1), extra = {}) => ({
  state: 'MOVE', pieces, next, nextPieces: [next], hold: null,
  garbage: { ratio: 0, height: -5, gauge: 0, columns: [] }, ...extra,
});
const cal = () => ({
  screen: { width: 1280, height: 720 }, confidence: 0.82, method: 'profile',
  board: { left: 450, right: 800, top: 220, bottom: 636, width: 350, height: 416 },
});

function withRemoteCdp(fn) {
  const old = process.env.SOREN91_REMOTE_CDP_URL;
  process.env.SOREN91_REMOTE_CDP_URL = 'http://remote-cdp.invalid:9222';
  try { return fn(); }
  finally {
    if (old == null) delete process.env.SOREN91_REMOTE_CDP_URL;
    else process.env.SOREN91_REMOTE_CDP_URL = old;
  }
}

test('remote slow-cadence queue shift can confirm a new turn in one observation', () => withRemoteCdp(() => {
  assert.equal(DEFAULT_SINGLE_FRAME_ADVANCE_MS, 2500);
  assert.equal(slowCadenceFastPathEnabled(), true);
  const c = cal();
  const q = [piece(1), piece(2), piece(3)];
  assert.equal(gateObservation(board([], q[0], { nextPieces: q }), c, 1000).state, 'DROP');
  const next = gateObservation(board([piece(1)], piece(2), {
    nextPieces: [piece(2), null, piece(4)],
  }), c, 6500);
  assert.equal(next.state, 'MOVE', JSON.stringify(next.perception));
  assert.equal(next.perception.reason, 'stable-slow-advance-temporal-next-hold-empty');
  assert.equal(next.nextPieces[1].type, 3);
  assert.equal(next.nextPieces[1].temporalSource, 'shifted');
  assert.equal(next.holdKnownEmpty, true);
}));

test('local fast cadence still requires stable board confirmation', () => {
  const old = process.env.SOREN91_REMOTE_CDP_URL;
  delete process.env.SOREN91_REMOTE_CDP_URL;
  try {
    const c = cal();
    const q = [piece(1), piece(2), piece(3)];
    gateObservation(board([], q[0], { nextPieces: q }), c, 1000);
    const next = gateObservation(board([piece(1)], piece(2), { nextPieces: [piece(2), piece(3)] }), c, 6500);
    assert.equal(next.state, 'DROP');
  } finally {
    if (old != null) process.env.SOREN91_REMOTE_CDP_URL = old;
  }
});

test('two trustworthy empty HOLD observations enable the first HOLD', () => {
  const c = cal();
  const b = board([piece(5, 1)], piece(1), { nextPieces: [piece(1), piece(5)], canHold: true });
  assert.equal(gateObservation(b, c, 1000).holdKnownEmpty, false);
  const stable = gateObservation(b, c, 1300);
  assert.equal(stable.holdKnownEmpty, true);
  assert.match(stable.perception.reason, /hold-empty/);
  assert.equal(decide(stable).hold, true);
});

test('after a real HOLD has been seen, recognition misses never become known-empty', () => {
  const c = cal();
  const held = board([], piece(1), { hold: piece(3) });
  gateObservation(held, c, 1000);
  assert.equal(gateObservation(held, c, 1300).hold.type, 3);
  const miss = board();
  assert.equal(gateObservation(miss, c, 1600).holdKnownEmpty, false);
  assert.equal(gateObservation(miss, c, 1900).holdKnownEmpty, false);
});

test('uncertain HOLD evidence is not reclassified as known-empty', () => {
  const c = cal();
  const uncertain = board([], piece(1), { hold: piece(3, 0, 0, { confidence: 0.5, fallback: true }) });
  assert.equal(gateObservation(uncertain, c, 1000).holdKnownEmpty, false);
  assert.equal(gateObservation(uncertain, c, 1300).holdKnownEmpty, false);
});

test('same-turn NEXT miss reuses only a previously observed slot', () => {
  const c = cal();
  const q = [piece(1), piece(2), piece(3)];
  gateObservation(board([], q[0], { nextPieces: q }), c, 1000);
  const stable = gateObservation(board([], q[0], { nextPieces: [q[0], null, q[2]] }), c, 1300);
  assert.equal(stable.nextPieces[1].type, 2);
  assert.equal(stable.nextPieces[1].temporalSource, 'same-turn');
  assert.match(stable.perception.reason, /temporal-next/);
});

test('beam-v2 keeps multiple future branches instead of one greedy continuation', () => {
  const b = board([
    piece(3, -1.1, -3.9), piece(3, 1.2, -3.8),
    piece(5, -0.2, -3.1), piece(6, 1.9, -2.7), piece(7, -2.1, -2.5),
  ], piece(3), { nextPieces: [piece(3), piece(5), piece(3)] });
  const d = decide(b);
  assert.equal(d.diagnostics.version, 'beam-v2');
  assert.equal(d.diagnostics.searchDepth, 2);
  assert.ok(d.diagnostics.expandedNodes > 20, JSON.stringify(d.diagnostics));
  assert.ok(Number.isFinite(d.diagnostics.minFutureClearance));
});

test('beam-v2 remains bounded on an 80-piece board with two-ply preview and HOLD', () => {
  const pieces = Array.from({ length: 80 }, (_, i) =>
    piece(1 + i % 10, -2.7 + i % 9 * 0.65, -4.5 + Math.floor(i / 9) * 0.7));
  const b = board(pieces, piece(3), {
    nextPieces: [piece(3), piece(2), piece(5)], hold: piece(4), canHold: true,
  });
  const start = performance.now();
  const d = decide(b);
  const elapsed = performance.now() - start;
  assert.ok(Number.isFinite(d.x));
  assert.ok(d.diagnostics.expandedNodes < 500);
  assert.ok(elapsed < 5000, `bounded search took ${elapsed.toFixed(1)}ms`);
});

test('beam-v2 preserves a reachable known nextNext merge instead of burying it', () => {
  const pieces = [
    piece(4, 2.4689236488193274, -4.62),
    piece(5, -2.7905295928940177, -4.586),
    piece(5, -0.626091118901968, -4.586),
    piece(2, 1.0119760637171566, -4.741),
    piece(6, 1.7152004661038518, -4.22706061047885),
    piece(1, -2.7326585054397583, -3.9677023878124213),
    piece(3, 1.841493914835155, -3.451273291165397),
  ];
  const current = piece(7);
  const middle = piece(5);
  const future = piece(4);
  const d = decide(board(pieces, current, { nextPieces: [current, middle, future] }));

  assert.equal(d.x, -1.75, JSON.stringify(d));
  assert.equal(d.diagnostics.pathRisk, 0);
  assert.equal(d.diagnostics.knownMergeReservations, 1);
  assert.equal(d.diagnostics.preservedReservations, 1);
  assert.equal(d.diagnostics.lostReservations, 0);
  assert.equal(d.diagnostics.reservationStatus, 'fulfilled');
  assert.equal(d.diagnostics.reservationType, 4);
  assert.equal(d.diagnostics.reservationDepth, 2);
});

test('low-confidence nextNext evidence never activates a merge reservation', () => {
  const current = piece(7);
  const middle = piece(5);
  const future = piece(4, 0, -5 + TYPE_RADII[4], { confidence: 0.5, fallback: true });
  const d = decide(board([piece(4, 2.4, -4.62)], current, {
    nextPieces: [current, middle, future],
  }));

  assert.equal(d.diagnostics.knownMergeReservations, 0);
  assert.equal(d.diagnostics.reservationStatus, 'none');
});

test('an equal guaranteed current merge outranks a future reservation', () => {
  const pieces = [
    piece(3, 1.169928040355444, -4.684),
    piece(2, -2.7761001521721482, -4.741),
    piece(6, -1.6607574429363012, -4.53),
    piece(1, 0.5501522468402982, -4.793),
    piece(4, -1.8219364061951637, -3.6954214585775658),
    piece(5, -2.6188086541369557, -4.086638949320908),
    piece(5, -1.1721794251352549, -3.239075246186235),
    piece(6, 2.8143906304612756, -4.53),
  ];
  const current = piece(3);
  const middle = piece(1);
  const future = piece(3);
  const d = decide(board(pieces, current, { nextPieces: [current, middle, future] }));

  assert.equal(d.x, 1.25, JSON.stringify(d));
  assert.equal(d.diagnostics.merges, 1);
  assert.equal(d.diagnostics.pathRisk, 0);
  assert.equal(d.diagnostics.reservationStatus, 'consumed');
  assert.equal(d.diagnostics.lostReservations, 0);
});

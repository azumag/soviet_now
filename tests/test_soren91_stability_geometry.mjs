import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyBlob, detectPieces } from '../soren91/screenshot_analyzer.mjs';
import { gateObservation } from '../soren91/observation_guard.mjs';

const width = 1280;
const height = 720;
const calibration = () => ({
  screen: { width, height }, confidence: 0.82, method: 'deadline-floor', coordinateSchema: 2, arena: { left: 450, right: 800, top: 130, bottom: 636, width: 350, height: 506 }, hud: { top: 0, bottom: 130 },
  board: { left: 450, right: 800, top: 220, bottom: 636, width: 350, height: 416 },
});
const next = { type: 1, r: 0.207, confidence: 0.9 };
const board = (pieces, extra = {}) => ({
  state: 'MOVE', pieces, next, nextPieces: [next, null, null],
  garbage: { columns: [] }, ...extra,
});

function sampledEllipse(centerX, centerY, a, b, angle = 0, red = 200) {
  const data = Buffer.alloc(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = data[i + 1] = data[i + 2] = 50;
    data[i + 3] = 255;
  }
  const bound = Math.ceil(Math.max(a, b));
  const theta = angle * Math.PI / 180;
  for (let y = centerY - bound; y <= centerY + bound; y++) {
    for (let x = centerX - bound; x <= centerX + bound; x++) {
      const dx = x - centerX, dy = y - centerY;
      const u = dx * Math.cos(theta) + dy * Math.sin(theta);
      const v = -dx * Math.sin(theta) + dy * Math.cos(theta);
      if (u * u / (a * a) + v * v / (b * b) > 1) continue;
      const i = (y * width + x) * 4;
      data[i] = red;
      data[i + 1] = data[i + 2] = 50;
    }
  }
  return detectPieces(data, width, height, calibration());
}

function sampledBoundary(pixelCount) {
  return classifyBlob({
    centerX: 625, centerY: 500, pixelCount,
    bboxWidth: 48, bboxHeight: 48, sampleStep: 4,
    avgColor: { r: 200, g: 50, b: 50 },
    minX: 601, maxX: 649, minY: 476, maxY: 524,
  }, calibration());
}

test('one-pixel sampling jitter cannot keep an unchanged-size disc waiting for 20 observations', () => {
  const a = sampledEllipse(627, 502, 19, 19);
  const b = sampledEllipse(628, 502, 19, 19);
  assert.equal(a.length, 1);
  assert.equal(b.length, 1);
  assert.equal(a[0].type, 4);
  assert.equal(b[0].type, 5);
  assert.ok(a[0].confidence > 0.8 && b[0].confidence > 0.8);
  const c = calibration();
  for (let i = 0; i < 20; i++) {
    const pieces = i % 2 ? b : a;
    const observed = gateObservation(board(pieces), c, 1000 + i * 3000);
    assert.equal(observed.state, i === 0 ? 'DROP' : 'MOVE');
    assert.deepEqual(observed.pieces, pieces, 'strategy receives the fresh, unchanged detection');
  }
});

test('a canonical-radius type boundary does not turn subpixel size noise into motion', () => {
  const a = sampledBoundary(126);
  const b = sampledBoundary(127);
  assert.equal(a.type, 6);
  assert.equal(b.type, 7);
  assert.ok(Math.abs(a.r - b.r) > 0.08);
  assert.ok(Math.abs(a.measuredRadius - b.measuredRadius) < 0.001);
  const c = calibration();
  const before = structuredClone([a, b]);
  assert.equal(gateObservation(board([a]), c, 1000).state, 'DROP');
  assert.equal(gateObservation(board([b]), c, 4000).state, 'MOVE');
  assert.equal(gateObservation(board([a]), c, 7000).state, 'MOVE');
  assert.deepEqual([a, b], before, 'neither the source detection nor its type/radius is rewritten');
});

test('rotation keeps an existing canonical-size match even when the measured outline changes', () => {
  const a = sampledEllipse(625, 420, 110, 110 / 2.8, 0, 220);
  const b = sampledEllipse(625, 420, 110, 110 / 2.8, 60, 220);
  assert.equal(a.length, 1);
  assert.equal(b.length, 1);
  assert.equal(a[0].type, 14);
  assert.equal(b[0].type, 14);
  assert.equal(a[0].r, b[0].r);
  assert.ok(Math.abs(a[0].measuredRadius - b[0].measuredRadius) > 0.18);
  const c = calibration();
  gateObservation(board(a), c, 1000);
  assert.equal(gateObservation(board(b), c, 4000).state, 'MOVE');
});

test('real center movement and incompatible size estimates still require confirmation', () => {
  const a = sampledBoundary(126);
  for (const changed of [
    { ...a, y: a.y - 0.5 },
    { ...a, type: 8, r: 0.660, measuredRadius: a.measuredRadius + 0.1 },
  ]) {
    const c = calibration();
    gateObservation(board([a]), c, 1000);
    const moving = gateObservation(board([changed]), c, 4000);
    assert.equal(moving.state, 'DROP');
    assert.equal(moving.perception.reason, 'board-moving');
    assert.equal(gateObservation(board([changed]), c, 7000).state, 'MOVE');
  }
});

test('missing or invalid measured radii use comparable canonical sizes on both sides', () => {
  const a = sampledBoundary(126);
  const b = sampledBoundary(127);
  for (const measuredRadius of [undefined, null, NaN, Infinity, 0, -1]) {
    for (const [first, second] of [
      [{ ...a, measuredRadius }, b],
      [a, { ...b, measuredRadius }],
    ]) {
      const c = calibration();
      gateObservation(board([first]), c, 1000);
      assert.equal(gateObservation(board([second]), c, 4000).perception.reason, 'board-moving');
    }
  }
});

test('changed piece counts and a rising garbage surface still block input', () => {
  const a = sampledBoundary(126);
  for (const changed of [
    board([]),
    board([a], { garbage: { columns: [{ left: -3.5, right: 3.5, top: -1 }] } }),
  ]) {
    const c = calibration();
    gateObservation(board([a]), c, 1000);
    assert.equal(gateObservation(changed, c, 4000).perception.reason, 'board-moving');
  }
});

test('nearby fragments keep a one-to-one match independently of detector order', () => {
  const piece = (type, x, r) => ({ type, x, y: -4, r, measuredRadius: r, confidence: 0.85 });
  const before = [piece(2, 0, 0.259), piece(1, 0.15, 0.207)];
  const after = [piece(2, 0.09, 0.259), piece(1, 0.15, 0.207)];
  for (const first of [before, [...before].reverse()]) {
    for (const second of [after, [...after].reverse()]) {
      const c = calibration();
      gateObservation(board(first), c, 1000);
      assert.equal(gateObservation(board(second), c, 4000).state, 'MOVE');
    }
  }
  const c = calibration();
  gateObservation(board([piece(1, 0, 0.207), piece(1, 0.4, 0.207)]), c, 1000);
  const twoForOne = board([piece(1, 0.01, 0.207), piece(1, 0.02, 0.207)]);
  assert.equal(gateObservation(twoForOne, c, 4000).perception.reason, 'board-moving');
});

test('one sampled garbage-row step tolerates floating-point roundoff, while a larger rise waits', () => {
  // At 50 pixels/world unit a six-pixel sample row is 0.12. A one-pixel
  // raster shift across the grid can produce this full row step in the readout.
  const state = top => board([], { garbage: { columns: [{ left: -3.5, right: -3.25, top }] } });
  const c = calibration();
  gateObservation(state(-0.88), c, 1000);
  assert.equal(gateObservation(state(-1), c, 4000).state, 'MOVE');
  assert.equal(gateObservation(state(-0.75), c, 7000).perception.reason, 'board-moving');
});

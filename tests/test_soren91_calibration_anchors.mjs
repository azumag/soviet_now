import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import sharp from 'sharp';
import { detectCalibration, loadCalibration, gameToPixel, pixelToGame, dropXToPixel }
  from '../soren91/calibration.mjs';
import { CALIBRATION_COORDINATE_SCHEMA, isUsableCalibration }
  from '../soren91/calibration_contract.mjs';

// A small raster of the measured layout: a distinct HUD divider, a thin red
// deadline above grey garbage, and a bright floor joined to the two walls.
// The Oct 3 JPEGs independently measured deadline 167, floor 518, HUD end 101
// at 960x540; unlike an empty dark rectangle, those anchors survive filling.
function frame({ garbageTop = 210, deadline = true, floor = true, divider = true,
  overlay = null, occlusion = 0, secondDeadline = false } = {}) {
  const width = 480, height = 270;
  const data = Buffer.alloc(width * height * 4);
  const rect = (left, top, right, bottom, rgb) => {
    for (let y = top; y < bottom; y++) for (let x = left; x < right; x++) {
      const i = (y * width + x) * 4;
      data[i] = rgb[0]; data[i + 1] = rgb[1]; data[i + 2] = rgb[2]; data[i + 3] = 255;
    }
  };
  rect(0, 0, width, height, [135, 75, 20]);
  rect(166, 35, 314, 260, [50, 50, 50]);
  rect(160, 51, 166, 266, [240, 240, 240]);
  rect(314, 51, 320, 266, [240, 240, 240]);
  if (garbageTop != null) {
    rect(166, garbageTop, 314, 260, [148, 148, 148]);
    for (let y = garbageTop; y < 260; y += 8) rect(166, y, 314, y + 1, [205, 205, 205]);
  }
  if (overlay) rect(166, 51, 314, 86, overlay);
  if (divider) rect(166, 49, 314, 51, [180, 180, 180]);
  if (floor) rect(166, 260, 314, 263, [235, 235, 235]);
  if (deadline) rect(166, 83, 314, 86, [225, 22, 25]);
  if (secondDeadline) rect(166, 80, 314, 82, [225, 22, 25]);
  if (occlusion) {
    const span = Math.round(148 * occlusion);
    const left = Math.floor(240 - span / 2);
    rect(left, 80, left + span, 89, [25, 170, 70]);
  }
  return { data, width, height };
}

const detect = options => {
  const { data, width, height } = frame(options);
  return detectCalibration(data, width, height);
};
const geometry = c => ({ board: c.board, arena: c.arena, hud: c.hud });

test('deadline and floor do not follow the grey garbage surface', () => {
  const empty = detect({ garbageTop: null });
  assert.equal(empty.coordinateSchema, CALIBRATION_COORDINATE_SCHEMA);
  assert.equal(isUsableCalibration(empty), true);
  assert.deepEqual(empty.board, { left: 166, right: 314, top: 84, bottom: 260, width: 148, height: 176 });
  assert.deepEqual(empty.arena, { left: 166, right: 314, top: 51, bottom: 260, width: 148, height: 209 });
  assert.deepEqual(empty.hud, { top: 0, bottom: 51 });
  for (const garbageTop of [250, 210, 140, 92]) {
    const c = detect({ garbageTop });
    assert.equal(isUsableCalibration(c), true, `garbage y=${garbageTop}`);
    assert.deepEqual(geometry(c), geometry(empty));
  }
});

test('an intact bright line remains distinct from the darker red loss overlay', () => {
  const c = detect({ garbageTop: 110, overlay: [196, 32, 31] });
  assert.equal(isUsableCalibration(c), true);
  assert.deepEqual(geometry(c), geometry(detect()));
});

test('a partially covered deadline needs enough visible line, never an inferred dark edge', () => {
  assert.deepEqual(geometry(detect({ occlusion: 0.18 })), geometry(detect()));
  const c = detect({ occlusion: 0.5 });
  assert.equal(c.isFallback, true);
  assert.equal(isUsableCalibration(c), false);
});

for (const [name, options] of [
  ['missing deadline', { deadline: false }],
  ['missing floor', { floor: false }],
  ['missing HUD divider', { divider: false }],
  ['bright red region instead of a thin line', { overlay: [225, 22, 25] }],
  ['two plausible deadline lines', { secondDeadline: true }],
]) {
  test(`${name} keeps calibration unusable`, () => {
    const c = detect(options);
    assert.equal(c.method, 'fallback');
    assert.equal(c.isFallback, true);
    assert.equal(isUsableCalibration(c), false);
  });
}

test('proportional image sizes preserve the physical anchors and upper arena', async () => {
  const im = frame({ garbageTop: 140 });
  for (const width of [360, 640, 960]) {
    const { data, info } = await sharp(im.data, { raw: { width: im.width, height: im.height, channels: 4 } })
      .resize({ width }).raw().toBuffer({ resolveWithObject: true });
    const c = detectCalibration(data, info.width, info.height);
    assert.equal(isUsableCalibration(c), true, JSON.stringify({ width, c }));
    const scale = width / im.width;
    for (const [actual, expected] of [[c.board.top, 84], [c.board.bottom, 260], [c.arena.top, 51]]) {
      assert.ok(Math.abs(actual / scale - expected) < 2, `${actual / scale} vs ${expected}`);
    }
  }
});

test('world coordinates use the deadline while keeping high real pieces representable', () => {
  const c = detect();
  assert.ok(Math.abs(pixelToGame(240, c.board.top, c).gameY - 3.32) < 1e-12);
  assert.equal(pixelToGame(240, c.board.bottom, c).gameY, -5);
  const above = pixelToGame(240, 70, c);
  assert.ok(above.gameY > 3.32);
  assert.deepEqual(gameToPixel(above.gameX, above.gameY, c), { px: 240, py: 70 });
  assert.ok(c.arena.top < 70 && c.board.top > 70, 'an arena observation is not clipped at the deadline');
  for (const x of [-3, 0, 3]) {
    assert.ok(Math.abs(dropXToPixel(x, c) - gameToPixel(x, 0, c).px) <= 1);
  }
});

test('schema, screen, and arena/HUD relationships are validated independently of confidence', () => {
  const c = detect();
  const variants = [
    { coordinateSchema: undefined }, { coordinateSchema: 1 }, { provisional: true },
    { isFallback: true }, { confidence: 0.59 }, { confidence: NaN },
    { screen: { width: 960, height: 540 } },
    { board: { ...c.board, top: NaN } },
    { board: { ...c.board, width: c.board.width + 2 } },
    { board: { ...c.board, left: -1 } },
    { board: { ...c.board, height: 20, bottom: c.board.top + 20 } },
    { arena: { ...c.arena, left: c.arena.left + 2, width: c.arena.width - 2 } },
    { arena: { ...c.arena, bottom: c.arena.bottom + 2, height: c.arena.height + 2 } },
    { arena: { ...c.arena, top: c.board.top, height: c.board.height } },
    { hud: { top: -1, bottom: c.arena.top } },
    { hud: { top: 0, bottom: c.arena.top + 2 } },
    { hud: { top: c.arena.top, bottom: c.arena.top } },
  ];
  for (const extra of variants) assert.equal(isUsableCalibration({ ...c, ...extra }, 480, 270), false);
  assert.equal(isUsableCalibration(c, 960, 540), false);
  assert.equal(isUsableCalibration(c, Infinity, 270), false);
});

test('cached profile needs the new anchors even when its old aspect ratio looks correct', () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-calibration-'));
  const path = join(dir, 'calibration.json');
  try {
    const c = detect();
    // The real saved profile (108..690 in 1280x720) and the earlier shifted
    // 81..429 profile must both be recalibrated, not upgraded by a schema label.
    const old = { ...c, board: { ...c.board, top: c.board.top - 30, bottom: c.board.bottom - 30 } };
    delete old.coordinateSchema; delete old.arena; delete old.hud;
    writeFileSync(path, JSON.stringify(old));
    assert.equal(loadCalibration(path), null);
    writeFileSync(path, JSON.stringify({ ...old, coordinateSchema: 1 }));
    assert.equal(loadCalibration(path), null);
    writeFileSync(path, JSON.stringify({ ...old, coordinateSchema: 2 }));
    assert.equal(loadCalibration(path), null);
    writeFileSync(path, '{');
    assert.equal(loadCalibration(path), null);
    writeFileSync(path, JSON.stringify({ ...c, dropArea: { pixelLeft: 0, pixelRight: 480 } }));
    const loaded = loadCalibration(path);
    assert.equal(isUsableCalibration(loaded), true);
    assert.deepEqual(loaded.dropArea, c.dropArea, 'input X limits are derived from the accepted board');
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('invalid RGBA geometry cannot produce a candidate calibration', () => {
  assert.throws(() => detectCalibration(Buffer.alloc(4), 0, 1), /RGBA/);
  assert.throws(() => detectCalibration(Buffer.alloc(4), 1, Infinity), /RGBA/);
  assert.throws(() => detectCalibration(Buffer.alloc(3), 1, 1), /RGBA/);
});

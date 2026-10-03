import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, mkdtempSync, mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import sharp from 'sharp';
import { detectCalibration } from '../soren91/calibration.mjs';
import { analyzeScreenshot } from '../soren91/screenshot_analyzer.mjs';
import { detectCurrentPiece, extractSpriteComponents } from '../soren91/sprite_perception.mjs';
import { decide } from '../soren91/strategy.mjs';

async function fixture(name) {
  const bytes = readFileSync(new URL(`./fixtures/soren91-sprites/${name}.jpg`, import.meta.url));
  const { data, info } = await sharp(bytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height,
    cal: detectCalibration(data, info.width, info.height) };
}

function png(frame, data = frame.data) {
  return sharp(data, { raw: { width: frame.width, height: frame.height, channels: 4 } }).png().toBuffer();
}

function moveComponent(frame, component, dx) {
  const data = Buffer.from(frame.data);
  for (const pixel of component.pixels) {
    data[pixel * 4] = data[pixel * 4 + 1] = data[pixel * 4 + 2] = 50;
  }
  for (const pixel of component.pixels) {
    assert.ok(pixel % frame.width + dx >= 0 && pixel % frame.width + dx < frame.width);
    frame.data.copy(data, (pixel + dx) * 4, pixel * 4, pixel * 4 + 4);
  }
  return data;
}

test('photographed current, horizontal futures and HOLD retain their distinct roles', async () => {
  const cases = [
    ['game_0011_turn_7', [8, 6, 11], 10],
    ['game_0011_turn_11', [2, 8, 7], 2],
    ['game_0013_turn_1', [7, 5, 3], null],
  ];
  for (const [name, queue, hold] of cases) {
    const frame = await fixture(name), bytes = await png(frame);
    const first = await analyzeScreenshot(bytes, frame.cal);
    assert.equal(first.state, 'DROP');
    const state = await analyzeScreenshot(bytes, frame.cal);
    assert.equal(state.state, 'MOVE', `${name}: ${JSON.stringify(state.perception)}`);
    assert.deepEqual(state.nextPieces.map(p => p?.type ?? null), queue, name);
    assert.equal(state.next.type, queue[0], name);
    assert.ok(Number.isFinite(state.next.y), 'the guard receives the candidate vertical position');
    assert.equal(state.hold?.type ?? null, hold, name);
    assert.equal(state.holdKnownEmpty, hold === null, name);
    assert.ok(state.pieces.every(p => p.y < 3.32), `${name}: the photographed current must not be a stack piece`);
    const decision = decide({ ...state, canHold: true });
    assert.ok(Number.isFinite(decision.x) && Math.abs(decision.x) <= 3);
    assert.doesNotMatch(JSON.stringify(state), /"(?:pixels|sampledPixels|component)":/,
      'pixel masks are not persisted in history');
  }
});

test('moving only the photographed current by twelve pixels leaves physical stability unchanged', async () => {
  const frame = await fixture('game_0013_turn_1');
  const current = detectCurrentPiece(frame.data, frame.width, frame.height, frame.cal);
  assert.equal(current.piece.type, 7);
  assert.ok(current.component.centerX > 340 && current.component.centerX < 380);
  const before = await analyzeScreenshot(await png(frame), frame.cal);
  const moved = await analyzeScreenshot(await png(frame, moveComponent(frame, current.component, 12)), frame.cal);
  assert.equal(moved.state, 'MOVE', JSON.stringify(moved.perception));
  assert.deepEqual(moved.pieces, before.pieces);
  assert.deepEqual(moved.garbage.columns, before.garbage.columns);
  assert.equal(moved.next.type, 7);
});

test('the previously missed white/red Belarus remains an obstacle and its real movement blocks input', async () => {
  const frame = await fixture('game_0013_turn_1');
  const body = extractSpriteComponents(frame.data, frame.width, frame.height,
    { left: 332, right: 430, top: 370, bottom: 430 }, { background: [50, 50, 50] })[0];
  assert.ok(body.pixelCount > 1000 && body.centerY > 390 && body.centerY < 415);
  const before = await analyzeScreenshot(await png(frame), frame.cal);
  assert.equal(before.pieces.length, 1, JSON.stringify(before.pieces));
  const movedBytes = await png(frame, moveComponent(frame, body, 12));
  const moved = await analyzeScreenshot(movedBytes, frame.cal);
  assert.equal(moved.state, 'DROP');
  assert.equal(moved.perception.reason, 'board-moving');
  assert.ok(moved.pieces.some(p => p.y < 0));
  assert.equal((await analyzeScreenshot(movedBytes, frame.cal)).state, 'MOVE');
});

test('a missing photographed current is not reconstructed from visible future icons', async () => {
  const frame = await fixture('game_0013_turn_1');
  const current = detectCurrentPiece(frame.data, frame.width, frame.height, frame.cal);
  await analyzeScreenshot(await png(frame), frame.cal);
  const data = Buffer.from(frame.data);
  for (let y = current.bounds.top; y < current.bounds.bottom; y++) {
    for (let x = current.bounds.left; x < current.bounds.right; x++) {
      const i = (y * frame.width + x) * 4; data[i] = data[i + 1] = data[i + 2] = 50;
    }
  }
  const state = await analyzeScreenshot(await png(frame, data), frame.cal);
  assert.equal(state.next, null);
  assert.equal(state.state, 'DROP');
  assert.equal(state.perception.reason, 'unknown-current');
  assert.deepEqual(state.nextPieces.slice(1).map(p => p?.type ?? null), [5, 3]);
});

test('the red loss overlay is not a current and high real stack pieces remain represented', async () => {
  const frame = await fixture('game_0011_turn_14');
  const bytes = await png(frame);
  await analyzeScreenshot(bytes, frame.cal);
  const state = await analyzeScreenshot(bytes, frame.cal);
  assert.equal(state.state, 'DROP');
  assert.equal(state.next, null);
  assert.equal(state.perception.reason, 'unknown-current');
  assert.ok(state.pieces.some(p => p.x > 2.5 && p.y > 3.32 && p.r > 0.35),
    'the real high piece at the upper right must not be removed with the current');
});

test('the saved old HUD-origin calibration is replaced before the first input', async () => {
  const frame = await fixture('game_0013_turn_1');
  const old = { screen: { width: 960, height: 540 }, confidence: 0.82, method: 'profile',
    board: { left: 331.5, right: 624, top: 81, bottom: 517.5, width: 292.5, height: 436.5 } };
  const directory = mkdtempSync(join(tmpdir(), 'soren91-old-anchor-'));
  const previous = process.cwd();
  try {
    mkdirSync(join(directory, 'tmp'));
    process.chdir(directory);
    const bytes = await png(frame);
    const first = await analyzeScreenshot(bytes, old);
    assert.equal(old.coordinateSchema, 2);
    assert.equal(old.board.top, 167);
    assert.equal(old.board.bottom, 518);
    assert.equal(first.state, 'DROP');
    assert.equal(first.next.type, 7);
    assert.equal((await analyzeScreenshot(bytes, old)).state, 'MOVE');
  } finally {
    process.chdir(previous);
    rmSync(directory, { recursive: true, force: true });
  }
});

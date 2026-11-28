import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import sharp from 'sharp';
import { detectCalibration } from '../soren91/calibration.mjs';
import { analyzeScreenshot, detectLossOverlay } from '../soren91/screenshot_analyzer.mjs';
import { createHoldEscapePolicy, holdEscapeObservationReason } from '../soren91/observation_guard.mjs';

async function fixture(name) {
  const bytes = readFileSync(new URL(`./fixtures/soren91-sprites/${name}.jpg`, import.meta.url));
  const { data, info } = await sharp(bytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  return { data, width: info.width, height: info.height,
    cal: detectCalibration(data, info.width, info.height) };
}

const png = frame => sharp(frame.data, {
  raw: { width: frame.width, height: frame.height, channels: 4 },
}).png().toBuffer();

const CALIBRATION = {
  board: { left: 332, right: 627, top: 167, bottom: 518, width: 295, height: 351 },
  arena: { left: 332, right: 627, top: 100, bottom: 518, width: 295, height: 418 },
};

function bandFrame([r, g, b], coverage = 1) {
  const width = 960;
  const height = 540;
  const data = Buffer.alloc(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = 30; data[i + 1] = 30; data[i + 2] = 30; data[i + 3] = 255;
  }
  const bandRight = CALIBRATION.board.left + 3
    + Math.round((CALIBRATION.board.right - CALIBRATION.board.left - 6) * coverage);
  for (let y = CALIBRATION.arena.top + 2; y < CALIBRATION.board.top - 2; y++) {
    for (let x = CALIBRATION.board.left + 3; x < bandRight; x++) {
      const i = (y * width + x) * 4;
      data[i] = r; data[i + 1] = g; data[i + 2] = b;
    }
  }
  return { data, width, height };
}

test('round-over overlay is measured on the retained loss frame without changing its waiting state', async () => {
  const frame = await fixture('game_0011_turn_14');
  const state = await analyzeScreenshot(await png(frame), frame.cal);
  assert.equal(state.lossOverlay, true);
  assert.equal(state.state, 'DROP');
  assert.equal(state.next, null);
  assert.equal(state.perception.reason, 'unknown-current');
});

test('live frames do not report the round-over overlay', async () => {
  for (const name of ['game_0011_turn_7', 'game_0011_turn_11', 'game_0013_turn_1']) {
    const frame = await fixture(name);
    const state = await analyzeScreenshot(await png(frame), frame.cal);
    assert.equal(state.lossOverlay, false, name);
  }
});

test('the band detector separates a dark-red overlay from neutral play', () => {
  assert.equal(detectLossOverlay(bandFrame([120, 30, 30]).data, 960, 540, CALIBRATION), true);
  assert.equal(detectLossOverlay(bandFrame([40, 40, 40]).data, 960, 540, CALIBRATION), false);
  // A partial tint (for example a large red piece near the spawn row) must not
  // be enough to suppress the escape.
  assert.equal(detectLossOverlay(bandFrame([120, 30, 30], 0.5).data, 960, 540, CALIBRATION), false);
  assert.equal(detectLossOverlay(Buffer.alloc(4), 960, 540, CALIBRATION), false, 'wrong buffer size');
  assert.equal(detectLossOverlay(bandFrame([120, 30, 30]).data, 960, 540, {}), false, 'no geometry');
});

test('overlay frames never advance the blind-drop stretch', () => {
  const policy = createHoldEscapePolicy({});
  for (let i = 0; i < 8; i++) {
    const reason = holdEscapeObservationReason('unknown-current', {
      frameHealthy: true, lossOverlay: true,
    });
    assert.equal(reason, 'uncalibrated');
    assert.equal(policy.observe(reason, i * 10_000), false);
  }
  assert.deepEqual(policy.snapshot(), { holds: 0, escapes: 0 });

  // A healthy live frame still advances exactly as before.
  const live = holdEscapeObservationReason('unknown-current', {
    frameHealthy: true, lossOverlay: false,
  });
  assert.equal(live, 'unknown-current');
  assert.equal(holdEscapeObservationReason('unknown-current', { frameHealthy: false }), 'uncalibrated');
  let fired = false;
  for (let i = 0; i < 20 && !fired; i++) fired = policy.observe(live, 100_000 + i * 10_000);
  assert.equal(fired, true);
});

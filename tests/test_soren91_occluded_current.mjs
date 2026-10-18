import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import sharp from 'sharp';
import { detectCalibration } from '../soren91/calibration.mjs';
import { detectCurrentPiece, classifySpriteComponent } from '../soren91/sprite_perception.mjs';
import { FLAG_TEMPLATES } from '../soren91/flag_templates.mjs';
import { analyzeScreenshot } from '../soren91/screenshot_analyzer.mjs';

const bytes = readFileSync(new URL('./fixtures/soren91-sprites/game_0008_turn_2_20261015.jpg', import.meta.url));
const { data, info } = await sharp(bytes).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
const width = info.width, height = info.height;
const calibration = () => detectCalibration(data, width, height);
const png = pixels => sharp(pixels, { raw: { width, height, channels: 4 } }).png().toBuffer();

test('the exact production frame identifies the HUD-occluded Uzbekistan without learning this source', () => {
  assert.equal(createHash('sha256').update(bytes).digest('hex'),
    'ca386ba404a46775282c2cc0295bc3be9b087a40a66bf525e11a2f4124c4ce34');
  const cal = calibration();
  assert.equal(cal.board.left, 442);
  assert.equal(cal.board.top, 223.5);
  const current = detectCurrentPiece(data, width, height, cal);
  assert.equal(current.piece?.type, 11, current.reason);
  assert.ok(current.piece.confidence >= 0.58);
  assert.ok(!current.piece.templateId.includes('game_0008'));
  assert.ok(current.bounds.right < 625, 'the deadline must not join the sprite to the far wall');
  assert.ok(current.component.pixels.every(p => Math.floor(p / width) < 220));
  const independent = detectCurrentPiece(data, width, height, cal, { excludeSource: 'soviet_now.png' });
  assert.equal(independent.piece?.type, 11, independent.reason);
  assert.ok(independent.piece.confidence >= 0.58);
});

test('moving the photographed occluded current keeps settled countries and physical stability', async () => {
  const cal = calibration(), current = detectCurrentPiece(data, width, height, cal);
  assert.equal(current.piece?.type, 11, current.reason);
  const first = await analyzeScreenshot(bytes, cal);
  const moved = Buffer.from(data), dx = 80;
  for (const pixel of current.component.pixels) moved.set([50, 50, 48, 255], pixel * 4);
  for (const pixel of current.component.pixels) moved.set(data.subarray(pixel * 4, pixel * 4 + 4), (pixel + dx) * 4);
  const next = await analyzeScreenshot(await png(moved), cal);
  assert.equal(next.next?.type, 11);
  assert.equal(next.state, 'MOVE', JSON.stringify(next.perception));
  assert.deepEqual(next.pieces, first.pieces);
  assert.ok(next.pieces.length >= 2, 'the two photographed floor pieces remain physical occupancy');
});

test('a missing occluded current stays unknown even with a visible NEXT queue', async () => {
  const cal = calibration(), current = detectCurrentPiece(data, width, height, cal);
  assert.equal(current.piece?.type, 11, current.reason);
  const empty = Buffer.from(data);
  for (const pixel of current.component.pixels) empty.set([50, 50, 48, 255], pixel * 4);
  const state = await analyzeScreenshot(await png(empty), cal);
  assert.equal(state.next, null);
  assert.equal(state.perception.reason, 'unknown-current');
});

test('a supported sprite crossing the deadline is still physical occupancy, not a cursor', () => {
  const supported = Buffer.from(data);
  for (let y = 216; y < 245; y++) {
    for (let x = 548; x < 560; x++) supported.set([145, 145, 145, 255], (y * width + x) * 4);
  }
  const current = detectCurrentPiece(supported, width, height, calibration());
  assert.equal(current.piece, null);
  assert.equal(current.component, null);
  assert.equal(current.reason, 'current-not-isolated');
});

test('field-colour body and arbitrary multicolour fragments do not become confident countries', () => {
  const cal = calibration(), current = detectCurrentPiece(data, width, height, cal);
  for (const variant of ['field-body', 'unlabelled']) {
    const changed = Buffer.from(data);
    for (const pixel of current.component.pixels) {
      const i = pixel * 4;
      if (variant === 'field-body') {
        if (Math.max(...data.subarray(i, i + 3)) < 75) changed.set([50, 50, 48, 255], i);
      } else changed.set(pixel % 2 ? [255, 0, 255, 255] : [0, 255, 255, 255], i);
    }
    assert.equal(detectCurrentPiece(changed, width, height, cal).piece, null, variant);
  }
});

test('upper-occluded labelled sprites never change country when their entire source is held out', () => {
  let identified = 0;
  for (const template of FLAG_TEMPLATES) {
    const rgba = Buffer.from(template.rgba, 'base64'), pixels = [];
    for (let p = 0; p < 32 * 32; p++) if (rgba[p * 4 + 3] >= 128) pixels.push(p);
    const top = Math.min(...pixels.map(p => Math.floor(p / 32)));
    const bottom = Math.max(...pixels.map(p => Math.floor(p / 32))) + 1;
    for (const hidden of [0.1, 0.2, 0.3, 0.35]) {
      const visible = pixels.filter(p => Math.floor(p / 32) >= top + (bottom - top) * hidden);
      const xs = visible.map(p => p % 32), ys = visible.map(p => Math.floor(p / 32));
      const left = Math.min(...xs), right = Math.max(...xs) + 1;
      const upper = Math.min(...ys), lower = Math.max(...ys) + 1;
      const component = { pixels: visible, pixelCount: visible.length,
        bounds: { left, right, top: upper, bottom: lower, width: right - left, height: lower - upper } };
      const match = classifySpriteComponent(rgba, 32, 32, component,
        { allowTopOcclusion: true, excludeSource: template.source });
      assert.ok(match.type == null || match.type === template.type,
        `${template.id} hidden=${hidden}: ${JSON.stringify(match)}`);
      if (match.type != null) identified++;
    }
  }
  assert.ok(identified > 50, 'independent evidence must still identify the majority of labelled cases');
});

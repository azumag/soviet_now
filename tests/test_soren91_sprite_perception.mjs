import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import sharp from 'sharp';
import { FLAG_TEMPLATES, FLAG_TEMPLATE_SIZE } from '../soren91/flag_templates.mjs';
import { COUNTRY_NAMES } from '../soren91/country_names.mjs';
import { extractSpriteComponents, classifySpriteComponent, detectHudPieces, detectCurrentPiece,
  hudSpriteRois } from '../soren91/sprite_perception.mjs';

const captures = [
  { source: 'game_0011_turn_7.jpg', current: 8, hold: 10, next: [6, 11, 6] },
  { source: 'game_0011_turn_11.jpg', current: 2, hold: 2, next: [8, 7, 9] },
  // A red loss overlay hides the cursor's red stripe and joins it to the
  // warning background. It must remain unknown, even though a person can
  // infer Azerbaijan from the remaining green/blue shape.
  { source: 'game_0011_turn_14.jpg', current: null, hold: 2, next: [9, 4, 11] },
  { source: 'game_0013_turn_1.jpg', current: 7, hold: null, next: [5, 3, 4] },
];
const calibration = () => ({ coordinateSchema: 2, confidence: 0.82,
  screen: { width: 960, height: 540 },
  board: { left: 332, right: 627, top: 167, bottom: 518, width: 295, height: 351 },
  arena: { left: 332, right: 627, top: 100, bottom: 518, width: 295, height: 418 },
  hud: { top: 0, bottom: 100 } });
const images = new Map();
for (const capture of captures) {
  const path = new URL(`fixtures/soren91-sprites/${capture.source}`, import.meta.url);
  const { data, info } = await sharp(readFileSync(path)).ensureAlpha().raw().toBuffer({ resolveWithObject: true });
  images.set(capture.source, { data, width: info.width, height: info.height });
}
const types = pieces => pieces.map(p => p?.type ?? null);

function paint(data, width, bounds, colour) {
  for (let y = Math.ceil(bounds.top); y < Math.floor(bounds.bottom); y++) {
    for (let x = Math.ceil(bounds.left); x < Math.floor(bounds.right); x++) {
      const rgba = typeof colour === 'function' ? colour(x, y) : colour;
      const i = (y * width + x) * 4;
      data[i] = rgba[0]; data[i + 1] = rgba[1]; data[i + 2] = rgba[2]; data[i + 3] = 255;
    }
  }
}

test('labelled seeds cover the canonical 15 countries and retain verifiable source hashes', () => {
  const legacy = FLAG_TEMPLATES.filter(t => t.source === 'soviet_now.png');
  assert.deepEqual(legacy.map(t => t.type), Array.from({ length: 15 }, (_, i) => i + 1));
  const hashes = new Map();
  for (const template of FLAG_TEMPLATES) {
    assert.equal(template.country, COUNTRY_NAMES[template.type]);
    assert.equal(Buffer.from(template.rgba, 'base64').length, FLAG_TEMPLATE_SIZE ** 2 * 4);
    if (!hashes.has(template.source)) {
      const url = template.source === 'soviet_now.png'
        ? new URL('../soviet_now.png', import.meta.url)
        : new URL(`fixtures/soren91-sprites/${template.source}`, import.meta.url);
      hashes.set(template.source, createHash('sha256').update(readFileSync(url)).digest('hex'));
    }
    assert.equal(template.sourceSha256, hashes.get(template.source));
  }
});

for (const capture of captures) {
  test(`real HUD and physical cursor agree with reviewed labels: ${capture.source}`, () => {
    const { data, width, height } = images.get(capture.source);
    const cal = calibration();
    const hud = detectHudPieces(data, width, height, cal);
    assert.deepEqual(types(hud.nextPieces), capture.next);
    assert.equal(hud.hold?.type ?? null, capture.hold);
    assert.equal(hud.holdObservedEmpty, capture.hold === null);
    const current = detectCurrentPiece(data, width, height, cal);
    assert.equal(current.piece?.type ?? null, capture.current, JSON.stringify(current.piece));
    if (capture.current == null) {
      assert.equal(current.component, null);
      assert.equal(current.bounds, null);
    } else {
      assert.ok(current.component.pixels instanceof Int32Array);
      assert.ok(current.bounds.top >= cal.arena.top && current.bounds.bottom < cal.board.top);
      assert.equal('r' in current.piece, false, 'Sprite scale must not invent a physical radius');
      for (const pixel of current.component.pixels) {
        const x = pixel % width, y = Math.floor(pixel / width);
        assert.ok(x >= current.bounds.left && x < current.bounds.right);
        assert.ok(y >= current.bounds.top && y < current.bounds.bottom);
      }
    }
  });

  test(`independent capture/legacy artwork classifies with this source fully held out: ${capture.source}`, () => {
    const { data, width, height } = images.get(capture.source);
    const options = { excludeSource: capture.source };
    const hud = detectHudPieces(data, width, height, calibration(), options);
    const current = detectCurrentPiece(data, width, height, calibration(), options);
    assert.deepEqual(types(hud.nextPieces), capture.next);
    assert.equal(hud.hold?.type ?? null, capture.hold);
    assert.equal(current.piece?.type ?? null, capture.current);
    for (const piece of [hud.hold, ...hud.nextPieces, current.piece].filter(Boolean)) {
      assert.ok(piece.confidence >= 0.58);
      assert.ok(piece.score < 0.11, JSON.stringify(piece));
      assert.ok(!piece.templateId.startsWith(capture.source + ':'));
    }
  });
}

test('full white/red/white Belarus is one component and matches an independent HUD seed', () => {
  const { data, width, height } = images.get('game_0013_turn_1.jpg');
  const components = extractSpriteComponents(data, width, height,
    { left: 333, right: 626, top: 200, bottom: 429 }, { minPixels: 50 });
  assert.equal(components.length, 1);
  assert.ok(components[0].pixelCount > 2500, 'Keep white regions, not just its red stripe');
  const match = classifySpriteComponent(data, width, height, components[0], { excludeSource: 'game_0013_turn_1.jpg' });
  assert.equal(match.type, 10);
  assert.ok(match.templateId.startsWith('game_0011_turn_7.jpg:10:'));
});

test('horizontal cursor motion changes only the confirmed component pixel positions', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const original = detectCurrentPiece(im.data, im.width, im.height, cal);
  const moved = Buffer.from(im.data), dx = 150;
  for (const pixel of original.component.pixels) moved.set([51, 51, 51, 255], pixel * 4);
  for (const pixel of original.component.pixels) moved.set(im.data.subarray(pixel * 4, pixel * 4 + 4), (pixel + dx) * 4);
  const observed = detectCurrentPiece(moved, im.width, im.height, cal);
  assert.equal(observed.piece.type, 7);
  assert.equal(observed.bounds.left, original.bounds.left + dx);
  assert.equal(observed.bounds.top, original.bounds.top);
  const expectedPixels = new Set([...original.component.pixels].map(p => p + dx));
  assert.ok([...observed.component.pixels].every(p => expectedPixels.has(p)));
  // JPEG edge pixels can straddle the locally estimated background threshold;
  // the whole country must move, without adding any unrelated scene pixel.
  assert.ok(observed.component.pixelCount >= original.component.pixelCount * 0.99);
});

test('small legal wall contacts retain a complete flag match; substantial side cuts remain unknown', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const original = detectCurrentPiece(im.data, im.width, im.height, cal).component;
  function move(dx) {
    const data = Buffer.from(im.data);
    for (const pixel of original.pixels) data.set([51, 51, 51, 255], pixel * 4);
    for (const pixel of original.pixels) data.set(im.data.subarray(pixel * 4, pixel * 4 + 4), (pixel + dx) * 4);
    return data;
  }
  // A 3–4px move from the captured x=-2.84 reaches the existing type-7 legal
  // wall limit. Contact or one missing boundary column must not deadlock play.
  for (const dx of [-3, -4, -6, 242, 243, 245]) {
    for (const options of [{}, { excludeSource: 'game_0013_turn_1.jpg' }]) {
      const current = detectCurrentPiece(move(dx), im.width, im.height, cal, options);
      assert.equal(current.piece?.type, 7, `dx=${dx}: ${current.reason}`);
      assert.ok(current.piece.score <= 0.12);
      assert.ok(current.component.pixels.every(pixel => pixel % im.width >= 333 && pixel % im.width < 626));
    }
  }
  for (const dx of [-10, -12, -20, 252, 260]) {
    for (const options of [{}, { excludeSource: 'game_0013_turn_1.jpg' }]) {
      const current = detectCurrentPiece(move(dx), im.width, im.height, cal, options);
      assert.equal(current.piece, null, `A substantial cut at dx=${dx} was accepted`);
      assert.equal(current.component, null);
    }
  }
});

test('bottom-connected spawn candidates stay unknown when side contact is permitted', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const data = Buffer.from(im.data);
  const b = detectCurrentPiece(im.data, im.width, im.height, cal).bounds;
  paint(data, im.width, { left: b.left, right: b.right, top: b.bottom - 1, bottom: 170 }, [145, 145, 145]);
  const current = detectCurrentPiece(data, im.width, im.height, cal);
  assert.equal(current.piece, null);
  assert.equal(current.component, null);
  assert.equal(current.reason, 'clipped-current');
});

test('rendered scale changes do not change the independently matched country or invent a radius', async () => {
  const im = images.get('game_0013_turn_1.jpg');
  const original = detectCurrentPiece(im.data, im.width, im.height, calibration()).component;
  const b = original.bounds;
  const isolated = Buffer.alloc(b.width * b.height * 4);
  for (const pixel of original.pixels) {
    const x = pixel % im.width - b.left, y = Math.floor(pixel / im.width) - b.top;
    isolated.set(im.data.subarray(pixel * 4, pixel * 4 + 4), (y * b.width + x) * 4);
  }
  for (const factor of [0.75, 1.5, 2]) {
    const resized = await sharp(isolated, { raw: { width: b.width, height: b.height, channels: 4 } })
      .resize(Math.round(b.width * factor), Math.round(b.height * factor), { kernel: 'nearest' })
      .raw().toBuffer({ resolveWithObject: true });
    const width = resized.info.width, height = resized.info.height;
    const component = extractSpriteComponents(resized.data, width, height,
      { left: 0, top: 0, right: width, bottom: height }, { minPixels: 20 })[0];
    const match = classifySpriteComponent(resized.data, width, height, component, { excludeSource: 'game_0013_turn_1.jpg' });
    assert.equal(match.type, 7, JSON.stringify(match));
    assert.equal('r' in match, false);
  }
});

test('two plausible cursor components remain unknown instead of picking the larger one', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const original = detectCurrentPiece(im.data, im.width, im.height, cal);
  const doubled = Buffer.from(im.data);
  for (const pixel of original.component.pixels) doubled.set(im.data.subarray(pixel * 4, pixel * 4 + 4), (pixel + 150) * 4);
  const current = detectCurrentPiece(doubled, im.width, im.height, cal);
  assert.equal(current.piece, null);
  assert.equal(current.component, null);
  assert.equal(current.reason, 'multiple-current-candidates');
});

test('a high settled country is not included in the cursor exclusion mask', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const data = Buffer.from(im.data);
  // Another country's upper part enters the current scan band, but its center
  // is below the spawn row. Never erase the whole top-of-board rectangle.
  const settled = extractSpriteComponents(im.data, im.width, im.height,
    { left: 333, right: 626, top: 200, bottom: 429 }, { minPixels: 50 })[0];
  const displacement = -254 * im.width + 180;
  const highPixels = new Set();
  for (const pixel of settled.pixels) {
    const target = pixel + displacement;
    data.set(im.data.subarray(pixel * 4, pixel * 4 + 4), target * 4);
    highPixels.add(target);
  }
  const current = detectCurrentPiece(data, im.width, im.height, cal);
  assert.equal(current.piece.type, 7);
  assert.ok([...current.component.pixels].every(p => !highPixels.has(p)));
});

test('a spawn-row country with a close neutral support is not removed as current', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const data = Buffer.from(im.data);
  const original = detectCurrentPiece(im.data, im.width, im.height, cal);
  const b = original.bounds;
  paint(data, im.width, { left: b.left, right: b.right, top: b.bottom + 2, bottom: b.bottom + 6 }, [145, 145, 145]);
  const current = detectCurrentPiece(data, im.width, im.height, cal);
  assert.equal(current.piece, null);
  assert.equal(current.component, null);
  assert.equal(current.reason, 'current-not-isolated');
});

test('unknown multicolour and single-colour cursors cannot borrow a type from NEXT', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  for (const colour of [[220, 40, 40], (x, y) => ((x + y) % 6 < 3 ? [230, 0, 230] : [255, 140, 0])]) {
    const data = Buffer.from(im.data);
    paint(data, im.width, { left: 333, right: 626, top: 104, bottom: 165 }, [51, 51, 51]);
    paint(data, im.width, { left: 460, right: 491, top: 109, bottom: 141 }, colour);
    const current = detectCurrentPiece(data, im.width, im.height, cal);
    assert.equal(current.piece, null);
    assert.equal(current.component, null);
    assert.deepEqual(types(detectHudPieces(data, im.width, im.height, cal).nextPieces), [5, 3, 4]);
  }
});

test('a missing middle NEXT stays null; an unrecognised HOLD is not empty', () => {
  const im = images.get('game_0013_turn_1.jpg'), cal = calibration();
  const data = Buffer.from(im.data), rois = hudSpriteRois(cal);
  paint(data, im.width, rois.nextPieces[1], [51, 51, 51]);
  paint(data, im.width, { left: 399, right: 416, top: 45, bottom: 65 }, [255, 140, 0]);
  const hud = detectHudPieces(data, im.width, im.height, cal);
  assert.deepEqual(types(hud.nextPieces), [5, null, 4]);
  assert.equal(hud.hold, null);
  assert.equal(hud.holdObservedEmpty, false);
  paint(data, im.width, rois.hold, [51, 51, 51]);
  paint(data, im.width, { left: 402, right: 406, top: 48, bottom: 51 }, [255, 140, 0]);
  assert.equal(detectHudPieces(data, im.width, im.height, cal).holdObservedEmpty, false,
    'A tiny unresolved foreground component is still evidence of presence');
});

test('neutral garbage filter and confirmed-pixel exclusion preserve white flag regions', () => {
  const im = images.get('game_0013_turn_1.jpg');
  const roi = { left: 333, right: 626, top: 370, bottom: 518 };
  const pixelFilter = (r, g, b) => {
    const max = Math.max(r, g, b), min = Math.min(r, g, b), brightness = (r + g + b) / 3;
    return (max > 0 && (max - min) / max > 0.15) || brightness > 215 || brightness < 28;
  };
  const components = extractSpriteComponents(im.data, im.width, im.height, roi, { pixelFilter, minPixels: 50 });
  const country = components.find(c => classifySpriteComponent(im.data, im.width, im.height, c).type === 10);
  assert.ok(country && country.pixelCount > 2400);
  const without = extractSpriteComponents(im.data, im.width, im.height, roi,
    { pixelFilter, minPixels: 50, excludePixels: new Set(country.pixels) });
  assert.ok(without.every(c => c.pixels.every(p => !country.pixels.includes(p))));
});

test('legacy or invalid coordinates fail closed', () => {
  const im = images.get('game_0013_turn_1.jpg');
  for (const cal of [{}, { ...calibration(), coordinateSchema: 1 }]) {
    assert.equal(detectCurrentPiece(im.data, im.width, im.height, cal).piece, null);
    assert.deepEqual(detectHudPieces(im.data, im.width, im.height, cal).nextPieces, [null, null, null]);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import sharp from 'sharp';
import { detectCalibration } from '../soren91/calibration.mjs';
import { isUsableCalibration } from '../soren91/calibration_contract.mjs';
import { detectHudPieces, detectCurrentPiece, hudSpriteRois } from '../soren91/sprite_perception.mjs';
import { queueAdvanceEvidence } from '../soren91/observation_guard.mjs';
import { decide } from '../soren91/strategy.mjs';

const widths = [960, 1280];
const captures = [
  { source: 'game_0011_turn_7.jpg', current: 8, hold: 10, next: [6, 11, 6] },
  { source: 'game_0011_turn_11.jpg', current: 2, hold: 2, next: [8, 7, 9] },
  { source: 'game_0011_turn_14.jpg', current: null, hold: 2, next: [9, 4, 11] },
  { source: 'game_0013_turn_1.jpg', current: 7, hold: null, next: [5, 3, 4] },
];
const images = new Map();
const types = pieces => pieces.map(piece => piece?.type ?? null);

for (const capture of captures) {
  for (const width of widths) {
    const { data, info } = await sharp(new URL(`fixtures/soren91-sprites/${capture.source}`, import.meta.url).pathname)
      .resize({ width }).toColourspace('srgb').ensureAlpha().raw().toBuffer({ resolveWithObject: true });
    const cal = detectCalibration(data, info.width, info.height);
    images.set(`${capture.source}:${width}`, { data, width: info.width, height: info.height, cal });
  }
}

function area(roi) {
  return { left: Math.ceil(roi.left), right: Math.floor(roi.right),
    top: Math.ceil(roi.top), bottom: Math.floor(roi.bottom) };
}

function paint(data, width, bounds, colour) {
  for (let y = bounds.top; y < bounds.bottom; y++) {
    for (let x = bounds.left; x < bounds.right; x++) {
      data.set([...(typeof colour === 'function' ? colour(x, y) : colour), 255], (y * width + x) * 4);
    }
  }
}

for (const width of widths) {
  test(`unresolved edge and interior fragments still make HOLD unknown at ${width}px`, () => {
    const frame = images.get(`game_0013_turn_1.jpg:${width}`);
    const b = area(hudSpriteRois(frame.cal).hold);
    const thickness = Math.round((b.bottom - b.top) * 0.06);
    const coloured = (_x, y) => y % 6 < 3 ? [230, 0, 230] : [255, 140, 0];
    const cases = [
      ['coloured edge', { ...b, right: b.left + thickness }, coloured],
      ['short neutral edge', { ...b, right: b.left + thickness,
        bottom: b.top + Math.round((b.bottom - b.top) * 0.65) }, [150, 150, 150]],
      ['neutral interior rule', { ...b, left: b.left + 12, right: b.left + 12 + thickness }, [150, 150, 150]],
      ['tiny unknown interior', { left: b.left + 18, right: b.left + 21,
        top: b.top + 18, bottom: b.top + 21 }, [255, 140, 0]],
    ];
    for (const [label, bounds, colour] of cases) {
      const data = Buffer.from(frame.data);
      paint(data, width, b, [51, 51, 51]);
      paint(data, width, bounds, colour);
      const hud = detectHudPieces(data, width, frame.height, frame.cal);
      assert.equal(hud.hold, null, label);
      assert.equal(hud.holdObservedEmpty, false, label);
    }
    // A sparse comb touches the edge and spans its full height, but its shape
    // is not a filled panel rule. Keep it as unresolved occupancy.
    const data = Buffer.from(frame.data);
    paint(data, width, b, [51, 51, 51]);
    paint(data, width, { ...b, right: b.left + thickness }, (x, y) =>
      x === b.left || y % 3 === 0 ? [150, 150, 150] : [51, 51, 51]);
    assert.equal(detectHudPieces(data, width, frame.height, frame.cal).holdObservedEmpty, false);
  });

  test(`a coloured boundary fragment cannot hide beside a known NEXT at ${width}px`, () => {
    const frame = images.get(`game_0013_turn_1.jpg:${width}`);
    const b = area(hudSpriteRois(frame.cal).nextPieces[0]);
    const data = Buffer.from(frame.data);
    paint(data, width, { ...b, right: b.left + Math.max(4, Math.round((b.bottom - b.top) * 0.06)) },
      (_x, y) => y % 6 < 3 ? [230, 0, 230] : [255, 140, 0]);
    const hud = detectHudPieces(data, width, frame.height, frame.cal);
    assert.deepEqual(types(hud.nextPieces), [null, 3, 4]);
  });
}

test('recovered first NEXT enables two-piece lookahead and conflict-free queue advance', () => {
  const frame = images.get('game_0013_turn_1.jpg:1280');
  const hud = detectHudPieces(frame.data, frame.width, frame.height, frame.cal);
  const current = detectCurrentPiece(frame.data, frame.width, frame.height, frame.cal).piece;
  const queue = [current, ...hud.nextPieces.slice(0, 2)];
  assert.deepEqual(types(queue), [7, 5, 3]);
  const state = { next: current, nextPieces: queue, pieces: [], canHold: false,
    garbage: { ratio: 0, gauge: 0, columns: [] } };
  assert.match(decide(state).reason, /depth=2$/);
  assert.match(decide({ ...state, nextPieces: [current, null, queue[2]] }).reason, /depth=0$/);
  // A subsequent observed [NEXT-left, NEXT-middle, NEXT-right] supplies both
  // cross-slot matches. This is queue evidence, not proof of game acceptance.
  assert.deepEqual(queueAdvanceEvidence(queue, hud.nextPieces), { evidence: 5, conflict: 0 });
});

for (const capture of captures) {
  for (const width of widths) {
    test(`all HUD slots and current retain their labels at ${width}px: ${capture.source}`, () => {
      const frame = images.get(`${capture.source}:${width}`);
      assert.equal(isUsableCalibration(frame.cal, frame.width, frame.height), true);
      for (const options of [{}, { excludeSource: capture.source }]) {
        const hud = detectHudPieces(frame.data, frame.width, frame.height, frame.cal, options);
        assert.deepEqual(types(hud.nextPieces), capture.next);
        assert.equal(hud.hold?.type ?? null, capture.hold);
        assert.equal(hud.holdObservedEmpty, capture.hold === null);
        const current = detectCurrentPiece(frame.data, frame.width, frame.height, frame.cal, options);
        assert.equal(current.piece?.type ?? null, capture.current, current.reason);
        if (options.excludeSource) {
          for (const piece of [hud.hold, ...hud.nextPieces, current.piece].filter(Boolean)) {
            assert.ok(!piece.templateId.startsWith(capture.source + ':'));
          }
        }
      }
    });
  }
}

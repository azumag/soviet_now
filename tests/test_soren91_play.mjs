import test from 'node:test';
import assert from 'node:assert/strict';
import { performance } from 'node:perf_hooks';
import { readFileSync, mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { decide, landingAt, simulateDrop, TYPE_RADII } from '../soren91/strategy.mjs';
import { TYPE_RADII as IMAGE_RADII, detectPieces, detectNextPieces, measureGarbage, analyzeScreenshot } from '../soren91/screenshot_analyzer.mjs';
import { DEFAULT_MAX_STALE_MS, gateObservation, maxStaleMs, usableCalibration } from '../soren91/observation_guard.mjs';
const sharp = createRequire(new URL('../soren91/package.json', import.meta.url))('sharp');
const piece = (type, x = 0, y = -5 + TYPE_RADII[type], extra = {}) => ({ type, r: TYPE_RADII[type], x, y, confidence: 0.9, ...extra });
const board = (pieces = [], next = piece(1), extra = {}) => ({ state: 'MOVE', pieces, next, nextPieces: [next], garbage: { ratio: 0, height: -5, gauge: 0 }, ...extra });
const cal = () => ({ screen: { width: 1280, height: 720 }, confidence: 0.82, method: 'profile', board: { left: 450, right: 800, top: 220, bottom: 636, width: 350, height: 416 } });
function image(w = 1280, h = 720) {
  const data = Buffer.alloc(w * h * 4);
  for (let i = 0; i < data.length; i += 4) { data[i] = data[i + 1] = data[i + 2] = 50; data[i + 3] = 255; }
  return { data, w, h };
}
function disc(im, x, y, r, rgb = [200, 50, 50]) {
  for (let py = Math.max(0, Math.ceil(y - r)); py < Math.min(im.h, y + r); py++) {
    for (let px = Math.max(0, Math.ceil(x - r)); px < Math.min(im.w, x + r); px++) {
      if ((px - x) ** 2 + (py - y) ** 2 > r * r) continue;
      const color = typeof rgb === 'function' ? rgb(px, py) : rgb;
      const i = (py * im.w + px) * 4;
      im.data[i] = color[0]; im.data[i + 1] = color[1]; im.data[i + 2] = color[2];
    }
  }
}
const approx = (a, b, tolerance = 1e-9) => assert.ok(Math.abs(a - b) <= tolerance, `${a} != ${b}`);

test('canonical radii agree with image recognition for all 15 types', () => assert.deepEqual(TYPE_RADII, IMAGE_RADII));
test('empty landing has no fabricated settling buffer', () => approx(landingAt([], piece(1), 0), -4.793));
test('circle-edge contact is lower than rectangular top stacking', () => approx(landingAt([piece(6, 0)], piece(1), 0.6), -5 + 0.47 + Math.sqrt(0.677 ** 2 - 0.6 ** 2)));
test('landing is enumeration-order invariant', () => {
  const ps = [piece(2, 0, -4), piece(3, 0.2, -2), piece(4, 0.1, -3)];
  assert.equal(landingAt(ps, piece(1), 0), landingAt([...ps].reverse(), piece(1), 0));
});
test('first-contact merge consumes both and promotes once', () => {
  const r = simulateDrop([piece(1)], piece(1), 0);
  assert.equal(r.merges, 1); assert.equal(r.pieces.length, 1); assert.equal(r.placed.type, 2);
});
test('cannot merge with a same type hidden below a different blocker', () => {
  const r = simulateDrop([piece(1), piece(5, 0, -3)], piece(1), 0);
  assert.equal(r.merges, 0); assert.equal(r.pieces.length, 3);
});
test('unknown or uncertain targets are obstacles, never merge rewards', () => {
  assert.equal(simulateDrop([piece(1, 0, -4.793, { confidence: 0.3 })], piece(1), 0).merges, 0);
  assert.ok(landingAt([{ type: 0, x: 0, y: 1, r: 0.5 }], piece(1), 0) > 1);
});
test('terminal type 15 never creates type 16 or disappears', () => {
  const r = simulateDrop([piece(15)], piece(15), 0); assert.equal(r.merges, 0); assert.equal(r.pieces.length, 2);
});
test('v221 fatal sign regression: choose low lane instead of a near-deadline merge', () => {
  const b = board([piece(1, 0, 2.7)]);
  const d = decide(b); assert.ok(Math.abs(d.x) > 0.414, JSON.stringify(d)); assert.ok(d.diagnostics.risk < 2); approx(d.diagnostics.landingY, -4.793);
});
test('retained replay: unsupported high cursor observations do not fabricate deadline risk', () => {
  // Distilled from retained game 8 turn 7. The screenshot shows the settled
  // board on the garbage surface while two cursor-like detections float near
  // the deadline. They must not turn a reachable type-4 merge into risk=1.
  const columns = [{ left: -3.5, right: 3.5, top: -2.512 }];
  const ps = [
    piece(7, 2.37, -1.86, { confidence: 0.79 }),
    piece(7, -2.83, -1.82, { confidence: 0.82 }),
    piece(6, 2.99, -1.46, { confidence: 0.81 }),
    piece(3, 2.86, -1.00, { confidence: 0.45 }),
    piece(3, 1.19, -1.94, { confidence: 0.45 }),
    piece(4, -0.43, -2.22, { confidence: 0.82 }),
    piece(2, 1.01, -2.30, { confidence: 0.85 }),
    piece(2, -1.00, -2.20, { confidence: 0.83 }),
    piece(1, -1.84, -1.80, { confidence: 0.84 }),
    piece(1, -1.97, -2.29, { confidence: 0.80 }),
    piece(1, -2.37, -2.30, { confidence: 0.69 }),
    piece(1, 0.05, 2.38, { confidence: 0.84 }),
    piece(1, -0.07, 2.68, { confidence: 0.45 }),
  ];
  const current = piece(4);
  const d = decide(board(ps, current, {
    nextPieces: [current, piece(1), null],
    hold: piece(5),
    canHold: true,
    garbage: { ratio: 0.265, height: -2.512, gauge: 0, columns },
  }));
  assert.equal(d.diagnostics.ignoredUnsupportedHigh, 2, JSON.stringify(d));
  assert.equal(d.diagnostics.risk, 0, JSON.stringify(d));
  assert.ok(d.diagnostics.merges >= 1, JSON.stringify(d));
});

test('physically supported high stack remains deadline-dangerous', () => {
  const r = TYPE_RADII[10];
  const ps = Array.from({ length: 5 }, (_, i) =>
    piece(10, 0, -5 + r + i * 2 * r));
  const d = decide(board(ps));
  assert.equal(d.diagnostics.ignoredUnsupportedHigh, 0, JSON.stringify(d));
  assert.equal(d.diagnostics.risk, 2, JSON.stringify(d));
});

test('crowding is a cost, not a positive reward', () => {
  const d = decide(board([piece(7, -1, -1), piece(8, -1.5, -2), piece(6, 0, -2)]));
  approx(d.diagnostics.landingY, -4.793);
});
test('prefers reachable merge over a merely nearby buried match', () => {
  const d = decide(board([piece(1, -1), piece(1, 1), piece(7, 1, -3.8)]));
  assert.ok(d.x < 0, JSON.stringify(d)); assert.ok(d.diagnostics.merges >= 1);
});
test('localized garbage is not a global imaginary floor', () => {
  const cols = [{ left: -3.5, right: -0.5, top: 2.7 }];
  approx(landingAt([], piece(1), 2, cols), -4.793);
  const d = decide(board([], piece(1), { garbage: { ratio: 0.5, gauge: 0.9, columns: cols } }));
  assert.ok(d.x - TYPE_RADII[1] > -0.5, JSON.stringify(d));
});
test('all bad boards still return finite bounded least-risk decisions', () => {
  const ps = [-3, -2, -1, 0, 1, 2, 3].map(x => piece(10, x, 2.7));
  const d = decide(board(ps)); assert.ok(Number.isFinite(d.x)); assert.equal(d.diagnostics.risk, 2);
  assert.ok(Number.isFinite(d.diagnostics.heuristicValue));
});
test('queue holes do not promote slot 3 into slot 2', () => {
  const ps = [piece(3, -1), piece(5, 1)];
  const a = decide(board(ps, piece(1), { nextPieces: [piece(1), null, piece(5)] }));
  const b = decide(board(ps)); assert.deepEqual(a, b);
});
test('lookahead carries negative values instead of zero-clipping', () => {
  const ps = [-2, 0, 2].map(x => piece(10, x, 1.7));
  const now = decide(board(ps));
  const later = decide(board(ps, piece(1), { nextPieces: [piece(1), piece(5)] }));
  assert.ok(later.diagnostics.heuristicValue < now.diagnostics.heuristicValue);
});
test('HOLD swaps only for a meaningful improvement and respects canHold', () => {
  const b = board([piece(5, 1)], piece(1), { hold: piece(5), canHold: true });
  assert.equal(decide(b).hold, true); assert.equal(decide({ ...b, canHold: false }).hold, false);
});
test('empty HOLD needs explicit evidence, not missing image detection', () => {
  const b = board([piece(5, 1)], piece(1), { hold: null, nextPieces: [piece(1), piece(5)], canHold: true });
  assert.equal(decide(b).hold, false); assert.equal(decide({ ...b, holdKnownEmpty: true }).hold, true);
});
test('same-type HOLD is not spent on a tie', () => assert.equal(decide(board([], piece(1), { canHold: true, hold: piece(1) })).hold, false));
test('reject missing/current fallback instead of blind center click', () => {
  assert.throws(() => decide(board([], null)));
  assert.throws(() => decide(board([], piece(1, 0, 0, { fallback: true }))));
});
test('does not mutate caller state', () => {
  const b = board([piece(1), piece(4, 1)], piece(2), { nextPieces: [piece(2), piece(3), piece(4)], canHold: true, hold: piece(1) });
  const before = structuredClone(b); decide(b); assert.deepEqual(b, before);
});
test('data-URL strategy snapshot remains importable without relative imports', async () => {
  const source = readFileSync(new URL('../soren91/strategy.mjs', import.meta.url), 'utf8');
  const mod = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
  assert.ok(Number.isFinite(mod.decide(board()).x));
});
test('seeded noisy boards: finite decisions, legal walls and deterministic results', () => {
  let seed = 0x91;
  const random = () => ((seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0) / 2 ** 32);
  for (let trial = 0; trial < 80; trial++) {
    const ps = Array.from({ length: Math.floor(random() * 25) }, () => {
      const t = 1 + Math.floor(random() * 12), r = TYPE_RADII[t];
      return piece(t, (random() * 2 - 1) * (3.5 - r), -5 + r + random() * 6.5);
    });
    const t = 1 + Math.floor(random() * 15), b = board(ps, piece(t));
    const a = decide(b); assert.deepEqual(a, decide(b)); assert.ok(Math.abs(a.x) <= Math.min(3, 3.5 - TYPE_RADII[t]));
  }
});

test('image: detects a small circle without systematic radius shrink', () => {
  const im = image(), c = cal(); disc(im, 625, 550, TYPE_RADII[3] * 50);
  const ps = detectPieces(im.data, im.w, im.h, c); assert.equal(ps.length, 1); assert.equal(ps[0].type, 3, JSON.stringify(ps));
});
test('image: uncalibrated red hue cannot turn size-3 circle into type 2', () => {
  const im = image(), c = cal(); disc(im, 625, 550, TYPE_RADII[3] * 50, [220, 100, 50]);
  const ps = detectPieces(im.data, im.w, im.h, c); assert.equal(ps[0].type, 3, JSON.stringify(ps));
});
test('image: two nearby complete circles remain two pieces', () => {
  const im = image(), c = cal(); disc(im, 610, 550, 10.35); disc(im, 634, 550, 10.35);
  assert.equal(detectPieces(im.data, im.w, im.h, c).length, 2);
});
test('image: multicolour flag fragments form one circle', () => {
  const im = image(), c = cal(); disc(im, 625, 550, 19, (_x, y) => y < 550 ? [210, 50, 50] : [50, 100, 210]);
  const ps = detectPieces(im.data, im.w, im.h, c); assert.equal(ps.length, 1, JSON.stringify(ps));
  assert.ok(ps[0].type >= 3 && ps[0].type <= 5, JSON.stringify(ps));
});
test('image: real piece at former magic ghost coordinate is retained', () => {
  const im = image(), c = cal(); disc(im, 450 + (-1.64 + 3.5) * 50, 636 - (1.91 + 5) * 50, 10.35);
  assert.equal(detectPieces(im.data, im.w, im.h, c).length, 1);
});
test('image: blank NEXT has three null slots, never fabricated type 1', () => {
  const im = image(); assert.deepEqual(detectNextPieces(im.data, im.w, im.h, cal().board), [null, null, null]);
});
test('image: missing first/second NEXT does not shift subsequent slots', () => {
  const im = image(); disc(im, 730, 155, 14, [220, 100, 50]);
  const q = detectNextPieces(im.data, im.w, im.h, cal().board);
  assert.equal(q.length, 3); assert.equal(q[0], null); assert.equal(q[1].type, 2); assert.equal(q[2], null);
});
test('image: proportional resolution and fractional ROI are bounded', () => {
  const im = image(640, 360), c = cal();
  c.board = Object.fromEntries(Object.entries(c.board).map(([k, v]) => [k, v / 2 + (k === 'left' ? 0.1 : 0)]));
  disc(im, 312.5, 275, TYPE_RADII[3] * 25);
  assert.ok(detectPieces(im.data, im.w, im.h, c).length > 0);
  assert.equal(detectNextPieces(im.data, im.w, im.h, c.board).length, 3);
});
test('image: garbage occupancy counts background and reports local surfaces', () => {
  const im = image(), c = cal();
  for (let y = 560; y < 630; y++) for (let x = 460; x < 550; x++) {
    const i = (y * im.w + x) * 4; im.data[i] = im.data[i + 1] = im.data[i + 2] = 150;
  }
  const g = measureGarbage(im.data, im.w, im.h, c);
  assert.ok(g.ratio > 0.02 && g.ratio < 0.1, JSON.stringify(g));
  assert.ok(g.columns.length > 0 && g.columns.every(c => c.right < 0));
});
test('image: gray UI hairline does not make a high garbage wall', () => {
  const im = image(), c = cal();
  for (let x = 460; x < 790; x++) { const i = (238 * im.w + x) * 4; im.data[i] = im.data[i + 1] = im.data[i + 2] = 150; }
  assert.equal(measureGarbage(im.data, im.w, im.h, c).columns.length, 0);
});
test('gate: first frame waits, second stable frame permits MOVE', () => {
  const c = cal(), b = board([piece(1)]);
  assert.equal(gateObservation(b, c, 1000).state, 'DROP');
  assert.equal(gateObservation(b, c, 1300).state, 'MOVE');
});
test('gate: missing preview never becomes a WAITING/round-end event', () => {
  const c = cal(); const o = gateObservation(board([], null), c, 1000);
  assert.equal(o.state, 'DROP'); assert.equal(o.perception.reason, 'unknown-current');
});
test('gate: moving board waits then recovers without carrying stale pieces', () => {
  const c = cal(); gateObservation(board([piece(1, 0, 1)]), c, 1000);
  const b = board([piece(1, 0, -3)]);
  const first = gateObservation(b, c, 1300); assert.equal(first.state, 'DROP'); assert.equal(first.pieces[0].y, -3);
  assert.equal(gateObservation(b, c, 1600).state, 'MOVE');
});
test('gate: real WAITING clears history; new round reconfirms', () => {
  const c = cal(), b = board(); gateObservation(b, c, 1000); gateObservation(b, c, 1300);
  assert.equal(gateObservation({ ...b, state: 'WAITING' }, c, 1500).state, 'WAITING');
  assert.equal(gateObservation(b, c, 1800).state, 'DROP');
});
test('gate: cached geometry for a different resolution is unusable', () => assert.equal(usableCalibration(cal(), 640, 360), false));
test('gate: a slow remote-CDP loop period still confirms MOVE', () => {
  // 2026-09-16: the cdp-host (SOREN91_SHARED_BROWSER) loop measured ~5-6s per
  // iteration, so the old hard-coded 5s freshness window rejected every frame
  // and the bot never left DROP (no drops, no play, no comments).
  const c = cal(), b = board([piece(1)]);
  assert.equal(gateObservation(b, c, 1000).state, 'DROP');
  assert.equal(gateObservation(b, c, 7000).state, 'MOVE');
  assert.equal(gateObservation(b, c, 13000).state, 'MOVE');
});
test('gate: stale window is bounded and configurable', () => {
  assert.equal(DEFAULT_MAX_STALE_MS, 15_000);
  assert.equal(maxStaleMs({}), 15_000);
  assert.equal(maxStaleMs({ SOREN91_OBSERVATION_MAX_STALE_MS: '30000' }), 30_000);
  assert.equal(maxStaleMs({ SOREN91_OBSERVATION_MAX_STALE_MS: '0' }), 15_000);
  assert.equal(maxStaleMs({ SOREN91_OBSERVATION_MAX_STALE_MS: 'nope' }), 15_000);
  const c = cal(), b = board([piece(1)]);
  assert.equal(gateObservation(b, c, 1000).state, 'DROP');
  // Older than the window -> one more confirmation frame, never a stale MOVE.
  assert.equal(gateObservation(b, c, 1000 + DEFAULT_MAX_STALE_MS + 1).state, 'DROP');
  assert.equal(gateObservation(b, c, 1000 + DEFAULT_MAX_STALE_MS + 2).state, 'MOVE');
});
test('Sharp decode → recognition → gate → real strategy contract', async () => {
  const im = image(), c = cal(); disc(im, 730, 50, 14); disc(im, 600, 550, 10.35);
  const png = await sharp(im.data, { raw: { width: im.w, height: im.h, channels: 4 } }).png().toBuffer();
  assert.equal((await analyzeScreenshot(png, c)).state, 'DROP');
  const state = await analyzeScreenshot(png, c); assert.equal(state.state, 'MOVE', JSON.stringify(state));
  assert.ok(Number.isFinite(decide(state).x));
});
test('bounded search benchmark on 80-piece noisy board (reported, not win rate)', () => {
  const ps = Array.from({ length: 80 }, (_, i) => piece(1 + i % 10, -2.7 + i % 9 * 0.65, -4.5 + Math.floor(i / 9) * 0.7));
  const b = board(ps, piece(3), { nextPieces: [piece(3), piece(2), piece(5)], hold: piece(4), canHold: true });
  const start = performance.now(); const result = decide(b); const elapsed = performance.now() - start;
  console.log(`# Soren91 80-piece/3-preview/HOLD: ${elapsed.toFixed(1)}ms`);
  assert.ok(Number.isFinite(result.x)); assert.ok(elapsed < 5000, 'search must be bounded even on a slow CI runner');
});

test('generated-strategy behavior contract accepts the replacement', async () => {
  const { validateStrategyBehavior } = await import('../soren91/strategy_contract.mjs');
  assert.deepEqual(validateStrategyBehavior(decide), { valid: true, error: null });
});
test('generated-strategy gate rejects NaN, blind center and mutation', async () => {
  const { validateStrategyBehavior } = await import('../soren91/strategy_contract.mjs');
  for (const f of [() => ({ x: NaN, reason: 'nan' }), () => ({ x: 0, reason: 'blind' }),
    s => { s.pieces.push(piece(1)); return { x: 0, reason: 'mutated' }; }]) {
    assert.equal(validateStrategyBehavior(f).valid, false);
  }
});
test('gate: low-confidence frame is not the first of two trusted frames', () => {
  const c = cal();
  gateObservation(board([], piece(1, 0, 0, { confidence: 0.2 })), c, 1000);
  assert.equal(gateObservation(board(), c, 1300).state, 'DROP');
  assert.equal(gateObservation(board(), c, 1600).state, 'MOVE');
});
test('gate history survives analyzer cache-busting imports', async () => {
  const im = image(), c = cal(); disc(im, 730, 50, 14);
  const png = await sharp(im.data, { raw: { width: im.w, height: im.h, channels: 4 } }).png().toBuffer();
  const first = await import('../soren91/screenshot_analyzer.mjs?round-a');
  const second = await import('../soren91/screenshot_analyzer.mjs?round-b');
  assert.equal((await first.analyzeScreenshot(png, c)).state, 'DROP');
  assert.equal((await second.analyzeScreenshot(png, c)).state, 'MOVE');
});


test('preview radii use canonical physics, not scaled UI radii', () => {
  const normal = decide(board([], piece(3)));
  const largeIcon = decide(board([], piece(3, 0, 0, { r: 3.5 })));
  assert.deepEqual(largeIcon, normal);
});
test('gate: incoming garbage motion must also settle', () => {
  const c = cal();
  const b = y => board([], piece(1), { garbage: { columns: [{ left: -3, right: -2, top: y }] } });
  gateObservation(b(-4), c, 1000);
  assert.equal(gateObservation(b(-2), c, 1300).state, 'DROP');
  assert.equal(gateObservation(b(-2), c, 1600).state, 'MOVE');
});
test('gate: HOLD must be confirmed without blocking a known current drop', () => {
  const c = cal(); gateObservation(board(), c, 1000);
  const b = board([], piece(1), { hold: piece(3) });
  const first = gateObservation(b, c, 1300);
  assert.equal(first.state, 'MOVE'); assert.equal(first.hold, null);
  assert.equal(gateObservation(b, c, 1600).hold.type, 3);
});

async function bootstrapFixture(withWalls) {
  const im = image();
  if (withWalls) {
    for (let y = 0; y < im.h; y++) for (let x = 0; x < im.w; x++) {
      const value = y >= 220 && y < 636 && x >= 450 && x < 800 ? 50 : 180;
      const i = (y * im.w + x) * 4; im.data[i] = im.data[i + 1] = im.data[i + 2] = value;
      if (y >= 220 && y < 636 && ((x >= 440 && x < 450) || (x >= 800 && x < 810))) {
        im.data[i] = im.data[i + 1] = im.data[i + 2] = 240;
      }
    }
  }
  disc(im, 730, 50, 14);
  const png = await sharp(im.data, { raw: { width: im.w, height: im.h, channels: 4 } }).png().toBuffer();
  const dir = mkdtempSync(join(tmpdir(), 'soren91-bootstrap-'));
  try {
    mkdirSync(join(dir, 'tmp')); writeFileSync(join(dir, 'frame.png'), png);
    const moduleUrl = new URL('../soren91/screenshot_analyzer.mjs', import.meta.url).href;
    const script = `
      import { analyzeScreenshot } from ${JSON.stringify(moduleUrl)};
      const c = { screen: { width: 1280, height: 720 }, provisional: true,
        board: { left: 0, right: 1279, top: 0, bottom: 719, width: 1279, height: 719 } };
      const first = await analyzeScreenshot('frame.png', c);
      const second = await analyzeScreenshot('frame.png', c);
      console.log(JSON.stringify({ first: first.state, second: second.state,
        reason: second.perception.reason, count: second.pieces.length,
        provisional: c.provisional, method: c.method, dropArea: c.dropArea }));
    `;
    const output = execFileSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: dir, encoding: 'utf8', timeout: 15000,
    });
    return JSON.parse(output.trim().split('\n').at(-1));
  } finally { rmSync(dir, { recursive: true, force: true }); }
}
test('real calibration bootstraps an empty board before the first drop', async () => {
  const result = await bootstrapFixture(true);
  assert.equal(result.provisional, false); assert.equal(result.method, 'profile');
  assert.equal(result.count, 0); assert.equal(result.first, 'DROP'); assert.equal(result.second, 'MOVE');
  assert.ok(result.dropArea.pixelLeft > 450 && result.dropArea.pixelRight < 800);
});
test('failed real calibration remains no-input, not false round-end', async () => {
  const result = await bootstrapFixture(false);
  assert.equal(result.provisional, true); assert.equal(result.first, 'DROP');
  assert.equal(result.second, 'DROP'); assert.equal(result.reason, 'uncalibrated');
});

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  computePhysicalOverlap,
  isFiniteRect,
  parseVDisplayBounds,
  parseVDisplayList,
  placementFor,
  rectIntersectionArea,
} from '../tools/soren91_offscreen_verify.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const helperSrc = path.join(root, 'tools/macos/soren91_virtual_display.swift');
const helperBin = path.join(root, 'tools/macos/bin/soren91_virtual_display');
const buildScript = path.join(root, 'tools/soren91_virtual_display_build.sh');

// --- Pure bounds/verification contracts (run on any platform) ---

test('parseVDisplayBounds accepts the holder handshake shape and bare bounds', () => {
  assert.deepEqual(
    parseVDisplayBounds('{"ok":true,"displayID":6,"bounds":{"x":1920,"y":0,"width":1920,"height":1080}}'),
    { displayID: 6, bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
  );
  assert.deepEqual(
    parseVDisplayBounds({ x: 0, y: 0, width: 1920, height: 1080 }),
    { displayID: null, bounds: { x: 0, y: 0, width: 1920, height: 1080 } },
  );
});

test('parseVDisplayBounds rejects non-JSON, missing and degenerate bounds', () => {
  assert.throws(() => parseVDisplayBounds('not json'), /valid JSON/);
  assert.throws(() => parseVDisplayBounds('{"ok":true}'), /bounds/);
  assert.throws(() => parseVDisplayBounds({ x: 0, y: 0, width: 0, height: 10 }), /positive size/);
  assert.throws(() => parseVDisplayBounds({ x: NaN, y: 0, width: 1, height: 1 }), /finite/);
  assert.throws(() => parseVDisplayBounds({ displayID: -1, bounds: { x: 0, y: 0, width: 1, height: 1 } }), /displayID/);
});

test('placementFor puts the window origin inside the virtual display with a margin', () => {
  assert.deepEqual(
    placementFor({ x: 1920, y: 0, width: 1920, height: 1080 }),
    { left: 1940, top: 20 },
  );
  assert.deepEqual(
    placementFor({ x: 1920, y: 0, width: 1920, height: 1080 }, { margin: 0 }),
    { left: 1920, top: 0 },
  );
  assert.throws(() => placementFor(null), /finite/);
});

test('rectIntersectionArea measures exact pixel overlap', () => {
  assert.equal(rectIntersectionArea(
    { x: 0, y: 0, width: 10, height: 10 },
    { x: 5, y: 5, width: 10, height: 10 },
  ), 25);
  assert.equal(rectIntersectionArea(
    { x: 0, y: 0, width: 10, height: 10 },
    { x: 10, y: 0, width: 10, height: 10 },
  ), 0); // edge-touching is not overlap
  assert.equal(rectIntersectionArea(
    { x: 1939, y: 29, width: 960, height: 540 },
    { x: 0, y: 0, width: 1920, height: 1080 },
  ), 0);
});

test('computePhysicalOverlap skips the virtual display and fails on 1px', () => {
  const displays = [
    { id: 3, bounds: { x: 0, y: 0, width: 1920, height: 1080 } },
    { id: 6, bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
  ];
  const inside = computePhysicalOverlap(
    { x: 1940, y: 30, width: 960, height: 540 }, displays, 6,
  );
  assert.deepEqual(inside, { overlap: false, area: 0, displayIds: [] });

  const spill = computePhysicalOverlap(
    { x: 1919, y: 30, width: 960, height: 540 }, displays, 6,
  );
  assert.equal(spill.overlap, true);
  assert.equal(spill.area, 540); // 1px column x 540px height
  assert.deepEqual(spill.displayIds, [3]);
});

test('computePhysicalOverlap requires measured inputs', () => {
  assert.throws(() => computePhysicalOverlap(null, []), /windowRect/);
  assert.throws(() => computePhysicalOverlap({ x: 0, y: 0, width: 1, height: 1 }, null), /displays array/);
});

test('computePhysicalOverlap fails closed when display enumeration is incomplete', () => {
  const windowRect = { x: 1940, y: 30, width: 960, height: 540 };
  assert.throws(
    () => computePhysicalOverlap(windowRect, [], 6),
    /virtual display 6 missing/,
  );
  assert.throws(
    () => computePhysicalOverlap(windowRect, [
      { id: 6, bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
      { id: 3, bounds: { x: 0, y: 0, width: 0, height: 1080 } },
    ], 6),
    /malformed display bounds/,
  );
  assert.throws(
    () => computePhysicalOverlap(windowRect, [
      { id: 6, bounds: { x: 1920, y: 0, width: 1920, height: 1080 } },
      { bounds: { x: 0, y: 0, width: 1920, height: 1080 } },
    ], 6),
    /malformed display bounds/,
  );
});

test('parseVDisplayList accepts ok:true and rejects everything else', () => {
  const displays = [{ id: 3, bounds: { x: 0, y: 0, width: 1, height: 1 } }];
  assert.deepEqual(parseVDisplayList(JSON.stringify({ ok: true, displays })), displays);
  assert.throws(() => parseVDisplayList('not json'), /non-JSON/);
  assert.throws(() => parseVDisplayList('{"ok":false,"error":"x"}'), /fail-closed/);
  assert.throws(() => parseVDisplayList('{"ok":true}'), /fail-closed/);
});

test('isFiniteRect guards degenerate rectangles', () => {
  assert.equal(isFiniteRect({ x: 0, y: 0, width: 1, height: 1 }), true);
  assert.equal(isFiniteRect(null), false);
  assert.equal(isFiniteRect({ x: 0, y: 0, width: 1 }), false);
});

// --- Static source/build contracts (run on any platform) ---

test('virtual display helper source keeps the fail-soft JSON + KVC contract', () => {
  const src = fs.readFileSync(helperSrc, 'utf8');
  assert.match(src, /--list/);
  assert.match(src, /NSClassFromString/);
  assert.match(src, /virtualClass\("CGVirtualDisplay"/);
  assert.match(src, /setDispatchQueue:/);
  assert.match(src, /fixedVendorID|fixedProductID|fixedSerialNum/);
  assert.match(src, /still online/); // leak-detection error text
  assert.match(src, /displayID/);
});

test('virtual display helper never captures pixels or enumerates window titles', () => {
  const src = fs.readFileSync(helperSrc, 'utf8');
  // Whole-display / whole-window-list pixel capture has no place here (the
  // helper only creates a display and reports bounds). The substring
  // "screencapture" alone would also match the allowed ScreenCaptureKit
  // mention in comments, so match the actual whole-screen APIs/binaries.
  assert.doesNotMatch(src, /CGDisplayCreateImage|CGWindowListCreateImage/);
  assert.doesNotMatch(src, /["'`]screencapture["'`\s]/i);
  assert.doesNotMatch(src, /CGWindowListCopyWindowInfo/);
  assert.doesNotMatch(src, /SCShareableContent|SCStream/);
  assert.doesNotMatch(src, /\.title/);
});

test('virtual display build script mirrors the capture build script format', () => {
  const script = fs.readFileSync(buildScript, 'utf8');
  assert.match(script, /must run on macOS/);
  assert.match(script, /soren91_virtual_display\.swift/);
  assert.match(script, /only when missing or stale/);
  assert.ok(fs.statSync(buildScript).mode & 0o111, 'build script is executable');
});

// --- Live helper CLI (macOS + built binary only; never creates a display) ---

const canRunLive = process.platform === 'darwin' && fs.existsSync(helperBin);

test('helper --help exits 0 without creating a display', { skip: !canRunLive || undefined }, () => {
  const listIds = () => parseVDisplayList(
    String(spawnSync(helperBin, ['--list'], { encoding: 'utf8' }).stderr || '').trim().split('\n').pop(),
  ).map((display) => display.id).sort();
  const before = listIds();
  const help = spawnSync(helperBin, ['--help'], { encoding: 'utf8' });
  assert.equal(help.status, 0);
  assert.deepEqual(listIds(), before, 'no display may be created by --help');
});

test('helper rejects unknown args with ok:false JSON and exit 2', { skip: !canRunLive || undefined }, () => {
  const result = spawnSync(helperBin, ['--bogus'], { encoding: 'utf8' });
  assert.equal(result.status, 2);
  assert.match(String(result.stderr || ''), /"ok":false/);
});

test('helper --list reports online displays as ok:true JSON', { skip: !canRunLive || undefined }, () => {
  const result = spawnSync(helperBin, ['--list'], { encoding: 'utf8' });
  assert.equal(result.status, 0);
  const displays = parseVDisplayList(String(result.stderr || '').trim().split('\n').pop());
  assert.ok(displays.length >= 1);
  for (const display of displays) assert.ok(isFiniteRect(display.bounds));
});

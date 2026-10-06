import test from 'node:test';
import assert from 'node:assert/strict';
import {
  lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync, writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';

import {
  MAX_REJECTED_FRAMES_PER_SESSION,
  rejectedFrameReason,
  saveRejectedFrame,
} from '../soren91/rejected_frame_diagnostics.mjs';

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const name = Buffer.from(type);
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length);
  const checksum = Buffer.alloc(4); checksum.writeUInt32BE(crc32(Buffer.concat([name, data])));
  return Buffer.concat([length, name, data, checksum]);
}
const WIDTH = 960;
const HEIGHT = 540;
const ihdr = Buffer.alloc(13);
ihdr.writeUInt32BE(WIDTH, 0); ihdr.writeUInt32BE(HEIGHT, 4);
ihdr[8] = 8; ihdr[9] = 2;
const scanline = Buffer.alloc(WIDTH * 3 + 1);
const PNG = Buffer.concat([
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr),
  chunk('IDAT', deflateSync(Buffer.concat(Array.from({ length: HEIGHT }, () => scanline)))),
  chunk('IEND', Buffer.alloc(0)),
]);
const SESSION = '2d51f1b2-32f3-4c3c-91eb-48573d36e52f';
const geometry = {
  x: 0, y: 0, width: WIDTH, height: HEIGHT, scrollX: 0, scrollY: 0, dpr: 1,
  viewportWidth: WIDTH, viewportHeight: HEIGHT, viewportScale: 1,
};

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'soren91-reject-frames-'));
  mkdirSync(join(root, 'tmp'));
  const save = (reason, values = {}) => saveRejectedFrame({
    enabled: true,
    runtimeDir: root,
    game: 7,
    turn: 12,
    sessionId: SESSION,
    reason,
    boardConfidence: 0.47,
    currentPieceConfidence: 0.31,
    observation: { buffer: PNG, format: 'png', width: WIDTH, height: HEIGHT,
      capturedAt: 1234.5, captureMs: 8.25, geometry },
    now: 1_780_000_000_000,
    ...values,
  });
  return { root, save };
}

test('rejectedFrameReason admits only the three diagnostic reason classes', () => {
  assert.equal(rejectedFrameReason('WAITING', 'non-move'), 'non-move');
  assert.equal(rejectedFrameReason('MOVE', 'unknown-current'), 'unknown-current');
  assert.equal(rejectedFrameReason('MOVE', 'confirm-frame-unstable'), 'confirm-frame');
  assert.equal(rejectedFrameReason('MOVE', 'board-moving'), null);
  assert.equal(rejectedFrameReason('WAITING', 'some arbitrary error'), 'non-move');
});

test('default-off and invalid image data never create diagnostic evidence', () => {
  const { root, save } = fixture();
  try {
    const disabled = save('unknown-current', { enabled: false });
    assert.deepEqual(disabled, { saved: false, reason: 'disabled' });
    const invalid = save('confirm-frame', { observation: { buffer: Buffer.from('no'), format: 'png', width: 1, height: 1 } });
    assert.deepEqual(invalid, { saved: false, reason: 'invalid-frame' });
    assert.equal(readdirSync(join(root, 'tmp')).length, 0);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('saves the already captured bytes once per eligible reason with private fixed metadata', () => {
  const { root, save } = fixture();
  const output = join(root, 'tmp', 'rejected_frame_diagnostics', `run_${SESSION}`);
  try {
    assert.deepEqual(save('unknown-current'), { saved: true, image: 'frame_00/image.png', metadata: 'frame_00/metadata.json' });
    assert.deepEqual(save('unknown-current'), { saved: false, reason: 'reason-already-saved' });
    assert.deepEqual(save('confirm-frame'), { saved: true, image: 'frame_01/image.png', metadata: 'frame_01/metadata.json' });
    assert.deepEqual(save('non-move'), { saved: true, image: 'frame_02/image.png', metadata: 'frame_02/metadata.json' });
    assert.deepEqual(save('non-move', { turn: 13 }), { saved: false, reason: 'limit' });
    assert.deepEqual(save('unknown-current', {
      sessionId: 'b09027a0-2d46-4c88-90fb-3c145db9c935',
      turn: 13,
    }), { saved: true, image: 'frame_00/image.png', metadata: 'frame_00/metadata.json' });
    assert.equal(readdirSync(output).filter(name => /^frame_\d{2}$/.test(name)).length, MAX_REJECTED_FRAMES_PER_SESSION);
    assert.deepEqual(readFileSync(join(output, 'frame_00/image.png')), PNG);
    const metadataText = readFileSync(join(output, 'frame_00/metadata.json'), 'utf8');
    const metadata = JSON.parse(metadataText);
    assert.deepEqual(metadata, {
      schema: 1,
      game: 7,
      turn: 12,
      sessionId: SESSION,
      reason: 'unknown-current',
      imageFormat: 'png',
      boardConfidence: 0.47,
      currentPieceConfidence: 0.31,
      image: { width: WIDTH, height: HEIGHT },
      fileMtimeMs: Math.round(lstatSync(join(output, 'frame_00/image.png')).mtimeMs),
      recordedAtMs: 1_780_000_000_000,
      capture: { capturedAtMs: 1234.5, captureMs: 8.25, geometry },
    });
    for (const privateField of ['path', 'host', 'url', 'credential', 'rawState']) {
      assert.equal(metadataText.toLowerCase().includes(privateField), false);
    }
    assert.equal(lstatSync(output).mode & 0o777, 0o700);
    assert.equal(lstatSync(join(output, 'frame_00')).mode & 0o777, 0o700);
    assert.equal(lstatSync(join(output, 'frame_00/image.png')).mode & 0o777, 0o600);
    assert.equal(metadata.currentPieceConfidence, 0.31);
    assert.equal(metadata.boardConfidence, 0.47);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('an interrupted staged pair is discarded without losing committed frames and next reason can save', () => {
  const { root, save } = fixture();
  const run = join(root, 'tmp', 'rejected_frame_diagnostics', `run_${SESSION}`);
  try {
    assert.equal(save('unknown-current').saved, true);
    const preserved = readFileSync(join(run, 'frame_00/image.png'));
    const staging = join(run, `.frame_01.${SESSION}.tmp`);
    mkdirSync(staging, { mode: 0o700 });
    // Simulate process interruption after the image write and before metadata.
    writeFileSync(join(staging, 'image.png'), PNG, { mode: 0o600 });
    assert.deepEqual(save('confirm-frame'), {
      saved: true, image: 'frame_01/image.png', metadata: 'frame_01/metadata.json',
    });
    assert.deepEqual(readFileSync(join(run, 'frame_00/image.png')), preserved);
    assert.equal(readdirSync(run).some(name => name.endsWith('.tmp')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('a complete but unpublished staging pair is discarded after interruption', () => {
  const { root, save } = fixture();
  const run = join(root, 'tmp', 'rejected_frame_diagnostics', `run_${SESSION}`);
  try {
    assert.equal(save('unknown-current').saved, true);
    const preserved = readFileSync(join(run, 'frame_00/image.png'));
    const staging = join(run, `.frame_01.${SESSION}.tmp`);
    mkdirSync(staging, { mode: 0o700 });
    // Simulate interruption after both files are fsynced but before atomic
    // directory publication. The staged pair is not adopted as evidence.
    writeFileSync(join(staging, 'image.png'), PNG, { mode: 0o600 });
    writeFileSync(join(staging, 'metadata.json'),
      readFileSync(join(run, 'frame_00/metadata.json')), { mode: 0o600 });
    assert.deepEqual(save('confirm-frame'), {
      saved: true, image: 'frame_01/image.png', metadata: 'frame_01/metadata.json',
    });
    assert.deepEqual(readFileSync(join(run, 'frame_00/image.png')), preserved);
    assert.equal(readdirSync(run).some(name => name.endsWith('.tmp')), false);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('injected failure between image and sidecar leaves no published half-pair', () => {
  const { root, save } = fixture();
  const run = join(root, 'tmp', 'rejected_frame_diagnostics', `run_${SESSION}`);
  try {
    assert.equal(save('unknown-current').saved, true);
    assert.deepEqual(save('confirm-frame', { stageHook: () => { throw new Error('fixture-fault'); } }),
      { saved: false, reason: 'write-failed' });
    assert.deepEqual(readdirSync(run), ['frame_00']);
    assert.equal(save('confirm-frame').saved, true);
    assert.deepEqual(readdirSync(run).sort(), ['frame_00', 'frame_01']);
  } finally { rmSync(root, { recursive: true, force: true }); }
});

test('fails closed for symlinked diagnostic directories and over-size frames', () => {
  const { root, save } = fixture();
  const outside = mkdtempSync(join(tmpdir(), 'soren91-outside-'));
  try {
    rmSync(join(root, 'tmp'), { recursive: true, force: true });
    symlinkSync(outside, join(root, 'tmp'));
    assert.deepEqual(save('unknown-current'), { saved: false, reason: 'unsafe-directory' });
    assert.equal(readdirSync(outside).length, 0);

    rmSync(join(root, 'tmp'), { force: true });
    mkdirSync(join(root, 'tmp'));
    const tooLarge = Buffer.concat([PNG, Buffer.alloc(8 * 1024 * 1024)]);
    assert.deepEqual(save('unknown-current', {
      observation: { buffer: tooLarge, format: 'png', width: 960, height: 540 },
    }), { saved: false, reason: 'invalid-frame' });
    assert.equal(readdirSync(join(root, 'tmp')).length, 0);
  } finally {
    rmSync(root, { recursive: true, force: true });
    rmSync(outside, { recursive: true, force: true });
  }
});

test('preserves captured JPEG bytes and records the matching extension', () => {
  const { root, save } = fixture();
  const jpeg = Buffer.from([0xff, 0xd8, 0x01, 0x02, 0xff, 0xd9]);
  const output = join(root, 'tmp', 'rejected_frame_diagnostics', `run_${SESSION}`);
  try {
    const result = save('confirm-frame', {
      observation: { buffer: jpeg, format: 'jpeg', width: 960, height: 540,
        capturedAt: 1234.5, captureMs: 8.25, geometry },
    });
    assert.deepEqual(result, { saved: true, image: 'frame_00/image.jpg', metadata: 'frame_00/metadata.json' });
    assert.deepEqual(readFileSync(join(output, 'frame_00/image.jpg')), jpeg);
    assert.equal(JSON.parse(readFileSync(join(output, 'frame_00/metadata.json'), 'utf8')).imageFormat, 'jpeg');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

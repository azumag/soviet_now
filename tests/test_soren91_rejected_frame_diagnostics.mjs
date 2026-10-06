import test from 'node:test';
import assert from 'node:assert/strict';
import {
  lstatSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, symlinkSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import {
  MAX_REJECTED_FRAMES_PER_SESSION,
  rejectedFrameReason,
  saveRejectedFrame,
} from '../soren91/rejected_frame_diagnostics.mjs';

const PNG = Buffer.concat([
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
  Buffer.from('synthetic-frame-payload'),
]);
const SESSION = '2d51f1b2-32f3-4c3c-91eb-48573d36e52f';

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
    confidence: 0.47,
    observation: { buffer: PNG, format: 'png', width: 960, height: 540 },
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
    assert.deepEqual(save('unknown-current'), { saved: true, image: 'frame_00.png', metadata: 'frame_00.json' });
    assert.deepEqual(save('unknown-current'), { saved: false, reason: 'reason-already-saved' });
    assert.deepEqual(save('confirm-frame'), { saved: true, image: 'frame_01.png', metadata: 'frame_01.json' });
    assert.deepEqual(save('non-move'), { saved: true, image: 'frame_02.png', metadata: 'frame_02.json' });
    assert.deepEqual(save('non-move', { turn: 13 }), { saved: false, reason: 'limit' });
    assert.deepEqual(save('unknown-current', {
      sessionId: 'b09027a0-2d46-4c88-90fb-3c145db9c935',
      turn: 13,
    }), { saved: true, image: 'frame_00.png', metadata: 'frame_00.json' });
    assert.equal(readdirSync(output).filter(name => name.endsWith('.png')).length, MAX_REJECTED_FRAMES_PER_SESSION);
    assert.deepEqual(readFileSync(join(output, 'frame_00.png')), PNG);
    const metadataText = readFileSync(join(output, 'frame_00.json'), 'utf8');
    const metadata = JSON.parse(metadataText);
    assert.deepEqual(metadata, {
      schema: 1,
      game: 7,
      turn: 12,
      sessionId: SESSION,
      reason: 'unknown-current',
      imageFormat: 'png',
      confidence: 0.47,
      image: { width: 960, height: 540 },
      fileMtimeMs: Math.round(lstatSync(join(output, 'frame_00.png')).mtimeMs),
      recordedAtMs: 1_780_000_000_000,
    });
    for (const privateField of ['path', 'host', 'url', 'credential', 'rawState']) {
      assert.equal(metadataText.toLowerCase().includes(privateField), false);
    }
    assert.equal(lstatSync(output).mode & 0o777, 0o700);
    assert.equal(lstatSync(join(output, 'frame_00.png')).mode & 0o777, 0o600);
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
      observation: { buffer: jpeg, format: 'jpeg', width: 960, height: 540 },
    });
    assert.deepEqual(result, { saved: true, image: 'frame_00.jpg', metadata: 'frame_00.json' });
    assert.deepEqual(readFileSync(join(output, 'frame_00.jpg')), jpeg);
    assert.equal(JSON.parse(readFileSync(join(output, 'frame_00.json'), 'utf8')).imageFormat, 'jpeg');
  } finally { rmSync(root, { recursive: true, force: true }); }
});

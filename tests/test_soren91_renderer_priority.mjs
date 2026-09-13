// Contract tests for the pure renderer backend selector
// (tools/soren91_renderer_priority.mjs). No I/O, no cloud calls.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  DEFAULT_LOCAL_ORDER,
  isUsableCandidate,
  selectRendererBackend,
  tierForBackend,
} from '../tools/soren91_renderer_priority.mjs';

const mac = { backend: 'local-macos', available: true };
const win = { backend: 'local-windows', available: true };
const interruptible = { backend: 'powergpu-p4-interruptible', available: true };
const ondemand = { backend: 'powergpu-p4-ondemand', available: true };

test('default local order prefers macOS first (provisional, pending measurement)', () => {
  assert.deepEqual(DEFAULT_LOCAL_ORDER, ['local-macos', 'local-windows']);
  const picked = selectRendererBackend([win, mac]);
  assert.equal(picked.backend, 'local-macos');
  assert.equal(picked.tier, -1);
});

test('local hosts win over cloud fallbacks', () => {
  const picked = selectRendererBackend([ondemand, interruptible, win]);
  assert.equal(picked.backend, 'local-windows');
  assert.equal(picked.tier, -1);
});

test('interruptible precedes on-demand when no local host is usable', () => {
  const picked = selectRendererBackend([ondemand, interruptible]);
  assert.equal(picked.backend, 'powergpu-p4-interruptible');
  assert.equal(picked.tier, 0);
});

test('busy and unhealthy candidates are skipped', () => {
  assert.equal(isUsableCandidate({ ...mac, busy: true }), false);
  assert.equal(isUsableCandidate({ ...mac, healthy: false }), false);
  assert.equal(isUsableCandidate({ ...mac, available: false }), false);
  // Unknown health (field missing) does not disqualify.
  assert.equal(isUsableCandidate(mac), true);
  const picked = selectRendererBackend([
    { ...mac, busy: true },
    { ...win, healthy: false },
    interruptible,
  ]);
  assert.equal(picked.backend, 'powergpu-p4-interruptible');
});

test('localOrder override reorders local preference', () => {
  const picked = selectRendererBackend([mac, win], { localOrder: ['local-windows', 'local-macos'] });
  assert.equal(picked.backend, 'local-windows');
});

test('known cloud backends precede unknown ones; unknowns fall back in input order', () => {
  const cloudFirst = selectRendererBackend([
    { backend: 'mystery-box', available: true },
    ondemand,
  ]);
  assert.equal(cloudFirst.backend, 'powergpu-p4-ondemand');
  const unknownOnly = selectRendererBackend([
    { backend: 'mystery-box', available: true },
    { backend: 'other-box', available: true },
  ]);
  assert.equal(unknownOnly.backend, 'mystery-box');
  assert.equal(unknownOnly.tier, null);
  assert.match(unknownOnly.reason, /mystery-box/);
});

test('no usable candidate returns a null selection', () => {
  assert.deepEqual(selectRendererBackend([]), {
    backend: null, tier: null, reason: 'no candidate available',
  });
  assert.deepEqual(selectRendererBackend([{ ...mac, busy: true }]), {
    backend: null, tier: null, reason: 'no candidate available',
  });
  assert.deepEqual(selectRendererBackend(null), {
    backend: null, tier: null, reason: 'no candidate available',
  });
});

test('tier mapping is stable', () => {
  assert.equal(tierForBackend('local-macos'), -1);
  assert.equal(tierForBackend('local-windows'), -1);
  assert.equal(tierForBackend('powergpu-p4-interruptible'), 0);
  assert.equal(tierForBackend('powergpu-p4-ondemand'), 1);
  assert.equal(tierForBackend('something-else'), null);
});

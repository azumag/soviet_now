import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { loadBroadcastStateBuilder } from '../lib/docich_broadcast_state_loader.mjs';
import { buildDirectBroadcastOverlayState as legacyBuilder } from '../lib/direct_broadcast_overlay.mjs';

const makeRoot = () => fs.mkdtempSync(path.join(os.tmpdir(), 'soren-docich-state-'));
const writeDocich = (root, source) => {
  const file = path.join(root, 'overlays', 'direct_broadcast_overlay_state.mjs');
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, source);
};

test('state builder uses docich contract when installed, not the Soviet fallback', async () => {
  const root = makeRoot();
  try {
    writeDocich(root, `
      export const DIRECT_BROADCAST_STATE_ROUTE = '/__soren_overlay/broadcast/state';
      export const DIRECT_BROADCAST_VERSION = 1;
      export function buildDirectBroadcastOverlayState() { return { owner: 'docich' }; }
    `);
    const build = await loadBroadcastStateBuilder({ DOCICH_ROOT: root });
    assert.deepEqual(build(), { owner: 'docich' });
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

test('missing, invalid and malformed docich modules fail open to Soviet', async () => {
  const root = makeRoot();
  try {
    assert.equal(await loadBroadcastStateBuilder({ DOCICH_ROOT: root }), legacyBuilder);
    writeDocich(root, 'export const DIRECT_BROADCAST_VERSION = 99;');
    assert.equal(await loadBroadcastStateBuilder({ DOCICH_ROOT: root }), legacyBuilder);
    const broken = makeRoot();
    try {
      writeDocich(broken, 'export const BROKEN = ;');
      assert.equal(await loadBroadcastStateBuilder({ DOCICH_ROOT: broken }), legacyBuilder);
    } finally { fs.rmSync(broken, { recursive: true, force: true }); }
  } finally { fs.rmSync(root, { recursive: true, force: true }); }
});

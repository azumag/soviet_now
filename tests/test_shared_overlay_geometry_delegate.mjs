import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { SHARED_OVERLAY_LAYOUT } from '../lib/shared_overlay.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const viewport = {
  innerWidth: 1280, innerHeight: 720,
  outerWidth: 1280, outerHeight: 720,
  screenX: 0, screenY: 0,
  screenWidth: 1280, screenHeight: 720,
  stage: { left: 0, top: 0, width: 1280, height: 720 },
};

function probe(docichRoot) {
  const script = `
    import { sharedOverlayViewportReady } from './lib/shared_overlay.mjs';
    const viewport = JSON.parse(process.argv[1]);
    process.stdout.write(String(sharedOverlayViewportReady(viewport)));
  `;
  const run = spawnSync(process.execPath, ['--input-type=module', '-e', script, JSON.stringify(viewport)], {
    cwd: ROOT,
    env: { ...process.env, DOCICH_ROOT: docichRoot },
    encoding: 'utf8',
    timeout: 10000,
  });
  assert.equal(run.status, 0, run.stderr);
  return run.stdout;
}

test('docich geometry takes priority while standalone fallback remains available', () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-docich-geometry-'));
  try {
    assert.equal(probe(root), 'true', 'missing docich must use the local readiness check');
    const dir = path.join(root, 'overlays');
    fs.mkdirSync(dir);
    const file = path.join(dir, 'shared_overlay_geometry.mjs');
    fs.writeFileSync(file, `
      export const SHARED_OVERLAY_LAYOUT = ${JSON.stringify(SHARED_OVERLAY_LAYOUT)};
      export function sharedOverlayViewportReady() { return false; }
    `);
    assert.equal(probe(root), 'false', 'matching docich contract must take precedence');
    fs.writeFileSync(file, `
      export const SHARED_OVERLAY_LAYOUT = {width:999,height:999};
      export function sharedOverlayViewportReady() { return false; }
    `);
    assert.equal(probe(root), 'true', 'wrong geometry must fail open to local layout');
    fs.writeFileSync(file, 'not valid javascript!');
    assert.equal(probe(root), 'true', 'broken module must not stop a standalone game');
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

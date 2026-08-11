import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DIRECT_OVERLAY_ELEMENT_ID,
  DIRECT_OVERLAY_ROUTE,
  directOverlayIdleHtml,
  directOverlaySurfaceVisible,
  installDirectOverlay,
  loadDirectOverlayConfig,
} from '../lib/direct_overlay.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';


test('direct overlay is enabled only for explicit Linux FFmpeg backend', () => {
  const base = {
    SOREN_DIRECT_OVERLAY_HTML_FILE: 'tmp/state/event_overlay.html',
  };
  assert.equal(loadDirectOverlayConfig({ ...base, SOREN_STREAM_BACKEND: 'obs' }, 'linux').enabled, false);
  assert.equal(loadDirectOverlayConfig({ ...base, SOREN_STREAM_BACKEND: 'ffmpeg' }, 'darwin').enabled, false);
  assert.equal(loadDirectOverlayConfig({
    ...base,
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_OVERLAY_ENABLED: '0',
  }, 'linux').enabled, false);
  const enabled = loadDirectOverlayConfig({ ...base, SOREN_STREAM_BACKEND: 'ffmpeg' }, 'linux');
  assert.equal(enabled.enabled, true);
  assert.equal(enabled.route, DIRECT_OVERLAY_ROUTE);
  assert.equal(enabled.elementId, DIRECT_OVERLAY_ELEMENT_ID);
  assert.match(enabled.htmlFile, /tmp\/state\/event_overlay[.]html$/);
  assert.deepEqual(enabled.surfaces.map((item) => item.key), [
    'event', 'stats', 'ops', 'improve', 'wildcard', 'avsync',
  ]);
});


test('overlay installer persists across reload and installs current page', async () => {
  const calls = [];
  const page = {
    async addInitScript(callback, payload) {
      calls.push(['init', callback, payload]);
    },
    async evaluate(callback, payload) {
      calls.push(['evaluate', callback, payload]);
    },
  };
  const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'ffmpeg' }, 'linux');
  assert.equal(await installDirectOverlay(page, config), true);
  assert.deepEqual(calls.map(([kind]) => kind), ['init', 'evaluate']);
  assert.equal(calls[0][2].length, 6);
  assert.equal(calls[0][2][0].route, DIRECT_OVERLAY_ROUTE);
  assert.equal(calls[0][2][0].elementId, DIRECT_OVERLAY_ELEMENT_ID);
  assert.equal(calls[0][2].find((item) => item.route.endsWith('/av-sync')).pollMs, 250);
});


test('state-driven surfaces match normal, improvement, and wildcard layouts', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'soren-direct-overlay-'));
  const wildcardState = path.join(temp, 'wildcard.json');
  const improveState = path.join(temp, 'improve.json');
  const config = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    WILDCARD_PARALLEL_STATUS_FILE: wildcardState,
    IMPROVE_STATE_FILE: improveState,
  }, 'linux');
  const byKey = Object.fromEntries(config.surfaces.map((item) => [item.key, item]));

  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'idle' }));
  fs.writeFileSync(improveState, JSON.stringify({ status: 'running' }));
  assert.equal(directOverlaySurfaceVisible(byKey.stats), true);
  assert.equal(directOverlaySurfaceVisible(byKey.ops), true);
  assert.equal(directOverlaySurfaceVisible(byKey.improve), true);
  assert.equal(directOverlaySurfaceVisible(byKey.wildcard), false);

  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'running' }));
  assert.equal(directOverlaySurfaceVisible(byKey.stats), false);
  assert.equal(directOverlaySurfaceVisible(byKey.ops), false);
  assert.equal(directOverlaySurfaceVisible(byKey.improve), false);
  assert.equal(directOverlaySurfaceVisible(byKey.wildcard), true);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('individual direct overlay surfaces can be disabled without affecting event', () => {
  const config = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_OPS_OVERLAY_ENABLED: '0',
  }, 'linux');
  assert.deepEqual(config.surfaces.map((item) => item.key), [
    'event', 'stats', 'improve', 'wildcard', 'avsync',
  ]);
});


test('A/V sync surface is transparent unless its generated probe file exists', () => {
  const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'ffmpeg' }, 'linux');
  const probe = config.surfaces.find((item) => item.key === 'avsync');
  assert.ok(probe);
  assert.equal(probe.route, '/__soren_overlay/av-sync');
  assert.equal(probe.style.width, '128px');
  assert.equal(probe.pollMs, 250);
  assert.equal(directOverlaySurfaceVisible(probe), true);
  assert.match(probe.htmlFile, /tmp\/state\/direct_av_sync_probe[.]html$/);
});


test('idle overlay actively reloads so a later generated probe is discovered', () => {
  const html = directOverlayIdleHtml();
  assert.match(html, /background:transparent/);
  assert.match(html, /setTimeout\(\(\)=>location[.]reload\(\),1000\)/);
});


test('disabled overlay performs no page mutation', async () => {
  const page = {
    addInitScript() { throw new Error('must not run'); },
    evaluate() { throw new Error('must not run'); },
  };
  const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'obs' }, 'linux');
  assert.equal(await installDirectOverlay(page, config), false);
});

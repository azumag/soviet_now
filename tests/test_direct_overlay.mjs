import assert from 'node:assert/strict';
import test from 'node:test';

import {
  DIRECT_OVERLAY_ELEMENT_ID,
  DIRECT_OVERLAY_ROUTE,
  DIRECT_STAGE_ELEMENT_ID,
  directOverlayIdleHtml,
  directOverlaySurfaceVisible,
  installDirectOverlay,
  loadDirectOverlayConfig,
  loadDirectStageConfig,
  stripOverlaySelfRefresh,
} from '../lib/direct_overlay.mjs';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';


const TEST_DIR = path.dirname(fileURLToPath(import.meta.url));


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
  assert.match(String(calls[0][1]), /window[.]top !== window/);
  assert.equal(calls[0][2][0].route, DIRECT_OVERLAY_ROUTE);
  assert.equal(calls[0][2][0].elementId, DIRECT_OVERLAY_ELEMENT_ID);
  assert.equal(calls[0][2][0].key, 'event');
  assert.equal(calls[0][2][0].pollMs, 1000);
  assert.equal(calls[0][2].find((item) => item.route.endsWith('/av-sync')).pollMs, 250);
  assert.match(String(calls[0][1]), /elementId}-buffer/);
  assert.match(String(calls[0][1]), /incoming[.]addEventListener\('load'/);
  assert.match(String(calls[0][1]), /outgoing[.]style[.]visibility = 'hidden'/);
});


test('default FFmpeg stage keeps a 960x540 game and a 320px dashboard', () => {
  const stage = loadDirectStageConfig({ SOREN_STREAM_BACKEND: 'ffmpeg' }, 'linux');
  assert.deepEqual(stage, {
    enabled: true,
    mode: 'dashboard',
    elementId: DIRECT_STAGE_ELEMENT_ID,
    outputWidth: 1280,
    outputHeight: 720,
    gameLeft: 0,
    gameTop: 90,
    gameWidth: 960,
    gameHeight: 540,
    sidebarLeft: 960,
    sidebarWidth: 320,
    topRailHeight: 90,
    bottomRailTop: 630,
    bottomRailHeight: 90,
  });

  const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'ffmpeg' }, 'linux');
  const byKey = Object.fromEntries(config.surfaces.map((item) => [item.key, item]));
  assert.equal(config.stage.gameWidth, 960);
  assert.equal(byKey.event.style.width, '960px');
  assert.equal(byKey.event.style.height, '720px');
  assert.ok(Number.parseInt(byKey.stats.style.left, 10) >= 960);
  assert.ok(Number.parseInt(byKey.ops.style.left, 10) >= 960);
  assert.match(byKey.stats.style.transform, /^scale\(0[.]/);
  assert.match(byKey.ops.style.transform, /^scale\(0[.]/);
});


test('stage validates dashboard room and supports explicit fullscreen compatibility', () => {
  assert.throws(
    () => loadDirectStageConfig({
      SOREN_STREAM_BACKEND: 'ffmpeg',
      SOREN_DIRECT_GAME_DISPLAY_SIZE: '1152x648',
    }, 'linux'),
    /at least 256px/,
  );
  assert.throws(
    () => loadDirectStageConfig({
      SOREN_STREAM_BACKEND: 'ffmpeg',
      SOREN_DIRECT_GAME_DISPLAY_SIZE: '900x540',
    }, 'linux'),
    /16:9/,
  );
  const fullscreen = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen',
  }, 'linux');
  assert.equal(fullscreen.stage.enabled, false);
  assert.equal(fullscreen.surfaces[0].style.width, '100vw');
});


test('stage changes only canvas CSS size, never the Unity drawing buffer', () => {
  const source = fs.readFileSync(path.join(TEST_DIR, '..', 'soviet_local.mjs'), 'utf8');
  const start = source.indexOf('// Linux FFmpeg dashboard');
  const end = source.indexOf("console.log('Canvas layout:'", start);
  const layout = source.slice(start, end);
  assert.match(layout, /stage[.]gameWidth/);
  assert.match(layout, /stage[.]gameHeight/);
  assert.match(layout, /imageRendering = staged \? 'pixelated'/);
  assert.doesNotMatch(layout, /canvas[.]width\s*=/);
  assert.doesNotMatch(layout, /canvas[.]height\s*=/);
  assert.match(source, /canvasCssWidth: rect \? Math[.]round\(rect[.]width\) : 0/);
  assert.match(source, /stageMode: document[.]getElementById\('soren-direct-stream-stage'\)/);
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
  assert.equal(probe.key, 'avsync');
});


test('idle overlay stays transparent without a self-refresh navigation', () => {
  const html = directOverlayIdleHtml();
  assert.match(html, /background:transparent/);
  assert.doesNotMatch(html, /http-equiv=["']?refresh/i);
  assert.doesNotMatch(html, /location[.]reload/);
});


test('direct overlay polling strips generated self-refresh navigation', () => {
  const html = '<!doctype html><meta http-equiv="refresh" content="2"><main>stable</main>'
    + '<script>setTimeout(()=>location.reload(),1000)</script>';
  const cleaned = stripOverlaySelfRefresh(html);
  assert.equal(cleaned, '<!doctype html><main>stable</main>');
});


test('disabled overlay performs no page mutation', async () => {
  const page = {
    addInitScript() { throw new Error('must not run'); },
    evaluate() { throw new Error('must not run'); },
  };
  const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'obs' }, 'linux');
  assert.equal(await installDirectOverlay(page, config), false);
});

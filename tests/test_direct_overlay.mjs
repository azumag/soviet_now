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
    'broadcastSidebar', 'broadcastTop', 'broadcastBottom', 'twica', 'improve', 'wildcard', 'avsync',
  ]);
  assert.equal(enabled.broadcast.stateRoute, '/__soren_overlay/broadcast/state');
  assert.match(enabled.broadcast.sources.eventHtmlFile, /event_overlay[.]html$/);
  assert.match(enabled.broadcast.sources.statsHtmlFile, /status_overlay[.]html$/);
  assert.match(enabled.broadcast.sources.opsHtmlFile, /show_status_overlay[.]html$/);
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
  assert.equal(calls[0][2].length, 7);
  assert.match(String(calls[0][1]), /window[.]top !== window/);
  assert.equal(calls[0][2][0].route, '/__soren_overlay/broadcast/sidebar');
  assert.equal(calls[0][2][0].elementId, 'soren-direct-stream-overlay-broadcastSidebar');
  assert.equal(calls[0][2][0].key, 'broadcastSidebar');
  assert.equal(calls[0][2][0].region, 'sidebar');
  assert.equal(calls[0][2][0].pollMs, 60000);
  assert.equal(calls[0][2].find((item) => item.route.endsWith('/av-sync')).pollMs, 250);
  assert.match(String(calls[0][1]), /elementId}-buffer/);
  assert.match(String(calls[0][1]), /incoming[.]addEventListener\('load'/);
  assert.match(String(calls[0][1]), /outgoing[.]style[.]visibility = 'hidden'/);
});


test('TwiCa external overlay uses iframe src and skips polling', () => {
  const config = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_TWICA_OVERLAY_ENABLED: '1',
    SOREN_DIRECT_TWICA_OVERLAY_URL: 'https://twica.bluemoon.works/overlay/demo?pName=true',
  }, 'linux');
  const twica = config.surfaces.find((item) => item.key === 'twica');
  assert.ok(twica);
  assert.equal(twica.srcUrl, 'https://twica.bluemoon.works/overlay/demo?pName=true');
  assert.equal(twica.route, '/__soren_overlay/twica');
  assert.equal(twica.style.zIndex, '2147483645');
  assert.equal(twica.style.inset, '0');
  assert.equal(twica.style.width, '100vw');
  assert.equal(twica.style.height, '100vh');
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
  assert.deepEqual(
    ['broadcastSidebar', 'broadcastTop', 'broadcastBottom'].map((key) => byKey[key].region),
    ['sidebar', 'top', 'bottom'],
  );
  assert.deepEqual(byKey.broadcastSidebar.style, {
    left: '960px', top: '0', width: '320px', height: '720px', zIndex: '2147483630',
  });
  assert.deepEqual(byKey.broadcastTop.style, {
    left: '0', top: '0', width: '960px', height: '90px', zIndex: '2147483630',
  });
  assert.deepEqual(byKey.broadcastBottom.style, {
    left: '0', top: '630px', width: '960px', height: '90px', zIndex: '2147483630',
  });
  for (const key of ['broadcastSidebar', 'broadcastTop', 'broadcastBottom']) {
    assert.match(byKey[key].htmlFile, /overlays\/direct_broadcast_overlay[.]html$/);
  }
  assert.equal(byKey.event, undefined);
  assert.equal(byKey.stats, undefined);
  assert.equal(byKey.ops, undefined);
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
  assert.deepEqual(fullscreen.surfaces.map((item) => item.key), [
    'event', 'stats', 'ops', 'improve', 'wildcard', 'avsync', 'twica',
  ]);
  assert.equal(fullscreen.broadcast, null);
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
  assert.equal(directOverlaySurfaceVisible(byKey.broadcastSidebar), true);
  assert.equal(directOverlaySurfaceVisible(byKey.broadcastTop), true);
  assert.equal(directOverlaySurfaceVisible(byKey.broadcastBottom), true);
  assert.equal(directOverlaySurfaceVisible(byKey.improve), true);
  assert.equal(directOverlaySurfaceVisible(byKey.wildcard), false);

  fs.writeFileSync(wildcardState, JSON.stringify({ phase: 'running' }));
  assert.equal(directOverlaySurfaceVisible(byKey.broadcastSidebar), true);
  assert.equal(directOverlaySurfaceVisible(byKey.improve), false);
  assert.equal(directOverlaySurfaceVisible(byKey.wildcard), true);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('broadcast opt-out falls back to legacy dashboard surfaces without changing their flags', () => {
  const config = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED: '0',
  }, 'linux');
  assert.deepEqual(config.surfaces.map((item) => item.key), [
    'event', 'stats', 'ops', 'improve', 'wildcard', 'avsync', 'twica',
  ]);
  assert.equal(config.broadcast, null);

  const fullscreen = loadDirectOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen',
    SOREN_DIRECT_OPS_OVERLAY_ENABLED: '0',
  }, 'linux');
  assert.deepEqual(fullscreen.surfaces.map((item) => item.key), [
    'event', 'stats', 'improve', 'wildcard', 'avsync', 'twica',
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

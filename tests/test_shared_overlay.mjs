import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  SHARED_OVERLAY_DEFAULT_PORT,
  SHARED_OVERLAY_LAYOUT,
  SHARED_OVERLAY_STAGE_ELEMENT_ID,
  assertSharedOverlayContract,
  blankSharedOverlayHtml,
  buildSharedBroadcastOverlayState,
  closeSharedOverlayServer,
  createSharedOverlayServer,
  installBlankDirectGameStage,
  loadActiveGameContext,
  loadSharedOverlayConfig,
  normalizeActiveGameContext,
  sharedOverlayAllowedRoutes,
  setSharedOverlayBrowserReady,
  setSharedOverlayFramesReady,
  startSharedOverlayServer,
  trackOwnedServerSockets,
  waitForSharedOverlayFrames,
} from '../lib/shared_overlay.mjs';

import { runSharedOverlay } from '../shared_overlay.mjs';


const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');


function request(server, pathname, method = 'GET') {
  const address = server.address();
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1',
      port: address.port,
      path: pathname,
      method,
    }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    req.end();
  });
}


function fixtureConfig(temp, contextFile) {
  const stats = path.join(temp, 'stats.html');
  const ops = path.join(temp, 'ops.html');
  const event = path.join(temp, 'event.html');
  const improve = path.join(temp, 'improve.json');
  const wildcard = path.join(temp, 'wildcard.json');
  const log = path.join(temp, 'improve.log');
  fs.writeFileSync(stats, '<pre>SOREN/OBS\nRecent30: 1\nStrategy: fixture</pre>');
  fs.writeFileSync(ops, '<pre>Backend: fixture\nGame: waiting</pre>');
  fs.writeFileSync(event, 'const EVENTS = [];\nconst WORK = {};\nconst GEN = [];\nconst VISIBLE_SEC = 18;\n');
  fs.writeFileSync(improve, JSON.stringify({ status: 'running', phase: 'test', progress: 10 }));
  fs.writeFileSync(wildcard, JSON.stringify({ phase: 'idle' }));
  fs.writeFileSync(log, '[TEST] should be hidden for a non-Soren game\n');
  return loadSharedOverlayConfig({
    SOREN_STREAM_BACKEND: 'ffmpeg',
    SOREN_DIRECT_OVERLAY_ENABLED: '1',
    SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED: '1',
    SOREN_DIRECT_STATS_OVERLAY_HTML_FILE: stats,
    SOREN_DIRECT_OPS_OVERLAY_HTML_FILE: ops,
    SOREN_DIRECT_OVERLAY_HTML_FILE: event,
    IMPROVE_STATE_FILE: improve,
    IMPROVE_AI_LOG_FILE: log,
    WILDCARD_PARALLEL_STATUS_FILE: wildcard,
    SOREN_ACTIVE_GAME_CONTEXT_FILE: contextFile,
  }, 'linux');
}


test('shared config uses the fixed 1280x720 layout and 8092 default', () => {
  const config = loadSharedOverlayConfig({}, 'linux');
  assert.equal(config.port, SHARED_OVERLAY_DEFAULT_PORT);
  assert.equal(config.host, '127.0.0.1');
  assert.deepEqual(config.layout.game, SHARED_OVERLAY_LAYOUT.game);
  assert.equal(config.direct.stage.outputWidth, 1280);
  assert.equal(config.direct.stage.outputHeight, 720);
  assert.equal(config.direct.stage.gameTop, 90);
  assert.equal(config.direct.stage.sidebarLeft, 960);
  assert.equal(config.direct.stage.sidebarWidth, 320);
  assert.ok(config.direct.broadcast);
});


test('shared config forces the dashboard contract and omits legacy routes', () => {
  const config = loadSharedOverlayConfig({
    // These are common game-process values.  The independent service must not
    // inherit them and accidentally expose fullscreen/legacy Soren surfaces.
    SOREN_STREAM_BACKEND: 'obs',
    SOREN_DIRECT_OVERLAY_ENABLED: '0',
    SOREN_DIRECT_BROADCAST_OVERLAY_ENABLED: '0',
    SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen',
    SOREN_DIRECT_STREAM_SIZE: '1920x1080',
    SOREN_DIRECT_GAME_DISPLAY_SIZE: '1280x720',
    SOREN_DIRECT_STATS_OVERLAY_ENABLED: '1',
    SOREN_DIRECT_OPS_OVERLAY_ENABLED: '1',
    SOREN_DIRECT_IMPROVE_OVERLAY_ENABLED: '1',
  }, 'linux');
  assert.doesNotThrow(() => assertSharedOverlayContract(config.direct));
  assert.equal(config.direct.backend, 'ffmpeg');
  assert.equal(config.direct.enabled, true);
  assert.equal(config.direct.stage.mode, 'dashboard');
  assert.equal(config.direct.stage.outputWidth, 1280);
  assert.equal(config.direct.stage.outputHeight, 720);
  assert.equal(config.direct.stage.gameTop, 90);
  assert.equal(config.direct.stage.gameWidth, 960);
  assert.equal(config.direct.stage.gameHeight, 540);
  assert.equal(config.direct.stage.sidebarLeft, 960);
  assert.equal(config.direct.stage.sidebarWidth, 320);
  assert.ok(config.direct.broadcast);
  const routes = sharedOverlayAllowedRoutes(config);
  for (const route of [
    '/__soren_overlay/event',
    '/__soren_overlay/stats',
    '/__soren_overlay/ops',
    '/__soren_overlay/improve',
  ]) {
    assert.equal(routes.includes(route), false, `legacy route ${route} must be absent`);
  }
  assert.ok(routes.includes('/__soren_overlay/broadcast/state'));
  assert.ok(routes.includes('/__soren_overlay/broadcast/sidebar'));
  assert.ok(routes.includes('/__soren_overlay/broadcast/top'));
  assert.ok(routes.includes('/__soren_overlay/broadcast/bottom'));
});


test('caller-supplied incompatible config fails closed before HTTP startup', () => {
  const valid = loadSharedOverlayConfig({}, 'linux');
  const incompatible = {
    ...valid,
    direct: {
      ...valid.direct,
      backend: 'obs',
      stage: { ...valid.direct.stage, mode: 'fullscreen', enabled: false },
      broadcast: null,
    },
  };
  assert.throws(
    () => createSharedOverlayServer(incompatible, { port: 0 }),
    /shared overlay direct contract mismatch:.*backend.*stage.*broadcast/,
  );
});


test('shared config rejects canonical stage and required-frame metadata drift', () => {
  const valid = loadSharedOverlayConfig({}, 'linux');
  const alter = (change) => ({
    ...valid.direct,
    ...change,
  });
  assert.throws(
    () => assertSharedOverlayContract(alter({
      stage: { ...valid.direct.stage, elementId: 'other-stage' },
    })),
    /stage\.elementId/,
  );
  assert.throws(
    () => assertSharedOverlayContract(alter({
      surfaces: valid.direct.surfaces.map((item) => item.key === 'broadcastTop'
        ? { ...item, elementId: 'other-frame' }
        : item),
    })),
    /surface\.broadcastTop/,
  );
  assert.throws(
    () => assertSharedOverlayContract(alter({
      surfaces: valid.direct.surfaces.map((item) => item.key === 'broadcastSidebar'
        ? { ...item, style: { ...item.style, inset: '0' } }
        : item),
    })),
    /surface\.broadcastSidebar\.style/,
  );
  assert.throws(
    () => assertSharedOverlayContract(alter({
      surfaces: [...valid.direct.surfaces, { ...valid.direct.surfaces[0] }],
    })),
    /surface-keys|surface-routes-duplicate/,
  );
});


test('context normalization treats absent active game as waiting and only exposes game/phase', () => {
  assert.deepEqual(normalizeActiveGameContext({ phase: 'draining', secret: 'do-not-return' }, 11), {
    active: false,
    game: '',
    phase: 'waiting',
    updatedAt: 11,
  });
  assert.equal(normalizeActiveGameContext({ game: 'sorengame', phase: 'running' }).active, false);
  assert.deepEqual(normalizeActiveGameContext({ active: { game: 'robots', phase: 'running', token: 'hidden' } }, 12), {
    active: true,
    game: 'robots',
    phase: 'running',
    updatedAt: 12,
  });
});


test('non-Soren context masks Soren stats and improve while retaining OPS and notifications', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-state-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  const state = buildSharedBroadcastOverlayState(config, 1780000020000);
  assert.deepEqual(state.gameContext, {
    active: true,
    game: 'robots',
    phase: 'running',
    updatedAt: Math.floor(fs.statSync(context).mtimeMs / 1000),
  });
  assert.equal(state.feeds.showStatusG.generic, true);
  assert.match(state.feeds.showStatusG.text, /GAME: robots/);
  assert.match(state.feeds.showStatusG.text, /INACTIVE/);
  assert.equal(state.feeds.improve.active, false);
  assert.equal(state.feeds.improve.status, 'inactive');
  assert.match(state.feeds.improve.detail, /robots/);
  assert.match(state.feeds.showStatus.text, /Backend: fixture/);
  assert.deepEqual(state.notifications.events, []);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('Soren context keeps existing broadcast stats and improve feeds', () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-soren-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'sorengame', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  const state = buildSharedBroadcastOverlayState(config, 1780000020000);
  assert.equal(state.gameContext.game, 'sorengame');
  assert.equal(state.feeds.showStatusG.generic, undefined);
  assert.equal(state.feeds.showStatusG.text, 'SOREN/OBS\nRecent30: 1\nStrategy: fixture');
  assert.equal(state.feeds.improve.active, true);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('HTTP service serves only health, blank root, and configured overlay routes', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-http-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  const server = await startSharedOverlayServer(config, { port: 0 });
  try {
    const health = await request(server, '/healthz');
    assert.equal(health.status, 200);
    assert.equal(health.headers['content-type'], 'application/json; charset=utf-8');
    const initialHealth = JSON.parse(health.body);
    assert.deepEqual(initialHealth.layout.game, [0, 90, 960, 540]);
    assert.equal(initialHealth.ok, true);
    assert.equal(initialHealth.ready, false);
    assert.equal(initialHealth.browserReady, false);
    assert.equal(initialHealth.layoutReady, false);
    assert.equal(initialHealth.overlayReady, false);

    setSharedOverlayBrowserReady(server, true, { innerWidth: 1920, innerHeight: 1080 });
    const wrongViewportHealth = JSON.parse((await request(server, '/healthz')).body);
    assert.equal(wrongViewportHealth.browserReady, true);
    assert.equal(wrongViewportHealth.layoutReady, false);
    assert.equal(wrongViewportHealth.ready, false);
    setSharedOverlayBrowserReady(server, true, { innerWidth: 1280, innerHeight: 720 });
    const framePendingHealth = JSON.parse((await request(server, '/healthz')).body);
    assert.equal(framePendingHealth.browserReady, true);
    assert.equal(framePendingHealth.layoutReady, true);
    assert.equal(framePendingHealth.overlayReady, false);
    assert.equal(framePendingHealth.ready, false);
    setSharedOverlayFramesReady(server, true);
    const readyHealth = JSON.parse((await request(server, '/healthz')).body);
    assert.equal(readyHealth.browserReady, true);
    assert.equal(readyHealth.layoutReady, true);
    assert.equal(readyHealth.overlayReady, true);
    assert.equal(readyHealth.ready, true);

    const root = await request(server, '/');
    assert.equal(root.status, 200);
    assert.match(root.body, /width=1280,height=720/);
    assert.doesNotMatch(root.body, /sorengame|unity|canvas/i);

    const state = await request(server, '/__soren_overlay/broadcast/state');
    assert.equal(state.status, 200);
    assert.match(state.headers['content-type'], /^application\/json/);
    assert.equal(JSON.parse(state.body).gameContext.game, 'robots');

    const rail = await request(server, '/__soren_overlay/broadcast/sidebar');
    assert.equal(rail.status, 200);
    assert.match(rail.body, /broadcast-sidebar/);
    for (const legacyRoute of [
      '/__soren_overlay/event',
      '/__soren_overlay/stats',
      '/__soren_overlay/ops',
      '/__soren_overlay/improve',
    ]) {
      const legacy = await request(server, legacyRoute);
      assert.equal(legacy.status, 404, `legacy route ${legacyRoute} must stay absent`);
    }
    const unknown = await request(server, '/tmp/state/status_overlay.html');
    assert.equal(unknown.status, 404);
    assert.doesNotMatch(unknown.body, /SOREN|fixture|secret/i);
    const method = await request(server, '/healthz', 'POST');
    assert.equal(method.status, 405);
    assert.deepEqual(sharedOverlayAllowedRoutes(config), [
      '/healthz', '/', '/__soren_overlay/broadcast/state',
      '/__soren_overlay/broadcast/sidebar',
      '/__soren_overlay/broadcast/top',
      '/__soren_overlay/broadcast/bottom',
      '/__soren_overlay/twica',
      '/__soren_overlay/wildcard',
      '/__soren_overlay/av-sync',
    ]);
  } finally {
    await closeSharedOverlayServer(server);
    fs.rmSync(temp, { recursive: true, force: true });
  }
});


test('blank stage installer never requires a game canvas', async () => {
  let callback;
  let payload;
  const page = {
    evaluate(fn, value) {
      callback = fn;
      payload = value;
      return Promise.resolve({ stageMode: 'blank', gameTop: 90, gameWidth: 960 });
    },
  };
  const result = await installBlankDirectGameStage(page, loadSharedOverlayConfig({}, 'linux'));
  assert.deepEqual(result, { stageMode: 'blank', gameTop: 90, gameWidth: 960 });
  assert.equal(typeof callback, 'function');
  assert.equal(payload.stage.elementId, SHARED_OVERLAY_STAGE_ELEMENT_ID);
  const source = callback.toString();
  assert.doesNotMatch(source, /querySelector\([^)]*canvas/i);
  assert.doesNotMatch(source, /installDirectGameStage/);
});


test('required frame readiness rejects missing HTML and failed route loads', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-frame-failure-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const baseConfig = fixtureConfig(temp, context);
  const missingHtmlConfig = {
    ...baseConfig,
    direct: {
      ...baseConfig.direct,
      surfaces: baseConfig.direct.surfaces.map((item) => item.key === 'broadcastSidebar'
        ? { ...item, htmlFile: path.join(temp, 'missing-broadcast.html') }
        : item),
    },
  };
  const server = await startSharedOverlayServer(missingHtmlConfig, { port: 0 });
  try {
    const missing = await request(server, '/__soren_overlay/broadcast/sidebar');
    assert.equal(missing.status, 200);
    assert.doesNotMatch(missing.body, /id=["']broadcast-overlay["']/);
  } finally {
    await closeSharedOverlayServer(server);
  }
  const routeFailurePage = {
    async evaluate() { return false; },
  };
  await assert.rejects(
    waitForSharedOverlayFrames(routeFailurePage, baseConfig, { timeoutMs: 25 }),
    /shared overlay required frames not ready/,
  );
  fs.rmSync(temp, { recursive: true, force: true });
});


test('owned proxy socket tracker destroys upgraded connections', () => {
  const proxy = new EventEmitter();
  const tracker = trackOwnedServerSockets(proxy);
  let destroyed = false;
  const socket = new EventEmitter();
  socket.destroy = () => {
    destroyed = true;
    socket.emit('close');
  };
  proxy.emit('connection', socket);
  assert.equal(tracker.sockets.size, 1);
  tracker.close();
  assert.equal(destroyed, true);
  assert.equal(tracker.sockets.size, 0);
  tracker.dispose();
  proxy.emit('connection', new EventEmitter());
  assert.equal(tracker.sockets.size, 0, 'dispose stops tracking new sockets');
});


class FakeSharedPage extends EventEmitter {
  constructor() {
    super();
    this.closed = false;
  }

  async goto() {}

  async addInitScript() {}

  async waitForFunction() {}

  async evaluate(fn) {
    if (fn.toString().includes('getBoundingClientRect')) {
      return {
        innerWidth: 1280,
        innerHeight: 720,
        screenX: 0,
        screenY: 0,
        devicePixelRatio: 1,
        stage: { left: 0, top: 0, width: 1280, height: 720 },
      };
    }
    return { stageMode: 'blank', gameTop: 90, gameWidth: 960 };
  }

  async close() {
    this.closed = true;
  }
}


class FakeSharedBrowser extends EventEmitter {
  constructor() {
    super();
    this.page = new FakeSharedPage();
    this.closed = false;
  }

  async newPage() { return this.page; }

  async close() {
    this.closed = true;
  }
}


class FakeOwnedProxy extends EventEmitter {
  constructor() {
    super();
    this.listening = true;
    this.closed = false;
  }

  closeAllConnections() {}

  close(callback) {
    this.closed = true;
    callback?.();
  }
}


test('SIGTERM during deferred proxy startup closes the late proxy assignment', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-proxy-race-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  config.direct.surfaces.find((item) => item.key === 'twica').upstreamUrl = 'http://127.0.0.1:1';
  let proxyStart;
  let releaseProxy;
  const proxyStarted = new Promise((resolve) => { proxyStart = resolve; });
  const proxyResult = new Promise((resolve) => { releaseProxy = resolve; });
  let proxy;
  let browserLaunchCalled = false;
  const fakeChromium = {
    async launch() {
      browserLaunchCalled = true;
      return new FakeSharedBrowser();
    },
  };
  const run = runSharedOverlay({
    config,
    chromium: fakeChromium,
    startTwicaOverlayProxy: async () => {
      proxyStart();
      return proxyResult;
    },
    headless: true,
    kiosk: false,
    log: false,
    port: 0,
  });
  await proxyStarted;
  process.emit('SIGTERM');
  proxy = new FakeOwnedProxy();
  releaseProxy(proxy);
  const result = await run;
  assert.equal(browserLaunchCalled, false, 'SIGTERM before launch must skip Chromium');
  assert.equal(proxy.closed, true, 'proxy assigned after the first cleanup must be closed');
  assert.equal(result.server.listening, false);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('SIGTERM during deferred browser launch closes the late browser assignment', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-browser-race-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  let launchStarted;
  let releaseLaunch;
  const launchCalled = new Promise((resolve) => { launchStarted = resolve; });
  const launchResult = new Promise((resolve) => { releaseLaunch = resolve; });
  const fakeChromium = {
    async launch() {
      launchStarted();
      return launchResult;
    },
  };
  const run = runSharedOverlay({
    config,
    chromium: fakeChromium,
    headless: true,
    kiosk: false,
    log: false,
    port: 0,
  });
  await launchCalled;
  process.emit('SIGTERM');
  const browser = new FakeSharedBrowser();
  releaseLaunch(browser);
  const result = await run;
  assert.equal(browser.closed, true, 'browser assigned after the first cleanup must be closed');
  assert.equal(browser.page.closed, false, 'no page was assigned before SIGTERM');
  assert.equal(result.server.listening, false);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('browser disconnect fails the service and closes only owned resources', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-browser-loss-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  let browser;
  const fakeChromium = {
    async launch() {
      browser = new FakeSharedBrowser();
      return browser;
    },
  };
  const run = runSharedOverlay({
    config,
    chromium: fakeChromium,
    headless: true,
    kiosk: false,
    log: false,
    port: 0,
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(browser, 'fake browser launched');
  browser.emit('disconnected');
  await assert.rejects(run, /shared overlay browser disconnected/);
  assert.equal(browser.closed, true);
  assert.equal(browser.page.closed, true);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('page crash fails the service and clears the ready state path', async () => {
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'shared-overlay-page-loss-'));
  const context = path.join(temp, 'game_switch.json');
  fs.writeFileSync(context, JSON.stringify({ active: { game: 'robots', phase: 'running' } }));
  const config = fixtureConfig(temp, context);
  let browser;
  const fakeChromium = {
    async launch() {
      browser = new FakeSharedBrowser();
      return browser;
    },
  };
  const run = runSharedOverlay({
    config,
    chromium: fakeChromium,
    headless: true,
    kiosk: false,
    log: false,
    port: 0,
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(browser?.page, 'fake page created');
  browser.page.emit('crash');
  await assert.rejects(run, /shared overlay browser page crashed/);
  assert.equal(browser.closed, true);
  assert.equal(browser.page.closed, true);
  fs.rmSync(temp, { recursive: true, force: true });
});


test('importing the CLI has no server or browser side effect', async () => {
  const before = process._getActiveHandles().length;
  await import('../shared_overlay.mjs');
  const after = process._getActiveHandles().length;
  assert.ok(after <= before + 1);
  assert.match(blankSharedOverlayHtml(), /Shared overlay/);
});

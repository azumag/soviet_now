import assert from 'node:assert/strict';
import test from 'node:test';


const MODULE_URL = new URL('../obs_capture_watchdog_check.mjs', import.meta.url);
let importSequence = 0;

async function importWithEnv(overrides = {}) {
  const keys = [
    'SOREN_OBS_PLATFORM',
    'SOREN_OBS_GAME_SOURCE_NAME',
    'OBS_WINDOW_CAPTURE_INPUT_KIND',
    'OBS_WINDOW_CAPTURE_FAMILY',
    'OBS_WINDOW_CAPTURE_WINDOW_PROPERTY',
    'OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED',
  ];
  const saved = new Map(keys.map((key) => [key, process.env[key]]));
  for (const key of keys) delete process.env[key];
  Object.assign(process.env, overrides);
  try {
    importSequence += 1;
    return await import(`${MODULE_URL.href}?test=${importSequence}`);
  } finally {
    for (const [key, value] of saved) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

function fakeObsClient() {
  const calls = [];
  return {
    calls,
    async req(requestType, requestData) {
      calls.push({ requestType, requestData });
      return {};
    },
  };
}

function fakeScreenshotClient(images) {
  const calls = [];
  let imageIndex = 0;
  return {
    calls,
    async req(requestType, requestData) {
      calls.push({ requestType, requestData });
      if (requestType !== 'GetSourceScreenshot') {
        throw new Error(`unexpected request: ${requestType}`);
      }
      const imageData = images[Math.min(imageIndex, images.length - 1)];
      imageIndex += 1;
      return { imageData };
    },
  };
}

function fakeLogger() {
  const logs = [];
  const warnings = [];
  return {
    logs,
    warnings,
    log(message) { logs.push(String(message)); },
    warn(message) { warnings.push(String(message)); },
  };
}

class FakeWebSocket {
  static instances = [];
  static handshakeBehavior = 'normal';

  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.sent = [];
    this.closed = false;
    FakeWebSocket.instances.push(this);
    queueMicrotask(() => {
      if (FakeWebSocket.handshakeBehavior === 'no-open') return;
      this.emit('open', {});
      if (FakeWebSocket.handshakeBehavior === 'no-hello') return;
      this.emit('message', { data: JSON.stringify({ op: 0, d: {} }) });
    });
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type, event) {
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  send(raw) {
    const payload = JSON.parse(String(raw));
    this.sent.push(payload);
    if (payload.op === 1 && FakeWebSocket.handshakeBehavior !== 'no-identify') {
      queueMicrotask(() => this.emit('message', { data: JSON.stringify({ op: 2, d: {} }) }));
    }
  }

  close() {
    this.closed = true;
    this.emit('close', {});
  }
}

test('capture recovery behavior uses platform-safe OBS requests', async (t) => {
  await t.test('Linux stale rebind preserves encoded itemValue and sends one overlay update', async () => {
    const watchdog = await importWithEnv({ SOREN_OBS_PLATFORM: 'linux' });
    const obs = fakeObsClient();
    const encoded = '41943043\r\nUnity WebGL Player | soren-game - Chromium\r\nchromium';

    const result = await watchdog.recoverCapture(obs, encoded, 'stale');

    assert.deepEqual(result, { action: 'rebound', requestCount: 1 });
    assert.deepEqual(obs.calls, [{
      requestType: 'SetInputSettings',
      requestData: {
        inputName: 'sorengame',
        inputSettings: { capture_window: encoded },
        overlay: true,
      },
    }]);
    assert.equal(obs.calls[0].requestData.inputSettings.capture_window, encoded);
  });

  await t.test('Linux frozen same-value recovery is disabled by default', async () => {
    const watchdog = await importWithEnv({ SOREN_OBS_PLATFORM: 'linux' });
    const obs = fakeObsClient();

    const result = await watchdog.recoverCapture(obs, 'encoded-window', 'frozen');

    assert.deepEqual(result, {
      action: 'no_action',
      reason: 'xcomposite_same_value_bounce_disabled',
      requestCount: 0,
    });
    assert.equal(obs.calls.length, 0);
  });

  await t.test('Linux frozen same-value recovery sends one request only after explicit opt-in', async () => {
    const watchdog = await importWithEnv({
      SOREN_OBS_PLATFORM: 'linux',
      OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED: '1',
    });
    const obs = fakeObsClient();

    const result = await watchdog.recoverCapture(obs, 'encoded-window', 'frozen');

    assert.deepEqual(result, { action: 'bounced', requestCount: 1 });
    assert.equal(obs.calls.length, 1);
    assert.equal(obs.calls[0].requestData.overlay, true);
  });

  await t.test('Darwin recovery keeps two overlay-false requests and 2500ms wait', async () => {
    const watchdog = await importWithEnv({ SOREN_OBS_PLATFORM: 'darwin' });
    const obs = fakeObsClient();
    const lockEvents = [];
    const sleeps = [];
    const lock = {
      async acquireObsSourceLock() { lockEvents.push('acquire'); return 'held'; },
      async releaseObsSourceLock(held) { lockEvents.push(`release:${held}`); },
    };

    const result = await watchdog.recoverCapture(obs, 42, 'stale', {
      lockOverride: lock,
      sleepFn: async (ms) => sleeps.push(ms),
    });

    assert.deepEqual(result, { action: 'rebound', requestCount: 2 });
    assert.equal(obs.calls.length, 2);
    assert.deepEqual(obs.calls.map((call) => call.requestData.overlay), [false, false]);
    assert.equal(obs.calls[0].requestData.inputSettings.type, 2);
    assert.equal(obs.calls[1].requestData.inputSettings.type, 1);
    assert.equal(obs.calls[1].requestData.inputSettings.window, 42);
    assert.deepEqual(sleeps, [2500]);
    assert.deepEqual(lockEvents, ['acquire', 'release:held']);
  });

  await t.test('computed property survives property-only, versioned, and custom kinds', async () => {
    const propertyOnly = await importWithEnv({
      SOREN_OBS_PLATFORM: 'darwin',
      OBS_WINDOW_CAPTURE_WINDOW_PROPERTY: 'vm_window',
    });
    assert.equal(
      propertyOnly.captureRecoveryPlan('value', 'stale')[1].requestData.inputSettings.vm_window,
      'value',
    );

    const versioned = await importWithEnv({
      SOREN_OBS_PLATFORM: 'darwin',
      OBS_WINDOW_CAPTURE_INPUT_KIND: 'xcomposite_input_v2',
      OBS_WINDOW_CAPTURE_WINDOW_PROPERTY: 'capture_window_v2',
    });
    assert.deepEqual(
      versioned.captureRecoveryPlan('value', 'stale')[0].requestData.inputSettings,
      { capture_window_v2: 'value' },
    );

    const custom = await importWithEnv({
      SOREN_OBS_PLATFORM: 'linux',
      OBS_WINDOW_CAPTURE_INPUT_KIND: 'vendor_capture',
      OBS_WINDOW_CAPTURE_WINDOW_PROPERTY: 'vendor_window',
    });
    assert.deepEqual(
      custom.captureRecoveryPlan('value', 'stale')[0].requestData.inputSettings,
      { vendor_window: 'value' },
    );
  });

  await t.test('XSHM recovery plans never send source setting mutations', async () => {
    const watchdog = await importWithEnv({
      SOREN_OBS_PLATFORM: 'linux',
      OBS_WINDOW_CAPTURE_INPUT_KIND: 'xshm_input_v2',
    });
    const obs = fakeObsClient();

    assert.deepEqual(watchdog.captureRecoveryPlan('ignored', 'stale'), []);
    assert.deepEqual(watchdog.captureRecoveryPlan('ignored', 'frozen'), []);
    assert.deepEqual(await watchdog.recoverCapture(obs, 'ignored', 'frozen'), {
      action: 'no_action',
      reason: 'xshm_screen_capture_never_rebound',
      requestCount: 0,
    });
    assert.equal(obs.calls.length, 0);
  });
});

test('XSHM watchdog cycle is screenshot-only and never mutates the source', async (t) => {
  await t.test('different frames are healthy with zero property or settings requests', async () => {
    const watchdog = await importWithEnv({
      SOREN_OBS_PLATFORM: 'linux',
      OBS_WINDOW_CAPTURE_INPUT_KIND: 'xshm_input',
      OBS_WINDOW_CAPTURE_FAMILY: 'xshm',
    });
    const obs = fakeScreenshotClient(['frame-a', 'frame-b', 'frame-c']);
    const logger = fakeLogger();
    const signatures = ['state-before', 'state-after'];

    const result = await watchdog.checkCapture(obs, {
      sleepFn: async () => {},
      gameStateSigFn: () => signatures.shift() || 'state-after',
      modeFn: () => 'china',
      logger,
    });

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'ok');
    assert.deepEqual(obs.calls.map((call) => call.requestType), [
      'GetSourceScreenshot',
      'GetSourceScreenshot',
      'GetSourceScreenshot',
    ]);
    assert.equal(obs.calls.filter((call) => call.requestType === 'SetInputSettings').length, 0);
    assert.equal(
      obs.calls.filter((call) => call.requestType === 'GetInputPropertiesListPropertyItems').length,
      0,
    );
    assert.match(logger.logs.at(-1), /XSHM screen capture live/);
  });

  await t.test('identical advancing frames warn and still send zero mutations', async () => {
    const watchdog = await importWithEnv({
      SOREN_OBS_PLATFORM: 'linux',
      OBS_WINDOW_CAPTURE_INPUT_KIND: 'xshm_input',
      OBS_WINDOW_CAPTURE_FAMILY: 'xshm',
      // Even the XComposite opt-in must not enable XSHM mutation.
      OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED: '1',
    });
    const obs = fakeScreenshotClient(['same-frame', 'same-frame', 'same-frame']);
    const logger = fakeLogger();
    const signatures = ['state-before', 'state-after'];

    const result = await watchdog.checkCapture(obs, {
      sleepFn: async () => {},
      gameStateSigFn: () => signatures.shift() || 'state-after',
      modeFn: () => 'china',
      logger,
    });

    assert.equal(result.exitCode, 0);
    assert.equal(result.action, 'warned_no_mutation');
    assert.equal(obs.calls.length, 3);
    assert.ok(obs.calls.every((call) => call.requestType === 'GetSourceScreenshot'));
    assert.equal(logger.warnings.length, 1);
    assert.match(logger.warnings[0], /never rebound or bounced/);
  });
});

test('OBS websocket handshake timeouts close the socket at every stage', async () => {
  const watchdog = await importWithEnv({
    SOREN_OBS_PLATFORM: 'linux',
    OBS_WINDOW_CAPTURE_INPUT_KIND: 'xshm_input',
    OBS_WINDOW_CAPTURE_FAMILY: 'xshm',
  });
  const savedPort = process.env.OBS_WEBSOCKET_PORT;
  const savedPassword = process.env.OBS_WEBSOCKET_PASSWORD;
  process.env.OBS_WEBSOCKET_PORT = '4455';
  process.env.OBS_WEBSOCKET_PASSWORD = 'test-only';
  try {
    for (const [behavior, expectedError] of [
      ['no-open', /connect timeout/],
      ['no-hello', /hello timeout/],
      ['no-identify', /identify timeout/],
    ]) {
      FakeWebSocket.handshakeBehavior = behavior;
      const startedAt = Date.now();
      await assert.rejects(
        watchdog.connectObs({ WebSocketCtor: FakeWebSocket, requestTimeoutMs: 20 }),
        expectedError,
      );
      assert.equal(FakeWebSocket.instances.at(-1).closed, true, `${behavior} socket was not closed`);
      assert.ok(Date.now() - startedAt < 500, `${behavior} timeout was not bounded`);
    }
  } finally {
    FakeWebSocket.handshakeBehavior = 'normal';
    if (savedPort === undefined) delete process.env.OBS_WEBSOCKET_PORT;
    else process.env.OBS_WEBSOCKET_PORT = savedPort;
    if (savedPassword === undefined) delete process.env.OBS_WEBSOCKET_PASSWORD;
    else process.env.OBS_WEBSOCKET_PASSWORD = savedPassword;
  }
});

test('OBS websocket client bounds requests and rejects all pending work on close/error', async () => {
  const watchdog = await importWithEnv({
    SOREN_OBS_PLATFORM: 'linux',
    OBS_WINDOW_CAPTURE_INPUT_KIND: 'xshm_input',
    OBS_WINDOW_CAPTURE_FAMILY: 'xshm',
  });
  const savedPort = process.env.OBS_WEBSOCKET_PORT;
  const savedPassword = process.env.OBS_WEBSOCKET_PASSWORD;
  process.env.OBS_WEBSOCKET_PORT = '4455';
  process.env.OBS_WEBSOCKET_PASSWORD = 'test-only';
  try {
    const timeoutClient = await watchdog.connectObs({
      WebSocketCtor: FakeWebSocket,
      requestTimeoutMs: 20,
    });
    await assert.rejects(timeoutClient.req('NeverReplies'), /timed out after 20ms/);
    timeoutClient.close();

    const closeClient = await watchdog.connectObs({
      WebSocketCtor: FakeWebSocket,
      requestTimeoutMs: 1000,
    });
    const closePending = closeClient.req('PendingAtClose');
    FakeWebSocket.instances.at(-1).emit('close', {});
    await assert.rejects(closePending, /websocket closed/);

    const errorClient = await watchdog.connectObs({
      WebSocketCtor: FakeWebSocket,
      requestTimeoutMs: 1000,
    });
    const pendingA = errorClient.req('PendingA');
    const pendingB = errorClient.req('PendingB');
    FakeWebSocket.instances.at(-1).emit('error', {});
    const results = await Promise.allSettled([pendingA, pendingB]);
    assert.deepEqual(results.map((result) => result.status), ['rejected', 'rejected']);
    assert.match(results[0].reason.message, /websocket error/);
    assert.match(results[1].reason.message, /websocket error/);
  } finally {
    if (savedPort === undefined) delete process.env.OBS_WEBSOCKET_PORT;
    else process.env.OBS_WEBSOCKET_PORT = savedPort;
    if (savedPassword === undefined) delete process.env.OBS_WEBSOCKET_PASSWORD;
    else process.env.OBS_WEBSOCKET_PASSWORD = savedPassword;
  }
});

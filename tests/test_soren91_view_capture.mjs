import test from 'node:test';
import assert from 'node:assert/strict';
import { createCanvasIO, viewCaptureEnabled, viewCaptureEligible } from '../soren91/realtime_io.mjs';

const full = (extra = {}) => ({
  canvasId: 1, documentId: 100, x: 0, y: 0, width: 1280, height: 720,
  scrollX: 0, scrollY: 0, dpr: 1, viewportWidth: 1280, viewportHeight: 720,
  viewportScale: 1, visible: true, ...extra,
});
function png(width = 1280, height = 720) {
  const b = Buffer.alloc(24);
  Buffer.from('89504e470d0a1a0a', 'hex').copy(b);
  b.write('IHDR', 12); b.writeUInt32BE(width, 16); b.writeUInt32BE(height, 20);
  return b; // Header fixture only; real decoding is covered by the browser test.
}
function jpeg(width = 1280, height = 720) {
  const b = Buffer.from([0xff, 0xd8, 0xff, 0xc0, 0, 17, 8, 0, 0, 0, 0, 3]);
  b.writeUInt16BE(height, 7); b.writeUInt16BE(width, 9); return b;
}
function fixture({ geometry = full(), native, legacy, rtt = 0, legacyCost = 0 } = {}) {
  let clock = 0, attachments = 0, detaches = 0, nativeCalls = 0, legacyCalls = 0;
  const messages = [];
  const state = { geometry, native, onDetach: null };
  const session = {
    async send(method, params) {
      messages.push({ method, params }); clock += rtt;
      if (method === 'Runtime.evaluate') return { result: { value: { ...state.geometry } } };
      assert.equal(method, 'Page.captureScreenshot'); nativeCalls++;
      return state.native ? state.native(params) : { data: png().toString('base64') };
    },
    async detach() { detaches++; state.onDetach?.(); },
  };
  const page = {
    context: () => ({ newCDPSession: async () => { attachments++; return session; } }),
    async screenshot(options) { legacyCalls++; clock += legacyCost; return legacy ? legacy(options) : png(); },
  };
  const io = createCanvasIO({ now: () => clock });
  return { io, page, state, messages, advance: n => { clock += n; },
    counts: () => ({ attachments, detaches, nativeCalls, legacyCalls, clock }) };
}
const calibration = { screen: { width: 1280, height: 720 } };

test('view capture defaults to remote only; explicit rollback and invalid values disable it', () => {
  assert.equal(viewCaptureEnabled({}), false);
  assert.equal(viewCaptureEnabled({ SOREN91_REMOTE_CDP_URL: ' ws://fixture ' }), true);
  assert.equal(viewCaptureEnabled({ SOREN91_REMOTE_CDP_URL: ' ' }), false);
  assert.equal(viewCaptureEnabled({ SOREN91_VIEW_CAPTURE: '1' }), true);
  for (const value of ['0', 'false', 'true', '2']) {
    assert.equal(viewCaptureEnabled({ SOREN91_REMOTE_CDP_URL: 'fixture', SOREN91_VIEW_CAPTURE: value }), false);
  }
});

test('view capture requires a visible canvas exactly filling the unscrolled viewport', () => {
  assert.equal(viewCaptureEligible(full()), true);
  for (const change of [
    { visible: false }, { visible: undefined }, { x: 1, width: 1279 },
    { y: 1, height: 719 }, { width: 800 }, { height: 450 }, { scrollX: 1 },
    { scrollY: 1 }, { width: 1279.5, viewportWidth: 1279.5 },
    { dpr: 0 }, { viewportScale: 2 }, { canvasId: 0 },
  ]) assert.equal(viewCaptureEligible(full(change)), false, JSON.stringify(change));
  assert.equal(viewCaptureEligible(null), false);
});

test('fast path uses exactly three commands on one reused session, with no surface/clip/emulation', async () => {
  const f = fixture();
  const frame = await f.io.capture(f.page, { view: true });
  assert.equal(frame.captureBackend, 'view');
  assert.deepEqual(f.messages.map(x => x.method), ['Runtime.evaluate', 'Page.captureScreenshot', 'Runtime.evaluate']);
  assert.deepEqual(f.messages[1].params, { format: 'png', fromSurface: false, captureBeyondViewport: false });
  assert.deepEqual(frame.buffer, png());
  await f.io.capture(f.page, { view: true });
  assert.equal(f.counts().attachments, 1);
  assert.equal(f.counts().legacyCalls, 0);
});

test('explicit rollback, clipped canvas and hidden page preserve Playwright capture', async () => {
  for (const [geometry, view] of [[full(), false], [full({ width: 800 }), true], [full({ visible: false }), true]]) {
    let options;
    const f = fixture({ geometry, legacy: opts => { options = opts; return png(geometry.width, geometry.height); } });
    const frame = await f.io.capture(f.page, { view });
    assert.equal(frame.captureBackend, 'playwright');
    assert.equal(f.counts().nativeCalls, 0);
    assert.deepEqual(options.clip, { x: geometry.x, y: geometry.y, width: geometry.width, height: geometry.height });
    assert.equal(options.scale, 'css');
  }
});

for (const dpr of [1, 2]) for (const scale of new Set([1, dpr])) {
  test(`native PNG accepts existing CSS/native scale contract dpr=${dpr} scale=${scale}`, async () => {
    const f = fixture({ geometry: full({ dpr }), native: () => ({ data: png(1280 * scale, 720 * scale).toString('base64') }) });
    const frame = await f.io.capture(f.page, { view: true });
    assert.equal(frame.width, 1280 * scale);
    assert.equal(frame.geometry.dpr, dpr);
  });
}

test('native JPEG keeps format/quality and validates actual dimensions', async () => {
  const f = fixture({ native: opts => {
    assert.deepEqual(opts, { format: 'jpeg', fromSurface: false, captureBeyondViewport: false, quality: 95 });
    return { data: 'unused' };
  } });
  f.state.native = opts => {
    assert.deepEqual(opts, { format: 'jpeg', fromSurface: false, captureBeyondViewport: false, quality: 95 });
    return { data: jpeg().toString('base64') };
  };
  const frame = await f.io.capture(f.page, { view: true, type: 'jpeg', quality: 100 });
  assert.equal(frame.format, 'jpeg'); assert.equal(frame.width, 1280);
});

for (const [name, native, reason] of [
  ['protocol rejection', () => { throw new Error('fixture internal error'); }, /capture-invalid-image/],
  ['missing data', () => ({}), /capture-invalid-image/],
  ['non-string data', () => ({ data: 42 }), /capture-invalid-image/],
  ['oversized data', () => ({ data: 'a'.repeat(32 * 1024 * 1024 + 1) }), /capture-invalid-image/],
  ['empty data', () => ({ data: '' }), /capture-invalid-png/],
  ['wrong dimensions', () => ({ data: png(640, 360).toString('base64') }), /capture-pixel-scale-mismatch/],
  ['invalid PNG', () => ({ data: Buffer.from('not an image').toString('base64') }), /capture-invalid-png/],
]) {
  test(`${name}: reject this observation and use sticky legacy fallback on the next iteration`, async () => {
    const f = fixture({ native });
    await assert.rejects(f.io.capture(f.page, { view: true }), reason);
    assert.equal(f.counts().legacyCalls, 0, 'no second screenshot inside a failed iteration');
    const next = await f.io.capture(f.page, { view: true });
    assert.equal(next.captureBackend, 'playwright');
    assert.equal(f.counts().nativeCalls, 1);
  });
}

for (const change of [{ canvasId: 2 }, { documentId: 101 }, { dpr: 2 }, { visible: false }]) {
  test(`capture rejects a changed/hidden target: ${JSON.stringify(change)}`, async () => {
    const f = fixture();
    f.state.native = () => { f.state.geometry = full(change); return { data: png().toString('base64') }; };
    await assert.rejects(f.io.capture(f.page, { view: true }), /capture-geometry-changed/);
    f.state.geometry = full();
    assert.equal((await f.io.capture(f.page, { view: true })).captureBackend, 'playwright');
  });
}

test('input keeps the fresh/calibration/geometry checks and rejects a now-hidden view', async () => {
  const f = fixture(); const frame = await f.io.capture(f.page, { view: true });
  assert.deepEqual(await f.io.validateInput(f.page, frame, calibration), full());
  await assert.rejects(f.io.validateInput(f.page, frame, { screen: { width: 1, height: 1 } }), /input-calibration-mismatch/);
  f.state.geometry = full({ visible: false });
  await assert.rejects(f.io.validateInput(f.page, frame, calibration), /input-geometry-changed/);
  f.state.geometry = full({ canvasId: 2 });
  await assert.rejects(f.io.validateInput(f.page, frame, calibration), /input-geometry-changed/);
  f.state.geometry = full(); f.advance(5000);
  await assert.rejects(f.io.validateInput(f.page, frame, calibration), /input-stale-observation/);
});

test('timeout retires the session, never overlaps capture, and disables native retry', async () => {
  const f = fixture(); let rejectNative, markStarted;
  const started = new Promise(resolve => { markStarted = resolve; });
  f.state.native = () => new Promise((_, reject) => {
    rejectNative = reject;
    markStarted();
  });
  const first = f.io.capture(f.page, { view: true, timeoutMs: 20 });
  // Register the expected rejection before yielding: a busy CI event loop may
  // expire the timer before setImmediate runs. Synchronize on capture startup.
  const timedOut = assert.rejects(first, /capture-timeout/);
  await started;
  await assert.rejects(f.io.capture(f.page, { view: true }), /capture-session-busy/);
  await timedOut;
  await assert.rejects(f.io.capture(f.page, { view: true }), /capture-session-busy/);
  rejectNative(new Error('fixture detached'));
  await new Promise(resolve => setImmediate(resolve));
  const next = await f.io.capture(f.page, { view: true });
  assert.equal(next.captureBackend, 'playwright');
  assert.equal(f.counts().nativeCalls, 1); assert.equal(f.counts().attachments, 2);
});

test('latency accounting measures the same safety checks without wrapper round trips (synthetic clock)', async () => {
  const fast = fixture({ rtt: 350, legacyCost: 2100 });
  const slow = fixture({ rtt: 350, legacyCost: 2100 });
  const candidate = await fast.io.capture(fast.page, { view: true, timeoutMs: 6000 });
  const baseline = await slow.io.capture(slow.page, { view: false, timeoutMs: 6000 });
  assert.equal(fast.counts().clock, 1050); assert.equal(slow.counts().clock, 2800);
  assert.equal(candidate.captureStageMs.geometryBefore, baseline.captureStageMs.geometryBefore);
  assert.equal(candidate.captureStageMs.geometryAfter, baseline.captureStageMs.geometryAfter);
  assert.equal(candidate.captureStageMs.screenshot, 350);
  assert.equal(baseline.captureStageMs.screenshot, 2100);
});

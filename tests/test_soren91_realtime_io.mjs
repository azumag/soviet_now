import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createCanvasIO, postDropProbeEnabled, probeBudget, boundedMs,
  validGeometry, sameGeometry, captureTimeoutMs, captureErrorBackoffMs,
  captureErrorLimit, captureImageFormat, captureJpegQuality } from '../soren91/realtime_io.mjs';
import { LoopMetrics, writeMetricsAtomically } from '../soren91/loop_metrics.mjs';
import { midgameCommentStatus } from '../soren91/commentary_schedule.mjs';
import { rejectedFrameReason } from '../soren91/rejected_frame_diagnostics.mjs';
import { createHoldEscapePolicy, usableCalibration } from '../soren91/observation_guard.mjs';
import { mkdtempSync, statSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const G = { canvasId: 1, documentId: 100, x: 10, y: 20, width: 800, height: 450,
  scrollX: 0, scrollY: 0, dpr: 1, viewportWidth: 1280, viewportHeight: 720, viewportScale: 1 };
const calibration = { screen: { width: 800, height: 450 },
  board: { left: 0, top: 0, width: 800, height: 450 } };
function png(width = 800, height = 450) {
  const b = Buffer.alloc(24);
  Buffer.from('89504e470d0a1a0a', 'hex').copy(b);
  b.write('IHDR', 12); b.writeUInt32BE(width, 16); b.writeUInt32BE(height, 20);
  return b.toString('base64');
}
function jpeg(width = 800, height = 450) {
  const b = Buffer.alloc(21);
  b[0] = 0xff; b[1] = 0xd8; b[2] = 0xff; b[3] = 0xc0;
  b.writeUInt16BE(17, 4);
  b[6] = 8;
  b.writeUInt16BE(height, 7);
  b.writeUInt16BE(width, 9);
  return b.toString('base64');
}
function mockPage({ geometries = [G], capture = async () => ({ data: png() }),
  detach = async () => {} } = {}) {
  const calls = [];
  let reads = 0, attaches = 0, detaches = 0;
  const session = {
    async send(method, args) {
      calls.push({ method, args });
      if (method === 'Runtime.evaluate') return { result: { value: geometries[Math.min(reads++, geometries.length - 1)] } };
      throw new Error('Unexpected method');
    },
    async detach() { detaches++; await detach(); },
  };
  return { context: () => ({ async newCDPSession() { attaches++; return session; } }),
    async screenshot(args) {
      calls.push({ method: 'Page.screenshot', args });
      const value = await capture(args);
      return Buffer.from(value.data, 'base64');
    },
    calls, counts: () => ({ reads, attaches, detaches }) };
}

test('remote defaults are effective in node after dotenv, and explicit opt-in/out survives', () => {
  assert.equal(postDropProbeEnabled({ SOREN91_REMOTE_CDP_URL: 'http://remote.invalid' }), false);
  assert.equal(postDropProbeEnabled({}), true);
  assert.equal(postDropProbeEnabled({ SOREN91_REMOTE_CDP_URL: 'x', SOREN91_RANK_POSTDROP_PROBE: '1' }), true);
  assert.equal(postDropProbeEnabled({ SOREN91_RANK_POSTDROP_PROBE: '0' }), false);
});
test('remote capture defaults to bounded JPEG while local capture stays PNG', () => {
  assert.equal(captureImageFormat({ SOREN91_REMOTE_CDP_URL: 'http://remote.invalid' }), 'jpeg');
  assert.equal(captureImageFormat({}), 'png');
  assert.equal(captureImageFormat({ SOREN91_REMOTE_CDP_URL: 'x', SOREN91_CAPTURE_FORMAT: 'png' }), 'png');
  assert.equal(captureImageFormat({ SOREN91_CAPTURE_FORMAT: 'jpg' }), 'jpeg');
  assert.equal(captureJpegQuality({}), 85);
  assert.equal(captureJpegQuality({ SOREN91_CAPTURE_JPEG_QUALITY: '50' }), 60);
  assert.equal(captureJpegQuality({ SOREN91_CAPTURE_JPEG_QUALITY: '99' }), 95);
});
test('invalid timeouts are bounded; zero cannot disable a native timeout', () => {
  for (const x of [undefined, '', 'NaN', 'Infinity']) assert.equal(boundedMs(x, 3000), 3000);
  assert.equal(boundedMs('0', 3000), 200);
  assert.equal(boundedMs('-5', 3000), 200);
  assert.equal(boundedMs('999999', 3000), 5000);
});
test('a slow screenshot consumes the whole burst budget instead of running 16 times', () => {
  let now = 0, shots = 0;
  const budget = probeBudget(1200, 75, () => now);
  for (let i = 0; i < budget.frames && budget.remaining() > 0; i++) { shots++; now += 1200; now += budget.sleepMs(); }
  assert.equal(shots, 1); assert.equal(now, 1200);
});
test('fast bursts still respect elapsed time and a finite frame limit', () => {
  let now = 0, shots = 0;
  const budget = probeBudget(1200, 75, () => now);
  for (let i = 0; i < budget.frames && budget.remaining() > 0; i++) { shots++; now += 50; now += budget.sleepMs(); }
  assert.equal(shots, 10); assert.equal(now, 1200);
  assert.ok(probeBudget(Infinity, -1).frames <= 64);
});
test('only a fully visible, finite, axis-aligned canvas geometry is accepted', () => {
  assert.equal(validGeometry(G), true);
  for (const patch of [{ x: -1 }, { width: 0 }, { height: Infinity }, { dpr: 0 },
    { viewportScale: 2 }, { viewportWidth: 20 }, { canvasId: null }]) {
    assert.equal(validGeometry({ ...G, ...patch }), false);
  }
  assert.equal(sameGeometry(G, { ...G, dpr: 2 }), false);
});
test('capture uses one reusable session, one verified geometry per frame, and no locator waits', async () => {
  const page = mockPage(); const io = createCanvasIO();
  const frame = await io.capture(page);
  await io.capture(page);
  assert.equal(frame.width, 800);
  assert.equal(page.counts().attaches, 1);
  // The geometry read before the screenshot is reused from the previous
  // frame's verified sample, so only the post-screenshot read runs again
  // (docich#414 speed work: each remote round trip is ~0.3s).
  assert.deepEqual(page.calls.map(c => c.method), ['Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate',
    'Page.screenshot', 'Runtime.evaluate']);
  const opts = page.calls[1].args;
  assert.equal(opts.scale, 'css');
  assert.ok(opts.timeout > 0 && opts.timeout <= 3000);
  assert.equal(opts.fullPage, undefined);
  assert.deepEqual(opts.clip, { x: 10, y: 20, width: 800, height: 450 });
  io.close(page);
});
test('a geometry sample is reused only while it is fresh; reuse expiry re-reads it', async () => {
  const saved = process.env.SOREN91_GEOMETRY_REUSE_MS;
  try {
    const page = mockPage(); const io = createCanvasIO();
    await io.capture(page);
    await io.capture(page);
    assert.deepEqual(page.calls.map(c => c.method).slice(3), ['Page.screenshot', 'Runtime.evaluate']);

    process.env.SOREN91_GEOMETRY_REUSE_MS = '0';
    const strict = mockPage(); const strictIo = createCanvasIO();
    await strictIo.capture(strict);
    await strictIo.capture(strict);
    assert.deepEqual(strict.calls.map(c => c.method),
      ['Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate',
        'Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate']);
    strictIo.close(strict);
    io.close(page);
  } finally {
    if (saved === undefined) delete process.env.SOREN91_GEOMETRY_REUSE_MS;
    else process.env.SOREN91_GEOMETRY_REUSE_MS = saved;
  }
});
test('a rejected frame drops the reused geometry so the next frame re-reads it', async () => {
  // Frame 1 is verified. Frame 2 moves the canvas during transfer, so its
  // reused baseline is rejected. Frame 3 must sample the geometry again
  // instead of trusting the rejected sample.
  const page = mockPage({ geometries: [G, G, { ...G, x: 11 }, { ...G, x: 11 }, { ...G, x: 11 }] });
  const io = createCanvasIO();
  await io.capture(page);
  await assert.rejects(io.capture(page), /geometry-changed/);
  await io.capture(page);
  assert.deepEqual(page.calls.map(c => c.method),
    ['Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate',
      'Page.screenshot', 'Runtime.evaluate',
      'Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate']);
  io.close(page);
});
for (const [name, patch] of Object.entries({ resize: { width: 801 }, scroll: { scrollY: 2 },
  navigation: { documentId: 200 }, replacement: { canvasId: 2 }, dpr: { dpr: 2 }, shift: { x: 11 } })) {
  test(`capture rejects ${name} during transfer`, async () => {
    await assert.rejects(createCanvasIO().capture(mockPage({ geometries: [G, { ...G, ...patch }] })), /geometry-changed/);
  });
}
test('JPEG capture preserves geometry checks and forwards bounded quality', async () => {
  const page = mockPage({ capture: async () => ({ data: jpeg() }) });
  const frame = await createCanvasIO().capture(page, { type: 'jpeg', quality: 85 });
  assert.equal(frame.format, 'jpeg');
  assert.equal(frame.width, 800);
  assert.equal(frame.height, 450);
  const opts = page.calls.find(c => c.method === 'Page.screenshot').args;
  assert.equal(opts.type, 'jpeg');
  assert.equal(opts.quality, 85);
  await assert.rejects(
    createCanvasIO().capture(mockPage({ capture: async () => ({ data: 'bm90LWEtanBlZw==' }) }), { type: 'jpeg' }),
    /invalid-jpeg/,
  );
});
test('CSS and native DPR PNGs are valid; unexpected scaling fails closed', async () => {
  const g = { ...G, dpr: 2 };
  for (const scale of [1, 2]) {
    const f = await createCanvasIO().capture(mockPage({ geometries: [g], capture: async () => ({ data: png(800 * scale, 450 * scale) }) }));
    assert.equal(f.width, 800 * scale);
  }
  await assert.rejects(createCanvasIO().capture(mockPage({ capture: async () => ({ data: png(400, 225) }) })), /scale-mismatch/);
});
test('invalid PNG never reaches the analyzer', async () => {
  await assert.rejects(createCanvasIO().capture(mockPage({ capture: async () => ({ data: 'not an image' }) })), /invalid-png/);
});
test('capture timeout retires only its CDP session and never returns a late frame', async () => {
  let finish;
  const page = mockPage({ capture: () => new Promise(resolve => { finish = resolve; }) });
  const io = createCanvasIO();
  await assert.rejects(io.capture(page, { timeoutMs: 15 }), /capture-timeout/);
  assert.equal(page.counts().detaches, 1);
  await assert.rejects(io.capture(page), /session-busy/);
  finish({ data: png() });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(page.counts().reads, 1); // No late post-capture geometry/side effects.
});
test('failed CDP detach still recovers after the timed-out screenshot settles', async () => {
  let finish, shots = 0;
  const page = mockPage({
    capture: () => ++shots === 1
      ? new Promise(resolve => { finish = resolve; })
      : Promise.resolve({ data: png() }),
    detach: async () => { throw new Error('session already disconnected'); },
  });
  const io = createCanvasIO();
  await assert.rejects(io.capture(page, { timeoutMs: 15 }), /capture-timeout/);
  assert.equal(page.counts().detaches, 1);
  // Even a failed detach cannot allow overlapping captures or late input.
  await assert.rejects(io.capture(page), /session-busy/);
  assert.equal(page.counts().attaches, 1);
  assert.equal(shots, 1);
  finish({ data: png() });
  await new Promise(resolve => setImmediate(resolve));
  const recovered = await io.capture(page);
  assert.equal(recovered.width, 800);
  assert.equal(page.counts().attaches, 2);
  assert.equal(page.counts().reads, 3); // The late first frame had no post-capture work.
  assert.equal(shots, 2);
  io.close(page);
});
test('concurrent calls cannot queue duplicate screenshot operations', async () => {
  let finish;
  const page = mockPage({ capture: () => new Promise(resolve => { finish = resolve; }) });
  const io = createCanvasIO(); const first = io.capture(page);
  await assert.rejects(io.capture(page), /session-busy/);
  await new Promise(resolve => setImmediate(resolve));
  finish({ data: png() }); await first;
  assert.equal(page.calls.filter(c => c.method === 'Page.screenshot').length, 1);
});
test('frameBox reuses only already-verified frame geometry and calibration dimensions', () => {
  const io = createCanvasIO();
  const frame = { geometry: G, width: 800, height: 450 };
  assert.deepEqual(io.frameBox(frame, calibration), G);
  assert.throws(() => io.frameBox({ ...frame, geometry: { ...G, x: -1 } }, calibration), /geometry-changed/);
  assert.throws(() => io.frameBox(frame, { screen: { width: 1600, height: 900 } }), /calibration-mismatch/);
});
test('stale observations, changed geometry, and calibration mismatch cannot reach input', async () => {
  let now = 0;
  const page = mockPage(); const io = createCanvasIO({ now: () => now });
  const frame = await io.capture(page);
  await io.validateInput(page, frame, calibration);
  await assert.rejects(io.validateInput(page, frame, { screen: { width: 1600, height: 900 } }), /calibration-mismatch/);
  now = 2501;
  await assert.rejects(io.validateInput(page, frame, calibration), /stale-observation/);
  now = 0;
  const changed = mockPage({ geometries: [{ ...G, x: 50 }] });
  await assert.rejects(createCanvasIO({ now: () => now }).validateInput(changed, frame, calibration), /geometry-changed/);
});
test('slow captures receive an adaptive freshness budget before input', async () => {
  let now = 5000;
  const frame = { geometry: G, width: 800, height: 450, capturedAt: 0, captureMs: 4600 };
  const io = createCanvasIO({ now: () => now });
  await assert.doesNotReject(io.validateInput(mockPage(), frame, calibration));
  await assert.rejects(io.validateInput(mockPage(), frame, calibration, { maxAgeMs: 4000 }),
    /stale-observation/);
});

test('freshness includes time spent validating input', async () => {
  let now = 0;
  const page = mockPage(); const io = createCanvasIO({ now: () => now });
  const frame = await io.capture(page);
  const slow = { context: () => ({ newCDPSession: async () => ({
    send: async () => { now = 3000; return { result: { value: G } }; }, detach: async () => {},
  }) }) };
  await assert.rejects(createCanvasIO({ now: () => now }).validateInput(slow, frame, calibration,
    { timeoutMs: 4000 }), /stale-observation/);
});
test('metrics count HOLD separately and measure actual sent-click intervals across observation retries', async () => {
  let now = 0; const writes = [];
  const m = new LoopMetrics({ now: () => now, write: v => writes.push(v) });
  m.begin(1, 0);
  await m.measure('capture', async () => { now += 5000; });
  m.observe({ perception: { reason: 'unknown-current' } });
  m.holdSent();
  m.begin(1, 0);
  await m.measure('capture', async () => { now += 5000; });
  m.observe({ perception: { reason: 'stable-slow-advance-temporal-next' } });
  m.dropSent(); const first = m.flush('drop-sent');
  assert.equal(first.observations, 2); assert.equal(first.holds, 1);
  assert.equal(first.stageMs.capture, 10000);
  assert.equal(first.reasonCounts['stable-slow-advance'], 1);
  assert.equal(first.dropSentIntervalMs.samples, 0);
  m.begin(1, 1); now += 20000; m.dropSent();
  assert.equal(m.flush('drop-sent').dropSentIntervalMs.last, 20000);
  m.begin(2, 0); m.dropSent();
  assert.equal(m.flush('drop-sent').dropSentIntervalMs.samples, 0); // No inter-round wait in APM.
});
test('metrics histories and untrusted reason strings remain bounded and sanitized', () => {
  let now = 0; const m = new LoopMetrics({ now: () => now });
  for (let i = 0; i < 300; i++) { m.begin(1, i); now += 1000; m.dropSent(); }
  m.observe({ perception: { reason: 'secret-name-or-prompt' } });
  const value = m.flush('unknown-outcome');
  assert.equal(value.dropSentIntervalMs.samples, 128);
  assert.equal(value.reasonCounts.other, 1);
  assert.equal(JSON.stringify(value).includes('secret-name'), false);
  assert.equal(value.outcome, 'error');
});
test('metrics writes are atomic 0600, and failures do not break the game', () => {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-latency-'));
  try {
    const path = join(dir, 'metrics.json');
    writeMetricsAtomically(path, { value: 1 }); writeMetricsAtomically(path, { value: 2 });
    assert.equal(statSync(path).mode & 0o777, 0o600);
    assert.deepEqual(readdirSync(dir), ['metrics.json']);
    const m = new LoopMetrics({ write: () => { throw new Error('disk-full'); } });
    m.begin(1, 0); assert.doesNotThrow(() => m.flush('error'));
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

const mainSource = readFileSync(new URL('../soren91/main.mjs', import.meta.url), 'utf8');
function extract(name, end) {
  const start = mainSource.indexOf(`async function ${name}(`);
  const stop = mainSource.indexOf(end, start);
  assert.ok(start >= 0 && stop > start);
  return mainSource.slice(start, stop).trim();
}
const loopSource = extract('gameLoop', '/**\n * HOLD').replaceAll('import.meta.url', '"file:///soren91/main.mjs"');
async function simulateLoop({ captureMs = 300, hold = false, blocked = 0, maxDrops = 2, pieces = [],
  observation = null, diagnosticsEnabled = false, env = null, calibration: cal = calibration,
  blockedReason = 'unknown-current', blockedState = 'DROP', source = loopSource } = {}) {
  let now = 0, shots = 0, drops = 0, holds = 0, decisions = 0;
  const dropTimes = [], writes = [], comments = [], diagnosticCalls = [], logs = [];
  const context = {
    join, HISTORY_DIR: 'history', SCREENSHOT_DIR: 'screens', SCREENSHOT_EXTENSION: 'png',
    DROP_COOLDOWN_MS: 1200, DROP_OBSERVATION_DELAY_MS: 800, POLL_INTERVAL_MS: 200, HOLD_ESCAPE_X: 0,
    createHoldEscapePolicy, usableCalibration,
    process: { env: env ?? {} },
    CALIBRATION_MIN_PIECES: 999, CALIBRATION_MIN_CONFIDENCE: 0.55, MIN_RANKING_DETECTION_TURNS: 999,
    performance: { now: () => now },
    Date: class extends Date { static now() { return now; } },
    LoopMetrics: class extends LoopMetrics { constructor(opts) { super({ ...opts, now: () => now }); } },
    writeMetricsAtomically: (_, value) => writes.push(value),
    REJECTED_FRAME_DIAGNOSTICS_ENABLED: diagnosticsEnabled,
    rejectedFrameReason,
    saveRejectedFrame: options => { diagnosticCalls.push(options); return { saved: false }; },
    console: { log: message => logs.push(String(message)), error: message => logs.push(String(message)) },
    snapshotCurrentStrategyForGame: () => ({ strategyHash: 'fixed', snapshotPath: 'fixed.mjs' }),
    archiveLatestHistory: () => null,
    existsSync: path => path === 'tmp/stop' && (drops >= maxDrops || shots > Math.max(12, maxDrops * 5)),
    writeFileSync() {}, appendFileSync() {},
    loadCommentModule: async () => null,
    midgameCommentStatus,
    captureGameScreenshot: async () => { shots++; now += captureMs; return typeof observation === 'function' ? observation(shots) : observation; },
    loadModule: async () => ({
      generateMidgameComment: async (game, turn) => { comments.push({ game, turn, at: now }); return 'test'; },
      detectConnectionErrorScreen: async () => false,
      detectRankingScreen: async () => null,
      analyzeScreenshot: async () => ({ state: shots <= blocked
        ? (typeof blockedState === 'function' ? blockedState(shots) : blockedState) : 'MOVE',
        pieces, confidence: 1, perception: { reason: shots <= blocked
          ? (typeof blockedReason === 'function' ? blockedReason(shots) : blockedReason) : 'stable' } }),
    }),
    loadStrategy: async () => ({ decide: () => ({ x: 0, reason: 'test', hold: hold && decisions++ === 0 }) }),
    executeHold: async () => { holds++; now += 300; },
    executeDrop: async () => { now += 200; drops++; dropTimes.push(now); },
    sleep: async ms => { now += ms; },
  };
  const loop = vm.runInNewContext(`(${source})`, context);
  await loop({}, cal, 1);
  return { now, shots, drops, holds, dropTimes, writes, comments, diagnosticCalls, logs };
}
test('real loop waits only for spawn before capturing, retaining the input cooldown', async () => {
  const s = await simulateLoop({ captureMs: 1200 });
  assert.deepEqual(s.dropTimes, [1400, 3600]);
  assert.equal(s.dropTimes[1] - s.dropTimes[0], 2200);
  assert.equal(s.writes.at(-1).dropProfile.records[0].stageMs.cooldown, 800);
});
test('real main loop requests midgame commentary once before turn 20 in a slow round', async () => {
  const s = await simulateLoop({ captureMs: 10000, maxDrops: 9, pieces: [{}, {}, {}] });
  assert.equal(s.drops, 9);
  assert.equal(s.comments.length, 1);
  assert.equal(s.comments[0].game, 1);
  assert.ok(s.comments[0].turn >= 5 && s.comments[0].turn < 20);
  assert.ok(s.comments[0].at >= 45000);
});
test('fast capture still cannot bypass minimum cooldown, and always reacquires after waiting', async () => {
  const s = await simulateLoop();
  assert.equal(s.drops, 2); assert.ok(s.dropTimes[1] - s.dropTimes[0] >= 1200);
  assert.ok(s.shots > 2);
});
test('HOLD no longer adds a fresh 1.2 second drop cooldown', async () => {
  const s = await simulateLoop({ hold: true });
  assert.equal(s.holds, 1); assert.equal(s.dropTimes[0], 1100);
});
// A usable schema-2 calibration whose screen matches the frame the loop sees.
const validCalibration = () => ({
  coordinateSchema: 2, confidence: 0.82,
  screen: { width: 960, height: 540 },
  board: { left: 332, right: 627, top: 167, bottom: 518, width: 295, height: 351 },
  arena: { left: 332, right: 627, top: 100, bottom: 518, width: 295, height: 418 },
  hud: { top: 0, bottom: 100 },
});
const stalledFrame = { width: 960, height: 540 };

test('a persistent recognition hold gets one bounded fallback drop instead of holding forever', async () => {
  // Measured live on 2026-10-15: 12 consecutive unknown-current frames and 93s
  // until the round ended, because no input means the frame never changes.
  const s = await simulateLoop({ blocked: 100, maxDrops: 1,
    observation: stalledFrame, calibration: validCalibration() });
  assert.equal(s.drops, 1);
  assert.equal(s.holds, 0);
  assert.ok(s.writes.some(v => v.reasonCounts['unknown-current'] > 1));
  assert.ok(s.logs.some(line => line.includes('Recognition hold escape')));
});
test('the recognition-hold escape is bounded and kill-switchable', async () => {
  const disabled = await simulateLoop({ blocked: 100, maxDrops: 4,
    observation: stalledFrame, calibration: validCalibration(),
    env: { SOREN91_HOLD_ESCAPE_FRAMES: '0' } });
  assert.equal(disabled.drops, 0); // explicit kill switch stays fail-closed
  const capped = await simulateLoop({ blocked: 100, maxDrops: 6,
    observation: stalledFrame, calibration: validCalibration(),
    env: { SOREN91_HOLD_ESCAPE_FRAMES: '2', SOREN91_HOLD_ESCAPE_INTERVAL_MS: '0',
      SOREN91_HOLD_ESCAPE_LIMIT: '3' } });
  assert.ok(capped.drops <= 3);
});
test('an unhealthy calibration or a frame without geometry withholds the escape', async () => {
  const mismatch = await simulateLoop({ blocked: 100, maxDrops: 1,
    observation: { width: 800, height: 450 }, calibration: validCalibration() });
  assert.equal(mismatch.drops, 0);
  const unknownFrame = await simulateLoop({ blocked: 100, maxDrops: 1,
    observation: null, calibration: validCalibration() });
  assert.equal(unknownFrame.drops, 0);
});
test('every other rejection reason still waits, never a blind drop', async () => {
  // The escape is scoped to one label. Motion, settling and board faults keep
  // the previous fail-closed behaviour.
  for (const reason of ['board-moving', 'preview-changed', 'confirm-frame',
    'invalid-board', 'uncalibrated', 'stable']) {
    const s = await simulateLoop({ blocked: 100, maxDrops: 1, blockedReason: reason,
      observation: stalledFrame, calibration: validCalibration() });
    assert.equal(s.drops, 0, reason);
    assert.equal(s.logs.some(line => line.includes('Recognition hold escape')), false, reason);
  }
});
test('intervening settling observations break the real loop escape streak', async () => {
  for (const reason of ['board-moving', 'preview-changed', 'invalid-board']) {
    const s = await simulateLoop({ blocked: 100, maxDrops: 1, captureMs: 1200,
      blockedReason: shot => shot % 4 === 0 ? reason : 'unknown-current',
      observation: stalledFrame, calibration: validCalibration() });
    assert.equal(s.drops, 0, reason);
  }
});
test('an intervening geometry mismatch also breaks the real loop escape streak', async () => {
  const s = await simulateLoop({ blocked: 100, maxDrops: 1, captureMs: 1200,
    observation: shot => shot % 4 === 0 ? { width: 800, height: 450 } : stalledFrame,
    calibration: validCalibration() });
  assert.equal(s.drops, 0);
});
test('diagnostic receives the original capture bytes only for an eligible rejection', async () => {
  const frame = { buffer: Buffer.from('original captured bytes'), format: 'jpeg', width: 960, height: 540 };
  const s = await simulateLoop({ blocked: 1, maxDrops: 1, observation: frame, diagnosticsEnabled: true });
  assert.equal(s.diagnosticCalls.length, 1);
  assert.equal(s.diagnosticCalls[0].observation, frame);
  assert.equal(s.diagnosticCalls[0].reason, 'unknown-current');
  assert.equal(s.diagnosticCalls[0].enabled, true);
});
test('real drop does one final remote geometry validation and never clicks after a changed aim', async () => {
  let validations = 0, clicks = 0;
  const frame = { geometry: G, width: 800, height: 450 };
  const fn = vm.runInNewContext(`(${extract('executeDrop', '/**\n * ラウンド終了処理')})`, {
    loadModule: async () => ({ dropXToPixel: () => 400 }),
    canvasIO: { frameBox: () => G },
    inputCanvasBox: async () => { validations++; throw new Error('input-geometry-changed'); },
    process: { env: {} }, sleep: async () => {},
    markObservationDropSent: () => {},
  });
  await assert.rejects(fn({ mouse: { move: async () => {}, click: async () => { clicks++; } } }, 0, calibration, frame),
    /geometry-changed/);
  assert.equal(validations, 1);
  assert.equal(clicks, 0);
});
test('real drop arms the post-drop observation boundary only after the click is sent', async () => {
  const events = [];
  const fn = vm.runInNewContext(`(${extract('executeDrop', '/**\n * ラウンド終了処理')})`, {
    loadModule: async () => ({ dropXToPixel: () => 400 }),
    canvasIO: { frameBox: () => G },
    inputCanvasBox: async () => G,
    process: { env: {} },
    sleep: async () => {},
    markObservationDropSent: () => { events.push('mark'); },
  });
  await fn({
    mouse: {
      move: async () => { events.push('move'); },
      click: async () => { events.push('click'); },
    },
  }, 0, calibration, { width: 800, height: 450 });
  assert.deepEqual(events, ['move', 'click', 'mark']);
});

test('input freshness uses adaptive capture budget unless an operator overrides it', async () => {
  const source = extract('inputCanvasBox', 'async function setNormalGameLifecycle');
  let options = null;
  const adaptive = vm.runInNewContext(`(${source})`, {
    canvasIO: { validateInput: async (_page, _frame, _calibration, value) => { options = value; return G; } },
    process: { env: {} }, boundedMs,
  });
  await adaptive({}, calibration, { captureMs: 4600 });
  assert.equal(Object.hasOwn(options, 'maxAgeMs'), false);

  const explicit = vm.runInNewContext(`(${source})`, {
    canvasIO: { validateInput: async (_page, _frame, _calibration, value) => value },
    process: { env: { SOREN91_INPUT_MAX_FRAME_AGE_MS: '7000' } }, boundedMs,
  });
  const configured = await explicit({}, calibration, { captureMs: 4600 });
  assert.equal(configured.maxAgeMs, 7000);
});

test('runtime wiring retains guards, bounds both ranking bursts, and avoids per-turn ESM churn', () => {
  assert.match(mainSource, /if \(!postDropProbeEnabled\(\)\)/);
  assert.equal((mainSource.match(/i < frames && budget\.remaining\(\) > 0/g) || []).length, 2);
  assert.match(mainSource, /if \(boardState\.state !== 'MOVE'\)/);
  assert.match(mainSource, /latency\.flush\(loopOutcome\)/);
  assert.match(extract('loadModule', '// comment.mjs'), /st\.mtimeMs/);
  assert.doesNotMatch(extract('loadStrategy', '// --- シグナル'), /Date\.now/);
});

test('capture timeout default covers the measured p95 capture cost', () => {
  assert.equal(captureTimeoutMs({}), 6000);
  assert.equal(captureTimeoutMs({ SOREN91_CAPTURE_TIMEOUT_MS: '9000' }), 9000);
  assert.equal(captureTimeoutMs({ SOREN91_CAPTURE_TIMEOUT_MS: '99999' }), 9000);
  assert.equal(captureTimeoutMs({ SOREN91_CAPTURE_TIMEOUT_MS: '10' }), 200);
});

test('consecutive-error policy backs off exponentially and only stops after a generous limit', () => {
  assert.equal(captureErrorBackoffMs(1, {}), 1000);
  assert.equal(captureErrorBackoffMs(2, {}), 2000);
  assert.equal(captureErrorBackoffMs(5, {}), 15000);
  assert.equal(captureErrorBackoffMs(9, {}), 15000);
  assert.equal(captureErrorBackoffMs(3, { SOREN91_ERROR_BACKOFF_MAX_MS: '4000' }), 4000);
  assert.equal(captureErrorLimit({}), 30);
  assert.equal(captureErrorLimit({ SOREN91_ERROR_LIMIT: '5' }), 5);
  assert.equal(captureErrorLimit({ SOREN91_ERROR_LIMIT: '0' }), 30);
});

test('WAITING observations break the real loop escape streak before the first drop', async () => {
  const s = await simulateLoop({ blocked: 100, maxDrops: 1, captureMs: 1200,
    blockedState: shot => shot % 4 === 0 ? 'WAITING' : 'DROP',
    observation: stalledFrame, calibration: validCalibration() });
  assert.equal(s.drops, 0);
  assert.equal(s.logs.some(line => line.includes('Recognition hold escape')), false);
});

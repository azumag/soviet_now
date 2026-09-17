import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { createCanvasIO, postDropProbeEnabled, probeBudget, boundedMs,
  validGeometry, sameGeometry } from '../soren91/realtime_io.mjs';
import { LoopMetrics, writeMetricsAtomically } from '../soren91/loop_metrics.mjs';
import { midgameCommentStatus } from '../soren91/commentary_schedule.mjs';
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
function mockPage({ geometries = [G], capture = async () => ({ data: png() }) } = {}) {
  const calls = [];
  let reads = 0, attaches = 0, detaches = 0;
  const session = {
    async send(method, args) {
      calls.push({ method, args });
      if (method === 'Runtime.evaluate') return { result: { value: geometries[Math.min(reads++, geometries.length - 1)] } };
      throw new Error('Unexpected method');
    },
    async detach() { detaches++; },
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
test('capture uses one reusable session and exactly geometry/canvas/geometry without locator waits', async () => {
  const page = mockPage(); const io = createCanvasIO();
  const frame = await io.capture(page);
  await io.capture(page);
  assert.equal(frame.width, 800);
  assert.equal(page.counts().attaches, 1);
  assert.deepEqual(page.calls.map(c => c.method), ['Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate',
    'Runtime.evaluate', 'Page.screenshot', 'Runtime.evaluate']);
  const opts = page.calls[1].args;
  assert.equal(opts.scale, 'css');
  assert.ok(opts.timeout > 0 && opts.timeout <= 3000);
  assert.equal(opts.fullPage, undefined);
  assert.deepEqual(opts.clip, { x: 10, y: 20, width: 800, height: 450 });
  io.close(page);
});
for (const [name, patch] of Object.entries({ resize: { width: 801 }, scroll: { scrollY: 2 },
  navigation: { documentId: 200 }, replacement: { canvasId: 2 }, dpr: { dpr: 2 }, shift: { x: 11 } })) {
  test(`capture rejects ${name} during transfer`, async () => {
    await assert.rejects(createCanvasIO().capture(mockPage({ geometries: [G, { ...G, ...patch }] })), /geometry-changed/);
  });
}
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
test('concurrent calls cannot queue duplicate screenshot operations', async () => {
  let finish;
  const page = mockPage({ capture: () => new Promise(resolve => { finish = resolve; }) });
  const io = createCanvasIO(); const first = io.capture(page);
  await assert.rejects(io.capture(page), /session-busy/);
  await new Promise(resolve => setImmediate(resolve));
  finish({ data: png() }); await first;
  assert.equal(page.calls.filter(c => c.method === 'Page.screenshot').length, 1);
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
async function simulateLoop({ captureMs = 300, hold = false, blocked = 0, maxDrops = 2, pieces = [], source = loopSource } = {}) {
  let now = 0, shots = 0, drops = 0, holds = 0, decisions = 0;
  const dropTimes = [], writes = [], comments = [];
  const context = {
    join, HISTORY_DIR: 'history', SCREENSHOT_DIR: 'screens', DROP_COOLDOWN_MS: 1200, POLL_INTERVAL_MS: 200,
    CALIBRATION_MIN_PIECES: 999, CALIBRATION_MIN_CONFIDENCE: 0.55, MIN_RANKING_DETECTION_TURNS: 999,
    performance: { now: () => now },
    Date: class extends Date { static now() { return now; } },
    LoopMetrics: class extends LoopMetrics { constructor(opts) { super({ ...opts, now: () => now }); } },
    writeMetricsAtomically: (_, value) => writes.push(value),
    console: { log() {}, error() {} },
    snapshotCurrentStrategyForGame: () => ({ strategyHash: 'fixed', snapshotPath: 'fixed.mjs' }),
    existsSync: path => path === 'tmp/stop' && (drops >= maxDrops || shots > Math.max(12, maxDrops * 5)),
    writeFileSync() {}, appendFileSync() {},
    loadCommentModule: async () => null,
    midgameCommentStatus,
    captureGameScreenshot: async () => { shots++; now += captureMs; return null; },
    loadModule: async () => ({
      generateMidgameComment: async (game, turn) => { comments.push({ game, turn, at: now }); return 'test'; },
      analyzeScreenshot: async () => ({ state: shots <= blocked ? 'DROP' : 'MOVE',
        pieces, confidence: 1, perception: { reason: shots <= blocked ? 'unknown-current' : 'stable' } }),
    }),
    loadStrategy: async () => ({ decide: () => ({ x: 0, reason: 'test', hold: hold && decisions++ === 0 }) }),
    executeHold: async () => { holds++; now += 300; },
    executeDrop: async () => { now += 200; drops++; dropTimes.push(now); },
    sleep: async ms => { now += ms; },
  };
  const loop = vm.runInNewContext(`(${source})`, context);
  await loop({}, calibration, 1);
  return { now, shots, drops, holds, dropTimes, writes, comments };
}
test('real main loop overlaps slow capture with cooldown instead of adding 1.2s each turn', async () => {
  const s = await simulateLoop({ captureMs: 1200 });
  assert.deepEqual(s.dropTimes, [1400, 2800]);
  assert.equal(s.dropTimes[1] - s.dropTimes[0], 1400);
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
test('unknown-current remains fail-closed; faster polling cannot force a blind drop', async () => {
  const s = await simulateLoop({ blocked: 100 });
  assert.equal(s.drops, 0); assert.equal(s.holds, 0);
  assert.ok(s.writes.some(v => v.reasonCounts['unknown-current'] > 1));
});
test('real drop function does not click if geometry changes during mouse aiming', async () => {
  let validations = 0, clicks = 0;
  const fn = vm.runInNewContext(`(${extract('executeDrop', '/**\n * ラウンド終了処理')})`, {
    loadModule: async () => ({ dropXToPixel: () => 400 }),
    inputCanvasBox: async () => { if (++validations === 2) throw new Error('input-geometry-changed'); return G; },
    process: { env: {} }, sleep: async () => {},
  });
  await assert.rejects(fn({ mouse: { move: async () => {}, click: async () => { clicks++; } } }, 0, calibration,
    { width: 800, height: 450 }), /geometry-changed/);
  assert.equal(clicks, 0);
});
test('runtime wiring retains guards, bounds both ranking bursts, and avoids per-turn ESM churn', () => {
  assert.match(mainSource, /if \(!postDropProbeEnabled\(\)\)/);
  assert.equal((mainSource.match(/i < frames && budget\.remaining\(\) > 0/g) || []).length, 2);
  assert.match(mainSource, /if \(boardState\.state !== 'MOVE'\)/);
  assert.match(mainSource, /latency\.flush\(loopOutcome\)/);
  assert.match(extract('loadModule', '// comment.mjs'), /st\.mtimeMs/);
  assert.doesNotMatch(extract('loadStrategy', '// --- シグナル'), /Date\.now/);
});

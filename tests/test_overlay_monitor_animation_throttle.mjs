// #970 regression: the Soren monitor animation is throttled.
//
// The monitor pan moves 4px/s and the health/observer view flips every
// MONITOR_VIEW_MS, but the motion loop used to read and write layout on every
// native animation frame (about 100 Hz under Xvfb). This executes the shipped
// `animateSorenMonitor` in a VM with the same helpers it closes over and pins
// two properties:
//
//   * layout work happens at the bounded throttle rate, not once per frame, and
//   * the pan still advances at 4px/s because the work uses elapsed wall time.

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const htmlPath = path.join(root, 'overlays', 'direct_broadcast_overlay.html');
const START = '// overlay-monitor-animation:start';
const END = '// overlay-monitor-animation:end';

function shippedAnimation() {
  const html = fs.readFileSync(htmlPath, 'utf8');
  assert.ok(html.includes(START) && html.includes(END), 'animation markers must exist');
  const source = html.split(START)[1].split(END)[0];
  assert.ok(source.includes('function animateSorenMonitor('), 'snippet must hold the animation loop');
  return { html, source };
}

function shippedThrottleMs(html) {
  const match = /const MONITOR_ANIM_MIN_MS = (\d+);/.exec(html);
  assert.ok(match, 'overlay must declare MONITOR_ANIM_MIN_MS');
  const value = Number(match[1]);
  assert.ok(value >= 33 && value <= 200, `throttle must stay a bounded slice (got ${value})`);
  return value;
}

function runAnimation(source, minMs) {
  const state = { layoutReads: 0, rafScheduled: 0, elapsed: [] };
  const viewport = { scrollTop: 0 };
  const rootElement = {
    getBoundingClientRect() {
      state.layoutReads += 1;
      return { width: 100 };
    },
    querySelector() {
      return viewport;
    },
  };
  const context = vm.createContext({
    Math,
    Number,
    String,
    Object,
    Boolean,
    feedG: { querySelector: () => rootElement },
    feedS: { ariaHidden: '' },
    slot: null,
    document: {
      getElementById: (id) => (id === 'monitor-observer' ? { querySelector: () => null, ariaHidden: '' } : null),
      documentElement: { dataset: {} },
    },
    window: { matchMedia: () => ({ matches: false }) },
    performance: { now: () => 0 },
    requestAnimationFrame: () => {
      state.rafScheduled += 1;
      return state.rafScheduled;
    },
    cancelAnimationFrame: () => {},
    advanceMonitorScroll: (target, motion, now, elapsed) => {
      state.elapsed.push(elapsed);
      motion.position += elapsed * 4;
    },
    MONITOR_ANIM_MIN_MS: minMs,
    MONITOR_VIEW_MS: 45000,
    monitorMotion: {
      startedAt: 0,
      lastTick: null,
      lastWork: null,
      frame: null,
      upper: { position: 0, direction: 1, waitUntil: 0 },
      observer: { position: 0, direction: 1, waitUntil: 0 },
    },
  });
  vm.runInContext(`${source}\nglobalThis.__animate = animateSorenMonitor;`, context, {
    filename: 'direct_broadcast_overlay.html',
  });
  const animate = context.__animate;
  assert.equal(typeof animate, 'function');
  // Drive one virtual second at a native ~60 Hz cadence.
  for (let ms = 0; ms <= 1000; ms += 16) animate(ms);
  return state;
}

test('monitor animation touches layout at the throttle rate, not every frame', () => {
  const { html, source } = shippedAnimation();
  const minMs = shippedThrottleMs(html);
  const state = runAnimation(source, minMs);

  const nativeFrames = Math.floor(1000 / 16) + 1;
  assert.ok(state.rafScheduled === nativeFrames, `every frame is still scheduled (got ${state.rafScheduled})`);

  // The gate is evaluated on the 16ms frame grid, so the effective slice is the
  // throttle rounded up to a whole frame.
  const effectiveMs = Math.ceil(minMs / 16) * 16;
  const lower = Math.floor(1000 / effectiveMs) - 1;
  const upper = Math.ceil(1000 / minMs) + 2;
  assert.ok(
    state.layoutReads >= lower && state.layoutReads <= upper,
    `layout work must run at the throttled rate (read ${state.layoutReads} times in 1s, expected ${lower}..${upper})`,
  );
  assert.ok(
    state.layoutReads < nativeFrames / 2,
    'throttle must remove most per-frame layout work',
  );
});

test('elapsed wall time keeps the pan at 4px/s', () => {
  const { html, source } = shippedAnimation();
  const minMs = shippedThrottleMs(html);
  const state = runAnimation(source, minMs);

  const covered = state.elapsed.reduce((sum, value) => sum + value, 0);
  assert.ok(covered > 0.8 && covered < 1.15, `one virtual second must advance ~1s of pan (got ${covered})`);
});

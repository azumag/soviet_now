import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { waitForInlineRails } from '../soren91/presentation_ready.mjs';

const source = readFileSync(new URL('../soren91/main.mjs', import.meta.url), 'utf8');
const start = source.indexOf('    gamePage = await gotoGamePageWithRecovery({', source.indexOf('async function main()'));
const end = source.indexOf("    console.log('[main] Soren91 ready marker written');", start);
assert.ok(start > 0 && end > start);
const startup = source.slice(start, end);
async function simulate({ remote = false, readyError = false } = {}) {
  const events = [];
  const page = {
    bringToFront: async () => events.push('raise'),
    waitForSelector: async () => events.push('canvas'),
    evaluate: async () => true,
  };
  const context = {
    gamePage: page, context: {}, gameUrl: 'https://example.invalid', isSharedMode: true,
    ownsContext: false, audioGainMultiplier: 1, audioOutputLabel: '', anchorPage: {},
    gotoGamePageWithRecovery: async () => page,
    process: { env: { SOREN91_BRING_TO_FRONT: '1' } }, console: { log() {} },
    fullscreenBrowserWindow: async () => events.push('fullscreen'),
    reportAudioOutputRoute: async () => {}, sleep: async () => {},
    DIRECT_OVERLAY_CONFIG: {}, VIEWPORT_WIDTH: 480, VIEWPORT_HEIGHT: 270,
    installDirectGameStage: async () => { events.push('stage'); return {}; },
    remoteBrowserOwnsViewport: () => remote,
    installInlineDirectBroadcastOverlay: async () => events.push('iframes'),
    startInlineBroadcastState: async () => events.push('state'),
    waitForInlineRails: async () => {
      await new Promise(resolve => setImmediate(resolve));
      assert.ok(!events.includes('raise'), 'must await pending rail rendering');
      assert.ok(!events.includes('fullscreen'), 'must not fullscreen during rail rendering');
      assert.ok(!events.includes('ready-marker'));
      events.push('rendered');
      if (readyError) throw Error('not-ready');
    },
    handleTitleScreen: async () => events.push('title'),
    writeFileSync: () => events.push('ready-marker'), SOREN91_READY_FILE: '', Date,
  };
  await vm.runInNewContext(`(async () => {${startup}})()`, context);
  return events;
}
test('real startup code publishes state and renders rails before raising/fullscreening', async () => {
  const events = await simulate();
  for (const name of ['raise', 'fullscreen']) {
    assert.ok(events.indexOf(name) > events.indexOf('stage'));
    assert.ok(events.indexOf(name) > events.indexOf('rendered'));
  }
  assert.ok(events.indexOf('state') < events.indexOf('iframes'));
  assert.ok(events.indexOf('ready-marker') > events.indexOf('raise'));
});
test('rail timeout continues only after the stage and bounded wait attempt', async () => {
  const events = await simulate({ readyError: true });
  assert.ok(events.includes('raise'), 'fail-open: startup proceeds past rail wait');
  assert.ok(events.indexOf('raise') > events.indexOf('stage'));
  assert.ok(events.includes('ready-marker'), 'fail-open: ready marker still written');
});
test('remote game-only presentation does not install or wait on VM-owned rails', async () => {
  const events = await simulate({ remote: true });
  assert.ok(!events.includes('iframes'));
  assert.ok(!events.includes('rendered'));
  assert.ok(events.includes('ready-marker'));
});
test('readiness checks rendered state, error, and region for every expected rail', async () => {
  const config = { enabled: true, broadcast: {}, surfaces: [
    { region: 'top', elementId: 'top', htmlFile: 'rail.html' },
    { region: 'bottom', elementId: 'bottom', htmlFile: 'rail.html' },
  ] };
  await waitForInlineRails({ waitForFunction: async (fn, items, opts) => {
    assert.equal(opts.timeout, 10000);
    const health = { merged: true, error: '', region: 'top', updatedAt: 123 };
    const frames = { top: { contentDocument: { readyState: 'complete' }, contentWindow: { __sorenBroadcastOverlayHealth: health } } };
    const check = vm.runInNewContext(`(${fn})`, { document: { getElementById: id => frames[id] }, window: { __sorenBroadcastState: { updatedAt: 123 } } });
    assert.equal(check(items), false);
    frames.bottom = { contentDocument: { readyState: 'complete' }, contentWindow: { __sorenBroadcastOverlayHealth: { ...health, region: 'bottom' } } };
    assert.equal(check(items), true);
    health.error = 'fetch failed'; assert.equal(check(items), false);
    health.error = ''; health.updatedAt = 122; assert.equal(check(items), false);
  } }, config);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Run the actual bridge control path and its browser callback without booting
// Playwright, Unity, services or game input. Mutate Unity state during claim.
const bridge = fs.readFileSync(new URL('../soviet_local.mjs', import.meta.url), 'utf8');
const start = bridge.indexOf('async function processGameLifecycleControl(');
const end = bridge.indexOf('async function inspectUnityAudio(', start);
assert.ok(start >= 0 && end > start);
const source = bridge.slice(start, end).trim();
const stop = { state: 'STOP', score: 6401, makeSorenCount: 1, pieces: [{ type: 16, x: 2 }] };

async function replay({ afterClaim = stop, exceptional = true, expected = stop, claimed = false } = {}) {
  let ack = { status: claimed ? 'stopping' : 'stop_requested', boundary_snapshot: {
    stale_founding_stop: exceptional, founding_boundary_board: expected,
  } };
  const control = { action: 'stop', request_id: 'synthetic-request' };
  const events = [], writes = [];
  const window = { __sorenGameState: structuredClone(claimed ? afterClaim : stop), unityInstance: {
    Quit() { events.push(`Quit:${window.__sorenGameState?.state}`); return undefined; },
  } };
  const document = { querySelector: () => ({}) };
  const page = { evaluate: async (callback, payload) =>
    vm.runInNewContext(`(${callback.toString()})(payload)`, { window, document, payload }) };
  const context = {
    console: { error() {} }, GAME_LIFECYCLE_DIR: 'synthetic',
    readGameLifecycleControl: () => control, lifecycleRequestIsCurrent: () => true,
    lifecycleAckAllows: () => true, lifecycleResourceMatches: (_control, resource) => Boolean(resource),
    readGameLifecycleResource: () => null, readGameLifecycleAck: () => ack,
    lifecycleImproveStillActive: () => ({ active: false }),
    sharedOverlayReady: async () => ({ ready: true }),
    lifecycleStopRequestStillCurrent: () => true, lifecycleStopFenceStillCurrent: () => true,
    lifecycleStopWriteStillCurrent: () => true,
    getGameState: async () => { events.push(`observe:${window.__sorenGameState?.state}`); return window.__sorenGameState; },
    claimLifecycleStop: async () => { events.push('claim'); ack = { ...ack, status: 'stopping' };
      window.__sorenGameState = structuredClone(afterClaim); return { ok: true }; },
    withTimeout: promise => promise,
    restoreGameOnlyRuntime: async () => { events.push('restore'); return { ok: true }; },
    closeGameOnlyRuntime: async () => { events.push('close'); return { ok: true }; },
    writeGameLifecycleResource: (_control, status, evidence) => { writes.push({ status, ...evidence }); return writes.at(-1); },
  };
  const processControl = vm.runInNewContext(`(${source})`, context);
  const result = await processControl(page, {
    markLifecycleIrreversible: () => events.push('mark'),
    clearLifecycleIrreversible: () => events.push('clear'),
    externalGameAudio: { shutdownAndWait: async () => { events.push('audio'); return { ok: true }; } },
  });
  return { result, events, writes, window };
}

test('STOP observation followed by successful claim and MOVE cannot Quit or stop resources', async () => {
  const r = await replay({ afterClaim: { ...stop, state: 'MOVE' } });
  assert.deepEqual(r.events.slice(0, 2), ['observe:STOP', 'claim']);
  assert.ok(!r.events.some(x => /^(Quit|restore|audio|close)/.test(x)), JSON.stringify(r.events));
  assert.equal(r.window.__sorenGameLifecycleStopping, undefined);
  assert.equal(r.result.status, 'failed');
  assert.equal(r.writes.at(-1).resource_changed, false);
  assert.equal(r.writes.at(-1).quit_called, false);
});

for (const [name, afterClaim] of [
  ['board change', { ...stop, pieces: [{ type: 17, x: 2 }] }],
  ['counter change', { ...stop, makeSorenCount: 2 }],
  ['missing state', null], ['invalid counter', { ...stop, makeSorenCount: NaN }],
]) {
  test(`post-claim ${name} rejects exceptional stop`, async () => {
    const r = await replay({ afterClaim });
    assert.ok(!r.events.some(x => /^(Quit|restore|audio|close)/.test(x)), JSON.stringify(r.events));
    assert.equal(r.result.status, 'failed');
  });
}

test('already claimed exceptional stop still checks current board and missing expectation fails closed', async () => {
  for (const options of [{ claimed: true, afterClaim: { ...stop, state: 'MOVE' } }, { expected: null }]) {
    const r = await replay(options);
    assert.ok(!r.events.some(x => /^(Quit|restore|audio|close)/.test(x)), JSON.stringify(r.events));
    assert.equal(r.result.status, 'failed');
  }
});

test('identical STOP passes the atomic gate and completes explicit resource stop', async () => {
  const r = await replay();
  assert.equal(r.result.status, 'stopped');
  assert.equal(r.events.filter(x => x.startsWith('Quit')).length, 1);
  assert.ok(r.events.includes('audio') && r.events.includes('close'));
});

test('ordinary terminal stop keeps its existing contract', async () => {
  const r = await replay({ exceptional: false, expected: null, afterClaim: { state: 'GAMEOVER' } });
  assert.equal(r.result.status, 'stopped');
  assert.ok(r.events.includes('Quit:GAMEOVER'));
});

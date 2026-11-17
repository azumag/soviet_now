import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { positive, jobsFor, summarize, snapshotPolicies, runGame, abortable, closeLab } from '../tools/simulation_lab.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const complete = (arm, seed, extra = {}) => ({ arm, seed, status: 'GAMEOVER', evidence_complete: true,
  seeded: true, policy_bundle: arm, queue: [1, 2, 3], score: arm === 'A' ? 10 : 15, max_type: 13, makeSorenCount: 0, ...extra });

test('bounded slot and speed arguments', () => {
  assert.equal(positive('4', 'slots', 4), 4);
  for (const x of ['0', '-1', 'NaN', '1.2', 'Infinity', '5']) assert.throws(() => positive(x, 'slots', 4));
});
test('alternates pair order by seed', () => {
  assert.deepEqual(jobsFor([1, 2], true), [{ seed: 1, arm: 'A' }, { seed: 1, arm: 'B' }, { seed: 2, arm: 'B' }, { seed: 2, arm: 'A' }]);
});
test('only complete matched seeds contribute to comparison', () => {
  const results = [complete('A', 1), complete('B', 1, { status: 'timeout' }), complete('A', 2), complete('B', 2)];
  const report = summarize(results, true, [1, 2]);
  assert.equal(report.comparison_valid, false);
  assert.equal(report.paired_games, 1);
  assert.equal(report.arms.A.games, 1);
  assert.deepEqual(report.paired_deltas, [{ seed: 2, score_delta: 5 }]);
});
test('duplicate arms, mixed bundles, unseeded and mismatched queues invalidate pairs', () => {
  for (const results of [
    [complete('A', 1), complete('A', 1), complete('B', 1)],
    [complete('A', 1), complete('B', 1, { seeded: false })],
    [complete('A', 1), complete('B', 1, { queue: [1, 4] })],
    [complete('A', 1), complete('B', 1), complete('A', 2, { policy_bundle: 'changed' }), complete('B', 2)],
  ]) assert.equal(summarize(results, true, [1]).comparison_valid, false);
});
test('terminal artifacts must be complete and error-free', () => {
  for (const extra of [{ evidence_complete: false }, { error: 'screenshot failed' }]) {
    assert.equal(summarize([complete('A', 1, extra)]).completed, 0);
  }
});
test('longer games compare the common prefix of their piece queues', () => {
  assert.equal(summarize([complete('A', 1), complete('B', 1, { queue: [1, 2, 3, 4] })], true, [1]).comparison_valid, true);
});
test('snapshot freezes sources and rejects malicious or symlinked policies before import', () => {
  fs.mkdirSync(path.join(ROOT, 'tmp'), { recursive: true });
  const tmp = fs.mkdtempSync(path.join(ROOT, 'tmp/simulation-unit-'));
  try {
    const policy = path.join(tmp, 'policy.py');
    fs.writeFileSync(policy, 'def decide(gs, an):\n    return {"x": 0, "reason": "test"}\n');
    const first = path.join(tmp, 'first'); fs.mkdirSync(first);
    const snapshot = snapshotPolicies(first, { A: policy });
    const saved = fs.readFileSync(path.join(snapshot, 'policies/A.py'), 'utf8');
    fs.writeFileSync(policy, 'open("/never-execute-this-policy", "w")\ndef decide(gs, an): return {"x": 0}\n');
    assert.equal(fs.readFileSync(path.join(snapshot, 'policies/A.py'), 'utf8'), saved);
    const second = path.join(tmp, 'second'); fs.mkdirSync(second);
    assert.throws(() => snapshotPolicies(second, { A: policy }), /AST validation rejected/);
    const third = path.join(tmp, 'third'); fs.mkdirSync(third);
    const link = path.join(tmp, 'link.py'); fs.symlinkSync(policy, link);
    assert.throws(() => snapshotPolicies(third, { A: link }), /regular files/);
  } finally { fs.rmSync(tmp, { recursive: true, force: true }); }
});
test('context setup and close failures still close workers', async () => {
  for (const failContext of [true, false]) {
    let closed = 0;
    const args = { out: '/does-not-exist', snapshot: '', image: '' };
    const browser = { newContext: async () => {
      if (failContext) throw new Error('context setup failed');
      return { close: async () => { throw new Error('context close failed'); } };
    } };
    const rec = await runGame(browser, '', { arm: 'A', seed: 1 }, args, new AbortController().signal,
      () => ({ close: async () => { closed++; } }));
    assert.equal(closed, 1);
    assert.equal(rec.status, 'error');
  }
});
test('browser close failure still closes server and removes signal listeners', async () => {
  let serverClosed = false;
  const stop = () => {};
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
  await assert.rejects(closeLab({ close: async () => { throw new Error('browser failed'); } }, {
    server: { closeAllConnections() {}, close(cb) { serverClosed = true; cb(); } },
  }, stop), /cleanup failed/);
  assert.equal(serverClosed, true);
  assert.equal(process.listeners('SIGINT').includes(stop), false);
  assert.equal(process.listeners('SIGTERM').includes(stop), false);
});
test('abort interrupts initialization waits without waiting for page timeout', async () => {
  const signal = new AbortController();
  const waiting = abortable(new Promise(() => {}), signal.signal);
  signal.abort();
  await assert.rejects(waiting, { name: 'AbortError' });
});
test('already-aborted operations cannot leak late rejections', async () => {
  const signal = new AbortController(); signal.abort();
  let reject;
  const operation = new Promise((_, r) => { reject = r; });
  await assert.rejects(abortable(operation, signal.signal), { name: 'AbortError' });
  reject(new Error('late worker shutdown'));
  await new Promise(resolve => setImmediate(resolve));
});
test('aborting a hung page initialization closes both context and policy', async () => {
  const out = fs.mkdtempSync(path.join(ROOT, 'tmp/simulation-abort-unit-'));
  const signal = new AbortController();
  let contextClosed = false, workerClosed = false;
  const context = { route: async () => {}, addInitScript: async () => {},
    newPage: async () => ({ goto: () => new Promise(() => {}) }),
    close: async () => { contextClosed = true; } };
  const worker = { request: async () => ({}), close: async () => { workerClosed = true; } };
  const timer = setTimeout(() => signal.abort(), 20);
  try {
    const result = await runGame({ newContext: async () => context }, 'http://127.0.0.1:1234',
      { arm: 'A', seed: 1 }, { out }, signal.signal, () => worker);
    assert.equal(result.status, 'interrupted');
    assert.equal(contextClosed, true); assert.equal(workerClosed, true);
  } finally { clearTimeout(timer); fs.rmSync(out, { recursive: true, force: true }); }
});

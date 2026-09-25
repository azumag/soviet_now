import test from 'node:test';
import assert from 'node:assert/strict';
import { LoopMetrics } from '../soren91/loop_metrics.mjs';

test('recovered measured failure remains in bounded sanitized history', async () => {
  let now = 0;
  const metrics = new LoopMetrics({ now: () => now });
  metrics.begin(1, 0);

  await assert.rejects(metrics.measure('capture', async () => {
    now += 10;
    throw new Error('capture-timeout');
  }));

  const failed = metrics.flush('error');
  assert.deepEqual(failed.lastFailure, { stage: 'capture', errorClass: 'capture-timeout' });
  assert.deepEqual(failed.failureHistory.records, [
    { sequence: 1, game: 1, turn: 0, stage: 'capture', errorClass: 'capture-timeout' },
  ]);

  metrics.begin(1, 0);
  now += 1001;
  const recovered = metrics.flush('observe');
  assert.equal(recovered.lastFailure, null);
  assert.deepEqual(recovered.failureHistory.records, failed.failureHistory.records);
});

test('failure history stays process-scoped and evicts oldest entries at capacity', async () => {
  let now = 0;
  const metrics = new LoopMetrics({ now: () => now });

  for (let turn = 0; turn < 20; turn++) {
    metrics.begin(turn < 10 ? 1 : 2, turn);
    const stage = turn % 2 === 0 ? 'capture' : 'input';
    const errorClass = stage === 'capture' ? 'capture-timeout' : 'input-stale-observation';
    await assert.rejects(metrics.measure(stage, async () => {
      now += 1;
      throw new Error(errorClass);
    }));
    metrics.flush('error');
    now += 1;
  }

  metrics.begin(2, 20);
  now += 1001;
  const recovered = metrics.flush('observe');

  assert.equal(recovered.failureHistory.capacity, 16);
  assert.equal(recovered.failureHistory.total, 20);
  assert.equal(recovered.failureHistory.evicted, 4);
  assert.equal(recovered.failureHistory.records.length, 16);
  assert.equal(recovered.failureHistory.records[0].sequence, 5);
  assert.equal(recovered.failureHistory.records.at(-1).sequence, 20);
  assert.deepEqual(
    new Set(recovered.failureHistory.records.map(record => record.errorClass)),
    new Set(['capture-timeout', 'input-stale-observation']),
  );
});

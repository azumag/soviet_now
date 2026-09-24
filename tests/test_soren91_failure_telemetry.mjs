import test from 'node:test';
import assert from 'node:assert/strict';
import { LoopMetrics } from '../soren91/loop_metrics.mjs';

test('loop metrics expose only allowlisted failure details for an error attempt', async () => {
  let now = 0;
  const metrics = new LoopMetrics({ now: () => now });
  metrics.begin(1, 0);

  await assert.rejects(
    metrics.measure('capture', async () => {
      now += 25;
      throw new Error('capture-timeout');
    }),
    /capture-timeout/,
  );

  const snapshot = metrics.flush('error');
  assert.deepEqual(snapshot.lastFailure, {
    stage: 'capture',
    errorClass: 'capture-timeout',
  });
});

test('unknown error text is redacted and recovered attempts do not retain old failures', async () => {
  let now = 0;
  const metrics = new LoopMetrics({ now: () => now });
  metrics.begin(1, 0);

  await assert.rejects(
    metrics.measure('capture', async () => {
      now += 10;
      throw new Error('private-token=/Users/example/secret');
    }),
  );
  const failed = metrics.flush('error');
  assert.deepEqual(failed.lastFailure, { stage: 'capture', errorClass: 'other' });
  assert.doesNotMatch(JSON.stringify(failed), /private-token|Users|secret/);

  metrics.begin(1, 0);
  now += 1001;
  const recovered = metrics.flush('observe');
  assert.equal(recovered.lastFailure, null);
});

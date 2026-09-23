import test from 'node:test';
import assert from 'node:assert/strict';
import { LoopMetrics } from '../soren91/loop_metrics.mjs';

const stages = (geometryBefore, screenshot, imageValidate, geometryAfter) => ({
  geometryBefore, screenshot, imageValidate, geometryAfter,
});

function fixture() {
  let time = 0;
  const m = new LoopMetrics({ now: () => time, write: () => {} });
  return {
    m,
    advance(ms) { time += ms; },
    capture(ms, captureStageMs) {
      return m.measure('capture', async () => { time += ms; return { captureStageMs }; });
    },
  };
}

test('pairs each successful capture with the sanitized observation reason without changing aggregate timing', async () => {
  const f = fixture();
  f.m.begin(1, 0); f.m.dropSent(); f.m.begin(1, 1);

  await f.capture(100, stages(10, 70, 1, 19));
  f.m.observe({ perception: { reason: 'preview-changed', queueTransition: 'same' } });
  await f.capture(120, stages(20, 75, 2, 23));
  f.m.observe({ perception: { reason: 'board-moving', queueTransition: 'advanced' } });
  f.m.dropSent();

  const record = f.m.flush('drop-sent').dropProfile.records[0];
  assert.deepEqual(record.captureStageMs, stages(30, 145, 3, 42));
  assert.equal(record.stageMs.capture, 220);
  assert.equal(record.phaseCalls.capture, 2);
  assert.deepEqual(record.observationCaptureRecords, [
    { observation: 1, reason: 'preview-changed', queueTransition: 'same',
      captureStageMs: stages(10, 70, 1, 19) },
    { observation: 2, reason: 'board-moving', queueTransition: 'advanced',
      captureStageMs: stages(20, 75, 2, 23) },
  ]);
  assert.equal(record.observationCaptureDropped, 0);
  assert.equal(record.dropAcceptance.confirmed, true);
  assert.equal(record.dropAcceptance.confirmReason, 'board-moving');
});

test('failed or malformed captures cannot reuse an earlier capture association or persist free-form data', async () => {
  const f = fixture();
  f.m.begin(1, 0); f.m.dropSent(); f.m.begin(1, 1);

  await f.capture(50, stages(10, 20, 1, 19));
  await assert.rejects(
    f.m.measure('capture', async () => { f.advance(30); throw new Error('private-token'); }),
  );
  f.m.observe({ perception: { reason: 'board-moving', queueTransition: 'same' } });

  await f.capture(40, { geometryBefore: 10, screenshot: 'private-token', imageValidate: 1, geometryAfter: 9 });
  f.m.observe({ perception: { reason: 'untrusted-free-form-private-token', queueTransition: 'bogus' } });
  f.m.dropSent();

  const snapshot = f.m.flush('drop-sent');
  const record = snapshot.dropProfile.records[0];
  assert.deepEqual(record.observationCaptureRecords, []);
  assert.equal(record.reasonCounts.other, 1);
  assert.equal(record.reasonCounts['board-moving'], 1);
  assert.equal(JSON.stringify(snapshot).includes('private-token'), false);
});

test('per-observation capture evidence is bounded to a tail of 16 records and snapshots deep-clone it', async () => {
  const f = fixture();
  f.m.begin(1, 0); f.m.dropSent(); f.m.begin(1, 1);

  for (let i = 1; i <= 20; i++) {
    await f.capture(10, stages(1, 7, 0, 2));
    f.m.observe({ perception: { reason: 'stable', queueTransition: 'same' } });
  }
  f.m.dropSent();

  const snapshot = f.m.flush('drop-sent');
  const record = snapshot.dropProfile.records[0];
  assert.equal(record.observationCaptureRecords.length, 16);
  assert.equal(record.observationCaptureDropped, 4);
  assert.equal(record.observationCaptureRecords[0].observation, 5);
  assert.equal(record.observationCaptureRecords.at(-1).observation, 20);

  snapshot.dropProfile.records[0].observationCaptureRecords[0].captureStageMs.screenshot = 999;
  assert.equal(f.m.profileRecords[0].observationCaptureRecords[0].captureStageMs.screenshot, 7);
});

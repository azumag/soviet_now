import test from 'node:test';
import assert from 'node:assert/strict';
import { createCanvasIO } from '../soren91/realtime_io.mjs';
import { CAPTURE_PROFILE_STAGES, LoopMetrics } from '../soren91/loop_metrics.mjs';

const geometry = {
  canvasId: 1, documentId: 100, x: 10, y: 20, width: 800, height: 450,
  scrollX: 0, scrollY: 0, dpr: 1, viewportWidth: 1280, viewportHeight: 720, viewportScale: 1,
};

function png(width = 800, height = 450) {
  const buffer = Buffer.alloc(24);
  Buffer.from('89504e470d0a1a0a', 'hex').copy(buffer);
  buffer.write('IHDR', 12);
  buffer.writeUInt32BE(width, 16);
  buffer.writeUInt32BE(height, 20);
  return buffer;
}

test('canvas capture exposes fixed phase timings without changing capture freshness semantics', async () => {
  let clock = 0;
  const session = {
    async send(method) {
      assert.equal(method, 'Runtime.evaluate');
      clock += 7;
      return { result: { value: geometry } };
    },
    async detach() {},
  };
  const page = {
    context: () => ({ async newCDPSession() { return session; } }),
    async screenshot() { clock += 13; return png(); },
  };
  const frame = await createCanvasIO({ now: () => clock }).capture(page);
  assert.deepEqual(Object.keys(frame.captureStageMs), CAPTURE_PROFILE_STAGES);
  assert.deepEqual(frame.captureStageMs, {
    geometryBefore: 7, screenshot: 13, imageValidate: 0, geometryAfter: 7,
  });
  assert.equal(frame.capturedAt, 7);
  assert.equal(frame.captureMs, 20);
});

test('drop profile accepts only complete numeric capture timing objects and never persists free text', async () => {
  let clock = 0;
  const metrics = new LoopMetrics({ now: () => clock });
  metrics.begin(1, 0);
  metrics.dropSent();
  metrics.begin(1, 1);
  await metrics.measure('capture', async () => {
    clock += 28;
    return { captureStageMs: { geometryBefore: 7, screenshot: 13, imageValidate: 1, geometryAfter: 7 } };
  });
  clock += 2;
  metrics.dropSent();
  let record = metrics.flush('drop-sent').dropProfile.records.at(-1);
  assert.deepEqual(record.captureStageMs, {
    geometryBefore: 7, screenshot: 13, imageValidate: 1, geometryAfter: 7,
  });

  metrics.begin(1, 2);
  await metrics.measure('capture', async () => {
    clock += 5;
    return { captureStageMs: { geometryBefore: 1, screenshot: 'secret-provider', imageValidate: 1, geometryAfter: 1 } };
  });
  metrics.dropSent();
  record = metrics.flush('drop-sent').dropProfile.records.at(-1);
  assert.deepEqual(record.captureStageMs, {
    geometryBefore: 0, screenshot: 0, imageValidate: 0, geometryAfter: 0,
  });
  assert.equal(JSON.stringify(record).includes('secret-provider'), false);
});

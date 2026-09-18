import test from 'node:test';
import assert from 'node:assert/strict';
import { LoopMetrics } from '../soren91/loop_metrics.mjs';

function fixture() {
  let time = 0;
  const m = new LoopMetrics({ now: () => time });
  return {
    m,
    advance(ms) { time += ms; },
    at(ms) { time = ms; },
    record() { return m.flush('drop-sent').dropProfile.records.at(-1); },
  };
}

test('queue advance confirms the preceding sent drop without adding gameplay waits', () => {
  const f = fixture();
  f.m.begin(1, 0);
  f.m.dropSent();
  f.m.begin(1, 1);
  f.advance(240);
  f.m.observe({ perception: {
    reason: 'stable-slow-advance-temporal-next',
    queueTransition: 'advanced',
  } });
  f.advance(60);
  f.m.dropSent();

  const snapshot = f.m.flush('drop-sent');
  const record = snapshot.dropProfile.records[0];
  assert.equal(record.dropAcceptance.confirmed, true);
  assert.equal(record.dropAcceptance.confirmLatencyMs, 240);
  assert.equal(record.dropAcceptance.confirmObservation, 1);
  assert.equal(record.dropAcceptance.confirmReason, 'stable-slow-advance');
  assert.deepEqual(record.dropAcceptance.transitionCounts, { advanced: 1, same: 0, unknown: 0 });
  assert.equal(snapshot.dropProfile.acceptedDropsMeasured, false);
  assert.deepEqual(snapshot.dropProfile.acceptanceEvidence, {
    measured: true,
    basis: 'post-send-queue-transition',
    authoritativeGameAcceptance: false,
    changesGameplay: false,
  });
});

test('same or unknown queue transitions remain unconfirmed instead of inventing acceptance', () => {
  const f = fixture();
  f.m.begin(4, 10);
  f.m.dropSent();
  f.m.begin(4, 11);
  f.advance(100);
  f.m.observe({ perception: { reason: 'board-moving', queueTransition: 'same' } });
  f.advance(100);
  f.m.observe({ perception: { reason: 'private-error-text', queueTransition: 'unexpected-value' } });
  f.advance(100);
  f.m.dropSent();

  const record = f.record();
  assert.equal(record.dropAcceptance.confirmed, false);
  assert.equal(record.dropAcceptance.confirmLatencyMs, null);
  assert.equal(record.dropAcceptance.confirmObservation, null);
  assert.equal(record.dropAcceptance.confirmReason, null);
  assert.equal(record.dropAcceptance.observations, 2);
  assert.deepEqual(record.dropAcceptance.transitionCounts, { advanced: 0, same: 1, unknown: 1 });
  assert.equal(JSON.stringify(record).includes('private-error-text'), false);
});

test('only the first observed advance sets confirmation latency', () => {
  const f = fixture();
  f.m.begin(2, 3);
  f.m.dropSent();
  f.m.begin(2, 4);
  f.advance(80);
  f.m.observe({ perception: { reason: 'confirm-frame', queueTransition: 'advanced' } });
  f.advance(120);
  f.m.observe({ perception: { reason: 'stable', queueTransition: 'advanced' } });
  f.advance(50);
  f.m.dropSent();

  const acceptance = f.record().dropAcceptance;
  assert.equal(acceptance.confirmed, true);
  assert.equal(acceptance.confirmLatencyMs, 80);
  assert.equal(acceptance.confirmObservation, 1);
  assert.equal(acceptance.confirmReason, 'confirm-frame');
  assert.deepEqual(acceptance.transitionCounts, { advanced: 2, same: 0, unknown: 0 });
});

test('backwards clock marks the interval invalid and never reports negative confirmation latency', () => {
  const f = fixture();
  f.at(100);
  f.m.begin(3, 0);
  f.m.dropSent();
  f.m.begin(3, 1);
  f.at(50);
  f.m.observe({ perception: { reason: 'stable', queueTransition: 'advanced' } });
  f.at(120);
  f.m.dropSent();

  const record = f.record();
  assert.equal(record.dropAcceptance.confirmLatencyMs, 0);
  assert.equal(record.accountingValid, false);
});

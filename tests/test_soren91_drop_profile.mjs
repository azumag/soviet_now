import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, writeFileSync, statSync, readdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';
import { LoopMetrics, writeMetricsAtomically } from '../soren91/loop_metrics.mjs';
import { summarizeDropProfiles, formatDropProfileReport } from '../soren91/summarize_drop_profile.mjs';

function fixture(write) {
  let time = 0;
  const m = new LoopMetrics({ now: () => time, write });
  return { m, advance: n => { time += n; }, at: n => { time = n; },
    phase: (name, duration) => m.measure(name, async () => { time += duration; }),
    snapshot: () => m.flush('drop-sent'),
  };
}
const total = record => Object.values(record.stageMs).reduce((a, b) => a + b, 0);
const last = f => f.snapshot().dropProfile.records.at(-1);

test('drop interval includes previous post-drop ranking, retries and unmeasured gaps, not first-drop setup', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); await f.phase('capture', 5000); m.dropSent();
  assert.equal(f.snapshot().dropProfile.records.length, 0);
  await f.phase('ranking', 1200); f.advance(7); m.flush('drop-sent');
  m.begin(1, 1);
  await f.phase('capture', 300); await f.phase('analyze', 40);
  m.observe({ perception: { reason: 'board-moving' } });
  await f.phase('poll', 200); m.begin(1, 1);
  await f.phase('capture', 300); await f.phase('analyze', 40);
  m.observe({ perception: { reason: 'stable' } });
  await f.phase('decide', 10); await f.phase('input', 200); m.dropSent();
  const snapshot = f.snapshot(), r = snapshot.dropProfile.records[0];
  assert.equal(r.durationMs, 2297); assert.equal(total(r), r.durationMs);
  assert.equal(r.stageMs.ranking, 1200); assert.equal(r.stageMs.unattributed, 7);
  assert.equal(r.stageMs.capture, 600); assert.equal(r.phaseCalls.capture, 2);
  assert.equal(r.observations, 2); assert.equal(r.reasonCounts['board-moving'], 1);
  assert.equal(snapshot.stageMs.ranking, 0); // Legacy turn API remains turn-scoped.
  assert.equal(snapshot.dropSentIntervalMs.last, r.durationMs);
});

test('HOLD input is separate, including its fixed animation wait, and never resets drop boundary', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  await f.phase('input', 300); m.holdSent(); m.flush('hold-sent');
  m.begin(1, 1); await f.phase('capture', 150); await f.phase('analyze', 30);
  await f.phase('input', 200); m.dropSent();
  const r = last(f);
  assert.equal(r.durationMs, 680); assert.equal(r.stageMs.holdInput, 300);
  assert.equal(r.stageMs.input, 200); assert.equal(r.phaseCalls.input, 1);
  assert.equal(r.phaseCalls.holdInput, 1); assert.equal(r.holds, 1);
  assert.equal(r.accountingErrorMs, 0);
});

test('cooldown counts only actual measured sleep, never the configured 1200ms again', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  await f.phase('capture', 800); await f.phase('cooldown', 200);
  await f.phase('capture', 300); await f.phase('input', 200); m.dropSent();
  const r = last(f);
  assert.equal(r.durationMs, 1500); assert.equal(r.stageMs.capture, 1100);
  assert.equal(r.stageMs.cooldown, 200); assert.equal(total(r), 1500);
});

test('nested and concurrent timing is exposed as overlap, never double-counted', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  await m.measure('ranking', async () => {
    f.advance(100); await f.phase('capture', 200); f.advance(50);
  });
  m.dropSent(); const r = last(f);
  assert.equal(r.stageMs.ranking, 150); assert.equal(r.stageMs.overlap, 200);
  assert.equal(total(r), 350); assert.equal(r.accountingValid, true);
});

test('a measurement crossing a drop marker is split at the marker', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  await m.measure('input', async () => { f.advance(20); m.dropSent(); f.advance(30); });
  m.begin(1, 2); f.advance(5); m.dropSent();
  const records = f.snapshot().dropProfile.records;
  assert.equal(records[0].stageMs.input, 20);
  assert.equal(records[1].stageMs.input, 30);
  assert.equal(records[1].stageMs.unattributed, 5);
  assert.ok(records.every(r => total(r) === r.durationMs));
});

test('failed work and retry backoff are included without raw exception text', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  await assert.rejects(m.measure('capture', async () => { f.advance(50); throw Error('private-token'); }));
  f.advance(1000); m.flush('error');
  m.observe({ perception: { reason: 'private-token' } });
  await f.phase('input', 200); m.dropSent();
  const snapshot = f.snapshot(), r = snapshot.dropProfile.records[0];
  assert.equal(r.durationMs, 1250); assert.equal(r.stageMs.unattributed, 1000);
  assert.equal(r.errors, 1); assert.equal(r.reasonCounts.other, 1);
  assert.equal(JSON.stringify(snapshot).includes('private-token'), false);
});

test('round boundaries exclude waiting and preserve completed records from preceding rounds', () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1); f.advance(100); m.dropSent();
  f.advance(60000); m.begin(2, 0); f.advance(5000); m.dropSent();
  assert.equal(f.snapshot().dropSentIntervalMs.samples, 0);
  m.begin(2, 1); f.advance(200); m.dropSent();
  assert.deepEqual(f.snapshot().dropProfile.records.map(r => [r.game, r.durationMs]), [[1, 100], [2, 200]]);
});

test('bounded ring reports evictions and does not misrepresent a retained tail as an entire match', () => {
  const f = fixture(), { m } = f;
  for (let i = 0; i < 300; i++) { m.begin(1, i); f.advance(100); m.dropSent(); }
  const p = f.snapshot().dropProfile;
  assert.equal(p.records.length, 128); assert.equal(p.totalSamples, 299);
  assert.equal(p.evictedSamples, 171); assert.equal(p.records[0].sample, 172);
  assert.equal(summarizeDropProfiles([f.snapshot()]).missingSamples, 171);
});

test('legacy turn counters, sanitized outcomes and drop interval contract remain compatible', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); await f.phase('capture', 5000);
  m.observe({ perception: { reason: 'unknown-current' } }); m.holdSent();
  m.begin(1, 0); await f.phase('capture', 5000);
  m.observe({ perception: { reason: 'stable-slow-advance-temporal-next' } });
  m.dropSent(); const first = f.snapshot();
  assert.equal(first.observations, 2); assert.equal(first.holds, 1);
  assert.equal(first.stageMs.capture, 10000); assert.equal(first.dropSentIntervalMs.samples, 0);
  m.begin(1, 1); f.advance(20000); m.dropSent();
  assert.equal(f.snapshot().dropSentIntervalMs.last, 20000);
  assert.equal(m.flush('bad-outcome').outcome, 'error');
});

test('atomic output persists profile at 0600; a failing sink never prevents a drop', () => {
  const dir = mkdtempSync(join(tmpdir(), 's91-profile-'));
  try {
    const path = join(dir, 'metrics.json');
    const f = fixture(value => writeMetricsAtomically(path, value)), { m } = f;
    m.begin(1, 0); m.dropSent(); m.begin(1, 1); f.advance(100); m.dropSent(); f.snapshot();
    assert.equal(JSON.parse(readFileSync(path, 'utf8')).dropProfile.records.length, 1);
    assert.equal(statSync(path).mode & 0o777, 0o600); assert.deepEqual(readdirSync(dir), ['metrics.json']);
    const fail = fixture(() => { throw Error('disk-full'); });
    fail.m.begin(1, 0); fail.m.dropSent(); assert.doesNotThrow(() => fail.snapshot());
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('snapshot callbacks cannot corrupt the retained profile ring', () => {
  const f = fixture(value => { if (value.dropProfile.records[0]) value.dropProfile.records[0].stageMs.capture = 999; });
  f.m.begin(1, 0); f.m.dropSent(); f.m.begin(1, 1); f.advance(100); f.m.dropSent(); f.snapshot();
  assert.equal(f.m.profileRecords[0].stageMs.capture, 0);
});

test('invalid/backwards clocks are excluded rather than silently reported as valid latency', () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); f.at(100); m.dropSent(); m.begin(1, 1); f.at(50); m.dropSent();
  const report = summarizeDropProfiles([f.snapshot()]);
  assert.equal(report.samples, 0); assert.equal(report.excludedSamples, 1);
  assert.equal(report.intervalSeconds.mean, null);
});

test('report deduplicates overlapping snapshots and includes exact mean phase contributions', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1); await f.phase('capture', 100); m.dropSent();
  const first = f.snapshot();
  m.begin(1, 2); await f.phase('input', 300); m.holdSent(); await f.phase('input', 100); m.dropSent();
  const r = summarizeDropProfiles([first, f.snapshot()]);
  assert.equal(r.samples, 2); assert.equal(r.duplicateSamples, 1); assert.equal(r.missingSamples, 0);
  assert.equal(r.intervalSeconds.mean, 0.25); assert.equal(r.intervalSeconds.p95, 0.4);
  assert.equal(r.groups.withHold.samples, 1); assert.equal(r.groups.withoutHold.samples, 1);
  assert.equal(r.phases.capture.sharePercent, 20); assert.equal(r.phases.holdInput.sharePercent, 60);
  assert.equal(r.acceptedDropsMeasured, false);
});

test('reports distinguish independent process sessions and pre/post turn-10 intervals', () => {
  const a = fixture(), b = fixture();
  for (const f of [a, b]) { f.m.begin(1, 10); f.m.dropSent(); f.m.begin(1, 11); f.advance(100); f.m.dropSent(); }
  const r = summarizeDropProfiles([a.snapshot(), b.snapshot()]);
  assert.equal(r.samples, 2); assert.equal(r.sessionCount, 2); assert.equal(r.games.length, 2);
  assert.equal(r.groups.beforeTurn10.samples, 0); assert.equal(r.groups.fromTurn10.samples, 2);
});

test('missing, malformed, conflicting or non-reconciling evidence is never accepted as measurement', () => {
  assert.throws(() => summarizeDropProfiles([{ schemaVersion: 1 }]), /drop-profile/);
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1); f.advance(100); m.dropSent();
  const good = f.snapshot();
  for (const corrupt of [
    x => { x.dropProfile.records[0].stageMs.capture = NaN; },
    x => { x.dropProfile.records[0].durationMs = 999; },
    x => { x.dropProfile.records[0].sample = 9; },
    x => { x.dropProfile.acceptedDropsMeasured = true; },
    x => { x.dropProfile.totalSamples = 10; },
  ]) { const bad = structuredClone(good); corrupt(bad); assert.throws(() => summarizeDropProfiles([bad])); }
  const conflict = structuredClone(good); conflict.dropProfile.records[0].observations++;
  assert.throws(() => summarizeDropProfiles([good, conflict]), /conflicting/);
});

test('zero complete intervals show N/A, not synthetic zero-second measurements', () => {
  const f = fixture(); f.m.begin(1, 0); f.m.dropSent();
  const r = summarizeDropProfiles([f.snapshot()]);
  assert.equal(r.samples, 0); assert.equal(r.intervalSeconds.mean, null);
  assert.match(formatDropProfileReport(r), /TOTAL\tN\/A/);
});

test('CLI is read-only, supports JSON, and rejects missing evidence with sanitized errors', () => {
  const dir = mkdtempSync(join(tmpdir(), 's91-report-'));
  const script = fileURLToPath(new URL('../soren91/summarize_drop_profile.mjs', import.meta.url));
  try {
    const f = fixture(); f.m.begin(1, 0); f.m.dropSent(); f.m.begin(1, 1); f.advance(100); f.m.dropSent();
    const path = join(dir, 'fixture.json'), text = JSON.stringify(f.snapshot()); writeFileSync(path, text);
    const run = spawnSync(process.execPath, [script, '--json', path], { encoding: 'utf8' });
    assert.equal(run.status, 0); assert.equal(JSON.parse(run.stdout).samples, 1);
    assert.equal(readFileSync(path, 'utf8'), text); assert.deepEqual(readdirSync(dir), ['fixture.json']);
    const bad = spawnSync(process.execPath, [script, join(dir, 'private-path')], { encoding: 'utf8' });
    assert.equal(bad.status, 1); assert.equal(bad.stderr.includes('private-path'), false);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});


test('parallel operations are measured once and both operation counts survive', async () => {
  const f = fixture(), { m } = f;
  m.begin(1, 0); m.dropSent(); m.begin(1, 1);
  let finishA, finishB;
  const a = m.measure('capture', () => new Promise(resolve => { finishA = resolve; }));
  f.advance(10);
  const b = m.measure('analyze', () => new Promise(resolve => { finishB = resolve; }));
  f.advance(20); finishA(); await a;
  f.advance(30); finishB(); await b;
  m.dropSent(); const r = last(f);
  assert.equal(r.stageMs.capture, 10); assert.equal(r.stageMs.overlap, 20);
  assert.equal(r.stageMs.analyze, 30); assert.equal(total(r), 60);
  assert.equal(r.phaseCalls.capture, 1); assert.equal(r.phaseCalls.analyze, 1);
});

test('maximum retained snapshot size stays bounded without retaining screenshot or state payloads', () => {
  const f = fixture(), { m } = f;
  for (let i = 0; i < 300; i++) {
    m.begin(1, i); f.advance(100);
    m.observe({ pieces: ['private-game-state'], perception: { reason: 'stable' } }); m.dropSent();
  }
  const text = JSON.stringify(f.snapshot());
  assert.ok(Buffer.byteLength(text) < 256 * 1024);
  assert.equal(text.includes('private-game-state'), false);
});

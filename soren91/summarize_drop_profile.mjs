#!/usr/bin/env node
/** Read-only report of complete SENT-to-SENT intervals, never acceptance rate. */
import { readFileSync, statSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { DROP_PROFILE_STAGES } from './loop_metrics.mjs';

const CALLS = DROP_PROFILE_STAGES.filter(s => !['overlap', 'unattributed'].includes(s));
const REASONS = ['unknown-current', 'uncalibrated', 'invalid-board', 'confirm-frame',
  'preview-changed', 'board-moving', 'stable', 'stable-slow-advance', 'non-move', 'other'];
const nonnegative = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const integer = value => Number.isSafeInteger(value) && value >= 0;
const sum = values => values.reduce((a, b) => a + b, 0);
const round = value => Math.round(value * 1000) / 1000;
function stats(values) {
  if (!values.length) return { mean: null, p50: null, p95: null, max: null };
  const sorted = [...values].sort((a, b) => a - b);
  const p = value => sorted[Math.ceil(sorted.length * value) - 1];
  return { mean: round(sum(values) / values.length), p50: round(p(0.5)),
    p95: round(p(0.95)), max: round(sorted.at(-1)) };
}
function numericMap(value, names, check) {
  if (!value || names.some(key => !check(value[key]))) throw new Error('invalid-profile-record');
  return Object.fromEntries(names.map(key => [key, value[key]]));
}
function cleanRecord(record) {
  if (!record || !['sample', 'game', 'fromTurn', 'toTurn', 'observations', 'holds', 'errors'].every(k => integer(record[k]))
      || record.sample === 0 || !integer(record.endedAtMs) || !nonnegative(record.durationMs)
      || typeof record.accountingValid !== 'boolean'
      || !Number.isFinite(record.accountingErrorMs)) throw new Error('invalid-profile-record');
  const stageMs = numericMap(record.stageMs, DROP_PROFILE_STAGES, nonnegative);
  const phaseCalls = numericMap(record.phaseCalls, CALLS, integer);
  const reasonCounts = numericMap(record.reasonCounts, REASONS, integer);
  const error = record.durationMs - sum(Object.values(stageMs));
  if (Math.abs(error - record.accountingErrorMs) > 0.11) throw new Error('invalid-profile-accounting');
  return { sample: record.sample, game: record.game, fromTurn: record.fromTurn, toTurn: record.toTurn,
    endedAtMs: record.endedAtMs, durationMs: record.durationMs, stageMs, phaseCalls, reasonCounts,
    observations: record.observations, holds: record.holds, errors: record.errors,
    accountingValid: record.accountingValid && Math.abs(error) <= 1 };
}
function summarizeRows(records) {
  const durationTotal = sum(records.map(r => r.durationMs));
  return {
    samples: records.length,
    intervalSeconds: stats(records.map(r => r.durationMs / 1000)),
    phases: Object.fromEntries(DROP_PROFILE_STAGES.map(stage => [stage, {
      seconds: stats(records.map(r => r.stageMs[stage] / 1000)),
      sharePercent: durationTotal > 0 ? round(sum(records.map(r => r.stageMs[stage])) / durationTotal * 100) : null,
      callsPerInterval: CALLS.includes(stage) ? stats(records.map(r => r.phaseCalls[stage])) : null,
    }])),
    observationsPerInterval: stats(records.map(r => r.observations)),
    holdIntervals: records.filter(r => r.holds > 0).length,
    errorIntervals: records.filter(r => r.errors > 0).length,
    overlapIntervals: records.filter(r => r.stageMs.overlap > 0).length,
    reasonCounts: Object.fromEntries(REASONS.map(reason => [reason, sum(records.map(r => r.reasonCounts[reason]))])),
  };
}
export function summarizeDropProfiles(snapshots) {
  const unique = new Map(), sessions = new Map();
  let duplicateSamples = 0;
  for (const snapshot of snapshots) {
    const p = snapshot?.dropProfile;
    if (snapshot?.schemaVersion !== 1 || p?.schemaVersion !== 1 || p.basis !== 'sent-to-sent'
        || p.acceptedDropsMeasured !== false || !/^[a-f0-9-]{36}$/.test(p.session || '')
        || p.capacity !== 128 || !integer(p.totalSamples) || !integer(p.evictedSamples)
        || !Array.isArray(p.records) || p.records.length > p.capacity
        || p.totalSamples !== p.evictedSamples + p.records.length) throw new Error('missing-or-invalid-drop-profile');
    sessions.set(p.session, Math.max(sessions.get(p.session) || 0, p.totalSamples));
    let previous = p.evictedSamples;
    for (const record of p.records) {
      const clean = cleanRecord(record);
      if (clean.sample !== previous + 1) throw new Error('invalid-profile-sequence');
      previous = clean.sample;
      const key = `${p.session}:${clean.sample}`;
      if (unique.has(key)) {
        if (JSON.stringify(unique.get(key)) !== JSON.stringify({ session: p.session, ...clean })) {
          throw new Error('conflicting-profile-sample');
        }
        duplicateSamples++;
      } else unique.set(key, { session: p.session, ...clean });
    }
  }
  const all = [...unique.values()];
  const usable = all.filter(r => r.accountingValid && r.durationMs > 0);
  const games = new Map();
  for (const r of usable) {
    const key = `${r.session}:${r.game}`;
    if (!games.has(key)) games.set(key, []);
    games.get(key).push(r);
  }
  return {
    basis: 'sent-to-sent', acceptedDropsMeasured: false,
    snapshotCount: snapshots.length, sessionCount: sessions.size,
    latestDropAtMs: all.length ? Math.max(...all.map(r => r.endedAtMs)) : null,
    duplicateSamples, missingSamples: sum([...sessions.values()]) - all.length,
    excludedSamples: all.length - usable.length,
    ...summarizeRows(usable),
    groups: {
      withoutHold: summarizeRows(usable.filter(r => r.holds === 0)),
      withHold: summarizeRows(usable.filter(r => r.holds > 0)),
      beforeTurn10: summarizeRows(usable.filter(r => r.fromTurn < 10)),
      fromTurn10: summarizeRows(usable.filter(r => r.fromTurn >= 10)),
    },
    games: [...games.values()].map(rows => ({ session: rows[0].session, game: rows[0].game,
      ...summarizeRows(rows) })),
    intervals: usable,
  };
}
export function formatDropProfileReport(report) {
  const f = n => n == null ? 'N/A' : n.toFixed(3);
  const lines = [
    'Soren91: complete sent-to-sent intervals (NOT game-accepted drops)',
    `samples=${report.samples} excluded=${report.excludedSamples} missing=${report.missingSamples} duplicates=${report.duplicateSamples}`,
    `latest_drop_unix_ms=${report.latestDropAtMs ?? 'N/A'}`,
    'phase\tmean_s\tp50_s\tp95_s\tmax_s\tshare_%\tmean_calls',
  ];
  const total = report.intervalSeconds;
  lines.push(`TOTAL\t${f(total.mean)}\t${f(total.p50)}\t${f(total.p95)}\t${f(total.max)}\t${report.samples ? '100.000' : 'N/A'}\tN/A`);
  for (const [phase, row] of Object.entries(report.phases)) {
    const s = row.seconds;
    lines.push(`${phase}\t${f(s.mean)}\t${f(s.p50)}\t${f(s.p95)}\t${f(s.max)}\t${f(row.sharePercent)}\t${f(row.callsPerInterval?.mean)}`);
  }
  return lines.join('\n') + '\n';
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  try {
    const args = process.argv.slice(2), json = args.includes('--json');
    const paths = args.filter(a => a !== '--json');
    if (!paths.length || paths.length > 64 || paths.some(p => p.startsWith('--'))) throw new Error('usage');
    const snapshots = paths.map(path => {
      const size = statSync(path);
      if (!size.isFile() || size.size > 2 * 1024 * 1024) throw new Error('invalid-input');
      return JSON.parse(readFileSync(path, 'utf8'));
    });
    const report = summarizeDropProfiles(snapshots);
    process.stdout.write(json ? JSON.stringify(report, null, 2) + '\n' : formatDropProfileReport(report));
    if (!report.samples) process.exitCode = 2;
  } catch {
    // No raw path, file content or exception text in a shared diagnostic log.
    process.stderr.write('drop-profile-unavailable: expected bounded v1 snapshots; usage: node soren91/summarize_drop_profile.mjs [--json] SNAPSHOT...\n');
    process.exitCode = 1;
  }
}

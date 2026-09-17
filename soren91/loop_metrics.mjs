/** Fixed numeric diagnostics; drop SENT intervals are not game-accepted intervals. */
import { performance } from 'node:perf_hooks';
import { openSync, writeFileSync, closeSync, renameSync, unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const STAGES = ['capture', 'analyze', 'ranking', 'decide', 'input', 'cooldown', 'poll'];
export const DROP_PROFILE_STAGES = [...STAGES, 'holdInput', 'overlap', 'unattributed'];
const REASONS = ['unknown-current', 'uncalibrated', 'invalid-board', 'confirm-frame',
  'preview-changed', 'board-moving', 'stable', 'stable-slow-advance', 'non-move', 'other'];
const zeros = names => Object.fromEntries(names.map(name => [name, 0]));
const rounded = n => Math.round(n * 10) / 10;
const PROFILE_CAPACITY = 128;

export function writeMetricsAtomically(path, value) {
  const temporary = `${path}.${process.pid}.${randomUUID()}.tmp`;
  let fd;
  try {
    fd = openSync(temporary, 'wx', 0o600);
    writeFileSync(fd, JSON.stringify(value) + '\n');
    closeSync(fd);
    fd = undefined;
    renameSync(temporary, path);
  } finally {
    if (fd !== undefined) closeSync(fd);
    try { unlinkSync(temporary); } catch {}
  }
}

export class LoopMetrics {
  constructor({ now = () => performance.now(), write = () => {} } = {}) {
    this.now = now;
    this.write = write;
    this.game = null;
    this.turn = null;
    this.lastDrop = null;
    this.intervals = [];
    this.lastWrite = -Infinity;
    // Independent of the legacy turn-scoped metrics. No extra timer or I/O.
    this.profileSession = randomUUID();
    this.profileRecords = [];
    this.profileTotal = 0;
    this.profileEvicted = 0;
    this.profileOpen = null;
    this.profileActive = new Set();
    this.profileLastInput = null;
  }
  begin(game, turn) {
    if (this.game !== game) {
      this.lastDrop = null;
      this.intervals = [];
      // Never turn an inter-round wait or the first drop into a complete interval.
      this.profileOpen = null;
      this.profileLastInput = null;
    }
    if (this.game !== game || this.turn !== turn) {
      this.startedAt = this.now();
      this.stages = zeros(STAGES);
      this.reasons = zeros(REASONS);
      this.observations = 0;
      this.holds = 0;
    }
    this.game = game;
    this.turn = turn;
  }
  _profileSettle(now) {
    const bucket = this.profileOpen;
    if (!bucket) return;
    if (now < bucket.cursor) bucket.clockValid = false;
    const elapsed = Math.max(0, now - bucket.cursor);
    const active = [...this.profileActive];
    // Nested/concurrent measures are deliberately not added twice. Their shared
    // time stays explicit rather than falsely attributed to one of the phases.
    const stage = active.length === 0 ? 'unattributed'
      : active.length === 1 ? active[0].stage : 'overlap';
    bucket.stageMs[stage] += elapsed;
    if (active.length === 1 && stage === 'input') {
      const token = active[0];
      if (token.inputBucket !== bucket) { token.inputBucket = bucket; token.inputMs = 0; }
      token.inputMs += elapsed;
    }
    bucket.cursor = now;
  }
  async measure(stage, action) {
    if (!STAGES.includes(stage)) throw new TypeError('Unknown latency stage');
    const start = this.now();
    this._profileSettle(start);
    const token = { stage, inputMs: 0, inputBucket: null };
    this.profileActive.add(token);
    if (this.profileOpen) this.profileOpen.phaseCalls[stage]++;
    try { return await action(); }
    finally {
      const end = this.now();
      this._profileSettle(end);
      this.profileActive.delete(token);
      if (stage === 'input') this.profileLastInput = token;
      this.stages[stage] += Math.max(0, end - start);
    }
  }
  observe(state) {
    this.observations++;
    const reason = String(state?.perception?.reason || 'non-move');
    const key = [...REASONS].reverse().find(r => reason === r || reason.startsWith(r + '-')) || 'other';
    this.reasons[key]++;
    if (this.profileOpen) {
      this.profileOpen.observations++;
      this.profileOpen.reasonCounts[key]++;
    }
  }
  holdSent() {
    this.holds++;
    const bucket = this.profileOpen;
    if (!bucket) return;
    bucket.holds++;
    // Existing main calls holdSent immediately after measure('input', executeHold).
    // Reclassify that input's exclusive time, not its following re-observation.
    const input = this.profileLastInput;
    if (input?.inputBucket === bucket) {
      bucket.stageMs.input -= input.inputMs;
      bucket.stageMs.holdInput += input.inputMs;
      bucket.phaseCalls.input--;
      bucket.phaseCalls.holdInput++;
    }
    this.profileLastInput = null;
  }
  dropSent() {
    const now = this.now();
    this._profileSettle(now);
    if (this.lastDrop != null) {
      this.intervals.push(Math.max(0, now - this.lastDrop));
      if (this.intervals.length > 128) this.intervals.shift();
    }
    const bucket = this.profileOpen;
    if (bucket) {
      const stageMs = Object.fromEntries(DROP_PROFILE_STAGES.map(s => [s, rounded(bucket.stageMs[s])]));
      const durationMs = rounded(Math.max(0, now - bucket.start));
      const accountingErrorMs = rounded(durationMs - Object.values(stageMs).reduce((a, b) => a + b, 0));
      this.profileRecords.push({
        sample: ++this.profileTotal, game: this.game, fromTurn: bucket.fromTurn, toTurn: this.turn,
        endedAtMs: Date.now(), durationMs, stageMs, phaseCalls: { ...bucket.phaseCalls },
        observations: bucket.observations, holds: bucket.holds, errors: bucket.errors,
        reasonCounts: { ...bucket.reasonCounts },
        accountingErrorMs, accountingValid: bucket.clockValid && Math.abs(accountingErrorMs) <= 1,
      });
      if (this.profileRecords.length > PROFILE_CAPACITY) {
        this.profileRecords.shift(); this.profileEvicted++;
      }
    }
    this.profileOpen = {
      start: now, cursor: now, fromTurn: this.turn, clockValid: true,
      stageMs: zeros(DROP_PROFILE_STAGES), phaseCalls: zeros([...STAGES, 'holdInput']),
      observations: 0, holds: 0, errors: 0, reasonCounts: zeros(REASONS),
    };
    // A measured operation crossing a boundary touches both intervals.
    for (const token of this.profileActive) this.profileOpen.phaseCalls[token.stage]++;
    this.profileLastInput = null;
    this.lastDrop = now;
  }
  flush(outcome = 'observe') {
    const now = this.now();
    if (outcome === 'error' && this.profileOpen) this.profileOpen.errors++;
    if (outcome === 'observe' && now - this.lastWrite < 1000) return;
    this.lastWrite = now;
    const sorted = [...this.intervals].sort((a, b) => a - b);
    const percentile = p => sorted.length ? rounded(sorted[Math.ceil(sorted.length * p) - 1]) : null;
    const value = {
      schemaVersion: 1, updatedAtMs: Date.now(), game: this.game, turn: this.turn,
      outcome: ['observe', 'hold-sent', 'drop-sent', 'error'].includes(outcome) ? outcome : 'error',
      observations: this.observations, holds: this.holds,
      elapsedMs: rounded(Math.max(0, now - this.startedAt)),
      sinceDropSentMs: this.lastDrop == null ? null : rounded(now - this.lastDrop),
      stageMs: Object.fromEntries(STAGES.map(s => [s, rounded(this.stages[s])])),
      reasonCounts: { ...this.reasons },
      dropSentIntervalMs: { samples: sorted.length,
        last: this.intervals.length ? rounded(this.intervals.at(-1)) : null,
        p50: percentile(0.5), p95: percentile(0.95), max: sorted.length ? rounded(sorted.at(-1)) : null },
      dropProfile: {
        schemaVersion: 1, basis: 'sent-to-sent', acceptedDropsMeasured: false,
        session: this.profileSession, capacity: PROFILE_CAPACITY,
        totalSamples: this.profileTotal, evictedSamples: this.profileEvicted,
        // Retain completed intervals across rounds; a process restart starts a
        // new session. Snapshot callbacks cannot mutate the internal ring.
        records: this.profileRecords.map(record => ({ ...record,
          stageMs: { ...record.stageMs }, phaseCalls: { ...record.phaseCalls },
          reasonCounts: { ...record.reasonCounts } })),
      },
    };
    // Diagnostics must not stop gameplay (full disk / permissions / rotation).
    try { this.write(value); } catch {}
    return value;
  }
}

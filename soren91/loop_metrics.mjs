/** Fixed numeric diagnostics; drop SENT intervals are not game-accepted intervals. */
import { performance } from 'node:perf_hooks';
import { openSync, writeFileSync, closeSync, renameSync, unlinkSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

const STAGES = ['capture', 'analyze', 'ranking', 'decide', 'input', 'cooldown', 'poll'];
const REASONS = ['unknown-current', 'uncalibrated', 'invalid-board', 'confirm-frame',
  'preview-changed', 'board-moving', 'stable', 'stable-slow-advance', 'non-move', 'other'];
const zeros = names => Object.fromEntries(names.map(name => [name, 0]));
const rounded = n => Math.round(n * 10) / 10;

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
  }
  begin(game, turn) {
    if (this.game !== game) {
      this.lastDrop = null;
      this.intervals = [];
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
  async measure(stage, action) {
    if (!STAGES.includes(stage)) throw new TypeError('Unknown latency stage');
    const start = this.now();
    try { return await action(); }
    finally { this.stages[stage] += Math.max(0, this.now() - start); }
  }
  observe(state) {
    this.observations++;
    const reason = String(state?.perception?.reason || 'non-move');
    const key = [...REASONS].reverse().find(r => reason === r || reason.startsWith(r + '-')) || 'other';
    this.reasons[key]++;
  }
  holdSent() { this.holds++; }
  dropSent() {
    const now = this.now();
    if (this.lastDrop != null) {
      this.intervals.push(Math.max(0, now - this.lastDrop));
      if (this.intervals.length > 128) this.intervals.shift();
    }
    this.lastDrop = now;
  }
  flush(outcome = 'observe') {
    const now = this.now();
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
    };
    // Diagnostics must not stop gameplay (full disk / permissions / rotation).
    try { this.write(value); } catch {}
    return value;
  }
}

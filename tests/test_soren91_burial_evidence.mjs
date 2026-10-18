/**
 * docich#605 acceptance-evidence measurement (soren91/burial_evidence.mjs).
 *
 * Pins the retained-history replay measurement on synthetic records: the
 * recorded-burial geometry (including HOLD swaps and the same-type exception),
 * the lone-exposed residual pattern, the pairLaneBurials diagnostics
 * cross-check, skip classification, and the CLI entry point. Replay outcomes
 * are driven by stub decide functions so the tool contract stays independent
 * of strategy tuning; one real-strategy case guards the tool against the
 * current no-burial guarantee from tests/test_soren91_pair_lane_burial.mjs.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { execFileSync } from 'node:child_process';
import { analyzeRecords, loadHistory, exposedPairLanes, countLaneBurials } from '../soren91/burial_evidence.mjs';
import { decide, simulateDrop, TYPE_RADII } from '../soren91/strategy.mjs';

const piece = (type, x = 0, y = -5 + TYPE_RADII[type], extra = {}) => ({
  type, r: TYPE_RADII[type], x, y, confidence: 0.9, ...extra,
});
const state = (pieces, next = piece(5), extra = {}) => ({
  state: 'MOVE', pieces, next, nextPieces: [next], hold: null, canHold: false,
  garbage: { ratio: 0, height: -5, gauge: 0, columns: [] }, ...extra,
});
const decision = (x, extra = {}) => ({ x, reason: 'HEIGHT_CONTROL', hold: false, ...extra });
const laneBoard = () => [piece(8, -1.0), piece(8, 1.0)];

test('lane geometry mirrors the strategy: wedge over the pair gap is a burial', () => {
  const pieces = laneBoard();
  const lanes = exposedPairLanes(pieces);
  assert.equal(lanes.length, 1);
  assert.deepEqual(
    { type: lanes[0].type, lo: lanes[0].lo, hi: lanes[0].hi },
    { type: 8, lo: -1.0, hi: 1.0 });
  const burying = simulateDrop(pieces, piece(5), 0);
  assert.equal(countLaneBurials(burying, lanes), 1);
  const safe = simulateDrop(pieces, piece(5), 2.5);
  assert.equal(countLaneBurials(safe, lanes), 0);
});

test('recorded burial turn is counted, safe landing is not', () => {
  const bury = analyzeRecords([{ turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(0) }]);
  assert.equal(bury.laneOpportunityTurns, 1);
  assert.equal(bury.recorded.burialTurns, 1);
  assert.equal(bury.recorded.burialEvents, 1);
  assert.equal(bury.recorded.loneExposedBurialTurns, 0);
  const safe = analyzeRecords([{ turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(2.5) }]);
  assert.equal(safe.recorded.burialTurns, 0);
});

test('HOLD swap drops the held piece; the exemption tracks the post-merge type', () => {
  const holdOther = analyzeRecords([{
    turn: 1, file: 'g.jsonl',
    state: state(laneBoard(), piece(5), { hold: piece(7), canHold: true }),
    decision: { x: 0, hold: true },
  }]);
  assert.equal(holdOther.recorded.burialTurns, 1);
  // A lane-type drop into the gap center merges and the promoted type no longer
  // matches the lane, so the covered interior counts — exactly as
  // strategy.mjs laneBurialCount() (move.placed is the post-merge piece).
  const holdLaneType = analyzeRecords([{
    turn: 2, file: 'g.jsonl',
    state: state(laneBoard(), piece(5), { hold: piece(8), canHold: true }),
    decision: { x: 0, hold: true },
  }]);
  assert.equal(holdLaneType.recorded.burialTurns, 1);
  // A piece that is still of the lane type is the merge the lane waits for.
  const lanes = exposedPairLanes(laneBoard());
  assert.equal(countLaneBurials({ placed: { type: 8, x: 0, y: -3.5 }, peak: -3.0 }, lanes), 0);
});

test('a lone exposed piece covered from above is the residual pattern', () => {
  const lone = analyzeRecords([{ turn: 3, file: 'g.jsonl', state: state([piece(9, 0)], piece(5)), decision: decision(0) }]);
  assert.equal(lone.laneOpportunityTurns, 0);
  assert.equal(lone.recorded.burialTurns, 0);
  assert.equal(lone.recorded.loneExposedBurialTurns, 1);
});

test('pairLaneBurials diagnostics are aggregated and cross-checked', () => {
  const rows = [
    { turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(0, { diagnostics: { pairLaneBurials: 1, pairLaneBurialAvoided: false } }) },
    { turn: 1, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(2.5, { diagnostics: { pairLaneBurials: 1, pairLaneBurialAvoided: false } }) },
    { turn: 2, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(2.5, { diagnostics: { pairLaneBurials: 0, pairLaneBurialAvoided: true } }) },
  ];
  const s = analyzeRecords(rows);
  assert.equal(s.recordedDiagnostics.turnsWithFields, 3);
  assert.equal(s.recordedDiagnostics.burialTurns, 2);
  assert.equal(s.recordedDiagnostics.avoidedTurns, 1);
  assert.equal(s.recordedDiagnostics.agreementMismatches, 1, 'turn 1 claims a burial but lands outside the lane');
});

test('replay counting works against stub decisions', () => {
  const rows = [
    { turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(0) },
  ];
  const same = analyzeRecords(rows, { replayDecide: () => ({ x: 0 }) });
  assert.equal(same.replay.attempted, 1);
  assert.equal(same.replay.burialTurns, 1);
  assert.equal(same.replay.avoidedRecordedBurials, 0);
  assert.equal(same.replay.changedX, 0);
  const elsewhere = analyzeRecords(rows, { replayDecide: () => ({ x: 2.5 }) });
  assert.equal(elsewhere.replay.burialTurns, 0);
  assert.equal(elsewhere.replay.avoidedRecordedBurials, 1);
  assert.equal(elsewhere.replay.changedX, 1);
  const disabled = analyzeRecords(rows, { replayDecide: null });
  assert.equal(disabled.replay.attempted, 0);
});

test('replay uses its own occupied HOLD choice instead of the recorded piece', () => {
  const pieces = [piece(1, -0.5), piece(1, 0.5)];
  const observed = state(pieces, piece(1), { hold: piece(2), canHold: true });
  const s = analyzeRecords([{ turn: 0, state: observed, decision: decision(0) }], {
    replayDecide: () => ({ x: 0, hold: true }),
  });
  assert.equal(s.recorded.burialTurns, 0);
  assert.equal(s.replay.burialTurns, 1);
  assert.equal(s.replay.avoidedRecordedBurials, 0);
  assert.equal(s.replay.changedX, 0, 'a HOLD-only change can alter burial at the same x');
  assert.equal(s.examples.find(e => e.kind === 'replay-burial').type, 2);
});

test('replay can avoid a recorded HOLD burial by keeping the current piece', () => {
  const pieces = [piece(1, -0.5), piece(1, 0.5)];
  const observed = state(pieces, piece(1), { hold: piece(2), canHold: true });
  const s = analyzeRecords([{ turn: 0, state: observed, decision: decision(0, { hold: true }) }], {
    replayDecide: () => ({ x: 0, hold: false }),
  });
  assert.equal(s.recorded.burialTurns, 1);
  assert.equal(s.replay.burialTurns, 0);
  assert.equal(s.replay.avoidedRecordedBurials, 1);
});

test('replay HOLD into a known-empty slot uses the first preview piece', () => {
  const pieces = [piece(1, -0.5), piece(1, 0.5)];
  const observed = state(pieces, piece(1), {
    holdKnownEmpty: true, canHold: true, nextPieces: [piece(1), piece(2)],
  });
  const s = analyzeRecords([{ turn: 0, state: observed, decision: decision(0) }], {
    replayDecide: () => ({ x: 0, hold: true }),
  });
  assert.equal(s.recorded.burialTurns, 0);
  assert.equal(s.replay.burialTurns, 1);
  assert.equal(s.examples.find(e => e.kind === 'replay-burial').type, 2);
});

test('replay with an unknown HOLD source is skipped without claiming an avoidance', () => {
  const rows = [{ turn: 0, state: state(laneBoard(), piece(5)), decision: decision(0) }];
  const s = analyzeRecords(rows, { replayDecide: () => ({ x: 2.5, hold: true }) });
  assert.equal(s.recorded.burialTurns, 1);
  assert.equal(s.replay.attempted, 1);
  assert.equal(s.replay.skipReasons['unusable-drop-piece'], 1);
  assert.equal(s.replay.burialTurns, 0);
  assert.equal(s.replay.avoidedRecordedBurials, 0);
  assert.equal(s.replay.changedX, 0);
});

test('unchanged HOLD decisions preserve the recorded burial result', () => {
  const observed = state(laneBoard(), piece(5), { hold: piece(7), canHold: true });
  const s = analyzeRecords([{ turn: 0, state: observed, decision: decision(0, { hold: true }) }], {
    replayDecide: () => ({ x: 0, hold: true }),
  });
  assert.equal(s.recorded.burialTurns, 1);
  assert.equal(s.replay.burialTurns, 1);
  assert.equal(s.replay.avoidedRecordedBurials, 0);
});

test('the current strategy keeps the #605 no-burial guarantee on the fixture board', () => {
  const s = analyzeRecords([{ turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: decision(0) }]);
  assert.equal(s.replay.attempted, 1);
  assert.equal(s.replay.burialTurns, 0, JSON.stringify(s.replay));
  assert.equal(s.replay.avoidedRecordedBurials, 1);
});

test('uncertain current and unusable inputs are skipped with reasons', () => {
  const rows = [
    { turn: 0, file: 'g.jsonl', state: state(laneBoard(), piece(5, 0, -3, { confidence: 0.48, fallback: true })), decision: decision(0) },
    { turn: 1, file: 'g.jsonl', state: state(laneBoard(), null), decision: decision(0) },
    { turn: 2, file: 'g.jsonl', state: { state: 'WAITING', pieces: [], next: null }, decision: decision(0) },
    { turn: 3, file: 'g.jsonl', state: state(laneBoard(), piece(5)), decision: { reason: 'no-x' } },
    { turn: 4, file: 'g.jsonl', state: state([piece(8, -1.0), { type: 99, x: 0, y: -3 }], piece(5)), decision: decision(0) },
  ];
  const s = analyzeRecords(rows, { replayDecide: () => ({ x: 0 }) });
  assert.equal(s.turns, 5);
  assert.equal(s.skipped.notMove, 1);
  assert.equal(s.skipped.noDecision, 1);
  assert.equal(s.skipped.badPieces, 1);
  assert.equal(s.skipped.unusableDropPiece, 1);
  assert.equal(s.replay.skipReasons['uncertain-current'], 1, 'geometry is still measured when the replay is skipped');
  assert.equal(s.replay.attempted, 0);
});

test('malformed lines become parse skips through loadHistory', () => {
  const dir = mkdtempSync(join(tmpdir(), 'burial-evidence-'));
  try {
    const file = join(dir, 'game_0001.jsonl');
    writeFileSync(file, [
      JSON.stringify({ turn: 0, state: state(laneBoard(), piece(5)), decision: decision(0) }),
      '{not json',
      '',
    ].join('\n'));
    const s = analyzeRecords(loadHistory(file));
    assert.equal(s.skipped.parse, 1);
    assert.equal(s.turns, 2);
    assert.equal(s.recorded.burialTurns, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('CLI prints a JSON summary for history files', () => {
  const dir = mkdtempSync(join(tmpdir(), 'burial-evidence-cli-'));
  try {
    const file = join(dir, 'game_0002.jsonl');
    writeFileSync(file, `${JSON.stringify({ turn: 0, state: state(laneBoard(), piece(5)), decision: decision(0) })}\n`);
    const out = JSON.parse(execFileSync(process.execPath, [
      'soren91/burial_evidence.mjs', file, '--max-examples', '1',
    ], { cwd: join(import.meta.dirname, '..'), encoding: 'utf8', timeout: 30000 }));
    assert.equal(out.files[0], 'game_0002.jsonl');
    assert.equal(out.recorded.burialTurns, 1);
    assert.equal(out.replay.burialTurns, 0);
    assert.equal(out.examples.length, 1);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

test('real decide stays usable inside the replay path on an ordinary board', () => {
  const ordinary = analyzeRecords([{
    turn: 5, file: 'g.jsonl', state: state([piece(1, -1.0), piece(3, 1.0)], piece(2)), decision: decision(-2),
  }]);
  assert.equal(ordinary.laneOpportunityTurns, 0);
  assert.equal(ordinary.recorded.burialTurns, 0);
  assert.equal(ordinary.replay.attempted, 0);
  assert.ok(Number.isFinite(decide(state([piece(1, -1.0), piece(3, 1.0)], piece(2))).x));
});

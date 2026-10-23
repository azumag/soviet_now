/**
 * docich#605 acceptance-evidence measurement for the Soren91 runtime path.
 *
 * Retained game history (game_history/*.jsonl records {turn, state, decision})
 * is replayed with the same lane-burial geometry that soren91/strategy.mjs
 * (pairLaneBurials) and tests/test_soren91_pair_lane_burial.mjs use, so the
 * "different-type piece wedged over an exposed same-type pair lane" placement
 * pattern can be counted:
 *
 * - recorded: what the strategy that produced the history actually chose.
 *   This is the baseline (pre-deploy) and the post-deploy audit number.
 * - replay: what the checkout's current strategy.mjs decide() would choose on
 *   the same boards (counterfactual; post-deploy it doubles as a consistency
 *   check between the recorded run and this checkout).
 *
 * The geometry is intentionally re-implemented here instead of imported from
 * strategy.mjs internals: an independent measurement that agrees with the
 * strategy's own pairLaneBurials diagnostics is worth more than a call that
 * shares its code. Only node builtins and the exported strategy surface are
 * used, so the module stays safe to run against production evidence.
 *
 * Known limitation: the tool does not replicate the strategy's
 * filterUnsupportedHighObservations pruning, so boards with detached high
 * duplicate detections may disagree slightly with decide()'s internal view;
 * such disagreements are reported as recordedDiagnostics.agreementMismatches.
 *
 * This module only measures. It never proposes, generates, or applies strategy
 * changes from evidence (soren91/CLAUDE.md: no unattended candidates from
 * retained evidence).
 */
import { readFileSync } from 'node:fs';
import { decide, simulateDrop, TYPE_RADII } from './strategy.mjs';

const WALL = 3.5;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const certainty = p => p?.fallback ? 0 : Number.isFinite(p?.confidence) ? clamp(p.confidence, 0, 1) : 1;
const TOUCH_GAP = 0.035;
const LANE_EDGE_SLACK = 0.25;

// Mirrors strategy.mjs normalizePiece() without throwing: unusable observations
// are reported as skips instead of aborting the whole evidence run.
export function normalizePiece(p, positioned = false) {
  if (!p || !Number.isInteger(p.type) || !TYPE_RADII[p.type]) return null;
  const r = positioned && Number.isFinite(p.r) && p.r > 0 && p.r <= WALL ? p.r : TYPE_RADII[p.type];
  if (positioned && (!Number.isFinite(p.x) || !Number.isFinite(p.y))) return null;
  return { ...p, r, confidence: certainty(p) };
}

// Mirrors strategy.mjs exposedPieces().
function exposedPieces(pieces) {
  return pieces.filter((p, index) => {
    if (p.type <= 0 || certainty(p) < 0.6) return false;
    for (let i = 0; i < pieces.length; i++) {
      if (i === index) continue;
      const q = pieces[i];
      if (q.y <= p.y + 0.05) continue;
      if (Math.abs(q.x - p.x) < p.r + q.r * 0.7) return false;
    }
    return true;
  });
}

// Mirrors strategy.mjs exposedPairLanes(): an exposed same-type pair whose gap
// a future same-type piece could still fall into.
export function exposedPairLanes(pieces) {
  const exposed = exposedPieces(pieces);
  const byType = new Map();
  for (const p of exposed) {
    if (p.type >= 15) continue;
    if (!byType.has(p.type)) byType.set(p.type, []);
    byType.get(p.type).push(p);
  }
  const lanes = [];
  for (const [type, group] of byType) {
    if (group.length < 2) continue;
    const reach = TYPE_RADII[type] * 2 + 1.0;
    for (let i = 0; i < group.length; i++) {
      for (let j = i + 1; j < group.length; j++) {
        const a = group[i];
        const b = group[j];
        const lo = Math.min(a.x, b.x);
        const hi = Math.max(a.x, b.x);
        if (hi - lo <= 0 || hi - lo > reach) continue;
        const top = Math.min(a.y + a.r, b.y + b.r);
        const bottom = Math.max(a.y - a.r, b.y - b.r);
        if (bottom >= top) continue;
        lanes.push({ type, lo, hi, top, bottom });
      }
    }
  }
  return lanes;
}

// Mirrors strategy.mjs laneBurialCount(): a different-type piece landing inside
// the gap with its top at/above the pair's top covers the lane entry from above.
export function countLaneBurials(sim, lanes) {
  const type = sim?.placed?.type;
  let buried = 0;
  for (const lane of lanes) {
    if (lane.type === type) continue;
    if (!(lane.lo + LANE_EDGE_SLACK < sim.placed.x && sim.placed.x < lane.hi - LANE_EDGE_SLACK)) continue;
    if (sim.peak < lane.top - LANE_EDGE_SLACK) continue;
    buried++;
  }
  return buried;
}

// The residual issue pattern (#605's single-piece A/B/A stack): a different-type
// piece resting directly on top of an exposed piece that has no reachable
// same-type partner lane at all.
function countLoneExposedBurials(sim, pieces, lanes) {
  const laneTypes = new Set(lanes.map(l => l.type));
  let buried = 0;
  for (const a of exposedPieces(pieces)) {
    if (a.type >= 15 || laneTypes.has(a.type)) continue;
    if (a.type === sim.placed.type) continue;
    const gap = Math.hypot(sim.placed.x - a.x, sim.placed.y - a.y) - sim.placed.r - a.r;
    if (Math.abs(gap) <= TOUCH_GAP && sim.placed.y > a.y) buried++;
  }
  return buried;
}

// The piece selected by each decision, whether recorded or counterfactual:
// swapping makes the hold slot (or the known-empty swap source) the current.
function decisionDropPiece(state, decision) {
  if (decision?.hold === true) {
    return state?.hold ?? (state?.holdKnownEmpty === true ? state?.nextPieces?.[1] ?? null : null);
  }
  return state?.next ?? null;
}

function emptySummary() {
  return {
    schema: 1,
    turns: 0,
    moveTurns: 0,
    laneOpportunityTurns: 0,
    recorded: { burialTurns: 0, burialEvents: 0, loneExposedBurialTurns: 0 },
    recordedDiagnostics: { turnsWithFields: 0, burialTurns: 0, avoidedTurns: 0, agreementMismatches: 0 },
    replay: { attempted: 0, burialTurns: 0, avoidedRecordedBurials: 0, changedX: 0, skipReasons: {} },
    skipped: { parse: 0, notMove: 0, noDecision: 0, badPieces: 0, unusableDropPiece: 0 },
    examples: [],
  };
}

const bump = (map, key) => { map[key] = (map[key] ?? 0) + 1; };

/**
 * Analyze retained history records. `replayDecide` defaults to the checkout's
 * current soren91/strategy.mjs decide(); pass null to disable replay entirely.
 */
export function analyzeRecords(records, { replayDecide = decide, maxExamples = 5 } = {}) {
  const summary = emptySummary();
  const pushExample = row => {
    if (summary.examples.length >= maxExamples) return;
    summary.examples.push(row);
  };

  for (const record of records) {
    summary.turns++;
    if (record && record.parse === false) { summary.skipped.parse++; continue; }
    const state = record?.state;
    const decision = record?.decision;
    if (!state || state.state !== 'MOVE' || !Array.isArray(state.pieces)) { summary.skipped.notMove++; continue; }
    if (!decision || !Number.isFinite(decision.x)) { summary.skipped.noDecision++; continue; }
    const pieces = [];
    let badPieces = false;
    for (const raw of state.pieces) {
      const p = normalizePiece(raw, true);
      if (!p) { badPieces = true; break; }
      pieces.push(p);
    }
    if (badPieces) { summary.skipped.badPieces++; continue; }
    summary.moveTurns++;

    const lanes = exposedPairLanes(pieces);
    const hasLane = lanes.length > 0;
    if (hasLane) summary.laneOpportunityTurns++;

    const dropped = normalizePiece(decisionDropPiece(state, decision));
    if (!dropped) {
      summary.skipped.unusableDropPiece++;
      if (hasLane) pushExample({ file: record.file, turn: record.turn, reason: 'unusable-drop-piece', lanes: lanes.map(l => l.type) });
      continue;
    }
    const columns = Array.isArray(state.garbage?.columns) ? state.garbage.columns.slice(0, 64) : [];
    const recordedSim = simulateDrop(pieces, dropped, decision.x, columns);
    const recordedBurials = countLaneBurials(recordedSim, lanes);
    if (hasLane && recordedBurials > 0) {
      summary.recorded.burialTurns++;
      summary.recorded.burialEvents += recordedBurials;
      pushExample({ file: record.file, turn: record.turn, kind: 'recorded-burial', x: decision.x, type: dropped.type, buried: recordedBurials, laneTypes: [...new Set(lanes.map(l => l.type))] });
    }
    if (countLoneExposedBurials(recordedSim, pieces, lanes) > 0) summary.recorded.loneExposedBurialTurns++;

    const diag = decision.diagnostics;
    if (diag && Number.isFinite(diag.pairLaneBurials)) {
      summary.recordedDiagnostics.turnsWithFields++;
      if (diag.pairLaneBurials > 0) summary.recordedDiagnostics.burialTurns++;
      if (diag.pairLaneBurialAvoided === true) summary.recordedDiagnostics.avoidedTurns++;
      if (Boolean(diag.pairLaneBurials > 0) !== Boolean(recordedBurials > 0)) {
        summary.recordedDiagnostics.agreementMismatches++;
        pushExample({ file: record.file, turn: record.turn, kind: 'diagnostic-mismatch', recorded: diag.pairLaneBurials, geometric: recordedBurials });
      }
    }

    if (hasLane && replayDecide) {
      if (certainty(state.next) < 0.5) {
        bump(summary.replay.skipReasons, 'uncertain-current');
        continue;
      }
      summary.replay.attempted++;
      try {
        const replayed = replayDecide(state);
        const replayDropped = normalizePiece(decisionDropPiece(state, replayed));
        if (!replayDropped) {
          bump(summary.replay.skipReasons, 'unusable-drop-piece');
          continue;
        }
        const replaySim = simulateDrop(pieces, replayDropped, replayed.x, columns);
        const replayBurials = countLaneBurials(replaySim, lanes);
        if (replayBurials > 0) {
          summary.replay.burialTurns++;
          pushExample({ file: record.file, turn: record.turn, kind: 'replay-burial', x: replayed.x, type: replayDropped.type, buried: replayBurials });
        } else if (recordedBurials > 0) {
          summary.replay.avoidedRecordedBurials++;
        }
        if (Math.abs(replayed.x - decision.x) > 1e-9) summary.replay.changedX++;
      } catch (error) {
        bump(summary.replay.skipReasons, error?.message === 'Uncertain Soren91 current piece'
          ? 'uncertain-current' : 'replay-error');
      }
    }
  }

  return summary;
}

/** Parse one game_history JSONL file into records tagged with their file name. */
export function loadHistory(path) {
  const name = path.split('/').pop();
  const records = [];
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (line.trim() === '') continue;
    try {
      records.push({ ...JSON.parse(line), file: name });
    } catch {
      records.push({ parse: false, file: name });
    }
  }
  return records;
}

if (import.meta.url === new URL(process.argv[1] || '', 'file:').href) {
  const args = process.argv.slice(2);
  const options = { maxExamples: 5 };
  const files = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === '--no-replay') options.replayDecide = null;
    else if (args[i] === '--max-examples') {
      const n = Number(args[++i]);
      if (Number.isFinite(n) && n >= 0) options.maxExamples = Math.floor(n);
    } else files.push(args[i]);
  }
  if (files.length === 0) {
    console.error('usage: node soren91/burial_evidence.mjs <game_history.jsonl...> [--no-replay] [--max-examples N]');
    process.exit(2);
  }
  const records = files.flatMap(f => loadHistory(f));
  const summary = analyzeRecords(records, options);
  summary.files = files.map(f => f.split('/').pop());
  console.log(JSON.stringify(summary, null, 1));
}

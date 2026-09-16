/**
 * Soren91: bounded survival-first beam search.
 *
 * The model is deliberately self-contained because per-round snapshots are
 * imported through data: URLs. It uses measured circle geometry as a safe
 * approximation, then scores board structure separately from the physics
 * simulation. No external model or automatic-improvement behavior lives here.
 */
export const TYPE_RADII = Object.freeze({
  1: 0.207, 2: 0.259, 3: 0.316, 4: 0.380, 5: 0.414,
  6: 0.470, 7: 0.559, 8: 0.660, 9: 0.746, 10: 0.846,
  11: 0.982, 12: 1.068, 13: 1.207, 14: 1.385, 15: 1.600,
});

const FLOOR = -5;
const DEADLINE = 3.32;
const WALL = 3.5;
const FATAL_MARGIN = 0.08;
const WARNING_MARGIN = 0.62;
const ROOT_BEAM = 12;
const FUTURE_BEAM = 10;
const PER_NODE_BRANCH = 7;
const MAX_CANDIDATES = 49;
const LOOKAHEAD_DISCOUNT = [1, 0.46, 0.23];
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const certainty = p => p?.fallback ? 0 : Number.isFinite(p?.confidence) ? clamp(p.confidence, 0, 1) : 1;

function normalizePiece(p, positioned = false) {
  if (!p || !Number.isInteger(p.type) || !TYPE_RADII[p.type]) {
    if (positioned && p && Number.isFinite(p.x) && Number.isFinite(p.y)
        && Number.isFinite(p.r) && p.r > 0 && p.r <= WALL) {
      return { x: p.x, y: p.y, r: p.r, type: 0, confidence: 0 };
    }
    throw new TypeError('Unusable Soren91 piece observation');
  }
  const r = positioned && Number.isFinite(p.r) && p.r > 0 && p.r <= WALL
    ? p.r : TYPE_RADII[p.type];
  if (positioned && (!Number.isFinite(p.x) || !Number.isFinite(p.y))) {
    throw new TypeError('Unusable Soren91 piece position');
  }
  return { ...p, r, confidence: certainty(p) };
}

function limitFor(piece) {
  return Math.min(3, WALL - piece.r - 0.02);
}

/** First vertical circle contact. Independent of detector enumeration order. */
export function landingAt(pieces, piece, x, columns = []) {
  let y = FLOOR + piece.r;
  for (const p of pieces) {
    const rr = piece.r + p.r;
    const dx = x - p.x;
    if (Math.abs(dx) <= rr) {
      y = Math.max(y, p.y + Math.sqrt(Math.max(0, rr * rr - dx * dx)));
    }
  }
  for (const c of columns) {
    if (![c?.left, c?.right, c?.top].every(Number.isFinite) || c.left > c.right) continue;
    const dx = Math.max(c.left - x, x - c.right, 0);
    if (dx <= piece.r) {
      y = Math.max(y, c.top + Math.sqrt(Math.max(0, piece.r * piece.r - dx * dx)));
    }
  }
  return y;
}

function candidates(pieces, piece, columns = []) {
  const limit = limitFor(piece);
  const values = [];
  const seen = new Set();
  const add = raw => {
    const x = Math.round(clamp(raw, -limit, limit) * 1000) / 1000;
    const key = x.toFixed(3);
    if (!seen.has(key)) { seen.add(key); values.push(x); }
  };

  add(0); add(-limit); add(limit);
  for (let x = -3; x <= 3.001; x += 0.25) add(x);

  // Exact contacts are more useful than arbitrarily dense uniform sampling.
  const targets = [...pieces].sort((a, b) =>
    Number(b.type === piece.type) - Number(a.type === piece.type)
    || b.type - a.type || b.y - a.y || Math.abs(a.x) - Math.abs(b.x));
  for (const p of targets) {
    for (const x of [p.x, p.x - piece.r * 0.55, p.x + piece.r * 0.55]) {
      if (values.length >= MAX_CANDIDATES) break;
      add(x);
    }
    if (values.length >= MAX_CANDIDATES) break;
  }

  // Add centers of locally clear garbage spans/edges so a coarse 0.25 grid
  // does not miss an escape lane beside a measured obstacle.
  for (const c of columns) {
    if (values.length >= MAX_CANDIDATES) break;
    if (![c?.left, c?.right].every(Number.isFinite)) continue;
    add(c.left - piece.r - 0.03);
    add(c.right + piece.r + 0.03);
  }
  return values.slice(0, MAX_CANDIDATES).sort((a, b) => a - b);
}

/**
 * Conservative immediate merge simulation. Only first reachable equal-type
 * contacts merge. Other circles are not magically re-simulated by Unity-like
 * rolling, so uncertain futures remain penalized rather than rewarded.
 */
export function simulateDrop(pieces, piece, x, columns = []) {
  const board = pieces.map(p => ({ ...p }));
  let placed = { ...piece, x, y: landingAt(board, piece, x, columns) };
  const landingY = placed.y;
  let peak = placed.y + placed.r;
  let merges = 0;
  let mergeValue = 0;

  for (let step = 0; step < 14 && placed.type > 0 && placed.type < 15; step++) {
    if (certainty(placed) < 0.6) break;
    const touching = board.map((p, i) => ({
      p, i,
      gap: Math.hypot(p.x - placed.x, p.y - placed.y) - p.r - placed.r,
    })).filter(t => Math.abs(t.gap) <= 0.035);

    if (touching.some(t => t.p.type !== placed.type || certainty(t.p) < 0.6)) break;
    const target = touching.filter(t => t.p.type === placed.type)
      .sort((a, b) => a.gap - b.gap || a.p.x - b.p.x)[0];
    if (!target) break;

    const type = placed.type + 1;
    const r = TYPE_RADII[type];
    const mergedX = clamp((placed.x + target.p.x) / 2, -WALL + r, WALL - r);
    board.splice(target.i, 1);
    placed = {
      type, r, x: mergedX,
      y: landingAt(board, { r }, mergedX, columns),
      confidence: Math.min(certainty(placed), certainty(target.p)),
    };
    peak = Math.max(peak, placed.y + r);
    merges++;
    mergeValue += type * type;
  }
  board.push(placed);
  return { pieces: board, placed, landingY, peak, merges, mergeValue };
}

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

function boardStructure(pieces, columns) {
  const heights = [];
  for (let x = -3; x <= 3.001; x += 0.4) {
    heights.push(landingAt(pieces, { r: 0.08 }, x, columns));
  }
  const relative = heights.map(y => y - FLOOR);
  const meanHeight = relative.reduce((a, b) => a + b, 0) / relative.length;
  let roughness = 0;
  let pocketPenalty = 0;
  for (let i = 1; i < relative.length; i++) roughness += Math.abs(relative[i] - relative[i - 1]);
  for (let i = 1; i + 1 < relative.length; i++) {
    const rim = Math.min(relative[i - 1], relative[i + 1]);
    const depth = rim - relative[i];
    if (depth > 0.55) pocketPenalty += (depth - 0.55) ** 2;
  }

  const tops = pieces.map(p => p.y + p.r);
  const maxTop = Math.max(FLOOR, ...tops);
  const highMass = pieces.reduce((sum, p) => sum + Math.max(0, p.y + p.r - 0.4) ** 2, 0);
  const uncertain = pieces.filter(p => certainty(p) < 0.6).length;

  // Reward exposed same-type material that can still be brought together.
  // Penalize exposed duplicates that are split across the board with no short
  // path to reunion. This is a structural signal, not a fake future merge.
  const exposed = exposedPieces(pieces);
  const byType = new Map();
  for (const p of exposed) {
    if (p.type >= 15) continue;
    if (!byType.has(p.type)) byType.set(p.type, []);
    byType.get(p.type).push(p);
  }
  let pairPotential = 0;
  let splitPenalty = 0;
  for (const [type, group] of byType) {
    if (group.length < 2) continue;
    let nearest = Infinity;
    for (let i = 0; i < group.length; i++) {
      for (let j = i + 1; j < group.length; j++) {
        const d = Math.hypot(group[i].x - group[j].x, group[i].y - group[j].y);
        nearest = Math.min(nearest, d);
      }
    }
    const reach = TYPE_RADII[type] * 2 + 1.0;
    if (nearest <= reach) pairPotential += (reach - nearest + 0.2) * (2 + type * 0.9);
    else splitPenalty += Math.min(3, nearest - reach) * (1 + type * 0.35);
  }

  return { meanHeight, roughness, pocketPenalty, maxTop, highMass, uncertain, pairPotential, splitPenalty };
}

function evaluate(pieces, piece, x, garbage) {
  const sim = simulateDrop(pieces, piece, x, garbage.columns);
  const structure = boardStructure(sim.pieces, garbage.columns);
  const clearance = DEADLINE - Math.max(sim.peak, structure.maxTop);
  const pressure = clamp(garbage.gauge, 0, 1) + clamp(garbage.ratio, 0, 1);
  const risk = clearance <= FATAL_MARGIN ? 2 : clearance < WARNING_MARGIN ? 1 : 0;
  const localUncertain = pieces.filter(p => certainty(p) < 0.6 && Math.abs(p.x - x) < p.r + piece.r).length;

  const mergeReward = sim.mergeValue * (9 + pressure * 4.5) + sim.merges * 11;
  const shapeValue = structure.pairPotential * 1.8
    - structure.splitPenalty * 2.4
    - structure.meanHeight * 8.0
    - structure.roughness * 2.4
    - structure.pocketPenalty * 11
    - structure.highMass * 3.5
    - Math.max(0, structure.maxTop + 0.8) ** 2 * 28
    - Math.max(0, sim.peak + 0.8) ** 2 * 32
    - structure.uncertain * 2.5
    - localUncertain * 7
    - Math.abs(x) * 0.08;

  return { ...sim, x, risk, clearance, value: mergeReward + shapeValue, structure };
}

function compareMove(a, b) {
  return a.risk - b.risk
    || (a.risk === 2 ? b.clearance - a.clearance : 0)
    || b.value - a.value
    || b.clearance - a.clearance
    || Math.abs(a.x) - Math.abs(b.x)
    || a.x - b.x;
}

function knownQueue(queue, start) {
  const out = [];
  const values = Array.isArray(queue) ? queue : [];
  for (const p of values.slice(start, start + 2)) {
    if (!p || !TYPE_RADII[p.type] || certainty(p) < 0.6) break;
    out.push(normalizePiece(p));
  }
  return out;
}

function comparePath(a, b) {
  return a.pathRisk - b.pathRisk
    || (a.pathRisk === 2 ? b.minClearance - a.minClearance : 0)
    || b.totalValue - a.totalValue
    || b.minClearance - a.minClearance
    || compareMove(a.root, b.root);
}

function search(board, piece, queue, garbage) {
  const roots = candidates(board, piece, garbage.columns)
    .map(x => evaluate(board, piece, x, garbage))
    .sort(compareMove);
  const bestRootRisk = roots[0].risk;
  let beam = roots.filter(r => r.risk === bestRootRisk).slice(0, ROOT_BEAM).map(root => ({
    root,
    board: root.pieces,
    totalValue: root.value,
    pathRisk: root.risk,
    minClearance: root.clearance,
    nodes: 1,
  }));
  let expanded = beam.length;

  for (let depth = 0; depth < queue.length; depth++) {
    const next = queue[depth];
    const nextBeam = [];
    for (const state of beam) {
      const options = candidates(state.board, next, garbage.columns)
        .map(x => evaluate(state.board, next, x, garbage))
        .sort(compareMove);
      const bestRisk = options[0].risk;
      for (const move of options.filter(o => o.risk === bestRisk).slice(0, PER_NODE_BRANCH)) {
        nextBeam.push({
          root: state.root,
          board: move.pieces,
          totalValue: state.totalValue + LOOKAHEAD_DISCOUNT[depth + 1] * (move.value - move.risk * 420),
          pathRisk: Math.max(state.pathRisk, move.risk),
          minClearance: Math.min(state.minClearance, move.clearance),
          nodes: state.nodes + 1,
        });
      }
      expanded += Math.min(PER_NODE_BRANCH, options.length);
    }
    beam = nextBeam.sort(comparePath).slice(0, FUTURE_BEAM);
    if (beam.length === 0) break;
  }

  const winner = beam.sort(comparePath)[0];
  return {
    ...winner.root,
    value: winner.totalValue,
    pathRisk: winner.pathRisk,
    minClearance: winner.minClearance,
    expandedNodes: expanded,
    depth: queue.length,
  };
}

function reserveValue(piece, board) {
  if (!piece || piece.type <= 0 || piece.type >= 15) return 0;
  const exposed = exposedPieces(board).filter(p => p.type === piece.type);
  if (exposed.length === 0) return piece.type * 0.4;
  const best = Math.min(...exposed.map(p => Math.abs(p.x)));
  return 8 + piece.type * 1.7 - best * 0.4;
}

export function decide(boardState) {
if (!boardState || !Array.isArray(boardState.pieces) || boardState.pieces.length > 256) {
throw new TypeError('Unusable Soren91 board observation');
}
const pieces = boardState.pieces.map(p => normalizePiece(p, true));
const current = normalizePiece(boardState.next);
if (certainty(current) < 0.5) throw new TypeError('Uncertain Soren91 current piece');
const garbage = {
ratio: Number.isFinite(boardState.garbage?.ratio) ? boardState.garbage.ratio : 0,
gauge: Number.isFinite(boardState.garbage?.gauge) ? boardState.garbage.gauge : 0,
columns: Array.isArray(boardState.garbage?.columns) ? boardState.garbage.columns.slice(0, 64) : [],
};
// A current piece with no reachable equal-type partner has nothing to merge
// into; HOLD may then be used to escape a dead piece, but only when the
// swapped plan is at least as safe as the real non-HOLD plan.
let currentPartner = false;
for (const p of pieces) {
if (p.type !== current.type || certainty(p) < 0.6) continue;
const reach = current.r + p.r + 0.9;
if (Math.abs(p.x - current.x) <= reach) { currentPartner = true; break; }
}
const normal = search(pieces, current, knownQueue(boardState.nextPieces, 1), garbage);
let chosen = normal;
let hold = false;
if (boardState.canHold) {
const emptyHold = boardState.hold == null && boardState.holdKnownEmpty === true;
const alternative = boardState.hold || (emptyHold ? boardState.nextPieces?.[1] : null);
if (alternative && TYPE_RADII[alternative.type] && certainty(alternative) >= 0.6) {
const held = normalizePiece(alternative);
const result = search(
pieces,
held,
knownQueue(boardState.nextPieces, boardState.hold ? 1 : 2),
garbage,
);
// HOLD changes ordering, and the current piece becomes useful reserve
// material. Keep a meaningful hysteresis so tiny heuristic noise does not
// burn HOLD, while allowing it to escape a worse future risk tier.
const holdValue = result.value + reserveValue(current, pieces);
const normalValue = normal.value;
const betterRisk = result.pathRisk < normal.pathRisk
|| (result.pathRisk === normal.pathRisk && result.minClearance > normal.minClearance + 0.18);
const betterPlan = result.pathRisk === normal.pathRisk && holdValue > normalValue + 14
&& (result.pathRisk !== 2 || result.minClearance >= normal.minClearance);
const escapeStuck = !currentPartner && held.type !== current.type
&& result.pathRisk <= normal.pathRisk
&& result.minClearance >= normal.minClearance - 0.1;
if (held.type !== current.type && (betterRisk || betterPlan || escapeStuck)) {
chosen = { ...result, value: holdValue };
hold = true;
}
}
}
return {
x: chosen.x,
hold,
reason: (hold ? 'HOLD: ' : '') + (chosen.merges ? 'reachable-merge' : 'structured-stack')
+ '; risk=' + chosen.risk + '; pathRisk=' + chosen.pathRisk
+ '; clearance=' + chosen.clearance.toFixed(2) + '; depth=' + chosen.depth,
diagnostics: {
version: 'beam-v2',
risk: chosen.risk,
pathRisk: chosen.pathRisk,
clearance: chosen.clearance,
minFutureClearance: chosen.minClearance,
landingY: chosen.landingY,
merges: chosen.merges,
heuristicValue: chosen.value,
searchDepth: chosen.depth,
expandedNodes: chosen.expandedNodes,
pairPotential: chosen.structure.pairPotential,
roughness: chosen.structure.roughness,
pocketPenalty: chosen.structure.pocketPenalty,
},
};
}

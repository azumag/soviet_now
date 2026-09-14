/**
 * Soren91: bounded, survival-first geometric search.
 * Self-contained: improve.mjs and the per-round snapshot loader import data URLs.
 * This is a conservative circle model, NOT Unity physics or a win-rate claim.
 */
export const TYPE_RADII = Object.freeze({
  1: 0.207, 2: 0.259, 3: 0.316, 4: 0.380, 5: 0.414,
  6: 0.470, 7: 0.559, 8: 0.660, 9: 0.746, 10: 0.846,
  11: 0.982, 12: 1.068, 13: 1.207, 14: 1.385, 15: 1.600,
});
const FLOOR = -5;
const DEADLINE = 3.32;
const WALL = 3.5;
const MARGIN = 0.08;
const BEAM = 6;
const MAX_CANDIDATES = 49;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const certainty = p => p?.fallback ? 0 : Number.isFinite(p?.confidence) ? clamp(p.confidence, 0, 1) : 1;

function normalizePiece(p, positioned = false) {
  if (!p || !Number.isInteger(p.type) || !TYPE_RADII[p.type]) {
    // Unknown detections still occupy space; never discard them to invent a gap.
    if (positioned && p && Number.isFinite(p.x) && Number.isFinite(p.y)
        && Number.isFinite(p.r) && p.r > 0 && p.r <= 3.5) {
      return { x: p.x, y: p.y, r: p.r, type: 0, confidence: 0 };
    }
    throw new TypeError('Unusable Soren91 piece observation');
  }
  // Preview icons are scaled independently from the board; use canonical
  // radii for all future drops, never their UI/measured radius.
  const r = positioned && Number.isFinite(p.r) && p.r > 0 && p.r <= 3.5
    ? p.r : TYPE_RADII[p.type];
  if (positioned && (!Number.isFinite(p.x) || !Number.isFinite(p.y))) {
    throw new TypeError('Unusable Soren91 piece position');
  }
  return { ...p, r, confidence: certainty(p) };
}

function limitFor(p) { return Math.min(3, WALL - p.r - 0.02); }

/** First vertical circle contact. Independent of detector enumeration order. */
export function landingAt(pieces, piece, x, columns = []) {
  let y = FLOOR + piece.r;
  for (const p of pieces) {
    const rr = piece.r + p.r;
    const dx = x - p.x;
    if (Math.abs(dx) <= rr) y = Math.max(y, p.y + Math.sqrt(Math.max(0, rr * rr - dx * dx)));
  }
  // Image-derived garbage surfaces are obstacles, not mergeable circles.
  for (const c of columns) {
    if (![c?.left, c?.right, c?.top].every(Number.isFinite) || c.left > c.right) continue;
    const dx = Math.max(c.left - x, x - c.right, 0);
    if (dx <= piece.r) y = Math.max(y, c.top + Math.sqrt(Math.max(0, piece.r * piece.r - dx * dx)));
  }
  return y;
}

function candidates(pieces, piece) {
  const limit = limitFor(piece);
  const xs = new Set([0, -limit, limit]);
  // Preserve all uniform escape lanes, then add exact targets and small offsets.
  for (let x = -3; x <= 3.001; x += 0.25) xs.add(clamp(x, -limit, limit));
  const targets = [...pieces].sort((a, b) =>
    Number(b.type === piece.type) - Number(a.type === piece.type) || b.y - a.y || a.x - b.x);
  for (const p of targets) {
    for (const x of [p.x, p.x - piece.r * 0.5, p.x + piece.r * 0.5]) {
      if (xs.size >= MAX_CANDIDATES) break;
      xs.add(clamp(x, -limit, limit));
    }
    if (xs.size >= MAX_CANDIDATES) break;
  }
  return [...xs].sort((a, b) => a - b);
}

/**
 * Only award merges at first contact. A lower equal type hidden under another
 * piece is not reachable. Promotion removes its consumed target. No magical
 * garbage deletion or unconstrained chain/rolling prediction is assumed.
 */
export function simulateDrop(pieces, piece, x, columns = []) {
  let board = pieces.map(p => ({ ...p }));
  let placed = { ...piece, x, y: landingAt(board, piece, x, columns) };
  const landingY = placed.y;
  let peak = placed.y + placed.r;
  let merges = 0;
  let mergeValue = 0;
  for (let step = 0; step < 14 && placed.type > 0 && placed.type < 15; step++) {
    if (certainty(placed) < 0.6) break;
    const touching = board.map((p, i) => ({ p, i,
      gap: Math.hypot(p.x - placed.x, p.y - placed.y) - p.r - placed.r,
    })).filter(t => Math.abs(t.gap) <= 0.035);
    // A tied different/uncertain support makes the alleged merge non-certain.
    if (touching.some(t => t.p.type !== placed.type || certainty(t.p) < 0.6)) break;
    const target = touching.filter(t => t.p.type === placed.type)
      .sort((a, b) => a.gap - b.gap || a.p.x - b.p.x)[0];
    if (!target) break;
    const type = placed.type + 1;
    const r = TYPE_RADII[type];
    const mergedX = clamp((placed.x + target.p.x) / 2, -WALL + r, WALL - r);
    board.splice(target.i, 1);
    // Re-settle the promoted circle conservatively at its merged horizontal
    // location; other pieces are deliberately not re-simulated or removed.
    placed = { type, r, x: mergedX, y: landingAt(board, { r }, mergedX, columns),
      confidence: Math.min(certainty(placed), certainty(target.p)) };
    peak = Math.max(peak, placed.y + r);
    merges++;
    mergeValue += type;
  }
  board.push(placed);
  return { pieces: board, placed, landingY, peak, merges, mergeValue };
}

function evaluate(pieces, piece, x, garbage) {
  const sim = simulateDrop(pieces, piece, x, garbage.columns);
  const tops = sim.pieces.map(p => p.y + p.r);
  const maxTop = Math.max(FLOOR, ...tops);
  const clearance = DEADLINE - Math.max(sim.peak, maxTop);
  const pressure = clamp(garbage.gauge, 0, 1) + clamp(garbage.ratio, 0, 1);
  // Survival tiers are compared BEFORE all rewards; a merge cannot buy a fatal
  // drop, unlike the v221 negative-penalty sign error.
  const risk = clearance <= MARGIN ? 2 : clearance < 0.65 ? 1 : 0;
  let roughness = 0;
  const heights = [];
  for (let col = -3; col <= 3; col += 0.5) {
    heights.push(landingAt(sim.pieces, { r: 0.1 }, col, garbage.columns));
  }
  for (let i = 1; i < heights.length; i++) roughness += Math.abs(heights[i] - heights[i - 1]);
  const meanHeight = heights.reduce((a, b) => a + b, 0) / heights.length - FLOOR;
  const uncertain = pieces.filter(p => certainty(p) < 0.6 && Math.abs(p.x - x) < p.r + piece.r).length;
  let futurePairs = 0;
  for (const p of sim.pieces) {
    if (p === sim.placed || p.type !== sim.placed.type || p.type >= 15 || certainty(p) < 0.6) continue;
    const gap = Math.hypot(p.x - sim.placed.x, p.y - sim.placed.y) - p.r - sim.placed.r;
    if (gap > 0 && gap < 0.8) futurePairs += (0.8 - gap) * 8;
  }
  const value = sim.mergeValue * (22 + 12 * pressure) + futurePairs
    - meanHeight * 9 - roughness * 2 - Math.max(0, maxTop + 1) ** 2 * 25
    - Math.max(0, sim.peak + 1) ** 2 * 30 - uncertain * 8 - Math.abs(x) * 0.1;
  return { ...sim, x, risk, clearance, value };
}

function compare(a, b) {
  return a.risk - b.risk
    || (a.risk === 2 ? b.clearance - a.clearance : 0)
    || b.value - a.value || b.clearance - a.clearance || Math.abs(a.x) - Math.abs(b.x) || a.x - b.x;
}

function knownQueue(queue, start) {
  const out = [];
  for (const p of (Array.isArray(queue) ? queue : []).slice(start, start + 2)) {
    // A hole means the next turn is UNKNOWN, not that the following slot moves up.
    if (!p || !TYPE_RADII[p.type] || certainty(p) < 0.6) break;
    out.push(normalizePiece(p));
  }
  return out;
}

function search(board, piece, queue, garbage) {
  const roots = candidates(board, piece).map(x => evaluate(board, piece, x, garbage)).sort(compare);
  const bestRisk = roots[0].risk;
  const beam = roots.filter(r => r.risk === bestRisk).slice(0, BEAM);
  for (const root of beam) {
    let futureBoard = root.pieces;
    let discount = 0.4;
    for (const next of queue) {
      const options = candidates(futureBoard, next).map(x => evaluate(futureBoard, next, x, garbage)).sort(compare);
      const best = options[0];
      // Negative future values must remain negative, never max(0, bad future).
      root.value += discount * (best.value - best.risk * 400);
      futureBoard = best.pieces;
      discount *= 0.4;
    }
  }
  return beam.sort(compare)[0];
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
  const normal = search(pieces, current, knownQueue(boardState.nextPieces, 1), garbage);
  let chosen = normal;
  let hold = false;
  if (boardState.canHold) {
    const alternative = boardState.hold || (boardState.holdKnownEmpty === true ? boardState.nextPieces?.[1] : null);
    if (alternative && TYPE_RADII[alternative.type] && certainty(alternative) >= 0.6) {
      const held = normalizePiece(alternative);
      const result = search(pieces, held, knownQueue(boardState.nextPieces, boardState.hold ? 1 : 2), garbage);
      // Hysteresis: do not spend HOLD on a numerical tie or the same piece.
      if (held.type !== current.type && (result.risk < normal.risk
          || (result.risk === normal.risk && result.value > normal.value + 12
              && (result.risk !== 2 || result.clearance >= normal.clearance)))) {
        chosen = result;
        hold = true;
      }
    }
  }
  return {
    x: chosen.x, hold,
    reason: `${hold ? 'HOLD: ' : ''}${chosen.merges ? 'reachable-merge' : 'low-stack'}; risk=${chosen.risk}; clearance=${chosen.clearance.toFixed(2)}; merges=${chosen.merges}`,
    diagnostics: { version: 'geometry-v1', risk: chosen.risk, clearance: chosen.clearance,
      landingY: chosen.landingY, merges: chosen.merges, heuristicValue: chosen.value },
  };
}

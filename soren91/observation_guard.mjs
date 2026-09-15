/** Temporal state is kept outside the hot-reloaded analyzer, per calibration. */
const observations = new WeakMap();

export const DEFAULT_MAX_STALE_MS = 15_000;
export const DEFAULT_SINGLE_FRAME_ADVANCE_MS = 2_500;

export function maxStaleMs(env = process.env) {
  const raw = Number(env?.SOREN91_OBSERVATION_MAX_STALE_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_STALE_MS;
}

export function singleFrameAdvanceMs(env = process.env) {
  const raw = Number(env?.SOREN91_SINGLE_FRAME_ADVANCE_MS);
  return Number.isFinite(raw) && raw >= 1000 ? raw : DEFAULT_SINGLE_FRAME_ADVANCE_MS;
}

export function slowCadenceFastPathEnabled(env = process.env) {
  const explicit = String(env?.SOREN91_SINGLE_FRAME_ADVANCE || '').trim().toLowerCase();
  if (['0', 'false', 'no', 'off'].includes(explicit)) return false;
  if (['1', 'true', 'yes', 'on'].includes(explicit)) return true;
  // The measured 5-6s cadence is specifically the remote-CDP path. Keep the
  // stricter two-frame rule for the normal local loop unless explicitly opted in.
  return !!String(env?.SOREN91_REMOTE_CDP_URL || '').trim();
}

export function usableCalibration(cal, width, height) {
  const b = cal?.board;
  return !!b && !cal.provisional && !cal.isFallback && (cal.confidence ?? 0) >= 0.6
    && [b.left, b.right, b.top, b.bottom, b.width, b.height].every(Number.isFinite)
    && b.left >= 0 && b.top >= 0 && b.right <= width && b.bottom <= height
    && b.width > 20 && b.height > 20
    && Math.abs(b.width - (b.right - b.left)) < 1
    && Math.abs(b.height - (b.bottom - b.top)) < 1
    && (!cal.screen || (cal.screen.width === width && cal.screen.height === height));
}

function knownPreview(piece) {
  return !!piece && Number.isInteger(piece.type) && !piece.fallback && (piece.confidence ?? 0) >= 0.58;
}

function previewType(piece) {
  return knownPreview(piece) ? piece.type : null;
}

function copyPreview(piece, source) {
  if (!knownPreview(piece)) return null;
  return {
    ...piece,
    confidence: Math.max(0.6, Math.min(Number(piece.confidence ?? 0.6), source === 'detected' ? 1 : 0.72)),
    temporalSource: source,
  };
}

function rawQueue(state) {
  const queue = Array.isArray(state.nextPieces) ? state.nextPieces.slice(0, 3) : [];
  while (queue.length < 3) queue.push(null);
  if (!queue[0] && knownPreview(state.next)) queue[0] = state.next;
  return queue;
}

function classifyQueueTransition(previousQueue, currentQueue) {
  if (!previousQueue || previousQueue.length === 0) return 'unknown';
  const p = previousQueue;
  const c = currentQueue;

  let advanceEvidence = 0;
  let advanceConflict = 0;
  if (previewType(p[1]) != null && previewType(c[0]) != null) {
    if (previewType(p[1]) === previewType(c[0])) advanceEvidence += 2;
    else advanceConflict += 2;
  }
  if (previewType(p[2]) != null && previewType(c[1]) != null) {
    if (previewType(p[2]) === previewType(c[1])) advanceEvidence += 2;
    else advanceConflict += 1;
  }
  if (previewType(p[0]) != null && previewType(c[0]) != null && previewType(p[0]) !== previewType(c[0])) {
    advanceEvidence += 1;
  }
  if (advanceEvidence >= 2 && advanceConflict === 0) return 'advanced';

  let sameEvidence = 0;
  let sameConflict = 0;
  for (let i = 0; i < 3; i++) {
    const a = previewType(p[i]);
    const b = previewType(c[i]);
    if (a == null || b == null) continue;
    if (a === b) sameEvidence++;
    else sameConflict++;
  }
  if (sameEvidence >= 1 && sameConflict === 0) return 'same';
  return 'unknown';
}

function stabilizeQueue(currentQueue, previous, transition) {
  const out = currentQueue.map(p => copyPreview(p, 'detected'));
  const previousQueue = previous?.nextPieces || [];
  if (transition === 'advanced') {
    // Previous [current, next1, next2] becomes [next1, next2, unknown].
    // Reuse only an actually observed preview; never invent a type.
    if (!out[0] && knownPreview(previousQueue[1])) out[0] = copyPreview(previousQueue[1], 'shifted');
    if (!out[1] && knownPreview(previousQueue[2])) out[1] = copyPreview(previousQueue[2], 'shifted');
  } else if (transition === 'same') {
    for (let i = 0; i < 3; i++) {
      if (!out[i] && knownPreview(previousQueue[i])) out[i] = copyPreview(previousQueue[i], 'same-turn');
    }
  }
  return out;
}

function stableBoard(previous, state) {
  const unmatched = previous.pieces.map(p => ({ ...p }));
  let stable = previous.pieces.length === state.pieces.length;
  for (const p of state.pieces) {
    let best = -1;
    let distance = Infinity;
    for (let i = 0; i < unmatched.length; i++) {
      const q = unmatched[i];
      const d = Math.hypot(p.x - q.x, p.y - q.y);
      if ((q.type === p.type || (q.confidence < 0.6 && p.confidence < 0.6))
          && Math.abs(q.r - p.r) < 0.08 && d < distance) {
        distance = d;
        best = i;
      }
    }
    if (best < 0 || distance > 0.12) stable = false;
    else unmatched.splice(best, 1);
  }
  return stable;
}

function stableGarbage(previous, state) {
  const previousColumns = previous.garbage?.columns || [];
  const columns = state.garbage?.columns || [];
  return columns.length === previousColumns.length && columns.every(c =>
    previousColumns.some(p => Math.abs(c.left - p.left) < 0.02
      && Math.abs(c.right - p.right) < 0.02 && Math.abs(c.top - p.top) < 0.12));
}

export function gateObservation(state, calibration, now = Date.now()) {
  if (!calibration || typeof calibration !== 'object') throw new TypeError('Calibration object required');
  const previous = observations.get(calibration);
  if (state.state !== 'MOVE') {
    observations.delete(calibration);
    return { ...state, holdKnownEmpty: false,
      perception: { ready: false, reason: state.perception?.reason || 'non-move', stableFrames: 0 } };
  }

  const geometry = JSON.stringify(calibration.board);
  const detectedQueue = rawQueue(state);
  const transition = classifyQueueTransition(previous?.nextPieces, detectedQueue);
  const queue = stabilizeQueue(detectedQueue, previous, transition);
  const next = queue[0] || state.next || null;
  const gapMs = previous ? now - previous.at : null;
  const temporalNextUsed = queue.slice(1).some(piece =>
    piece?.temporalSource === 'shifted' || piece?.temporalSource === 'same-turn');

  const current = {
    ...state,
    next,
    nextPieces: queue,
    geometry,
    at: now,
    stableFrames: 1,
    pieces: state.pieces.map(p => ({ type: p.type, x: p.x, y: p.y, r: p.r, confidence: p.confidence })),
  };

  let reason = null;
  let slowAdvanceUsed = false;
  if (!next || next.fallback || !(next.confidence >= 0.58)) reason = 'unknown-current';
  else if (!usableCalibration(calibration, calibration.screen?.width, calibration.screen?.height)) reason = 'uncalibrated';
  else if (state.pieces.length > 256 || state.pieces.some(p => ![p.x, p.y, p.r].every(Number.isFinite) || p.r <= 0)) reason = 'invalid-board';
  else if (!previous || !previous.usable || previous.geometry !== geometry || now < previous.at) reason = 'confirm-frame';
  else if (gapMs > maxStaleMs()) reason = 'confirm-frame';
  else {
    // Remote CDP spends ~5-6s obtaining one observation. When the preview queue
    // proves that a turn advanced and that much real time has elapsed, waiting
    // for a second full remote screenshot only halves APM without adding useful
    // settling evidence. Local/fast paths retain the strict two-frame check.
    slowAdvanceUsed = transition === 'advanced'
      && slowCadenceFastPathEnabled()
      && gapMs >= singleFrameAdvanceMs();
    if (slowAdvanceUsed) {
      current.stableFrames = 2;
    } else if (previewType(previous.next) !== previewType(next)) {
      reason = 'preview-changed';
    } else if (!stableBoard(previous, state) || !stableGarbage(previous, state)) {
      reason = 'board-moving';
    } else {
      current.stableFrames = Math.min(3, previous.stableFrames + 1);
    }
  }

  current.usable = !['unknown-current', 'uncalibrated', 'invalid-board'].includes(reason);

  // HOLD: two trustworthy empty observations establish an empty slot. Once a
  // non-empty HOLD has ever been seen in this round, later misses are treated as
  // recognition misses rather than as an empty slot, preventing false swaps.
  const rawHoldPresent = Boolean(state.hold);
  const rawHoldKnown = rawHoldPresent && !state.hold.fallback && state.hold.confidence >= 0.6;
  const holdEverSeen = Boolean(previous?.holdEverSeen || rawHoldKnown);
  let emptyHoldFrames = 0;
  if (rawHoldKnown) emptyHoldFrames = 0;
  else if (!holdEverSeen && !rawHoldPresent && current.usable) {
    emptyHoldFrames = (previous?.emptyHoldFrames || 0) + 1;
  }
  current.holdEverSeen = holdEverSeen;
  current.emptyHoldFrames = emptyHoldFrames;

  const confirmedHold = rawHoldKnown && previous?.usable && previous.hold
    && state.hold.type === previous.hold.type && previous.hold.confidence >= 0.6
    && !previous.hold.fallback;
  const holdKnownEmpty = !holdEverSeen && emptyHoldFrames >= 2;

  let stableReason = slowAdvanceUsed ? 'stable-slow-advance' : 'stable';
  if (temporalNextUsed) stableReason += '-temporal-next';

  observations.set(calibration, current);
  return {
    ...state,
    next,
    nextPieces: queue,
    hold: confirmedHold ? state.hold : null,
    holdKnownEmpty,
    state: reason ? 'DROP' : 'MOVE',
    perception: {
      ready: !reason,
      reason: reason || stableReason,
      stableFrames: current.stableFrames,
      queueTransition: transition,
      frameGapMs: gapMs,
    },
  };
}

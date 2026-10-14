/** Temporal state is kept outside the hot-reloaded analyzer, per calibration. */
import { isUsableCalibration } from './calibration_contract.mjs';

const observations = new WeakMap();
const pendingDrops = new WeakMap();

export const DEFAULT_MAX_STALE_MS = 15_000;
export const DEFAULT_POSTDROP_BOARD_ADVANCE_MS = 3_000;
// Remote (Mac renderer) captures cost ~2.2-2.4s each, so consecutive
// observations sit just BELOW the old 2500ms guard: the slow-cadence fast
// path almost never fired and every turn fell back to the strict two-frame
// stability gate, which re-observed 4-6 times per drop (measured 11-16s
// turns vs 3.7s when the fast path did fire). The observed queue shift supports
// a new controllable piece; 1.2s matches the game-side minimum drop
// spacing (DROP_COOLDOWN_MS) and stays env-overridable. Queue evidence is
// observational support, not authoritative confirmation of game acceptance.
export const DEFAULT_SINGLE_FRAME_ADVANCE_MS = 1_200;

export function maxStaleMs(env = process.env) {
  const raw = Number(env?.SOREN91_OBSERVATION_MAX_STALE_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_STALE_MS;
}

export function singleFrameAdvanceMs(env = process.env) {
  const raw = Number(env?.SOREN91_SINGLE_FRAME_ADVANCE_MS);
  return Number.isFinite(raw) && raw >= 1000 ? raw : DEFAULT_SINGLE_FRAME_ADVANCE_MS;
}

// The strict 'advanced' classification needs cross-slot evidence (>=2). On the
// remote cadence that evidence is often unavailable (only queue[0] is freshly
// detected each frame), so turns fell back to the strict two-frame gate and
// re-observed 2-3 times per drop even after the 1.2s time floor was fixed.
// A conflict-free single-evidence advance (queue[0] changed, no contradicting
// slot) is the same post-drop signal the game itself gives; it only widens the
// SLOW-CADENCE readiness fast path (queue data stabilization still uses the
// strict transition), so the temporal-next filling is unchanged. Kill switch:
// SOREN91_ADVANCE_SINGLE_EVIDENCE=0.
export function singleEvidenceAdvanceEnabled(env = process.env) {
  const explicit = String(env?.SOREN91_ADVANCE_SINGLE_EVIDENCE || '').trim().toLowerCase();
  if (['0', 'false', 'no', 'off'].includes(explicit)) return false;
  return true;
}

export function slowCadenceFastPathEnabled(env = process.env) {
  const explicit = String(env?.SOREN91_SINGLE_FRAME_ADVANCE || '').trim().toLowerCase();
  if (['0', 'false', 'no', 'off'].includes(explicit)) return false;
  if (['1', 'true', 'yes', 'on'].includes(explicit)) return true;
  return !!String(env?.SOREN91_REMOTE_CDP_URL || '').trim();
}

// A successfully sent drop creates one narrow turn-boundary opportunity. On
// remote capture, the first post-click frame can arrive several seconds later;
// comparing that frame to the PRE-drop board otherwise mislabels the expected
// new stack/current as board-moving/preview-changed and forces another costly
// screenshot. The boundary is one-shot and remote-only by default.
export function postDropSingleFrameEnabled(env = process.env) {
  const explicit = String(env?.SOREN91_POSTDROP_SINGLE_FRAME || '').trim().toLowerCase();
  if (['0', 'false', 'no', 'off'].includes(explicit)) return false;
  if (['1', 'true', 'yes', 'on'].includes(explicit)) return true;
  return !!String(env?.SOREN91_REMOTE_CDP_URL || '').trim();
}

export function postDropBoardAdvanceMs(env = process.env) {
  const raw = Number(env?.SOREN91_POSTDROP_BOARD_ADVANCE_MS);
  return Number.isFinite(raw) && raw >= DEFAULT_SINGLE_FRAME_ADVANCE_MS
    ? raw
    : DEFAULT_POSTDROP_BOARD_ADVANCE_MS;
}

// How long a sent drop keeps offering its post-click advance path. Three
// remote frames (~1.2s each) fit the default, which covers the frame that
// cannot identify the new piece yet plus the first frame that can.
export const DEFAULT_POSTDROP_WINDOW_MS = 4_000;

export function postDropWindowMs(env = process.env) {
  const raw = Number(env?.SOREN91_POSTDROP_WINDOW_MS);
  return Number.isFinite(raw) && raw >= DEFAULT_SINGLE_FRAME_ADVANCE_MS
    ? raw
    : DEFAULT_POSTDROP_WINDOW_MS;
}

// Bounded escape for a persistent recognition hold. Measured 2026-10-15
// (docich#414): a cursor joined to the deadline's antialias fringe and hidden
// behind the HUD repeatedly reports unknown-current without any error.
// A rejected observation means no input, so the same frame can repeat
// indefinitely. Separate stored traces showed 12 consecutive holds and a
// completed round ending 93s after its final drop. After a bounded run of consecutive holds on a healthy frame
// (see the classification order in gateObservation) the loop may send one
// fallback drop at the centre so the game advances. Every other reason resets
// the run, so ordinary waits and settling frames never become blind drops.
// Owner-approved bounded relaxation of the "no blind timeout drop" rule
// (2026-10-15); kill switch: SOREN91_HOLD_ESCAPE_FRAMES=0.
export const DEFAULT_HOLD_ESCAPE_FRAMES = 6;
export const DEFAULT_HOLD_ESCAPE_INTERVAL_MS = 4_000;
export const DEFAULT_HOLD_ESCAPE_LIMIT = 3;

export function holdEscapeFrames(env = process.env) {
  const raw = Number(env?.SOREN91_HOLD_ESCAPE_FRAMES);
  return Number.isInteger(raw) && raw >= 0 && raw <= 200 ? raw : DEFAULT_HOLD_ESCAPE_FRAMES;
}

export function holdEscapeIntervalMs(env = process.env) {
  const raw = Number(env?.SOREN91_HOLD_ESCAPE_INTERVAL_MS);
  return Number.isFinite(raw) && raw >= 0 && raw <= 60_000 ? raw : DEFAULT_HOLD_ESCAPE_INTERVAL_MS;
}

export function holdEscapeLimit(env = process.env) {
  const raw = Number(env?.SOREN91_HOLD_ESCAPE_LIMIT);
  return Number.isInteger(raw) && raw >= 0 && raw <= 100 ? raw : DEFAULT_HOLD_ESCAPE_LIMIT;
}

/**
 * Counts consecutive `unknown-current` observations and permits at most
 * `limit` fallback drops per stretch, never closer than `intervalMs`.
 * `noteDrop()` is called after any real (strategy) drop: it ends the stretch.
 */
export function createHoldEscapePolicy(env = process.env) {
  const frames = holdEscapeFrames(env);
  const intervalMs = holdEscapeIntervalMs(env);
  const limit = holdEscapeLimit(env);
  let holds = 0;
  let escapes = 0;
  let lastAt = -Infinity;
  return {
    observe(reason, now = Date.now()) {
      if (frames <= 0 || limit <= 0) return false;
      if (reason !== 'unknown-current') {
        holds = 0;
        return false;
      }
      holds += 1;
      if (holds < frames || escapes >= limit || now - lastAt < intervalMs) return false;
      holds = 0;
      escapes += 1;
      lastAt = now;
      return true;
    },
    noteDrop() {
      holds = 0;
      escapes = 0;
      lastAt = -Infinity;
    },
    reset() {
      holds = 0;
      escapes = 0;
      lastAt = -Infinity;
    },
    snapshot() {
      return { holds, escapes };
    },
  };
}

export function markDropSent(calibration, now = Date.now()) {
  if (!calibration || typeof calibration !== 'object') return false;
  const previous = observations.get(calibration);
  if (!previous || previous.stableFrames < 2
      || !Number.isFinite(now) || now < previous.at) {
    pendingDrops.delete(calibration);
    return false;
  }
  // The placed piece's type is the baseline for the expected change: after a
  // sent drop the next controllable piece is a different one, so comparing
  // against it proves the queue advanced even when the first post-click frame
  // could not identify anything.
  pendingDrops.set(calibration, { at: now, previousAt: previous.at,
    previousCurrent: previewType(previous.next) });
  return true;
}

export function usableCalibration(cal, width, height) {
  return isUsableCalibration(cal, width, height);
}

function knownPreview(piece) {
  return !!piece && Number.isInteger(piece.type) && piece.type >= 1 && piece.type <= 15
    && !piece.fallback && (piece.confidence ?? 0) >= 0.58;
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

/** Conflict-free queue-advance evidence between two observations. */
export function queueAdvanceEvidence(previousQueue, currentQueue) {
  const p = Array.isArray(previousQueue) ? previousQueue : [];
  const c = Array.isArray(currentQueue) ? currentQueue : [];
  let evidence = 0;
  let conflict = 0;
  if (previewType(p[1]) != null && previewType(c[0]) != null) {
    if (previewType(p[1]) === previewType(c[0])) evidence += 2;
    else conflict += 2;
  }
  if (previewType(p[2]) != null && previewType(c[1]) != null) {
    if (previewType(p[2]) === previewType(c[1])) evidence += 2;
    else conflict += 1;
  }
  if (previewType(p[0]) != null && previewType(c[0]) != null && previewType(p[0]) !== previewType(c[0])) {
    evidence += 1;
  }
  // Repeated equal types fit a shifted queue even when nothing moved. Cross-
  // slot matches only prove an advance when a freshly observed slot changed.
  const observedChange = p.some((piece, i) => previewType(piece) != null
    && previewType(c[i]) != null && previewType(piece) !== previewType(c[i]));
  if (!observedChange) evidence = 0;
  return { evidence, conflict };
}

function classifyQueueTransition(previousQueue, currentQueue) {
  if (!previousQueue || previousQueue.length === 0) return 'unknown';
  const p = previousQueue;
  const c = currentQueue;
  const { evidence: advanceEvidence, conflict: advanceConflict } =
    queueAdvanceEvidence(previousQueue, currentQueue);
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
    if (!out[1] && knownPreview(previousQueue[2])) out[1] = copyPreview(previousQueue[2], 'shifted');
  } else if (transition === 'same') {
    // The current piece must be visible in this frame. Only future previews
    // can be carried across observations; a hidden current may be falling.
    for (let i = 1; i < 3; i++) {
      if (!out[i] && knownPreview(previousQueue[i])) out[i] = copyPreview(previousQueue[i], 'same-turn');
    }
  }
  return out;
}

function stableBoard(previous, state) {
  if (previous.pieces.length !== state.pieces.length) return false;
  const candidates = state.pieces.map(p => {
    const matches = [];
    for (let i = 0; i < previous.pieces.length; i++) {
      const q = previous.pieces[i];
      const d = Math.hypot(p.x - q.x, p.y - q.y);
      // A sampled flag can cross a type boundary without physically changing.
      // Keep the existing canonical-size match (rotation changes the measured
      // outline), and also accept a matching measured size across a type jump.
      // Neither kind of agreement bypasses position, count or garbage checks.
      const measured = Number.isFinite(p.measuredRadius) && p.measuredRadius > 0
        && Number.isFinite(q.measuredRadius) && q.measuredRadius > 0;
      const sameSize = Math.abs(q.r - p.r) < 0.08
        || (measured && Math.abs(q.measuredRadius - p.measuredRadius) < 0.08);
      if (sameSize && d <= 0.12) matches.push(i);
    }
    return matches;
  });
  // Removing type as a motion signal creates more possible correspondences.
  // Nearest-first can steal the only match of another fragment and make the
  // result depend on detector enumeration. Require a one-to-one assignment;
  // the existing 256-piece validity bound also bounds this search.
  const assigned = new Array(previous.pieces.length).fill(-1);
  function assign(piece, seen) {
    for (const candidate of candidates[piece]) {
      if (seen[candidate]) continue;
      seen[candidate] = 1;
      if (assigned[candidate] === -1 || assign(assigned[candidate], seen)) {
        assigned[candidate] = piece;
        return true;
      }
    }
    return false;
  }
  return candidates.every((_matches, i) => assign(i, new Uint8Array(assigned.length)));
}

function stableGarbage(previous, state) {
  const previousColumns = previous.garbage?.columns || [];
  const columns = state.garbage?.columns || [];
  return columns.length === previousColumns.length && columns.every(c =>
    previousColumns.some(p => Math.abs(c.left - p.left) < 0.02
      && Math.abs(c.right - p.right) < 0.02 && Math.abs(c.top - p.top) <= 0.12 + 1e-9));
}

export function gateObservation(state, calibration, now = Date.now()) {
  if (!calibration || typeof calibration !== 'object') throw new TypeError('Calibration object required');
  const previous = observations.get(calibration);
  if (state.state !== 'MOVE') {
    observations.delete(calibration);
    pendingDrops.delete(calibration);
    return { ...state, holdKnownEmpty: false,
      perception: { ready: false, reason: state.perception?.reason || 'non-move', stableFrames: 0 } };
  }

  const geometry = JSON.stringify({ coordinateSchema: calibration.coordinateSchema,
    board: calibration.board, arena: calibration.arena, hud: calibration.hud });
  const detectedQueue = rawQueue(state);
  const transition = classifyQueueTransition(previous?.detectedQueue, detectedQueue);
  const queue = stabilizeQueue(detectedQueue, previous, transition);
  const next = queue[0] || null;
  const gapMs = previous ? now - previous.at : null;
  const temporalNextUsed = queue.slice(1).some(piece =>
    piece?.temporalSource === 'shifted' || piece?.temporalSource === 'same-turn');

  const pendingDrop = pendingDrops.get(calibration);
  const postDropAgeMs = pendingDrop ? now - pendingDrop.at : null;
  // The post-click boundary stays available for a bounded window instead of a
  // single frame. On the remote cadence the first frame after the click often
  // cannot identify the new controllable piece yet (the previous one is still
  // falling), and a one-shot boundary made the whole post-drop advance path
  // unreachable there: measured 5-6 observations and 9-10s per drop, where the
  // intended fast path is 2 (docich#414 speed work). Bounded by wall time so a
  // click that was never accepted cannot reopen the path much later.
  const postDropWindow = !!pendingDrop && Number.isFinite(postDropAgeMs)
    && postDropAgeMs >= 0 && postDropAgeMs <= postDropWindowMs();
  const postDropBoundary = postDropWindow && !!previous
    && pendingDrop.previousAt <= previous.at;
  const postDropElapsedMs = postDropBoundary ? postDropAgeMs : null;
  if (pendingDrop && !postDropWindow) pendingDrops.delete(calibration);

  const current = {
    ...state,
    next,
    nextPieces: queue,
    detectedQueue: detectedQueue.map(piece => piece ? { ...piece } : null),
    geometry,
    at: now,
    stableFrames: 1,
    pieces: state.pieces.map(p => ({ type: p.type, x: p.x, y: p.y, r: p.r,
      measuredRadius: p.measuredRadius, confidence: p.confidence })),
  };

  let reason = null;
  let slowAdvanceUsed = false;
  let postDropAdvanceUsed = false;
  // Geometry failures are classified BEFORE the controllable piece. The order is
  // what makes `unknown-current` a usable precondition for the bounded
  // recognition-hold escape (docich#414): that label now means "the frame's
  // calibration and physical board are sound, only the current piece could not
  // be identified". A blind fallback drop must never be triggered by a broken
  // calibration or a corrupt board reading, which would otherwise hide behind
  // the missing current piece. `usable` / `frameValid` are unchanged: all three
  // reasons already withhold both.
  if (!usableCalibration(calibration, calibration.screen?.width, calibration.screen?.height)) reason = 'uncalibrated';
  else if (state.pieces.length > 256 || state.pieces.some(p => ![p.x, p.y, p.r].every(Number.isFinite) || p.r <= 0)) reason = 'invalid-board';
  else if (!next || next.fallback || !(next.confidence >= 0.58)) reason = 'unknown-current';
  else if (!previous || !(previous.usable || (postDropWindow && previous.frameValid))
      || previous.geometry !== geometry || now < previous.at) reason = 'confirm-frame';
  else {
    const sameTypeVerticalMotion = previewType(previous.next) === previewType(next)
      && Number.isFinite(previous.next?.y) && Number.isFinite(next.y)
      && Math.abs(previous.next.y - next.y) > 0.12;
    const boardStable = stableBoard(previous, state);
    const currentChanged = previewType(previous.next) != null
      && previewType(next) != null
      && previewType(previous.next) !== previewType(next);
    // Inside the bounded post-drop window the baseline for "the current piece
    // changed" is the piece that was placed, not the immediately preceding
    // frame: that frame often cannot identify the new piece yet, and comparing
    // against it made the intended fast path unreachable on the live cadence.
    const postDropCurrentChanged = !!pendingDrop && postDropBoundary
      && previewType(next) != null
      && pendingDrop.previousCurrent != null
      && previewType(next) !== pendingDrop.previousCurrent;

    // Do not mistake the expected PRE-drop -> POST-drop board delta for ongoing
    // motion. This is only available after a click was actually sent, only
    // inside the bounded post-drop window, and only after enough wall time for
    // the game-side drop/settle window. A same-type current visibly moving
    // through the spawn band remains blocked exactly as before.
    postDropAdvanceUsed = postDropBoundary
      && postDropSingleFrameEnabled()
      && !sameTypeVerticalMotion
      && !boardStable
      && (
        ((currentChanged || postDropCurrentChanged) && postDropElapsedMs >= singleFrameAdvanceMs())
        || postDropElapsedMs >= postDropBoardAdvanceMs()
      );

    if (postDropAdvanceUsed) {
      current.stableFrames = 2;
    } else if (gapMs > maxStaleMs()) {
      reason = 'confirm-frame';
    } else if (sameTypeVerticalMotion) {
      reason = 'board-moving';
    } else {
      const advance = queueAdvanceEvidence(previous?.detectedQueue, detectedQueue);
      slowAdvanceUsed = (transition === 'advanced'
          || (singleEvidenceAdvanceEnabled() && advance.evidence >= 1 && advance.conflict === 0))
        && slowCadenceFastPathEnabled()
        && gapMs >= singleFrameAdvanceMs();
      if (slowAdvanceUsed) {
        current.stableFrames = 2;
      } else if (previewType(previous.next) !== previewType(next)) {
        reason = 'preview-changed';
      } else if (!boardStable || !stableGarbage(previous, state)) {
        reason = 'board-moving';
      } else {
        current.stableFrames = Math.min(3, previous.stableFrames + 1);
      }
    }
  }

  current.usable = !['unknown-current', 'uncalibrated', 'invalid-board'].includes(reason);
  // A frame whose calibration and board reading are sound stays a valid
  // continuity baseline even when the controllable piece was not identified
  // yet (unknown-current). Only the bounded post-drop window may use it, so a
  // generic detection miss still requires a fresh confirmation.
  current.frameValid = !['uncalibrated', 'invalid-board'].includes(reason);

  const rawHoldPresent = Boolean(state.hold);
  const rawHoldKnown = rawHoldPresent && !state.hold.fallback && state.hold.confidence >= 0.6;
  const holdEverSeen = Boolean(previous?.holdEverSeen || rawHoldKnown);
  let emptyHoldFrames = 0;
  if (rawHoldKnown) emptyHoldFrames = 0;
  else if (!holdEverSeen && !rawHoldPresent && state.holdObservedEmpty !== false && current.usable) {
    emptyHoldFrames = (previous?.emptyHoldFrames || 0) + 1;
  }
  current.holdEverSeen = holdEverSeen;
  current.emptyHoldFrames = emptyHoldFrames;

  const confirmedHold = rawHoldKnown && previous?.usable && previous.hold
    && state.hold.type === previous.hold.type && previous.hold.confidence >= 0.6
    && !previous.hold.fallback;
  const holdKnownEmpty = !holdEverSeen && emptyHoldFrames >= 2;

  let stableReason = (slowAdvanceUsed || postDropAdvanceUsed) ? 'stable-slow-advance' : 'stable';
  if (postDropAdvanceUsed) stableReason += '-postdrop';
  if (temporalNextUsed) stableReason += '-temporal-next';
  if (holdKnownEmpty) stableReason += '-hold-empty';

  observations.set(calibration, current);
  // The boundary is not consumed by one frame: markDropSent() overwrites it
  // with the next sent drop, and the wall-clock window above expires it.
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

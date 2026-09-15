/** Temporal state is kept outside the hot-reloaded analyzer, per calibration. */
const observations = new WeakMap();

// How old the previous observation may be and still confirm a MOVE frame.
//
// The confirmation needs two consecutive stable frames, so this window must be
// comfortably larger than one loop iteration. The loop period is dominated by
// the screenshot path: a local browser iterates in well under a second, but the
// remote-CDP cdp-host (SOREN91_SHARED_BROWSER=1) measured ~5-6s per iteration
// (2026-09-16: 76 frames over ~8 minutes). With the old hard-coded 5000ms every
// frame arrived "too old", gateObservation returned `confirm-frame` -> DROP on
// every iteration, and the bot never reached MOVE, so it never dropped a piece
// (no play, no game history, no comments). 15s keeps a >2x margin over the
// measured remote period while still rejecting a genuinely stalled loop.
export const DEFAULT_MAX_STALE_MS = 15_000;
export function maxStaleMs(env = process.env) {
  const raw = Number(env?.SOREN91_OBSERVATION_MAX_STALE_MS);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_MAX_STALE_MS;
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

export function gateObservation(state, calibration, now = Date.now()) {
  if (!calibration || typeof calibration !== 'object') throw new TypeError('Calibration object required');
  const previous = observations.get(calibration);
  if (state.state !== 'MOVE') {
    observations.delete(calibration);
    return { ...state, perception: { ready: false, reason: state.perception?.reason || 'non-move', stableFrames: 0 } };
  }
  const geometry = JSON.stringify(calibration.board);
  const current = { ...state, geometry, at: now, stableFrames: 1,
    // Store a bounded numeric copy, not images, paths or credentials.
    pieces: state.pieces.map(p => ({ type: p.type, x: p.x, y: p.y, r: p.r, confidence: p.confidence })),
  };
  let reason = null;
  if (!state.next || state.next.fallback || !(state.next.confidence >= 0.58)) reason = 'unknown-current';
  else if (!usableCalibration(calibration, calibration.screen?.width, calibration.screen?.height)) reason = 'uncalibrated';
  else if (state.pieces.length > 256 || state.pieces.some(p => ![p.x, p.y, p.r].every(Number.isFinite) || p.r <= 0)) reason = 'invalid-board';
  else if (!previous || !previous.usable || previous.geometry !== geometry || now - previous.at > maxStaleMs() || now < previous.at) reason = 'confirm-frame';
  else if (previous.next?.type !== state.next.type) reason = 'preview-changed';
  else {
    const unmatched = previous.pieces.map(p => ({ ...p }));
    let stable = previous.pieces.length === state.pieces.length;
    for (const p of state.pieces) {
      let best = -1;
      let distance = Infinity;
      for (let i = 0; i < unmatched.length; i++) {
        const q = unmatched[i];
        const d = Math.hypot(p.x - q.x, p.y - q.y);
        if ((q.type === p.type || (q.confidence < 0.6 && p.confidence < 0.6)) && Math.abs(q.r - p.r) < 0.08 && d < distance) { distance = d; best = i; }
      }
      if (best < 0 || distance > 0.12) stable = false;
      else unmatched.splice(best, 1);
    }
    const previousColumns = previous.garbage?.columns || [];
    const columns = state.garbage?.columns || [];
    const garbageStable = columns.length === previousColumns.length && columns.every(c =>
      previousColumns.some(p => Math.abs(c.left - p.left) < 0.02
        && Math.abs(c.right - p.right) < 0.02 && Math.abs(c.top - p.top) < 0.12));
    if (!stable || !garbageStable) reason = 'board-moving';
    else current.stableFrames = Math.min(3, previous.stableFrames + 1);
  }
  current.usable = !['unknown-current', 'uncalibrated', 'invalid-board'].includes(reason);
  observations.set(calibration, current);
  const confirmedHold = state.hold && previous?.usable && previous.hold
    && state.hold.type === previous.hold.type && state.hold.confidence >= 0.6
    && previous.hold.confidence >= 0.6 && !state.hold.fallback && !previous.hold.fallback;
  return { ...state, hold: confirmedHold ? state.hold : null,
    // DROP is the existing loop's temporary no-input state, NOT a round ending
    // WAITING event. Never promote an unknown queue slot or reuse an old board.
    state: reason ? 'DROP' : 'MOVE',
    perception: { ready: !reason, reason: reason || 'stable', stableFrames: current.stableFrames },
  };
}

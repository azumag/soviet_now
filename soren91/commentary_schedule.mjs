/** Midgame commentary is once per round, not dependent on reaching 20 drops. */
export function midgameCommentStatus({ sent, turn, pieces, startedAt, now }) {
  const elapsedMs = Number.isFinite(startedAt) && Number.isFinite(now)
    ? Math.max(0, now - startedAt) : 0;
  let reason = 'waiting';
  if (sent) reason = 'already-requested';
  else if (!Number.isInteger(turn) || turn < 5) reason = 'too-early';
  else if (!Array.isArray(pieces) || pieces.length < 3) reason = 'insufficient-board';
  else if (turn >= 20) reason = 'turn-threshold';
  else if (elapsedMs >= 45_000) reason = 'elapsed-threshold';
  return { due: reason === 'turn-threshold' || reason === 'elapsed-threshold', reason, elapsedMs };
}

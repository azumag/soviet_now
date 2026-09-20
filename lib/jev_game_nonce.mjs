/**
 * Resolve the per-game JEV identity nonce (`game_instance_id`).
 *
 * Issue #771 section 5.1 requires `game_instance_id` to be a nonce that
 * changes on bridge start and on a real new game, and explicitly forbids
 * identifying a game by its number, score, or file mtime alone.  The
 * sorengame build does not push a game instance id, so the bridge owns the
 * nonce: it starts with a fresh value and rotates it when it observes the
 * real phase transition out of a terminal state into MOVE.  The game only
 * restarts on an explicit RETRY and the bridge polls every ~500ms, so the
 * terminal phase is observed before the reset instead of guessed from a
 * score reset.
 *
 * This module is pure so the rotation contract can be unit tested without a
 * browser or the long-lived bridge.
 */

export const TERMINAL_PHASES = new Set(['GAMEOVER', 'STOP']);

export function nextGameInstanceId({
  providedId = null,
  previousPhase = null,
  currentPhase = null,
  currentId = null,
  generate,
} = {}) {
  if (typeof generate !== 'function') {
    throw new TypeError('generate must be a function');
  }
  if (typeof providedId === 'string' && providedId) {
    // A game/bridge-provided nonce is authoritative; the bridge must not
    // override an identity it did not create.
    return { gameInstanceId: providedId, rotated: false, phase: currentPhase ?? null };
  }
  const restarted = TERMINAL_PHASES.has(previousPhase) && currentPhase === 'MOVE';
  const gameInstanceId = (!currentId || restarted) ? generate() : currentId;
  return { gameInstanceId, rotated: restarted, phase: currentPhase ?? null };
}

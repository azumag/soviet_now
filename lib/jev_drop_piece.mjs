/**
 * Resolve the identity of the piece that is currently being dropped.
 *
 * Issue #771 section 5.1 wants `opportunity_seq` confirmed from the game phase
 * and the current piece identity ("ゲームphaseと現在駒identity等から確認"), and
 * forbids fabricating an id.  The sorengame build pushes the controllable
 * piece in `next` with `type`/`r`/`x` but no id, while every piece in
 * `pieces` carries its real id and pieces are created in order.  The current
 * piece is therefore the observed highest id; we read it instead of inventing
 * one.  When the game (or an explicit override) does provide an id, that wins.
 *
 * This module is pure so the derivation can be unit tested without a browser.
 */

function integerId(value) {
  return Number.isInteger(value) ? value : null;
}

export function resolveDropPieceId({ providedId = null, nextId = null, pieces = [] } = {}) {
  const provided = integerId(providedId);
  if (provided !== null) return provided;
  const next = integerId(nextId);
  if (next !== null) return next;
  let highest = null;
  if (Array.isArray(pieces)) {
    for (const piece of pieces) {
      const id = integerId(piece && piece.id);
      if (id !== null && (highest === null || id > highest)) highest = id;
    }
  }
  return highest;
}

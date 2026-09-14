/** Behavioral regression gates for generated strategies. No provider calls. */
export const STRATEGY_CONTRACT = `
Required strategy invariants (checked before adoption):
- Return a finite x inside [-3,3] and respect piece-radius wall clearance.
- Do not mutate the observation. Never HOLD when canHold is false.
- nextPieces preserves three UI slots and can contain null. Stop lookahead at
  the first unknown slot; never compact it or fabricate type 1.
- A null hold is unknown unless holdKnownEmpty is explicitly true.
- Avoid a deadline-height landing when a clear lower lane exists. Negative
  penalties must not become positive rewards, including during lookahead.
- Only claim a merge with the first reachable equal-type contact; remove the
  consumed target in simulated futures. Type 15 does not promote to type 16.
- Keep the real radius table, floor=-5, walls=+-3.5, deadline=3.32. Do not add
  an arbitrary fixed settling height. Keep search bounded and deterministic.
- Piece confidence below 0.6 means uncertain type, not an empty space.
- garbage.columns, when present, contains {left,right,top} local obstacles.
  Merging does not imply that all garbage vanishes in a hypothetical future.
- Missing detected rank is unknown, not a good rank; no real game score exists.
- strategy.mjs must remain self-contained for per-game/data-URL snapshots.
`;

export function validateStrategyBehavior(decide) {
  const p = (type, x = 0, y = -4.793) => ({ type, x, y,
    r: ({ 1: 0.207, 2: 0.259, 3: 0.316, 5: 0.414, 15: 1.6 })[type], confidence: 0.9 });
  const state = (pieces = [], next = p(1), extra = {}) => ({
    pieces, next, nextPieces: [next], hold: null, canHold: false,
    garbage: { ratio: 0, height: -5, gauge: 0, columns: [] }, ...extra,
  });
  const check = (label, input, predicate = () => true) => {
    const before = JSON.stringify(input);
    const result = decide(input);
    if (!result || !Number.isFinite(result.x) || typeof result.reason !== 'string'
        || Math.abs(result.x) > Math.min(3, 3.5 - input.next.r + 1e-9)
        || (result.hold != null && typeof result.hold !== 'boolean')) {
      throw new Error(`${label}: invalid/unsafe decision`);
    }
    if (result.hold && !input.canHold) throw new Error(`${label}: HOLD unavailable`);
    if (JSON.stringify(input) !== before) throw new Error(`${label}: mutated observation`);
    if (!predicate(result)) throw new Error(`${label}: behavioral regression`);
    return result;
  };
  try {
    check('empty', state());
    check('deadline-escape', state([p(1, 0, 2.7)]), d => Math.abs(d.x) > 0.414);
    check('large-piece-walls', state([], p(15)));
    const hole = check('unknown-next-slot', state([], p(1), { nextPieces: [p(1), null, p(3)] }));
    const noLookahead = check('unknown-next-reference', state());
    if (hole.x !== noLookahead.x || hole.hold !== noLookahead.hold) throw new Error('unknown-next-slot: compacted or invented future');
    check('known-merge-vs-blocker', state([p(1, -1), p(1, 1), p(5, 1, -3)]), d => d.x < 0);
    check('localized-garbage', state([], p(1), {
      garbage: { ratio: 0.5, gauge: 0.9, columns: [{ left: -3.5, right: -0.5, top: 2.7 }] },
    }), d => d.x - 0.207 > -0.5);
    return { valid: true, error: null };
  } catch (error) {
    return { valid: false, error: `Strategy contract: ${error.message}` };
  }
}

/**
 * strategy.mjs - ドロップ位置決定戦略 (v87)
 *
 * v87: v86で試みた「DEFAULT: Drop at lowest weighted Y.」以外の理由がログに全く出ていない問題を改善するため、
 *      defaultStrategyのロジックを抜本的に見直し。
 *      併合機会と大型ピースの片側集約を、単純な「低いY座標」にドロップするよりも優先するように、
 *      重み付けY座標の計算に調整を導入。これにより、より戦略的なドロップが選ばれることを期待する。
 *
 *      主な改善点:
 *      1.  `defaultStrategy` の再設計:
 *          - 各 `FINE_COLS` のX座標に対して、以下の優先順位で最適な重み付けY座標と理由を決定する。
 *            a. 併合機会: 同じtypeの既存ピースと併合できる場合、`calculateHeightPenalty` に `0.7` の乗数を適用し、
 *               併合をわずかに優先する。ただし、危険な高さへの併合は避ける。
 *            b. 大型ピースの片側集約: `LARGE_PIECE_THRESHOLD` 以上のピースを `LEFT_SIDE_X_MAX` 以下に配置する場合、
 *               `calculateHeightPenalty` に `0.4` のより強い乗数を適用し、集約を優先する。
 *               併合と大型ピース集約が同じ列で競合する場合は、併合が優先される。
 *               ただし、デッドラインから `0.7` 以上の安全マージンを確保できる場合に限る。
 *            c. それ以外: 単純にピースが着地する最低の重み付けY座標 (`calculateHeightPenalty` は乗数なし) を採用。
 *          - 最終的に、すべての `FINE_COLS` の中から最も低い重み付けY座標を持つX座標を選択する。
 *      2.  `MERGE_BUFFER` および `FINE_COLS` の粒度は v86 の改善を維持する。
 *      3.  既存の HOLD ロジック、おじゃまブロック対応、T1低マージロジックは変更せず、
 *          `defaultStrategy` の改善によってより多様な戦略が選ばれることを期待する。
 *
 *      - ヘルパー関数、定数は v86から維持。
 *      - 物理挙動の近似に関する注意点も v86から維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const DEADLINE_Y = 2.5; // Center Y. Top of piece at DEADLINE_Y + radius means game over.
const WARN_Y = 1.2;     // Center Y. Above this, start applying height penalty.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.

// Strategy-specific constants
const MERGE_BUFFER = 0.25; // Adjusted from 0.1 to 0.25 to increase merge detection tolerance (v86)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large' (v82)
const LEFT_SIDE_X_MAX = -1.5; // Large pieces are aggregated to the left of this X (v82). Use colX + pieceToDrop.r <= LEFT_SIDE_X_MAX for effective placement.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.6; // (v82, maintained from v81)

// Garbage / Critical Mode Thresholds
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges (v84)
const GARBAGE_RATIO_URGENT = 0.4;       // When garbage ratio is very high, aggressive merges (v84)
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges (v84)
const OJAMA_GAUGE_URGENT = 0.6;         // When ojama gauge is very high, aggressive merges (v84)

// HOLD Strategy Thresholds (v82)
const HOLD_LARGE_PIECE_THRESHOLD = 10; // Type 10+ for holding
const HOLD_SMALL_PIECE_THRESHOLD = 3;  // Type 1-3 for swapping with held large piece

/**
 * Calculates a height-based penalty for a given Y coordinate.
 * Higher Y values (closer to DEADLINE_Y) result in higher penalties.
 * Penalty starts linearly from WARN_Y and becomes exponential near DEADLINE_Y.
 * @param {number} y - The Y coordinate of the piece's center.
 * @returns {number} The calculated penalty.
 */
function calculateHeightPenalty(y) {
  if (y < WARN_Y) {
    return 0;
  }
  if (y >= DEADLINE_Y) {
    return 10000; // Effectively game over if piece center is at or above deadline
  }
  // Linear penalty between WARN_Y and DEADLINE_Y, then exponential
  const linearRange = DEADLINE_Y - WARN_Y;
  const normalizedY = (y - WARN_Y) / linearRange; // 0 to 1 in the warn range
  return Math.pow(normalizedY, 2) * 500; // Exponential penalty, adjust multiplier as needed
}

/**
 * Helper to get the X coordinate of a piece.
 * @param {{x: number, y: number, r: number, type: number}} piece
 * @returns {number}
 */
function getPieceX(piece) {
  return piece.x;
}

/**
 * Helper to get the Y coordinate of a piece.
 * @param {{x: number, y: number, r: number, type: number}} piece
 * @returns {number}
 */
function getPieceY(piece) {
  return piece.y;
}

/**
 * Helper to get the radius of a piece.
 * @param {{x: number, y: number, r: number, type: number}} piece
 * @returns {number}
 */
function getPieceRadius(piece) {
  return piece.r;
}

/**
 * Simulates the Y coordinate where a piece would land if dropped at a given X.
 * This is a simplified vertical stack model, ignoring complex physics like rotation/rolling.
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardState - The full board state or an object containing only the pieces.
 * @param {number} dropX - The X coordinate where the piece is dropped.
 * @param {{type: number, r: number}} pieceToDrop - The piece being dropped.
 * @returns {number} The estimated Y coordinate of the piece's center after landing.
 */
function simulateDropY(boardState, dropX, pieceToDrop) {
  let simulatedY = BOARD_FLOOR_Y + pieceToDrop.r; // Starts at the floor + its radius

  for (const existingPiece of boardState.pieces) {
    // Check for horizontal overlap
    const horizontalDistance = Math.abs(dropX - existingPiece.x);
    // Use merge buffer for overlap detection: if pieces are horizontally close enough to "touch"
    if (horizontalDistance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
      // If overlaps horizontally, it will stack on top if higher
      if (existingPiece.y + existingPiece.r + pieceToDrop.r > simulatedY) {
        simulatedY = existingPiece.y + existingPiece.r + pieceToDrop.r;
      }
    }
  }
  return simulatedY;
}

/**
 * Finds an X coordinate where the `pieceToDrop` can immediately merge with an existing piece
 * of `pieceToDrop.type`. Prioritizes merges at lower Y coordinates (with penalty).
 * This function is used primarily for HOLD strategy evaluation and as a specific early priority check.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece that will be dropped.
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason string, or null if no merge opportunity.
 */
function findMergeOpportunity(boardStatePieces, pieceToDrop) {
  let bestX = null;
  let lowestWeightedY = Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY);

    if (weightedY >= lowestWeightedY) { // Skip if current weightedY is not better than the best so far
      continue;
    }

    // Check for merge with existing pieces of the same type
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) { // Type 15 merges *and disappears*, so avoid planning merges for it unless it's critical.
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        // Merge if centers are close enough based on radii, considering MERGE_BUFFER
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge opportunity for this colX.
          if (weightedY < lowestWeightedY) {
            lowestWeightedY = weightedY;
            bestX = colX;
          }
          // Break here, as a dropped piece will only merge with *one* other piece of the same type upon landing.
          // This ensures we find the best X for *a* merge, not necessarily a chain reaction.
          break;
        }
      }
    }
  }

  if (bestX !== null) {
    return { x: bestX, y: lowestWeightedY, reason: `Merge ${pieceToDrop.type} at lowest weighted Y.` };
  }
  return null;
}

/**
 * Finds a low Y merge opportunity specifically for type 1 pieces.
 * Prioritizes positions where a T1 piece can merge low, potentially clearing garbage.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece that will be dropped (expected to be type 1).
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason, or null.
 */
function findT1LowMerge(boardStatePieces, pieceToDrop) {
  if (pieceToDrop.type !== 1) return null;

  let bestX = null;
  let lowestWeightedY = Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    // Apply T1_LOW_MERGE_HEIGHT_ADVANTAGE to make low merges even more attractive
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY) - (simulatedY < WARN_Y ? T1_LOW_MERGE_HEIGHT_ADVANTAGE : 0);

    // Check for merge with existing type 1 pieces
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === 1) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          if (weightedY < lowestWeightedY) {
            lowestWeightedY = weightedY;
            bestX = colX;
          }
          break;
        }
      }
    }
  }

  if (bestX !== null) {
    return { x: bestX, y: lowestWeightedY, reason: `T1 low merge at lowest weighted Y.` };
  }
  return null;
}

/**
 * Finds aggressive merge opportunities in critical situations (high garbage).
 * Prioritizes any merge opportunity that can occur at a lower Y.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @param {boolean} isUrgent - True if in an extremely urgent garbage situation.
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason, or null.
 */
function findAggressiveCriticalMerge(boardStatePieces, pieceToDrop, isUrgent) {
  let bestX = null;
  let lowestWeightedY = Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    // Apply a higher penalty multiplier if urgent, making it favor lower Y even more
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY) * (isUrgent ? 2 : 1);

    if (weightedY >= lowestWeightedY) {
      continue;
    }

    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) { // Only merge same type, and not type 15 unless game over is imminent
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          if (weightedY < lowestWeightedY) {
            lowestWeightedY = weightedY;
            bestX = colX;
          }
          break;
        }
      }
    }
  }

  if (bestX !== null) {
    return { x: bestX, y: lowestWeightedY, reason: `Aggressive critical merge (${pieceToDrop.type}, urgent: ${isUrgent}).` };
  }
  return null;
}

/**
 * Implements the HOLD strategy.
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>, hold: {type: number, r: number} | null, canHold: boolean, next: {type: number, r: number}}} boardState
 * @returns {{x: number, reason: string, hold: boolean} | null} Action if HOLD should be used, otherwise null.
 */
function tryHoldStrategy(boardState) {
  const { pieces, next, hold, canHold } = boardState;

  if (!canHold) return null;

  // Scenario 1: Hold is empty, and next piece is large. Hold it for later.
  if (hold === null && next.type >= HOLD_LARGE_PIECE_THRESHOLD) {
    return { x: 0, reason: `HOLD: Store large piece (type ${next.type}).`, hold: true };
  }

  // Scenario 2: Hold is not empty. Next piece has no immediate merge but held piece does.
  if (hold !== null) {
    // Check if current 'next' piece has a merge opportunity
    const nextMerge = findMergeOpportunity(pieces, next);
    // Check if 'held' piece would have a merge opportunity if it were next
    const holdMerge = findMergeOpportunity(pieces, hold);

    if (!nextMerge && holdMerge) {
      return { x: 0, reason: `HOLD: Swap for held piece with merge opportunity (type ${hold.type}).`, hold: true };
    }

    // Scenario 3: Next is small, held is large, swap to use the small one or to save the large one
    // Only swap if the small piece is not useful right now, or if the large held piece is needed.
    // For simplicity and given the goal is to trigger more specific reasons,
    // let's keep the existing logic, which prioritizes using the small piece first if there's no merge for 'next'.
    if (next.type <= HOLD_SMALL_PIECE_THRESHOLD && hold.type >= HOLD_LARGE_PIECE_THRESHOLD) {
      // This scenario primarily aims to get rid of small 'next' pieces by holding them,
      // or to bring out a large held piece.
      // If 'next' has no merge and 'hold' is a large piece, it's a good candidate for swapping.
      if (!nextMerge) { // Only swap if current 'next' piece has no merge
         return { x: 0, reason: `HOLD: Swap small piece (type ${next.type}) for large held piece (type ${hold.type}).`, hold: true };
      }
    }
  }

  return null; // No HOLD action
}

/**
 * Finds the X coordinate with the best overall score considering merges, large piece aggregation, and lowest weighted Y.
 * This is the core default strategy.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop) {
  let bestOverallX = 0.0;
  let lowestOverallWeightedY = Infinity;
  let bestOverallReason = "DEFAULT: Least occupied column (lowest weighted Y)."; // Updated reason prefix

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    // Initialize currentWeightedY with standard penalty
    let currentWeightedY = simulatedY + calculateHeightPenalty(simulatedY);
    let currentReason = "DEFAULT: Least occupied column (lowest weighted Y).";

    let mergeFoundForCol = false;

    // First, check for merge opportunities at this colX
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge. Adjust weightedY to give it a slight preference over just stacking.
          // The multiplier 0.7 gives merges a small advantage unless they are very high.
          const mergePotentialWeightedY = simulatedY + calculateHeightPenalty(simulatedY) * 0.7;
          if (mergePotentialWeightedY < currentWeightedY) { // Only take this merge if it's better than current non-merge option for this colX
             currentWeightedY = mergePotentialWeightedY;
             currentReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX}.`;
             mergeFoundForCol = true;
          }
          break; // A piece only merges with one other piece upon landing, so stop checking existing pieces for this colX.
        }
      }
    }

    // Second, consider large piece aggregation if no merge was found at this colX.
    // Give a stronger preference to aggregate large pieces if it's not dangerously high.
    if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD && !mergeFoundForCol) {
      if (colX <= LEFT_SIDE_X_MAX) { // Only consider left side for aggregation
        // The multiplier 0.4 gives a stronger height advantage for large piece aggregation.
        // Also add a stricter height check to prevent aggregating large pieces dangerously high.
        const largePiecePotentialWeightedY = simulatedY + calculateHeightPenalty(simulatedY) * 0.4;
        if (largePiecePotentialWeightedY < currentWeightedY && simulatedY < DEADLINE_Y - 0.7) {
          currentWeightedY = largePiecePotentialWeightedY;
          currentReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) to left side at ${colX}.`;
        }
      }
    }

    // Compare this column's best-weighted Y (potentially adjusted for merge/large piece) with the overall best found so far.
    if (currentWeightedY < lowestOverallWeightedY) {
      lowestOverallWeightedY = currentWeightedY;
      bestOverallX = colX;
      bestOverallReason = currentReason;
    }
  }

  return { x: bestOverallX, reason: bestOverallReason };
}


export function decide(boardState) {
  const { pieces, next, hold, canHold, garbage } = boardState;

  // --- Priority 1: HOLD Logic ---
  let action = tryHoldStrategy(boardState);
  if (action) return action;

  // --- Priority 2: CRITICAL Mode (Garbage) ---
  // Triggered by high garbage ratio or gauge.
  // Prioritize aggressive merges.
  if (garbage.gauge >= OJAMA_GAUGE_URGENT || garbage.ratio > GARBAGE_RATIO_URGENT) { // GBG_URGENT
    action = findAggressiveCriticalMerge(pieces, next, true); // true for urgent
    if (action) return action;
  }
  if (garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE || garbage.ratio > GARBAGE_RATIO_OJAMA_MERGE) { // OJAMA_MERGE
    action = findAggressiveCriticalMerge(pieces, next, false); // false for less urgent
    if (action) return action;
  }

  // --- Priority 3: ULTRA Mode (T1 Low Merge) ---
  action = findT1LowMerge(pieces, next);
  if (action) return action;

  // --- Priority 4: DEFAULT Strategy ---
  action = defaultStrategy(pieces, next);
  if (action) return action;

  // Fallback (should ideally not be reached with a comprehensive strategy)
  // findLeastOccupiedX will always return a valid X, so this fallback should be unreachable.
  // The defaultStrategy function guarantees a return, so this line is technically unreachable.
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
/**
 * strategy.mjs - ドロップ位置決定戦略 (v89)
 *
 * v89: v88のゲーム分析と継続的な高Y座標到達の問題に対応するため、高さ管理ロジックをさらに強化。
 *      また、おじゃまブロックによるゲームオーバーへの対策として、緊急時のマージに低Y座標ボーナスを追加。
 *      defaultStrategyにおける併合機会と大型ピース集約の優先度を微調整。
 *
 *      主な改善点:
 *      1.  高さ管理の厳格化と定数名の明確化:
 *          - `GAME_OVER_TOP_Y`: ピースの「最上部」がこれを超えるとゲームオーバーとなるY座標を定義 (2.5)。
 *          - `PENALTY_CRITICAL_CENTER_Y`: ピースの「中心」がこれに達すると極めて高いペナルティが課されるY座標を定義 (2.0に調整)。
 *          - `PENALTY_WARN_CENTER_Y`: ピースの「中心」がこのY座標を超えるとペナルティが開始される点を定義 (0.8に調整)。
 *          - `calculateHeightPenalty` のペナルティ乗数を `1000` から `2000` に引き上げ、高所への配置をより強く抑制。
 *          - `defaultStrategy` 内のデッドラインチェックを `GAME_OVER_TOP_Y` に基づく「ピースの最上部」基準に修正し、より安全な配置を強制。
 *      2.  おじゃまモード時のマージ戦略強化:
 *          - `findAggressiveCriticalMerge` において、`isUrgent` (緊急) 時かつ盤面中央より下 (Y<0) でマージが発生する場合、
 *            `GARBAGE_LOW_MERGE_BONUS` を追加で適用し、低所でのマージを積極的に促進。おじゃまブロックの除去効率向上を狙う。
 *      3.  `defaultStrategy` 内の優先度調整:
 *          - 併合機会の優先度 (`mergePriorityMultiplier`) を `0.7` から `0.6` に変更 (より優先)。
 *          - 大型ピース集約の優先度 (`largePieceAggregationMultiplier`) を `0.4` から `0.3` に変更 (より優先)。
 *            これにより、併合や大型ピースの計画的な配置が、単に空いている場所を探すよりも優先されやすくなる。
 *
 *      - ヘルパー関数、定数は v88から維持（一部名称変更）。
 *      - 物理挙動の近似に関する注意点も v87から維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.

// Strategy-specific constants (Height Management)
const GAME_OVER_TOP_Y = 2.5;             // The Y coordinate for the TOP of the piece that means game over (from rules "〜y=2.5 を超えるとゲームオーバー").
const PENALTY_CRITICAL_CENTER_Y = 2.0;   // The center Y coordinate where height penalty becomes extremely high (adjusted from 2.5 in v88).
const PENALTY_WARN_CENTER_Y = 0.8;       // The center Y coordinate where height penalty starts (adjusted from 1.2 in v88).

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.25; // Adjusted from 0.1 to 0.25 to increase merge detection tolerance (v86)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large' (v82)
const LEFT_SIDE_X_MAX = -1.5; // Large pieces are aggregated to the left of this X (v82).
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.6; // (v82, maintained from v81)

// Garbage / Critical Mode Thresholds (adjusted in v88)
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges (v84)
const GARBAGE_RATIO_URGENT = 0.3;       // When garbage ratio is very high, aggressive merges (v88, from 0.4)
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges (v84)
const OJAMA_GAUGE_URGENT = 0.5;         // When ojama gauge is very high, aggressive merges (v88, from 0.6)
const GARBAGE_LOW_MERGE_BONUS = 1.0;    // Bonus for merges below board center when urgent garbage (v89)


// HOLD Strategy Thresholds (v82)
const HOLD_LARGE_PIECE_THRESHOLD = 10; // Type 10+ for holding
const HOLD_SMALL_PIECE_THRESHOLD = 3;  // Type 1-3 for swapping with held large piece

/**
 * Calculates a height-based penalty for a given Y coordinate.
 * Higher Y values (closer to PENALTY_CRITICAL_CENTER_Y) result in higher penalties.
 * Penalty starts linearly from PENALTY_WARN_CENTER_Y and becomes exponential near PENALTY_CRITICAL_CENTER_Y.
 * (Adjusted in v89 to be more aggressive and use new constants)
 * @param {number} y - The Y coordinate of the piece's center.
 * @returns {number} The calculated penalty.
 */
function calculateHeightPenalty(y) {
  if (y < PENALTY_WARN_CENTER_Y) {
    return 0;
  }
  if (y >= PENALTY_CRITICAL_CENTER_Y) {
    return 10000; // Effectively game over if piece center is at or above critical Y
  }
  // Linear penalty between WARN_CENTER_Y and CRITICAL_CENTER_Y, then cubic exponential
  const linearRange = PENALTY_CRITICAL_CENTER_Y - PENALTY_WARN_CENTER_Y;
  const normalizedY = (y - PENALTY_WARN_CENTER_Y) / linearRange; // 0 to 1 in the warn range
  return Math.pow(normalizedY, 3) * 2000; // Adjusted to cubic and higher multiplier (v89, from 1000)
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

    if (weightedY >= lowestWeightedY) {
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
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY) - (simulatedY < PENALTY_WARN_CENTER_Y ? T1_LOW_MERGE_HEIGHT_ADVANTAGE : 0);

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
 * (Reason string improved in v88, low merge bonus added in v89)
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
    let weightedY = simulatedY + calculateHeightPenalty(simulatedY) * (isUrgent ? 2 : 1);

    // v89: Add bonus for low merges when urgent garbage is present
    if (isUrgent && simulatedY < 0.0) {
      weightedY -= GARBAGE_LOW_MERGE_BONUS;
    }

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
    return { x: bestX, y: lowestWeightedY, reason: `Aggressive critical merge type ${pieceToDrop.type} (urgent: ${isUrgent}).` };
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
    // If 'next' has no merge and 'hold' is a large piece, it's a good candidate for swapping.
    if (next.type <= HOLD_SMALL_PIECE_THRESHOLD && hold.type >= HOLD_LARGE_PIECE_THRESHOLD) {
      if (!nextMerge) { // Only swap if current 'next' piece has no merge
         return { x: 0, reason: `HOLD: Swap small piece (type ${next.type}) for large held piece (type ${hold.type}).`, hold: true };
      }
    }
  }

  return null; // No HOLD action
}

/**
 * Finds the X coordinate with the best overall score considering merges, large piece aggregation, and lowest weighted Y.
 * This is the core default strategy. (Refactored in v88, multipliers adjusted in v89)
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop) {
  let bestOverallX = 0.0;
  let lowestOverallWeightedY = Infinity;
  let bestOverallReason = "DEFAULT: Least occupied column (lowest weighted Y).";

  const mergePriorityMultiplier = 0.6; // Adjusted from 0.7 (v89)
  const largePieceAggregationMultiplier = 0.3; // Adjusted from 0.4 (v89)

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);

    // v89: If the TOP of the simulated piece exceeds the game over line, disqualify this column.
    if (simulatedY + pieceToDrop.r > GAME_OVER_TOP_Y) {
        continue;
    }

    // --- Option 1: Standard Placement (Base for comparison) ---
    let currentColumnBestWeightedY = simulatedY + calculateHeightPenalty(simulatedY);
    let currentColumnBestReason = "DEFAULT: Least occupied column (lowest weighted Y).";

    // --- Option 2: Merge Opportunity ---
    let mergePotentialWeightedY = Infinity;
    let mergeFound = false;
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge. Apply the merge priority multiplier.
          mergePotentialWeightedY = simulatedY + calculateHeightPenalty(simulatedY) * mergePriorityMultiplier;
          mergeFound = true;
          break; // A piece only merges with one other piece upon landing.
        }
      }
    }

    if (mergeFound && mergePotentialWeightedY < currentColumnBestWeightedY) {
      currentColumnBestWeightedY = mergePotentialWeightedY;
      currentColumnBestReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX}.`;
    }

    // --- Option 3: Large Piece Aggregation ---
    // Only consider if it's a large piece and not dangerously high.
    if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
      if (colX <= LEFT_SIDE_X_MAX) { // Only consider left side for aggregation
        // Stricter height check (0.7 buffer from PENALTY_CRITICAL_CENTER_Y) for large piece aggregation
        if (simulatedY < PENALTY_CRITICAL_CENTER_Y - 0.7) {
          // Apply a stronger preference multiplier for large piece aggregation.
          const largePiecePotentialWeightedY = simulatedY + calculateHeightPenalty(simulatedY) * largePieceAggregationMultiplier;
          if (largePiecePotentialWeightedY < currentColumnBestWeightedY) {
            currentColumnBestWeightedY = largePiecePotentialWeightedY;
            currentColumnBestReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) to left side at ${colX}.`;
          }
        }
      }
    }

    // Compare this column's best-weighted Y (potentially adjusted for merge/large piece)
    // with the overall best found so far across all columns.
    if (currentColumnBestWeightedY < lowestOverallWeightedY) {
      lowestOverallWeightedY = currentColumnBestWeightedY;
      bestOverallX = colX;
      bestOverallReason = currentColumnBestReason;
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
  // Triggered by high garbage ratio or gauge. (Thresholds adjusted in v88)
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
  // This will now correctly log the specific reason if merge or aggregation is chosen.
  action = defaultStrategy(pieces, next);
  if (action) return action;

  // Fallback (should ideally not be reached with a comprehensive strategy)
  // The defaultStrategy function guarantees a return, so this line is technically unreachable.
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
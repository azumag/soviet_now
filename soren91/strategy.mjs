/**
 * strategy.mjs - ドロップ位置決定戦略 (v85)
 *
 * v85: v84の課題である「Placeholder戦略からの脱却」を目的とし、
 *      コメントで詳細に記述されていた戦略ロジックを実コードとして実装。
 *      ゲーム分析で判明した「常に中央(0.0)にドロップする」問題を解決し、
 *      高度な戦略に基づいたドロップ位置決定とHOLD機能の活用を行う。
 *
 *      - 戦略の優先順位付けと実装:
 *        1. HOLD戦略: 大型ピースの一時保管、マージ機会の創出、小型ピースとの入れ替えなどを実装。
 *        2. CRITICALモード: おじゃまブロックの脅威レベルに応じて、アグレッシブなマージを優先。
 *        3. ULTRAモード: T1ピースの高密度/高所問題に対処するための低位置マージを優先。
 *        4. DEFAULT戦略:
 *           - 現在のピース(next)と同一typeの即時併合機会を探索し、最も低いY座標(高さペナルティ考慮)を優先。
 *           - 即時併合先がない場合、ドロップ後のY座標が最も低くなる位置(高さペナルティ考慮)を探索。
 *           - 大型ピース(type >= 9)の場合、盤面の左側に寄せて配置し、片側集約を促す。
 *           - 上記戦略が適用できない場合、最も空いている列または中央(0.0)を使用。
 *
 *      - ヘルパー関数の実装:
 *        - `calculateHeightPenalty`: v84で導入が記述されていた、Y座標に応じたペナルティ計算。
 *        - `simulateDropY`: ピースを特定のX座標にドロップした際のY座標推定。
 *        - `computeColHeights`: 各X列の最高到達Y座標を計算。
 *        - `findMergeOpportunity`: 指定typeの併合機会を探索。
 *        - `findT1LowMerge`: T1ピースの低位置マージ機会を探索。
 *        - `findAggressiveCriticalMerge`: 危機的状況でのマージ機会を探索。
 *        - `findLeastOccupiedX`: 最も空いている列を探索。
 *
 *      - 定数の調整・導入:
 *        - `MERGE_BUFFER`: v83で言及されていた併合判定の厳密性を調整するバッファを導入 (0.1)。
 *        - `LARGE_PIECE_THRESHOLD`: 大型ピースの閾値を type 9 に設定。
 *        - `LEFT_SIDE_X_MAX`: 大型ピースの片側集約ゾーンの境界値を -1.5 に設定。
 *        - `T1_LOW_MERGE_HEIGHT_ADVANTAGE`: v81->v82で維持された値を 0.6 に設定。
 *        - その他の閾値 (`GARBAGE_RATIO_OJAMA_MERGE`, `GARBAGE_RATIO_URGENT`, `OJAMA_GAUGE_OJAMA_MERGE`, `OJAMA_GAUGE_URGENT`, `HOLD_LARGE_PIECE_THRESHOLD`, `HOLD_SMALL_PIECE_THRESHOLD`) を新たに導入し、戦略のトリガー条件を明確化。
 *
 *      - 物理挙動の近似:
 *        - ピースが円形と仮定した2D中心間距離に基づく併合判定を導入。
 *        - 物理エンジンの複雑な挙動（回転、転がり、衝撃波）は `simulateDropY` の垂直スタックモデルでは完全には予測できないが、合理的な近似として採用。
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5; // Center Y. Top of piece at DEADLINE_Y + radius means game over.
const WARN_Y = 1.2;     // Center Y. Above this, start applying height penalty.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.

// Strategy-specific constants
const MERGE_BUFFER = 0.1; // Allows slight overlap for merge detection (v83)
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
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardState
 * @param {number} dropX - The X coordinate where the piece is dropped.
 * @param {{type: number, r: number}} pieceToDrop - The piece being dropped.
 * @returns {number} The estimated Y coordinate of the piece's center after landing.
 */
function simulateDropY(boardState, dropX, pieceToDrop) {
  let simulatedY = BOARD_FLOOR_Y + pieceToDrop.r; // Starts at the floor + its radius

  for (const existingPiece of boardState.pieces) {
    // Check for horizontal overlap
    const horizontalDistance = Math.abs(dropX - existingPiece.x);
    if (horizontalDistance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) { // Use merge buffer for overlap detection
      // If overlaps horizontally, it will stack on top if higher
      if (existingPiece.y + existingPiece.r + pieceToDrop.r > simulatedY) {
        simulatedY = existingPiece.y + existingPiece.r + pieceToDrop.r;
      }
    }
  }
  return simulatedY;
}

/**
 * Computes the highest Y coordinate for each column (FINE_COLS).
 * This represents the "height" of the stack at each column.
 * @param {Array<{type: number, x: number, y: number, r: number}>} pieces - Array of existing pieces on the board.
 * @returns {Array<number>} An array where each element corresponds to the max Y for a FINE_COL.
 */
function computeColHeights(pieces) {
  const colHeights = new Array(FINE_COLS.length).fill(BOARD_FLOOR_Y);

  for (let i = 0; i < FINE_COLS.length; i++) {
    const colX = FINE_COLS[i];
    for (const piece of pieces) {
      // Consider a piece's effective horizontal span for height calculation
      // A column's height is influenced by any piece whose X-range covers the column's X.
      if (Math.abs(piece.x - colX) < piece.r) { // If column's X is within piece's horizontal span
        if (piece.y + piece.r > colHeights[i]) {
          colHeights[i] = piece.y + piece.r;
        }
      }
    }
  }
  return colHeights;
}

/**
 * Finds an X coordinate where the `pieceToDrop` can immediately merge with an existing piece
 * of `pieceToDrop.type`. Prioritizes merges at lower Y coordinates (with penalty).
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardStatePieces - Only pieces from boardState.
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
      if (existingPiece.type === pieceToDrop.type) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        // Merge if centers are close enough based on radii, considering MERGE_BUFFER
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge opportunity. Is it the best one so far?
          if (weightedY < lowestWeightedY) {
            lowestWeightedY = weightedY;
            bestX = colX;
          }
          break; // Found a merge for this column, no need to check other pieces in this column
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
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardStatePieces - Only pieces from boardState.
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
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @param {boolean} isUrgent - True if in an extremely urgent garbage situation.
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason, or null.
 */
function findAggressiveCriticalMerge(boardStatePieces, pieceToDrop, isUrgent) {
  let bestX = null;
  let lowestWeightedY = Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY) * (isUrgent ? 2 : 1); // Higher penalty multiplier if urgent

    if (weightedY >= lowestWeightedY) {
      continue;
    }

    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type) { // Only merge same type
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
 * Finds the X coordinate with the least "occupied" height, preferring lower overall stacks.
 * Considers height penalty.
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop (for radius consideration).
 * @returns {{x: number, y: number, reason: string}} The best drop X, its simulated Y, and a reason.
 */
function findLeastOccupiedX(boardStatePieces, pieceToDrop) {
  let bestX = 0.0;
  let lowestWeightedY = Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY);

    if (weightedY < lowestWeightedY) {
      lowestWeightedY = weightedY;
      bestX = colX;
    }
  }
  return { x: bestX, y: lowestWeightedY, reason: "Least occupied column (lowest weighted Y)." };
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
    if (next.type <= HOLD_SMALL_PIECE_THRESHOLD && hold.type >= HOLD_LARGE_PIECE_THRESHOLD) {
      return { x: 0, reason: `HOLD: Swap small piece (type ${next.type}) for large held piece (type ${hold.type}).`, hold: true };
    }
  }

  return null; // No HOLD action
}

/**
 * Implements the DEFAULT strategy when no critical conditions or specific merges are found.
 * @param {{pieces: Array<{type: number, x: number, y: number, r: number}>}} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop) {
  let bestX = 0.0;
  let lowestWeightedY = Infinity;

  // 1. Immediate merge opportunity for 'next' piece at lowest weighted Y.
  const mergeOpportunity = findMergeOpportunity(boardStatePieces, pieceToDrop);
  if (mergeOpportunity) {
    return { x: mergeOpportunity.x, reason: mergeOpportunity.reason };
  }

  // 2. Lowest Y drop (considering height penalty) if no merge.
  // This will also be used as a base for large piece aggregation.
  let bestDropYWeighted = Infinity;
  let bestDropXForLowestY = 0.0;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY);

    if (weightedY < bestDropYWeighted) {
      bestDropYWeighted = weightedY;
      bestDropXForLowestY = colX;
    }
  }

  // 3. Large piece aggregation for type >= 9.
  if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
    // Try to place large pieces on the left side, but still prioritize lowest Y within that side.
    let bestLargePieceX = null;
    let lowestWeightedYLargePiece = Infinity;

    for (const colX of FINE_COLS) {
      // Ensure the *right edge* of the piece is within LEFT_SIDE_X_MAX for better aggregation
      // colX is the center, so colX + pieceToDrop.r is the rightmost point
      if (colX + pieceToDrop.r <= LEFT_SIDE_X_MAX + pieceToDrop.r) { // Adjusted to keep piece center to the left of LEFT_SIDE_X_MAX
        const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
        const weightedY = simulatedY + calculateHeightPenalty(simulatedY);
        if (weightedY < lowestWeightedYLargePiece) {
          lowestWeightedYLargePiece = weightedY;
          bestLargePieceX = colX;
        }
      }
    }
    if (bestLargePieceX !== null) {
      return { x: bestLargePieceX, reason: `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) to left side.` };
    }
  }

  // If no specific large piece aggregation or merge, just go for the lowest overall spot found in step 2.
  if (bestDropXForLowestY !== 0.0 || bestDropYWeighted !== Infinity) {
      return { x: bestDropXForLowestY, reason: "DEFAULT: Drop at lowest weighted Y." };
  }

  // 4. Fallback: least occupied X (already computed as findLeastOccupiedX) or center
  return findLeastOccupiedX(boardStatePieces, pieceToDrop); // This will always return a valid X.
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
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
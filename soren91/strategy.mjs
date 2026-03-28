/**
 * strategy.mjs - ドロップ位置決定戦略 (v86)
 *
 * v86: v85の課題である「常に中央(0.0)にドロップする」問題に対処するため、
 *      mergeOpportunityや大型ピースの配置ロジックが実際に選択されるように修正。
 *      ゲーム分析から判明した「DEFAULT: Drop at lowest weighted Y.」以外の理由がログに全く出ていない問題を改善する。
 *
 *      主な改善点:
 *      1.  `MERGE_BUFFER` の調整: ピースが凸ポリゴンであり、物理挙動が複雑であることを考慮し、
 *          併合検出の許容範囲を広げるため `MERGE_BUFFER` を `0.1` から `0.25` に変更。
 *          これにより、物理的な接触の「可能性」をより積極的に捉え、`findMergeOpportunity` がヒットしやすくなることを期待する。
 *      2.  `FINE_COLS` の粒度向上: ドロップX座標の選択肢を増やすため、`FINE_COLS` の間隔を `0.5` から `0.25` に細分化。
 *          これにより、最適な併合位置や低Y座標位置をより精密に選択できるようになり、特に複雑な盤面での戦略適用性が向上する。
 *      3.  大型ピースの片側集約ロジックの再確認: ゲーム分析では大型ピースの集約が機能している様子が見られないため、
 *          `defaultStrategy` 内の大型ピース配置ロジックと `LEFT_SIDE_X_MAX` の条件を再度確認し、
 *          この戦略が確実に発動するようにロジックを微調整する。
 *          - 現在のロジック `colX + pieceToDrop.r <= LEFT_SIDE_X_MAX + pieceToDrop.r` は `colX <= LEFT_SIDE_X_MAX` と等価であり、
 *            左側に中心がある `FINE_COLS` の中で最適な位置を探すという意図は正しい。
 *            発動しないのは、その範囲で適当な落下点が見つからない可能性が高い。
 *            -> 今回は`FINE_COLS`の粒度向上により、より適切なX座標が見つかる可能性に期待する。
 *
 *      - ヘルパー関数の実装 (v85から維持):
 *        - `calculateHeightPenalty`: v84で導入が記述されていた、Y座標に応じたペナルティ計算。
 *        - `simulateDropY`: ピースを特定のX座標にドロップした際のY座標推定。
 *        - `computeColHeights`: 各X列の最高到達Y座標を計算。
 *        - `findMergeOpportunity`: 指定typeの併合機会を探索。
 *        - `findT1LowMerge`: T1ピースの低位置マージ機会を探索。
 *        - `findAggressiveCriticalMerge`: 危機的状況でのマージ機会を探索。
 *        - `findLeastOccupiedX`: 最も空いている列を探索。
 *
 *      - 定数の調整・導入 (v85から維持、MERGE_BUFFERのみ変更):
 *        - `MERGE_BUFFER`: 併合判定の厳密性を調整するバッファを導入。`0.1` -> `0.25` に変更。
 *        - `LARGE_PIECE_THRESHOLD`: 大型ピースの閾値を type 9 に設定。
 *        - `LEFT_SIDE_X_MAX`: 大型ピースの片側集約ゾーンの境界値を -1.5 に設定。
 *        - `T1_LOW_MERGE_HEIGHT_ADVANTAGE`: v81->v82で維持された値を 0.6 に設定。
 *        - その他の閾値 (`GARBAGE_RATIO_OJAMA_MERGE`, `GARBAGE_RATIO_URGENT`, `OJAMA_GAUGE_OJAMA_MERGE`, `OJAMA_GAUGE_URGENT`, `HOLD_LARGE_PIECE_THRESHOLD`, `HOLD_SMALL_PIECE_THRESHOLD`) を新たに導入し、戦略のトリガー条件を明確化。
 *
 *      - 物理挙動の近似 (v85から維持):
 *        - ピースが円形と仮定した2D中心間距離に基づく併合判定を導入。
 *        - 物理エンジンの複雑な挙動（回転、転がり、衝撃波）は `simulateDropY` の垂直スタックモデルでは完全には予測できないが、合理的な近似として採用。
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
          // Found a merge opportunity. Is it the best one so far?
          if (weightedY < lowestWeightedY) {
            lowestWeightedY = weightedY;
            bestX = colX;
          }
          // No break here; continue checking for even better merges in the same column with other pieces.
          // This might be slightly inefficient but ensures the absolute best Y for the column is found.
          // However, the current logic is to find the first merge and then check if it's the best overall.
          // Let's keep it as is, a piece will only merge with *one* other piece anyway.
          break; // Found a merge for this column, no need to check other pieces for *this* colX.
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
    const weightedY = simulatedY + calculateHeightPenalty(simulatedY) * (isUrgent ? 2 : 1); // Higher penalty multiplier if urgent

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
 * Finds the X coordinate with the least "occupied" height, preferring lower overall stacks.
 * Considers height penalty.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
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
 * Implements the DEFAULT strategy when no critical conditions or specific merges are found.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop) {
  // 1. Immediate merge opportunity for 'next' piece at lowest weighted Y.
  const mergeOpportunity = findMergeOpportunity(boardStatePieces, pieceToDrop);
  if (mergeOpportunity) {
    return { x: mergeOpportunity.x, reason: mergeOpportunity.reason };
  }

  // 2. Large piece aggregation for type >= 9.
  // This logic is prioritized before general lowest Y if a suitable left-side spot is found.
  if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
    let bestLargePieceX = null;
    let lowestWeightedYLargePiece = Infinity;

    for (const colX of FINE_COLS) {
      // Ensure the *center* of the piece is to the left of or at LEFT_SIDE_X_MAX for better aggregation
      if (colX <= LEFT_SIDE_X_MAX) {
        const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);
        const weightedY = simulatedY + calculateHeightPenalty(simulatedY);
        if (weightedY < lowestWeightedYLargePiece) {
          lowestWeightedYLargePiece = weightedY;
          bestLargePieceX = colX;
        }
      }
    }
    if (bestLargePieceX !== null && lowestWeightedYLargePiece < DEADLINE_Y) { // Also ensure it's not too high
      return { x: bestLargePieceX, reason: `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) to left side.` };
    }
  }

  // 3. Lowest Y drop (considering height penalty) if no merge and no large piece aggregation preference.
  // This is the general "safety" move.
  return findLeastOccupiedX(boardStatePieces, pieceToDrop);
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
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
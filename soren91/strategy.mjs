/**
 * strategy.mjs - ドロップ位置決定戦略 (v91)
 *
 * v91: v90をベースに、ゲーム分析と戦略原則（特に「先読み」「大型ピースの片側集約」「連鎖設計」）をより深く反映させるための改善。
 *      物理エンジンの不確実性に対応しつつ、より能動的・計画的なピース配置を促すためのスコアリング調整を行う。
 *
 *      主な改善点:
 *      1.  `nextPieces[1]` (次に降ってくるピース) を考慮した**先読み（Look-ahead）ボーナス**を追加:
 *          - 現在のピースを置いた後に、次に降るピースがその場所に隣接して併合できる場合にボーナスを付与。
 *          - これにより、単発の併合だけでなく、連続的な併合機会を創出する配置を奨励する。
 *      2.  **大型ピース集約の動的化** (`DYNAMIC_AGGREGATION_BONUS_SCORE`):
 *          - `LEFT_SIDE_X_MAX` のような静的な左右どちらかへの集約ではなく、既に配置されている大型ピースの重心X座標に
 *            現在の大型ピースを近づけることでボーナスを付与。これにより、盤面状況に応じた柔軟な集約を促す。
 *      3.  **併合ボーナスの type スケーリング** (`MERGE_BONUS_BASE_SCORE`):
 *          - 単一の併合ボーナスではなく、併合によって生成されるピースの type が大きいほど高いボーナスを与えるように調整。
 *          - 高い type の併合は盤面クリア効果も高く、おじゃまを多く送れるため、戦略的価値が高い。
 *      4.  **おじゃまブロック緊急時の低Y併合ボーナス強化** (`GARBAGE_LOW_MERGE_URGENT_BONUS`):
 *          - `GARBAGE_RATIO_URGENT` や `OJAMA_GAUGE_URGENT` のような非常に危険な状況下での併合機会を、より強く優先させるため、
 *            低Yでの併合ボーナスを大幅に強化。これにより、緊急時のおじゃまクリア能力向上を図る。
 *      5.  既存のスコアリング重みの微調整:
 *          - `defaultStrategy` 内の各種ボーナス/ペナルティのバランスを調整し、戦略的行動が「最低Y優先」だけでなく、
 *            より積極的に選択されるように再調整。
 *
 *      - 物理挙動の近似に関する注意点も維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.

// Strategy-specific constants (Height Management)
const GAME_OVER_TOP_Y = 2.5;             // The Y coordinate for the TOP of the piece that means game over (from rules "〜y=2.5 を超えるとゲームオーバー").
const PENALTY_CRITICAL_CENTER_Y = 2.0;   // The center Y coordinate where height penalty becomes extremely high.
const PENALTY_WARN_CENTER_Y = 0.8;       // The center Y coordinate where height penalty starts.

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.35; // Adjusted from 0.25 to 0.35 to increase merge detection tolerance (v90)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
// LEFT_SIDE_X_MAX is removed for dynamic aggregation
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.6; // Bonus for T1 merges at low Y.

// Garbage / Critical Mode Thresholds
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges.
const GARBAGE_RATIO_URGENT = 0.3;       // When garbage ratio is very high, aggressive merges.
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges.
const OJAMA_GAUGE_URGENT = 0.5;         // When ojama gauge is very high, aggressive merges.
const GARBAGE_LOW_MERGE_BONUS = 1.0;    // Small bonus for low merges when not urgent garbage.
const GARBAGE_LOW_MERGE_URGENT_BONUS = 50.0; // Significant bonus for low merges when urgent garbage.

// HOLD Strategy Thresholds
const HOLD_LARGE_PIECE_THRESHOLD = 10; // Type 10+ for holding
const HOLD_SMALL_PIECE_THRESHOLD = 3;  // Type 1-3 for swapping with held large piece

// Default Strategy Scoring Weights (v91 adjustments)
const HEIGHT_PENALTY_WEIGHT = 1.0; // How much to penalize calculated height penalty (higher is worse)
const MERGE_BONUS_BASE_SCORE = 80.0; // Base for scaling merge bonus by piece type
const DYNAMIC_AGGREGATION_BONUS_SCORE = 40.0; // Bonus for aggregating large pieces near centroid
const LOOKAHEAD_MERGE_BONUS_SCORE = 30.0; // Bonus for a merge opportunity for nextPieces[1]
const BASE_Y_PREFERENCE_WEIGHT = 5.0; // Preference for lower Y (higher score for lower Y)


/**
 * Calculates a height-based penalty for a given Y coordinate.
 * Higher Y values (closer to PENALTY_CRITICAL_CENTER_Y) result in higher penalties.
 * Penalty starts linearly from PENALTY_WARN_CENTER_Y and becomes exponential near PENALTY_CRITICAL_CENTER_Y.
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
  return Math.pow(normalizedY, 3) * 2000; // Adjusted to cubic and higher multiplier
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

    // Add bonus for low merges when urgent garbage is present
    if (isUrgent && simulatedY < 0.0) {
      weightedY -= GARBAGE_LOW_MERGE_URGENT_BONUS; // Use the new, higher urgent bonus
    } else if (simulatedY < 0.0) {
      weightedY -= GARBAGE_LOW_MERGE_BONUS; // Small bonus for low merges if not urgent
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
 * Calculates the average X coordinate of existing large pieces to guide aggregation.
 * @param {Array<{type: number, x: number, y: number, r: number}>} pieces - Array of pieces on the board.
 * @returns {number} The average X coordinate of large pieces, or 0 if none exist.
 */
function calculateLargePieceCentroid(pieces) {
  let totalX = 0;
  let largePieceCount = 0;
  for (const p of pieces) {
    if (p.type >= LARGE_PIECE_THRESHOLD) {
      totalX += p.x;
      largePieceCount++;
    }
  }
  return largePieceCount > 0 ? totalX / largePieceCount : 0; // Default to center if no large pieces
}

/**
 * Finds the X coordinate with the best overall score considering merges, large piece aggregation, and lowest weighted Y.
 * This is the core default strategy. (Refactored to use a scoring mechanism in v90, improved in v91)
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @param {Array<{type: number, r: number}> | undefined} nextPieces - Array of next pieces for look-ahead (nextPieces[1] specifically).
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop, nextPieces) {
  let bestOverallX = 0.0;
  let highestOverallScore = -Infinity; // We want to maximize the score
  let bestOverallReason = "DEFAULT: Least occupied column (lowest weighted Y).";

  const largePieceCentroidX = calculateLargePieceCentroid(boardStatePieces);

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);

    // If the TOP of the simulated piece exceeds the game over line, disqualify this column.
    if (simulatedY + pieceToDrop.r > GAME_OVER_TOP_Y) {
        continue;
    }

    let currentScore = 0;
    let columnReason = "DEFAULT: Least occupied column (lowest weighted Y).";

    // 1. Base preference for lower Y & Height Penalty
    // (GAME_OVER_TOP_Y - simulatedY) gives a higher positive number for lower Y.
    currentScore += (GAME_OVER_TOP_Y - simulatedY) * BASE_Y_PREFERENCE_WEIGHT;
    currentScore -= calculateHeightPenalty(simulatedY) * HEIGHT_PENALTY_WEIGHT;

    // 2. Merge Opportunity Bonus (scaled by type)
    let mergeFoundForNext = false;
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          mergeFoundForNext = true;
          // Scale merge bonus by type: higher type merges are more valuable
          currentScore += MERGE_BONUS_BASE_SCORE * (1 + (pieceToDrop.type / 15));
          columnReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX}.`;
          break;
        }
      }
    }

    // 3. Dynamic Large Piece Aggregation Bonus
    if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
      // Calculate distance to the centroid of other large pieces. Closer is better.
      const distanceToCentroid = Math.abs(colX - largePieceCentroidX);
      // Bonus scales down from DYNAMIC_AGGREGATION_BONUS_SCORE as distance increases
      const aggregationBonus = DYNAMIC_AGGREGATION_BONUS_SCORE * (1 - Math.min(1, distanceToCentroid / WALL_MARGIN));
      currentScore += aggregationBonus;
      if (!mergeFoundForNext) { // Only assign this reason if no merge was found, merge is higher priority.
         columnReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) near centroid at ${colX}.`;
      }
    }

    // 4. Look-ahead Bonus for nextPieces[1]
    if (nextPieces && nextPieces.length > 1 && nextPieces[1]) {
        const next1Piece = nextPieces[1];
        // Create a hypothetical board state by adding the current pieceToDrop at simulatedY
        const hypotheticalPieces = [...boardStatePieces, { type: pieceToDrop.type, x: colX, y: simulatedY, r: pieceToDrop.r }];

        // Simulate nextPieces[1] landing at the same colX on this hypothetical board
        const simulatedY_next1 = simulateDropY({ pieces: hypotheticalPieces }, colX, next1Piece);

        // Check for merge opportunity for nextPieces[1] in the hypothetical board
        for (const existingPiece of hypotheticalPieces) {
            if (existingPiece.type === next1Piece.type && existingPiece.type < 15) {
                const distance = Math.sqrt(
                    Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY_next1 - existingPiece.y, 2)
                );
                if (distance < (next1Piece.r + existingPiece.r - MERGE_BUFFER)) {
                    currentScore += LOOKAHEAD_MERGE_BONUS_SCORE;
                    // No need to change columnReason here, as it's a secondary bonus
                    break;
                }
            }
        }
    }

    // Compare with highest score found so far
    if (currentScore > highestOverallScore) {
      highestOverallScore = currentScore;
      bestOverallX = colX;
      bestOverallReason = columnReason;
    }
  }

  return { x: bestOverallX, reason: bestOverallReason };
}


export function decide(boardState) {
  const { pieces, next, hold, canHold, garbage, nextPieces } = boardState; // Destructure nextPieces

  // --- Priority 1: HOLD Logic ---
  let action = tryHoldStrategy(boardState);
  if (action) return action;

  // --- Priority 2: CRITICAL Mode (Garbage) ---
  // Triggered by high garbage ratio or gauge.
  // Prioritize aggressive merges.
  const isUrgentGarbage = garbage.gauge >= OJAMA_GAUGE_URGENT || garbage.ratio > GARBAGE_RATIO_URGENT;
  const isOjamaMerge = garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE || garbage.ratio > GARBAGE_RATIO_OJAMA_MERGE;

  if (isUrgentGarbage) {
    action = findAggressiveCriticalMerge(pieces, next, true);
    if (action) return action;
  }
  if (isOjamaMerge) {
    action = findAggressiveCriticalMerge(pieces, next, false);
    if (action) return action;
  }

  // --- Priority 3: ULTRA Mode (T1 Low Merge) ---
  action = findT1LowMerge(pieces, next);
  if (action) return action;

  // --- Priority 4: DEFAULT Strategy ---
  action = defaultStrategy(pieces, next, nextPieces); // Pass nextPieces for look-ahead
  if (action) return action;

  // Fallback (should ideally not be reached with a comprehensive strategy)
  // The defaultStrategy function guarantees a return, so this line is technically unreachable.
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
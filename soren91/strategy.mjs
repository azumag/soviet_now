/**
 * strategy.mjs - ドロップ位置決定戦略 (v96)
 *
 * v96: v95をベースに、ゲーム分析（特に max_y が高すぎる問題）と戦略原則をより深く反映させるための改善。
 *      物理エンジンの不確実性に対応しつつ、より能動的・計画的なピース配置を促すためのスコアリング調整を継続する。
 *
 *      主な改善点:
 *      1.  **高さ管理の抜本的強化のさらなる強化**:
 *          - `PENALTY_CRITICAL_CENTER_Y` を 2.0 → 1.8 に引き下げ、危険水域をより早く検知。
 *          - `calculateHeightPenalty` のペナルティ乗数を 12000 → 20000 に増加させ、デッドラインに近づくほど急峻なペナルティとなるように調整。
 *          - `HEIGHT_PENALTY_WEIGHT` を 4.0 → 5.0 に引き上げ、高さに対するペナルティの影響力を増大。
 *          - 分析結果から、既存の高さ管理強化でもmax_yがデッドラインを超えるケースが見られたため、さらなるペナルティ強化が必要と判断。
 *
 *      2.  **大型ピースの集約ボーナス強化**:
 *          - `DYNAMIC_AGGREGATION_BONUS_SCORE` を 80.0 → 100.0 に引き上げ、大型ピースの分散を防ぎ、より集中的に配置することを奨励。
 *
 *      3.  **おじゃまブロック対応時の低Yマージボーナス強化**:
 *          - `defaultStrategy` 内での `GARBAGE_LOW_MERGE_URGENT_BONUS` と `GARBAGE_LOW_MERGE_BONUS` の乗数をそれぞれ 3.0 → 5.0 および 4.0 → 6.0 に引き上げ、
 *            おじゃま発生時の低Yでのマージをさらに強力に推奨。これにより、おじゃまブロックを効率的に除去し、盤面を低く保つことを促す。
 *
 *      - 物理挙動の近似に関する注意点も維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.

// Strategy-specific constants (Height Management)
const GAME_OVER_TOP_Y = 2.5;             // The Y coordinate for the TOP of the piece that means game over (from rules "〜y=2.5 を超えるとゲームオーバー").
const PENALTY_CRITICAL_CENTER_Y = 1.8;   // Adjusted from 2.0. The center Y coordinate where height penalty becomes extremely high.
const PENALTY_WARN_CENTER_Y = 0.8;       // The center Y coordinate where height penalty starts.

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.4; // Adjusted from 0.35 to 0.4 for increased merge detection tolerance (v92)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.0; // Increased from 0.6. Bonus for T1 merges at low Y.

// Garbage / Critical Mode Thresholds
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges.
const GARBAGE_RATIO_URGENT = 0.3;       // When garbage ratio is very high, aggressive merges.
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges.
const OJAMA_GAUGE_URGENT = 0.5;         // When ojama gauge is very high, aggressive merges.
const GARBAGE_LOW_MERGE_BONUS = 3.0;
const GARBAGE_LOW_MERGE_URGENT_BONUS = 100.0;

// HOLD Strategy Thresholds
const HOLD_LARGE_PIECE_THRESHOLD = 10; // Type 10+ for holding
const HOLD_SMALL_PIECE_THRESHOLD = 3;  // Type 1-3 for swapping with held large piece

// Default Strategy Scoring Weights (v96 adjustments)
const HEIGHT_PENALTY_WEIGHT = 5.0; // Increased from 4.0 to give height much more influence
const MERGE_BONUS_BASE_SCORE = 120.0;
const DYNAMIC_AGGREGATION_BONUS_SCORE = 100.0; // Increased from 80.0
const LOOKAHEAD_MERGE_BONUS_SCORE = 50.0;
const BASE_Y_PREFERENCE_WEIGHT = 5.0;


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
    // Greatly increased to reflect criticality earlier
    return 100000;
  }
  // Linear penalty between WARN_CENTER_Y and CRITICAL_CENTER_Y, then cubic exponential
  const linearRange = PENALTY_CRITICAL_CENTER_Y - PENALTY_WARN_CENTER_Y;
  const normalizedY = (y - PENALTY_WARN_CENTER_Y) / linearRange; // 0 to 1 in the warn range
  return Math.pow(normalizedY, 3) * 20000; // Adjusted from 12000 to 20000 for significantly higher impact
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
 * of `pieceToDrop.type`. Uses a scoring system that includes height penalties.
 * This function is used primarily for HOLD strategy evaluation and as a specific early priority check.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece that will be dropped.
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason string, or null if no merge opportunity.
 */
function findMergeOpportunity(boardStatePieces, pieceToDrop) {
  let bestX = null;
  let highestScore = -Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);

    // If the TOP of the simulated piece exceeds the game over line, disqualify this column.
    if (simulatedY + pieceToDrop.r > GAME_OVER_TOP_Y) {
        continue;
    }

    let currentScore = 0;
    currentScore += (GAME_OVER_TOP_Y - simulatedY) * BASE_Y_PREFERENCE_WEIGHT;
    currentScore -= calculateHeightPenalty(simulatedY) * HEIGHT_PENALTY_WEIGHT;

    // Check for merge with existing pieces of the same type
    for (const existingPiece of boardStatePieces) {
      // Type 15 merges *and disappears*, so avoid planning merges for it unless it's critical.
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        // Merge if centers are close enough based on radii, considering MERGE_BUFFER
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge opportunity for this colX. Add merge bonus.
          currentScore += MERGE_BONUS_BASE_SCORE * (1 + (pieceToDrop.type / 15));

          if (currentScore > highestScore) {
            highestScore = currentScore;
            bestX = colX;
          }
          break; // Found merge for this colX, move to next colX
        }
      }
    }
  }

  if (bestX !== null) {
    const finalSimulatedY = simulateDropY({pieces: boardStatePieces}, bestX, pieceToDrop);
    return { x: bestX, y: finalSimulatedY, reason: `Merge ${pieceToDrop.type} at highest score.` };
  }
  return null;
}

/**
 * Finds a low Y merge opportunity specifically for type 1 pieces.
 * Prioritizes positions where a T1 piece can merge low, potentially clearing garbage.
 * Uses a scoring system that includes height penalties.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece that will be dropped (expected to be type 1).
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason, or null.
 */
function findT1LowMerge(boardStatePieces, pieceToDrop) {
  if (pieceToDrop.type !== 1) return null;

  let bestX = null;
  let highestScore = -Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);

    // If the TOP of the simulated piece exceeds the game over line, disqualify this column.
    if (simulatedY + pieceToDrop.r > GAME_OVER_TOP_Y) {
        continue;
    }

    let currentScore = 0;
    currentScore += (GAME_OVER_TOP_Y - simulatedY) * BASE_Y_PREFERENCE_WEIGHT;
    currentScore -= calculateHeightPenalty(simulatedY) * HEIGHT_PENALTY_WEIGHT;

    // Apply T1_LOW_MERGE_HEIGHT_ADVANTAGE to make low merges even more attractive
    if (simulatedY < PENALTY_WARN_CENTER_Y) {
      currentScore += T1_LOW_MERGE_HEIGHT_ADVANTAGE * MERGE_BONUS_BASE_SCORE; // Boosted T1 low merge bonus
    }

    // Check for merge with existing type 1 pieces
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === 1) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge. Add a merge bonus.
          currentScore += MERGE_BONUS_BASE_SCORE * (1 + (pieceToDrop.type / 15));

          if (currentScore > highestScore) {
            highestScore = currentScore;
            bestX = colX;
          }
          break;
        }
      }
    }
  }

  if (bestX !== null) {
    const finalSimulatedY = simulateDropY({pieces: boardStatePieces}, bestX, pieceToDrop);
    return { x: bestX, y: finalSimulatedY, reason: `T1 low merge at highest score.` };
  }
  return null;
}

/**
 * Finds aggressive merge opportunities in critical situations (high garbage).
 * Prioritizes any merge opportunity that can occur with a high score (low Y, merge bonus).
 * Uses a scoring system.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @param {boolean} isUrgent - True if in an extremely urgent garbage situation.
 * @returns {{x: number, y: number, reason: string} | null} The best drop X, its simulated Y, and a reason, or null.
 */
function findAggressiveCriticalMerge(boardStatePieces, pieceToDrop, isUrgent) {
  let bestX = null;
  let highestScore = -Infinity;

  for (const colX of FINE_COLS) {
    const simulatedY = simulateDropY({pieces: boardStatePieces}, colX, pieceToDrop);

    // If the TOP of the simulated piece exceeds the game over line, disqualify this column.
    if (simulatedY + pieceToDrop.r > GAME_OVER_TOP_Y) {
        continue;
    }

    let currentScore = 0;
    // Base preference for lower Y
    currentScore += (GAME_OVER_TOP_Y - simulatedY) * BASE_Y_PREFERENCE_WEIGHT;
    currentScore -= calculateHeightPenalty(simulatedY) * HEIGHT_PENALTY_WEIGHT;

    // Add a strong bonus for low Y when urgent, similar to defaultStrategy
    if (isUrgent && simulatedY < 0.0) {
        // Increased multiplier from 3.0 to 5.0
        currentScore += GARBAGE_LOW_MERGE_URGENT_BONUS * 5.0;
    } else if (simulatedY < 0.0) { // For non-urgent critical merges, still prefer low Y
        // Increased multiplier from 4.0 to 6.0
        currentScore += GARBAGE_LOW_MERGE_BONUS * 6.0;
    }

    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) { // Only merge same type, and not type 15 unless game over is imminent
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          // Found a merge opportunity for this colX. Add merge bonus.
          currentScore += MERGE_BONUS_BASE_SCORE * (1 + (pieceToDrop.type / 15));
          if (isUrgent) {
              currentScore *= 1.5; // Further boost this entire score if urgent
          }

          if (currentScore > highestScore) {
            highestScore = currentScore;
            bestX = colX;
          }
          break;
        }
      }
    }
  }

  if (bestX !== null) {
    const finalSimulatedY = simulateDropY({pieces: boardStatePieces}, bestX, pieceToDrop);
    return { x: bestX, y: finalSimulatedY, reason: `Aggressive critical merge type ${pieceToDrop.type} (urgent: ${isUrgent}).` };
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
 * This is the core default strategy, now also garbage-aware.
 * @param {Array<{type: number, x: number, y: number, r: number}>} boardStatePieces - Only pieces from boardState.
 * @param {{type: number, r: number}} pieceToDrop - The piece to drop.
 * @param {Array<{type: number, r: number}> | undefined} nextPieces - Array of next pieces for look-ahead (nextPieces[1] specifically).
 * @param {boolean} isUrgentGarbage - True if in an extremely urgent garbage situation.
 * @param {boolean} isOjamaMerge - True if in a general ojama merge situation.
 * @returns {{x: number, reason: string}} The chosen drop X and a reason.
 */
function defaultStrategy(boardStatePieces, pieceToDrop, nextPieces, isUrgentGarbage, isOjamaMerge) {
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
    let mergeFoundForNext = false; // Track if a merge is found for the current piece

    // 1. Base preference for lower Y & Height Penalty (significantly increased)
    currentScore += (GAME_OVER_TOP_Y - simulatedY) * BASE_Y_PREFERENCE_WEIGHT;
    currentScore -= calculateHeightPenalty(simulatedY) * HEIGHT_PENALTY_WEIGHT;

    // 2. Merge Opportunity Bonus (scaled by type, now garbage-aware)
    for (const existingPiece of boardStatePieces) {
      if (existingPiece.type === pieceToDrop.type && existingPiece.type < 15) {
        const distance = Math.sqrt(
          Math.pow(colX - existingPiece.x, 2) + Math.pow(simulatedY - existingPiece.y, 2)
        );
        if (distance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
          mergeFoundForNext = true;
          let mergeBonus = MERGE_BONUS_BASE_SCORE * (1 + (pieceToDrop.type / 15));
          if (isUrgentGarbage) {
              mergeBonus *= 2.0; // Double merge bonus in urgent garbage
              columnReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX} (Urgent Garbage Merge).`;
          } else if (isOjamaMerge) {
              mergeBonus *= 1.5; // 1.5x merge bonus in ojama merge
              columnReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX} (Ojama Merge).`;
          } else {
              columnReason = `DEFAULT: Merge ${pieceToDrop.type} at ${colX}.`;
          }
          currentScore += mergeBonus;
          break;
        }
      }
    }

    // 3. Dynamic Large Piece Aggregation Bonus (Increased weight)
    if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
      // Calculate distance to the centroid of other large pieces. Closer is better.
      const distanceToCentroid = Math.abs(colX - largePieceCentroidX);
      // Bonus scales down from DYNAMIC_AGGREGATION_BONUS_SCORE as distance increases
      const aggregationBonus = DYNAMIC_AGGREGATION_BONUS_SCORE * (1 - Math.min(1, distanceToCentroid / WALL_MARGIN));
      currentScore += aggregationBonus;
      // Only assign this reason if no merge was found, merge is higher priority.
      if (!mergeFoundForNext && columnReason === "DEFAULT: Least occupied column (lowest weighted Y).") {
         columnReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) near centroid at ${colX}.`;
      }
    }

    // 4. Look-ahead Bonus for nextPieces[1] (Increased weight)
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

    // 5. Additional Direct Low Y Preference when garbage is active (always applied if conditions met)
    // This provides a strong incentive to place pieces low during garbage crises, even if a high-Y merge is available.
    if (isUrgentGarbage && simulatedY < 0.0) {
        // Significantly boosted multiplier from 3.0 to 5.0
        currentScore += GARBAGE_LOW_MERGE_URGENT_BONUS * 5.0;
        if (!mergeFoundForNext && !columnReason.includes("Merge")) { // Only overwrite reason if not already a merge reason
            columnReason = `DEFAULT: Urgent Garbage Low Y Preference at ${colX}.`;
        }
    } else if (isOjamaMerge && simulatedY < 0.0) {
        // Significantly boosted multiplier from 4.0 to 6.0
        currentScore += GARBAGE_LOW_MERGE_BONUS * 6.0;
        if (!mergeFoundForNext && !columnReason.includes("Merge") && !columnReason.includes("Urgent Garbage")) { // Only overwrite if not a merge or urgent garbage reason
            columnReason = `DEFAULT: Ojama Low Y Preference at ${colX}.`;
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
  const { pieces, next, hold, canHold, garbage, nextPieces } = boardState;

  // --- Priority 1: HOLD Logic ---
  let action = tryHoldStrategy(boardState);
  if (action) return action;

  // --- Garbage Conditions ---
  const isUrgentGarbage = garbage.gauge >= OJAMA_GAUGE_URGENT || garbage.ratio > GARBAGE_RATIO_URGENT;
  const isOjamaMerge = garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE || garbage.ratio > GARBAGE_RATIO_OJAMA_MERGE;

  // --- Priority 2: CRITICAL Mode (Garbage - direct merge attempt) ---
  // If in any garbage situation, first try to find an aggressive merge.
  // This now incorporates scoring and strong low Y preference.
  if (isUrgentGarbage || isOjamaMerge) {
    action = findAggressiveCriticalMerge(pieces, next, isUrgentGarbage);
    if (action) return action;
  }

  // --- Priority 3: ULTRA Mode (T1 Low Merge) ---
  // This is a specific merge for small pieces that can help clear the board.
  // This now incorporates scoring and strong height penalties.
  action = findT1LowMerge(pieces, next);
  if (action) return action;

  // --- Priority 4: DEFAULT Strategy ---
  // The comprehensive scoring strategy, now also garbage-aware and with heightened height management.
  action = defaultStrategy(pieces, next, nextPieces, isUrgentGarbage, isOjamaMerge);
  if (action) return action;

  // Fallback (should ideally not be reached with a comprehensive strategy)
  // The defaultStrategy function guarantees a return, so this line is technically unreachable.
  return { x: 0.0, reason: "Fallback: Should not happen, dropping at center." };
}
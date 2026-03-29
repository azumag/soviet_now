/**
 * strategy.mjs - ドロップ位置決定戦略 (v106)
 *
 * v106: v105をベースに、大型ピースの集約戦略をさらに洗練し、より柔軟な初期集約と高さ管理の微調整を行います。
 *      v105で強化された初期集約バイアスが特定の状況で盤面を窮屈にしていた可能性を考慮し、
 *      より盤面の状況に応じた自然な集約形成を促すように変更します。
 *
 *      主な改善点 (v105からの調整点):
 *      1.  **大型ピース集約ロジックの改善**:
 *          - `getLargePieceAggregationInfo` 関数を修正し、盤面に大型ピースが存在するが、
 *            まだ明確な左右どちらかの「優勢側」が確立されていない場合に、
 *            全ての大型ピースの平均X座標をターゲットとして返すように変更。
 *            これにより、固定的な初期左側バイアスに依存せず、ピースが自然に集まり始めた場所へ
 *            次の大型ピースを誘導しやすくなります。
 *          - `defaultStrategy` 内の大型ピースの配置決定ロジックを、上記の変更に合わせて更新。
 *            優勢側がない場合は、全体平均Xへの近さを評価基準に追加。
 *          - 盤面に大型ピースが全く存在しない場合の初期左側バイアスを、`DYNAMIC_AGGREGATION_BONUS_SCORE / 4`
 *            から `DYNAMIC_AGGREGATION_BONUS_SCORE / 8` に緩和。
 *            これにより、ゲーム開始直後の大型ピース配置の自由度を高め、不必要な高さ積み上げリスクを低減。
 *            これはv105で強化された部分を再調整するものです。
 *
 *      2.  **高さペナルティの微調整**:
 *          - `HEIGHT_PENALTY_WEIGHT` を `45.0` から `40.0` にわずかに減少。
 *            `calculateHeightPenalty` 関数自体が非常にアグレッシブなペナルティを持っているため、
 *            他のスコアリング要素（特にマージボーナス）がより効果的に働く余地を確保し、
 *            全体のスコアバランスを改善することを目的とします。
 *
 *      その他、v104/v105で導入された高さ管理の劇的強化や、おじゃまブロック対応、LOOKAHEADボーナス、MERGE_BUFFERなどの調整は維持。
 *      物理エンジンの不確実性に対応するための保守的かつ計画的なピース配置を促すスコアリング調整は引き続き重要視されます。
 *
 * - 物理挙動の近似に関する注意点も維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.

// Strategy-specific constants (Height Management)
const GAME_OVER_TOP_Y = 2.5;             // The Y coordinate for the TOP of the piece that means game over (from rules "〜y=2.5 を超えるとゲームオーバー").
const TOP_Y_CRITICAL_PENALTY_START = 1.6; // Adjusted (was 1.7): If piece's top Y reaches this, penalty becomes extremely high.
const TOP_Y_WARN_PENALTY_START = 1.2;     // Adjusted (was 1.3): If piece's top Y reaches this, penalty starts.
const GAME_OVER_DANGER_Y_THRESHOLD = 0.6; // Adjusted (was 0.5): If simulatedY + piece.r is within this distance of GAME_OVER_TOP_Y, apply massive penalty.

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.5; // Adjusted (was 0.4, then 0.5): Increased to account for irregular shapes (凸ポリゴン)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (from v101, unchanged)

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

// Default Strategy Scoring Weights (v104 adjustments)
const HEIGHT_PENALTY_WEIGHT = 40.0; // Adjusted from 45.0 to 40.0: Slightly reduced
const MERGE_BONUS_BASE_SCORE = 120.0;
const DYNAMIC_AGGREGATION_BONUS_SCORE = 400.0; // Unchanged from v103
const LARGE_PIECE_DIVERGENCE_PENALTY = 2000.0; // Unchanged from v103
const LOOKAHEAD_MERGE_BONUS_SCORE = 80.0;
const BASE_Y_PREFERENCE_WEIGHT = 7.0;


/**
 * Calculates a height-based penalty for a given Y coordinate and radius.
 * Higher Y values (closer to TOP_Y_CRITICAL_PENALTY_START) result in higher penalties.
 * Penalty starts linearly from TOP_Y_WARN_PENALTY_START and becomes exponential near TOP_Y_CRITICAL_PENALTY_START.
 * @param {number} y - The Y coordinate of the piece's center.
 * @param {number} r - The radius of the piece.
 * @returns {number} The calculated penalty.
 */
function calculateHeightPenalty(y, r) {
  const topY = y + r;

  // Immediately apply an immense penalty if the piece's top is very close to the game over line.
  // This is a last-resort safety measure due to physics engine approximations.
  if (topY >= GAME_OVER_TOP_Y - GAME_OVER_DANGER_Y_THRESHOLD) {
    return 2000000; // Massive penalty to strongly discourage game-over imminent placements
  }

  if (topY < TOP_Y_WARN_PENALTY_START) {
    return 0;
  }
  // If topY is above critical start but not yet in the immense penalty zone, apply strong critical penalty
  if (topY >= TOP_Y_CRITICAL_PENALTY_START) {
    return 750000; // Existing critical penalty, but now stronger
  }
  // Linear penalty between WARN_PENALTY_START and CRITICAL_PENALTY_START, then cubic exponential
  const linearRange = TOP_Y_CRITICAL_PENALTY_START - TOP_Y_WARN_PENALTY_START;
  // Ensure linearRange is positive to avoid division by zero or negative
  if (linearRange <= 0) return 0; // Should not happen with current constants but for safety
  const normalizedTopY = (topY - TOP_Y_WARN_PENALTY_START) / linearRange; // 0 to 1 in the warn range
  // Clamp normalizedTopY to [0, 1] to prevent negative results or extreme values if topY somehow exceeds critical start
  const clampedNormalizedTopY = Math.max(0, Math.min(1, normalizedTopY));
  return Math.pow(clampedNormalizedTopY, 3) * 350000; // For significantly higher impact
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
    // MERGE_BUFFER is also used here to approximate the effective "width" of a piece
    // to account for irregular shapes.
    if (horizontalDistance < (pieceToDrop.r + existingPiece.r - MERGE_BUFFER)) {
      // If overlaps horizontally, it will stack on top if higher
      // We want to simulate stacking, so we need to find the highest point the new piece would rest on.
      const currentStackHeight = existingPiece.y + existingPiece.r;
      if (currentStackHeight + pieceToDrop.r > simulatedY) {
        simulatedY = currentStackHeight + pieceToDrop.r;
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
    currentScore -= calculateHeightPenalty(simulatedY, pieceToDrop.r) * HEIGHT_PENALTY_WEIGHT;

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
    currentScore -= calculateHeightPenalty(simulatedY, pieceToDrop.r) * HEIGHT_PENALTY_WEIGHT;

    // Apply T1_LOW_MERGE_HEIGHT_ADVANTAGE to make low merges even more attractive
    if (simulatedY < TOP_Y_WARN_PENALTY_START) { // Use TOP_Y_WARN_PENALTY_START as a proxy for "low" based on topY
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
    currentScore -= calculateHeightPenalty(simulatedY, pieceToDrop.r) * HEIGHT_PENALTY_WEIGHT;

    // Add a strong bonus for low Y when urgent, similar to defaultStrategy
    if (isUrgent && simulatedY < 0.0) {
        currentScore += GARBAGE_LOW_MERGE_URGENT_BONUS * 5.0;
    } else if (simulatedY < 0.0) { // For non-urgent critical merges, still prefer low Y
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
 * Determines the dominant side for large pieces and calculates an average X for that side.
 * This aims to fulfill the "大型ピースの片側集約" principle more directly.
 * @param {Array<{type: number, x: number, y: number, r: number}>} pieces - Array of pieces on the board.
 * @returns {{targetX: number, dominantSide: 'left'|'right'|'none'}|null} An object with the target X and dominant side, or null if no large pieces.
 */
function getLargePieceAggregationInfo(pieces) {
  let leftSidePieces = [];
  let rightSidePieces = [];
  let allLargePieces = [];

  for (const p of pieces) {
    if (p.type >= LARGE_PIECE_THRESHOLD) {
      allLargePieces.push(p);
      if (p.x < 0) {
        leftSidePieces.push(p);
      } else {
        rightSidePieces.push(p);
      }
    }
  }

  if (allLargePieces.length === 0) {
      return null; // No large pieces at all
  }

  const leftCount = leftSidePieces.length;
  const rightCount = rightSidePieces.length;

  // A side is dominant if it has at least 2 pieces more than the other side.
  // This threshold helps prevent fluctuating dominant sides from single pieces.
  if (leftCount >= rightCount + 2) {
    const avgX = leftSidePieces.reduce((sum, p) => sum + p.x, 0) / leftCount;
    return { targetX: avgX, dominantSide: 'left' };
  } else if (rightCount >= leftCount + 2) {
    const avgX = rightSidePieces.reduce((sum, p) => sum + p.x, 0) / rightCount;
    return { targetX: avgX, dominantSide: 'right' };
  } else {
    // If no clear dominant side, but there are large pieces, return their overall average X
    const overallAvgX = allLargePieces.reduce((sum, p) => sum + p.x, 0) / allLargePieces.length;
    return { targetX: overallAvgX, dominantSide: 'none' }; // 'none' indicates no strong dominance, but an aggregation point exists
  }
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

  const aggregationInfo = getLargePieceAggregationInfo(boardStatePieces);

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
    currentScore -= calculateHeightPenalty(simulatedY, pieceToDrop.r) * HEIGHT_PENALTY_WEIGHT;

    // 2. Additional Direct Low Y Preference when garbage is active (moved earlier for higher impact)
    if (isUrgentGarbage && simulatedY < 0.0) {
        currentScore += GARBAGE_LOW_MERGE_URGENT_BONUS * 5.0;
        // Overwrite reason only if not already a more specific merge reason
        if (!mergeFoundForNext && !columnReason.includes("Merge")) {
            columnReason = `DEFAULT: Urgent Garbage Low Y Preference at ${colX}.`;
        }
    } else if (isOjamaMerge && simulatedY < 0.0) {
        currentScore += GARBAGE_LOW_MERGE_BONUS * 6.0;
        // Overwrite reason only if not a merge or urgent garbage reason
        if (!mergeFoundForNext && !columnReason.includes("Merge") && !columnReason.includes("Urgent Garbage")) {
            columnReason = `DEFAULT: Ojama Low Y Preference at ${colX}.`;
        }
    }

    // 3. Merge Opportunity Bonus (scaled by type, now garbage-aware)
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

    // 4. Dynamic Large Piece Aggregation Bonus/Penalty
    if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        if (aggregationInfo) {
            const { targetX, dominantSide } = aggregationInfo;

            if (dominantSide === 'none') {
                // If large pieces exist but no clear dominant side, aim for their average X
                const distanceToOverallAvg = Math.abs(colX - targetX);
                const aggregationBonus = DYNAMIC_AGGREGATION_BONUS_SCORE * (1 - Math.min(1, distanceToOverallAvg / (WALL_MARGIN * 2)));
                currentScore += aggregationBonus;
                if (!mergeFoundForNext && columnReason === "DEFAULT: Least occupied column (lowest weighted Y).") {
                    columnReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) towards overall average X at ${targetX.toFixed(2)}.`;
                }
            } else { // There is a clear dominant side
                const isLeft = colX < 0;
                const isRight = colX >= 0;

                if ((dominantSide === 'left' && isRight) || (dominantSide === 'right' && isLeft)) {
                    // If dropping a large piece on the non-dominant side, apply a strong penalty
                    currentScore -= LARGE_PIECE_DIVERGENCE_PENALTY;
                    if (!mergeFoundForNext) { // Only overwrite if no strong merge reason
                        columnReason = `DEFAULT: Penalty for large piece (type ${pieceToDrop.type}) on non-dominant side.`;
                    }
                } else {
                    // If on the dominant side, give a bonus based on proximity to targetX
                    const distanceToTarget = Math.abs(colX - targetX);
                    // Bonus scales down from DYNAMIC_AGGREGATION_BONUS_SCORE as distance increases
                    const aggregationBonus = DYNAMIC_AGGREGATION_BONUS_SCORE * (1 - Math.min(1, distanceToTarget / (WALL_MARGIN * 2)));
                    currentScore += aggregationBonus;
                    if (!mergeFoundForNext && columnReason === "DEFAULT: Least occupied column (lowest weighted Y).") {
                         columnReason = `DEFAULT: Aggregate large piece (type ${pieceToDrop.type}) towards dominant side target at ${targetX.toFixed(2)}.`;
                    }
                }
            }
        } else {
            // No large pieces on board at all. Apply a softer initial left bias.
            if (colX < 0) { // Favor left side for initiating aggregation, but softly
                currentScore += DYNAMIC_AGGREGATION_BONUS_SCORE / 8; // Reverted to /8 from /4 (v105 adjustment)
                if (!mergeFoundForNext && columnReason === "DEFAULT: Least occupied column (lowest weighted Y).") {
                    columnReason = `DEFAULT: Softly initiate large piece aggregation towards left for type ${pieceToDrop.type}.`;
                }
            }
        }
    }

    // 5. Look-ahead Bonus for nextPieces[1]
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
                    let lookAheadBonus = LOOKAHEAD_MERGE_BONUS_SCORE;
                    if (next1Piece.type >= LARGE_PIECE_THRESHOLD) {
                        lookAheadBonus *= 2.0; // Double bonus for look-ahead if next is a large piece
                    }
                    currentScore += lookAheadBonus;
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
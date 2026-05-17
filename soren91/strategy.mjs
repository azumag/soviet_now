/**
 * strategy.mjs - ドロップ位置決定戦略 (v204 - Enhanced Merge Priority, Aggressive Dispersion & Tuned Height Avoidance)
 *
 * v203をベースに以下の点を改善：
 * - 高さ管理のさらなる調整とデッドライン回避のバランス:
 *   - DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER をわずかに減少させ (0.52 -> 0.50)、絶対回避ラインをデッドラインに少し近づけることで、「No valid move」による中央ドロップを減らし、より多様なリスクテイクを可能にする。この変更は、厳しく設定された他の高さペナルティによって相殺されることを期待。
 *   - HEIGHT_PENALTY_WEIGHT をさらに増加させ (7_000_000.0 -> 7_500_000.0)、デッドラインに近い高所へのドロップに対するペナルティをさらに厳しく適用。
 *   - CRITICAL_HEIGHT_MARGIN を増加させ (2.4 -> 2.5)、高さペナルティの指数関数的な増加が適用される範囲をさらに拡大。
 *   - 全体的な盤面を低く保つため、Y座標に応じた汎用ペナルティ Y_POSITION_PENALTY_WEIGHT を増加 (500 -> 700)。
 * - 盤面密度管理のさらなる強化と分散促進:
 *   - CROWDING_PENALTY_START_THRESHOLD を減少させ (3 -> 2)、過密状態をより早期に検知しペナルティを適用。
 *   - CROWDING_PENALTY_PER_PIECE を増加させ (150 -> 200)、過密状態に対するペナルティをさらに強化し、水平方向へのピース分散を強力に促進。
 *   - 比較的空いているエリアへのドロップにボーナス EMPTY_SPACE_BONUS を増加させ (1000 -> 1500)、意図的に分散を促す。
 * - 併合とパイプラインの優先度向上:
 *   - MERGE_BONUS_SCALE_FACTOR を増加させ (95 -> 100)、併合をより積極的に狙う。
 *   - PIPELINE_BONUS_DIRECT_CHAIN および PIPELINE_BONUS_INDIRECT_CHAIN を増加させ (2100/850 -> 2200/900)、連鎖的な併合機会をより重視する。
 * - おじゃまブロック処理の強化:
 *   - GARBAGE_URGENT_GAUGE_BONUS_HIGH_RATIO を増加させ (20000 -> 25000)、高ゲージかつ高比率時のおじゃまクリアへのインセンティブを強化。
 *   - GARBAGE_IMMINENT_MERGE_BONUS を増加させ (20000 -> 25000)、おじゃまが差し迫っている際の併合優先度をさらに高める。
 *   - GARBAGE_CLEAR_MERGE_BONUS_LOW_Y を増加させ (5000 -> 6000)、盤面下部での併合によるおじゃまクリアをさらに促進。
 * - 大型ピースの片側集約ロジックの強化:
 *   - LARGE_PIECE_AGGREGATION_BONUS を増加させ (2300 -> 2500)、集約のインセンティブを強化。
 *   - LARGE_PIECE_AGGREGATION_PENALTY を増加させ (1000 -> 1200)、分散配置のペナルティを強化。
 * - 先読みの重み付けは v202 の設定を維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 2.5; // v204: Adjusted from 2.4 (v203) - Even broader critical zone
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.2; // v182: Maintained

const HEIGHT_PENALTY_WEIGHT = 7_500_000.0; // v204: Adjusted from 7_000_000.0 (v203) - Even steeper penalty
const Y_POSITION_PENALTY_WEIGHT = 700; // v204: Adjusted from 500 (v203) - Increased general penalty based on predictedY
const SETTLING_BUFFER = 0.70; // v200: Maintained
// Changed from absolute avoidance (Infinity) to a very large penalty.
const DEADLINE_ABSOLUTE_AVOID_PENALTY = -1_000_000_000; // Very large penalty instead of Infinity
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER = 0.50; // v204: Adjusted from 0.52 (v203) - Slightly less strict for "No valid move"

// Merge and Pipeline Bonuses
const MERGE_PROXIMITY_THRESHOLD = 0.20; // v181: Maintained from 0.20
const MERGE_BONUS_SCALE_FACTOR = 100; // v204: Adjusted from 95 (v198)
const PIPELINE_BONUS_DIRECT_CHAIN = 2200; // v204: Adjusted from 2100 (v198)
const PIPELINE_BONUS_INDIRECT_CHAIN = 900; // v204: Adjusted from 850 (v198)
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 6000; // v204: Adjusted from 5000 (v202)
const GARBAGE_URGENT_GAUGE_BONUS_HIGH_RATIO = 25000; // v204: Adjusted from 20000 (v203) - More aggressive incentive
const GARBAGE_URGENT_GAUGE_THRESHOLD = 0.60; // v197: Maintained from 0.60 (v196)
const GARBAGE_IMMINENT_GAUGE_THRESHOLD = 0.80; // v199: Maintained
const GARBAGE_IMMINENT_MERGE_BONUS = 25000; // v204: Adjusted from 20000 (v203) - More aggressive incentive
const GARBAGE_ANY_MERGE_BONUS = 500; // v195: Maintained

// Small Piece Catalyst (v196: increased)
const SMALL_PIECE_CATALYST_BONUS = 2000; // v196: Maintained

// Crowding Penalty
const CROWDING_PENALTY_START_THRESHOLD = 2; // v204: Adjusted from 3 (v203) - Detect crowding earlier
const CROWDING_PENALTY_PER_PIECE = 200; // v204: Adjusted from 150 (v203)
const EMPTY_SPACE_BONUS = 1500; // v204: Adjusted from 1000 (v203) - Bonus for dropping into relatively empty horizontal space

// Large Piece Aggregation (v182: further increased)
const LARGE_PIECE_AGGREGATION_BONUS = 2500; // v204: Adjusted from 2300 (v182)
const LARGE_PIECE_AGGREGATION_PENALTY = 1200; // v204: Adjusted from 1000
const LARGE_PIECE_TYPE_THRESHOLD = 9;

// Look-ahead constants (v192: Adjusted)
const LOOK_AHEAD_WEIGHT = 0.40; // v202: Maintained
const LOOK_AHEAD_WEIGHT_SECOND_PIECE = 0.15; // v202: Maintained

/**
 * Helper to get piece radius based on type. (Approximation)
 * In a real game, this would be a lookup table.
 */
function getPieceRadius(type) {
    // This is a simplified approximation. Actual radii would be in game config.
    // Assuming radius increases with type.
    return 0.15 + (type - 1) * 0.08; // Example scaling
}

/**
 * Predicts the landing Y coordinate for a piece.
 * This is a highly simplified model. Real physics engines are complex.
 * It assumes the piece falls straight down and rests on the first thing it hits (floor or another piece).
 * boardState parameter can be a full boardState object or just an object with a 'pieces' array.
 */
function predictLandingY(boardState, dropX, piece) {
    let predictedY = BOARD_FLOOR_Y + piece.r; // Start assuming it lands on the floor

    // Iterate through existing pieces to find if it lands on one of them
    for (const existingPiece of boardState.pieces) {
        // Simple 1D collision check for X-axis overlap
        const combinedRadius = piece.r + existingPiece.r;
        const xDistance = Math.abs(dropX - existingPiece.x);

        if (xDistance < combinedRadius) { // There's X-axis overlap
            // Check if dropping piece would land on top of existing piece
            // This is a very rough approximation, ignoring complex shapes and rotations.
            // Also considers a small vertical buffer to prevent immediate re-collision logic in simulation.
            if (existingPiece.y + existingPiece.r > predictedY - 0.01) { // Adjusted comparison (center of existing piece + its radius > current predicted center - 0.01)
                predictedY = existingPiece.y + existingPiece.r + piece.r;
            }
        }
    }
    return predictedY + SETTLING_BUFFER; // Add a buffer for physical settling
}

/**
 * Calculates bonus for potential merges.
 * Finds pieces of the same type within proximity.
 */
function calculateMergeBonus(boardStatePieces, dropX, piece, predictedY) {
    let bonus = 0;
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardStatePieces) {
        if (existingPiece.type === piece.type) {
            const distance = Math.sqrt(
                Math.pow(currentPiecePos.x - existingPiece.x, 2) +
                Math.pow(currentPiecePos.y - existingPiece.y, 2)
            );
            if (distance < (piece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD)) {
                // Closer pieces get higher bonus, and higher types get higher bonus
                const distanceFactor = 1 - (distance / (piece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD));
                bonus += MERGE_BONUS_SCALE_FACTOR * piece.type * distanceFactor * distanceFactor;
            }
        }
    }
    return bonus;
}

/**
 * Calculates bonus for maintaining merge pipelines (chains).
 * Encourages placing N-1 near N, and N near N+1.
 */
function calculatePipelineBonus(boardStatePieces, dropX, piece, predictedY) {
    let bonus = 0;
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardStatePieces) {
        const distance = Math.sqrt(
            Math.pow(currentPiecePos.x - existingPiece.x, 2) +
            Math.pow(currentPiecePos.y - existingPiece.y, 2)
        );
        const combinedRadius = piece.r + existingPiece.r;

        if (distance < combinedRadius * 1.5) { // Within a reasonable chain distance
            // Direct chain: N-1 merging into N, or N merging into N+1
            if (existingPiece.type === piece.type + 1 || existingPiece.type === piece.type - 1) {
                bonus += PIPELINE_BONUS_DIRECT_CHAIN;
            }
            // Indirect pipeline: N-2 merging into N-1, and N-1 is near N, etc.
            // This is a very simple heuristic and could be much more complex.
            if (existingPiece.type === piece.type + 2 || existingPiece.type === piece.type - 2) {
                bonus += PIPELINE_BONUS_INDIRECT_CHAIN;
            }
        }
    }
    return bonus;
}

/**
 * Calculates penalty/bonus for large piece aggregation.
 * Tries to keep large pieces (type >= LARGE_PIECE_TYPE_THRESHOLD) on one side.
 * This function now takes the pre-calculated dominant side as an argument.
 */
function calculateLargePieceAggregationBonus(dropX, piece, currentDominantSide) {
    if (piece.type < LARGE_PIECE_TYPE_THRESHOLD) {
        return 0; // Only applies to large pieces
    }

    if (currentDominantSide === 0) { // No clear dominant side or balanced
        return 0; // Don't apply bonus/penalty yet
    }

    if (currentDominantSide === -1 && dropX < 0) { // Dominant left, dropping left
        return LARGE_PIECE_AGGREGATION_BONUS;
    } else if (currentDominantSide === 1 && dropX > 0) { // Dominant right, dropping right
        return LARGE_PIECE_AGGREGATION_BONUS;
    } else if (currentDominantSide === -1 && dropX > 0) { // Dominant left, dropping right
        return -LARGE_PIECE_AGGREGATION_PENALTY;
    } else if (currentDominantSide === 1 && dropX < 0) { // Dominant right, dropping left
        return -LARGE_PIECE_AGGREGATION_PENALTY;
    }
    return 0;
}


/**
 * Calculates penalty based on predicted height.
 * Penalizes more heavily as predictedY approaches the deadline.
 */
function calculateHeightPenalty(predictedY, pieceR) {
    const topOfPiece = predictedY + pieceR;
    let penalty = 0;

    // If piece is predicted to be at or above the absolute avoidance threshold, return the absolute penalty.
    if (topOfPiece >= (DEADLINE_Y - DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER)) {
        return DEADLINE_ABSOLUTE_AVOID_PENALTY;
    }

    const heightFromDeadline = DEADLINE_Y - topOfPiece;

    if (heightFromDeadline < CRITICAL_HEIGHT_MARGIN) {
        // Critical penalty: exponentially increasing as it gets closer
        // v199: Changed exponent from 2 to 3 for more aggressive penalty growth
        penalty += HEIGHT_PENALTY_WEIGHT * Math.pow((CRITICAL_HEIGHT_MARGIN - heightFromDeadline) / CRITICAL_HEIGHT_MARGIN, 3);
    }
    if (topOfPiece >= TOP_Y_EXTREME_WARN_THRESHOLD) {
        penalty += HEIGHT_PENALTY_WEIGHT / 2; // Additional penalty for extreme warning
    } else if (topOfPiece >= DEADLINE_Y - 2.0) { // Using 2.0 as a general warning threshold
        penalty += HEIGHT_PENALTY_WEIGHT / 4; // Additional penalty for general warning
    }

    // NEW: General penalty based on overall Y position to encourage keeping the board low
    if (predictedY > BOARD_FLOOR_Y) { // Only penalize if above the floor
        penalty += Y_POSITION_PENALTY_WEIGHT * (predictedY - BOARD_FLOOR_Y);
    }


    return penalty;
}

/**
 * Calculates penalty for dropping into an overly crowded area without merge potential.
 * (Simplified: counts pieces in a cylinder below the drop point)
 * Also adds a bonus for dropping into an empty space.
 */
function calculateCrowdingPenalty(boardStatePieces, dropX, piece, predictedY) {
    let crowdedCount = 0;
    let emptySpaceBonus = 0;
    const horizontalCheckRadius = piece.r * 2; // Check horizontal proximity for crowding

    for (const existingPiece of boardStatePieces) {
        const xDistance = Math.abs(dropX - existingPiece.x);
        // Consider pieces directly below or very close horizontally
        if (xDistance < horizontalCheckRadius && existingPiece.y < predictedY) {
            crowdedCount++;
        }
    }

    if (crowdedCount > CROWDING_PENALTY_START_THRESHOLD) {
        return (crowdedCount - CROWDING_PENALTY_START_THRESHOLD) * CROWDING_PENALTY_PER_PIECE;
    } else if (crowdedCount <= 1 && predictedY < DEADLINE_Y - 1.0) { // v203: Bonus for empty space, especially lower down
        emptySpaceBonus = EMPTY_SPACE_BONUS;
    }

    return -emptySpaceBonus; // Return negative penalty for a bonus
}

/**
 * Calculates bonus for using small pieces as catalysts to agitate the board.
 * Assumes small pieces (type 1-4) can be used to shake things up if dropped in a dense area.
 */
function calculateSmallPieceCatalystBonus(boardStatePieces, dropX, piece, predictedY) {
    if (piece.type > 4) { // Only small pieces
        return 0;
    }

    let denseAreaPieces = 0;
    const searchRadius = piece.r * 3; // Check for density around the drop point
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardStatePieces) {
        const distance = Math.sqrt(
            Math.pow(currentPiecePos.x - existingPiece.x, 2) +
            Math.pow(currentPiecePos.y - existingPiece.y, 2)
        );
        if (distance < searchRadius) {
            denseAreaPieces++;
        }
    }

    // If it's a small piece and it's dropping into a reasonably dense area, give bonus
    if (denseAreaPieces > 5) { // Arbitrary density threshold
        return SMALL_PIECE_CATALYST_BONUS;
    }
    return 0;
}

/**
 * Adjusts strategy based on garbage block information.
 * Prioritizes merges, especially near the bottom, when garbage is present or imminent.
 * Note: This needs the full boardState to access garbage info.
 */
function calculateGarbageAwarenessBonus(boardState, dropX, piece, predictedY) {
    let bonus = 0;
    const potentialMergeBonus = calculateMergeBonus(boardState.pieces, dropX, piece, predictedY);

    // If garbage is present at all, give a base bonus for any merge.
    if (boardState.garbage.ratio > 0 && potentialMergeBonus > 0) {
        bonus += GARBAGE_ANY_MERGE_BONUS;
    }

    // Aggressively prioritize merges if garbage is high or ratio is high
    if (boardState.garbage.ratio > 0.15) { // OJAMA_MERGE mode
        bonus += piece.type * 75;
        if (boardState.garbage.ratio > 0.4) { // GBG_URGENT mode
            bonus += piece.type * 150;
        }
    }

    if (boardState.garbage.gauge >= 0.3) { // Prepare for incoming ojama
        bonus += piece.type * 25;
    }
    if (boardState.garbage.gauge >= GARBAGE_URGENT_GAUGE_THRESHOLD) { // Ojama imminent
        bonus += piece.type * 75;
    }

    // NEW: Extra aggressive bonus if gauge is critically high AND ratio is significant
    if (boardState.garbage.gauge >= GARBAGE_URGENT_GAUGE_THRESHOLD && boardState.garbage.ratio > 0.1) {
        if (potentialMergeBonus > 0) { // Only apply if there's a merge opportunity
            bonus += GARBAGE_URGENT_GAUGE_BONUS_HIGH_RATIO;
        }
    }

    // v199: New - Bonus for merging when garbage gauge is very high, regardless of current ratio
    if (boardState.garbage.gauge >= GARBAGE_IMMINENT_GAUGE_THRESHOLD) {
        if (potentialMergeBonus > 0) {
            bonus += GARBAGE_IMMINENT_MERGE_BONUS;
        }
    }

    // Bonus for merging near the bottom when significant garbage is present (clears more effectively)
    // Adjusted condition: predictedY < 0 (center below mid-board) and garbage.ratio > 0.1
    if (boardState.garbage.ratio > 0.1 && predictedY < 0) {
         if (potentialMergeBonus > 0) {
            bonus += GARBAGE_CLEAR_MERGE_BONUS_LOW_Y;
         }
    }

    return bonus;
}

/**
 * Calculates the score for a potential move, excluding look-ahead.
 * @param {object} piece The piece to drop.
 * @param {number} dropX The X coordinate for the drop.
 * @param {number} predictedY The predicted landing Y coordinate.
 * @param {object} currentBoardState The current board state or a hypothetical one (must have 'pieces' array and 'garbage' object).
 * @param {number} dominantLargePieceSide Pre-calculated dominant side for large pieces.
 * @returns {number} The calculated score.
 */
function calculateMoveScore(piece, dropX, predictedY, currentBoardState, dominantLargePieceSide) {
    let score = 0;
    score += calculateMergeBonus(currentBoardState.pieces, dropX, piece, predictedY);
    score += calculatePipelineBonus(currentBoardState.pieces, dropX, piece, predictedY);
    score += calculateLargePieceAggregationBonus(dropX, piece, dominantLargePieceSide);
    score += calculateCrowdingPenalty(currentBoardState.pieces, dropX, piece, predictedY); // Note: this can return negative for bonus
    score += calculateSmallPieceCatalystBonus(currentBoardState.pieces, dropX, piece, predictedY);
    score += calculateGarbageAwarenessBonus(currentBoardState, dropX, piece, predictedY); // Garbage awareness needs full boardState for 'garbage' field
    return score;
}

/**
 * Decides the next move based on the current board state.
 */
export function decide(boardState) {
    let bestX = 0.0;
    let bestScore = -Infinity;
    let reason = "No optimal move found, defaulting to center.";
    let useHold = false;

    // Determine the dominant side for large pieces once per turn
    let dominantLargePieceSide = 0; // 0: no clear dominant side, -1: left, 1: right
    let leftLargePiecesCount = 0;
    let rightLargePiecesCount = 0;
    for (const existingPiece of boardState.pieces) {
        if (existingPiece.type >= LARGE_PIECE_TYPE_THRESHOLD) {
            if (existingPiece.x < 0) leftLargePiecesCount++;
            else if (existingPiece.x > 0) rightLargePiecesCount++;
        }
    }
    // v195: Re-introduced hysteresis for dominant side calculation
    if (leftLargePiecesCount >= rightLargePiecesCount + 1) { // Left side has more with a buffer
        dominantLargePieceSide = -1;
    } else if (rightLargePiecesCount >= leftLargePiecesCount + 1) { // Right side has more with a buffer
        dominantLargePieceSide = 1;
    }


    // --- HOLD Logic ---
    let candidatePieces = [{ piece: boardState.next, isHeld: false }];

    if (boardState.canHold && boardState.hold) {
        // Evaluate dropping the held piece as an alternative
        candidatePieces.push({ piece: boardState.hold, isHeld: true });
    }


    for (const { piece: pieceToDrop, isHeld } of candidatePieces) {
        const pieceR = pieceToDrop.r ?? getPieceRadius(pieceToDrop.type);

        for (const x of FINE_COLS) {
            // Check if piece would be outside bounds
            if (x - pieceR < -BOARD_X_MAX_LIMIT || x + pieceR > BOARD_X_MAX_LIMIT) {
                continue; // Skip if piece would be outside walls
            }

            const predictedY = predictLandingY(boardState, x, pieceToDrop);
            let currentScore = 0;

            const heightPenalty = calculateHeightPenalty(predictedY, pieceR);
            // If this move leads to an immediate game over, skip it.
            if (heightPenalty === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                continue;
            }
            currentScore -= heightPenalty;

            // Calculate base score for the current move
            currentScore += calculateMoveScore(pieceToDrop, x, predictedY, boardState, dominantLargePieceSide);

            // --- Look-ahead for nextPieces[1] (second piece) ---
            let hypotheticalNextBoardState = null;
            let maxHypotheticalNextScore = 0;
            if ((boardState.nextPieces?.length ?? 0) > 1) {
                const nextPiece = boardState.nextPieces[1];
                const nextPieceR = nextPiece.r ?? getPieceRadius(nextPiece.type);

                // Create a hypothetical board state after the current piece is dropped
                // For now, assume garbage state remains the same for the immediate next turn.
                hypotheticalNextBoardState = {
                    pieces: [...boardState.pieces,
                        {type: pieceToDrop.type, x: x, y: predictedY, r: pieceToDrop.r}],
                    garbage: boardState.garbage
                };


                for (const hypoNextX of FINE_COLS) {
                    // Check if next piece would be outside bounds in hypothetical state
                    if (hypoNextX - nextPieceR < -BOARD_X_MAX_LIMIT || hypoNextX + nextPieceR > BOARD_X_MAX_LIMIT) {
                        continue;
                    }
                    const hypoNextPredictedY = predictLandingY(hypotheticalNextBoardState, hypoNextX, nextPiece);

                    const hypoNextHeightPenalty = calculateHeightPenalty(hypoNextPredictedY, nextPieceR);
                    if (hypoNextHeightPenalty === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                        continue;
                    }

                    // Calculate full score for the hypothetical next piece
                    let hypotheticalNextMoveScore = calculateMoveScore(nextPiece, hypoNextX, hypoNextPredictedY, hypotheticalNextBoardState, dominantLargePieceSide);
                    hypotheticalNextMoveScore -= hypoNextHeightPenalty;

                    // --- NEW: Look-ahead for nextPieces[2] (third piece) ---
                    if ((boardState.nextPieces?.length ?? 0) > 2) {
                        const thirdPiece = boardState.nextPieces[2];
                        const thirdPieceR = thirdPiece.r ?? getPieceRadius(thirdPiece.type);

                        // Create a hypothetical board state after the second piece is dropped
                        const hypotheticalThirdBoardState = {
                            pieces: [...hypotheticalNextBoardState.pieces,
                                {type: nextPiece.type, x: hypoNextX, y: hypoNextPredictedY, r: nextPiece.r}],
                            garbage: boardState.garbage
                        };

                        let maxHypotheticalThirdScore = 0;
                        for (const hypoThirdX of FINE_COLS) {
                             if (hypoThirdX - thirdPieceR < -BOARD_X_MAX_LIMIT || hypoThirdX + thirdPieceR > BOARD_X_MAX_LIMIT) {
                                continue;
                            }
                            const hypoThirdPredictedY = predictLandingY(hypotheticalThirdBoardState, hypoThirdX, thirdPiece);

                            const hypoThirdHeightPenalty = calculateHeightPenalty(hypoThirdPredictedY, thirdPieceR);
                            if (hypoThirdHeightPenalty === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                                continue;
                            }
                            let hypotheticalThirdMoveScore = calculateMoveScore(thirdPiece, hypoThirdX, hypoThirdPredictedY, hypotheticalThirdBoardState, dominantLargePieceSide);
                            hypotheticalThirdMoveScore -= hypoThirdHeightPenalty;

                            maxHypotheticalThirdScore = Math.max(maxHypotheticalThirdScore, hypotheticalThirdMoveScore);
                        }
                        hypotheticalNextMoveScore += maxHypotheticalThirdScore * LOOK_AHEAD_WEIGHT_SECOND_PIECE;
                    }
                    // --- End NEW Look-ahead for nextPieces[2] ---

                    maxHypotheticalNextScore = Math.max(maxHypotheticalNextScore, hypotheticalNextMoveScore);
                }
                currentScore += maxHypotheticalNextScore * LOOK_AHEAD_WEIGHT;
            }
            // --- End Look-ahead for nextPieces[1] ---


            // Add a small constant to prefer drops, avoiding zero scores for valid moves
            currentScore += 100;

            if (currentScore > bestScore) {
                bestScore = currentScore;
                bestX = x;
                useHold = isHeld;
                reason = `Calculated strategy: Type ${pieceToDrop.type} at X=${x.toFixed(2)}, Score=${currentScore.toFixed(0)}`;
                if (isHeld) reason += " (Used HOLD)";
            }
        }
    }

    // Fallback if no good move found (should only happen if ALL moves are predicted to be game over)
    if (bestScore === -Infinity) {
        reason = "No valid move found (all moves lead to game over), defaulting to center.";
        bestX = 0.0;
        useHold = false;
    }

    return { x: bestX, reason: reason, hold: useHold };
}
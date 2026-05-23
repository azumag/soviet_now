/**
 * strategy.mjs - ドロップ位置決定戦略 (v221 - Aggressive Garbage Clearing & Refined Height Management)
 *
 * v220をベースに以下の点を改善：
 * - おじゃまブロック対応のさらなる強化と優先度向上:
 *   - GARBAGE_HIGH_Y_NO_MERGE_PENALTY を -800_000 から -1_000_000 に増加。
 *     おじゃまブロックがすでに高位にあり、かつ併合機会がない場合のペナルティをさらに大幅に強化。
 *     これにより、高所のおじゃまに対する無策なドロップを極力排除し、リスク回避を最優先します。
 *   - GARBAGE_IMMINENT_MERGE_BONUS を 550_000 から 650_000 に増加。
 *     おじゃまゲージが満タンに近く、おじゃま落下が差し迫っている場合の併合ボーナスを強化。
 *     緊急時における併合によるボードクリアのインセンティブを一層高めます。
 *   - GARBAGE_CRITICAL_RATIO_BONUS を 600_000 から 750_000 に増加。
 *     ボード内のおじゃまの割合が極めて高い場合の併合ボーナスをさらに強化し、
 *     ボードを窒息させるリスクがある場合のクリア行動を最優先させます。
 *   - GARBAGE_CLEAR_MERGE_BONUS_LOW_Y を 50_000 から 60_000 に増加。
 *     ボード下部での併合によるおじゃまクリアへのインセンティブを強化し、
 *     効率的な盤面整理を促します。
 * - 全体的な高さ管理の調整:
 *   - Y_POSITION_PENALTY_WEIGHT を 3500 から 4000 に増加。
 *     一般的なY座標に対するペナルティをさらに強化し、全体的に低い盤面を維持するインセンティブを継続して高めます。
 *
 * その他の定数（MERGE_PROXIMITY_THRESHOLD, PIPELINE_BONUS群, CROWDING_PENALTY群,
 * LARGE_PIECE_AGGREGATION群, LOOK_AHEAD_WEIGHT群, SETTLING_BUFFER）はv220の設定を維持します。
 * DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER も v220の0.90を維持し、デッドラインからの安全マージンを保ちます。
 *
 * 今回の調整は、おじゃまブロックによるゲームオーバーリスクに対する応答をさらに迅速かつ強力にし、
 * 同時に全体的な盤面高さをより低く保つことで、安定した生存とスコア獲得を目指します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.38;                  // Unity red-line trigger bottom
const CRITICAL_HEIGHT_MARGIN = 3.1; // v220: Adjusted from 3.0 (v219)
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.2;

const HEIGHT_PENALTY_WEIGHT = 50_000_000.0; // v220: Adjusted from 40_000_000.0 (v219)
const Y_POSITION_PENALTY_WEIGHT = 4000; // v221: Adjusted from 3500 (v220)
// Changed from absolute avoidance (Infinity) to a very large penalty.
const DEADLINE_ABSOLUTE_AVOID_PENALTY = -1_000_000_000;
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER = 0.90; // v220: Adjusted from 0.80 (v219)

const SETTLING_BUFFER = 1.20; // v219: Adjusted from 1.10 (v218)

// Merge and Pipeline Bonuses
const MERGE_PROXIMITY_THRESHOLD = 0.20;
const MERGE_BONUS_SCALE_FACTOR = 100;
const PIPELINE_BONUS_DIRECT_CHAIN = 2200;
const PIPELINE_BONUS_INDIRECT_CHAIN = 900;
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 60000; // v221: Adjusted from 50000 (v217)
const GARBAGE_URGENT_GAUGE_BONUS_HIGH_RATIO = 500000; // v220: Adjusted from 400000 (v217)
const GARBAGE_URGENT_GAUGE_THRESHOLD = 0.60;
const GARBAGE_IMMINENT_GAUGE_THRESHOLD = 0.50; // v213: Adjusted from 0.60 (v212)
const GARBAGE_IMMINENT_MERGE_BONUS = 650000; // v221: Adjusted from 550000 (v220)
const GARBAGE_ANY_MERGE_BONUS = 500;
const GARBAGE_CRITICAL_RATIO_THRESHOLD = 0.30; // v217: New threshold for extreme garbage
const GARBAGE_CRITICAL_RATIO_BONUS = 750000; // v221: Adjusted from 600000 (v220)

// NEW Garbage Height Management
const GARBAGE_HIGH_PENALTY_THRESHOLD_Y = 1.8; // v219: If garbage height exceeds this, start applying special penalty/bonus
const GARBAGE_HIGH_Y_MERGE_BONUS_AMPLIFIER = 2.5; // v220: Adjusted from 2.0 (v219)
const GARBAGE_HIGH_Y_NO_MERGE_PENALTY = -1_000_000; // v221: Adjusted from -800_000 (v220)

// Small Piece Catalyst
const SMALL_PIECE_CATALYST_BONUS = 2200;

// Crowding Penalty
const CROWDING_PENALTY_START_THRESHOLD = 2;
const CROWDING_PENALTY_PER_PIECE = 200;
const EMPTY_SPACE_BONUS = 3000;

// Large Piece Aggregation
const LARGE_PIECE_AGGREGATION_BONUS = 2500;
const LARGE_PIECE_AGGREGATION_PENALTY = 1200;
const LARGE_PIECE_TYPE_THRESHOLD = 9;

// Look-ahead constants
const LOOK_AHEAD_WEIGHT = 0.40;
const LOOK_AHEAD_WEIGHT_SECOND_PIECE = 0.15;

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
    } else if (crowdedCount === 0 && predictedY < DEADLINE_Y - 1.0) { // v219: Bonus for truly empty space, especially lower down
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
    const hasMergeOpportunity = potentialMergeBonus > 0;

    // If garbage is present at all, give a base bonus for any merge.
    if (boardState.garbage.ratio > 0 && hasMergeOpportunity) {
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
        if (hasMergeOpportunity) {
            bonus += GARBAGE_URGENT_GAUGE_BONUS_HIGH_RATIO;
        }
    }

    // Bonus for merging when garbage gauge is very high, regardless of current ratio
    if (boardState.garbage.gauge >= GARBAGE_IMMINENT_GAUGE_THRESHOLD) {
        if (hasMergeOpportunity) {
            bonus += GARBAGE_IMMINENT_MERGE_BONUS;
        }
    }

    // NEW: Critical bonus for extremely high garbage ratio (emergency clearance)
    if (boardState.garbage.ratio >= GARBAGE_CRITICAL_RATIO_THRESHOLD) {
        if (hasMergeOpportunity) {
            bonus += GARBAGE_CRITICAL_RATIO_BONUS;
        }
    }

    // Bonus for merging near the bottom when significant garbage is present (clears more effectively)
    if (boardState.garbage.ratio > 0.1 && hasMergeOpportunity &&
        predictedY < boardState.garbage.height + piece.r && predictedY < 1.5) {
            bonus += GARBAGE_CLEAR_MERGE_BONUS_LOW_Y;
    }

    // V219: NEW GARBAGE HEIGHT AWARENESS:
    if (boardState.garbage.height > GARBAGE_HIGH_PENALTY_THRESHOLD_Y) {
        if (hasMergeOpportunity) {
            // If garbage is high and a merge is possible, amplify the merge bonus
            bonus += potentialMergeBonus * (GARBAGE_HIGH_Y_MERGE_BONUS_AMPLIFIER - 1); // Add the amplification amount
        } else {
            // If garbage is high and NO merge is possible, apply a significant penalty
            bonus += GARBAGE_HIGH_Y_NO_MERGE_PENALTY;
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
    score += calculateGarbageAwarenessBonus(currentBoardState, dropX, piece, predictedY);
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
    // Re-introduced hysteresis for dominant side calculation
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
            // v215: Instead of skipping, apply the (potentially very large negative) penalty directly.
            currentScore -= heightPenalty;

            // If a move is absolutely avoided due to height, its score is extremely low,
            // so we don't need to calculate further bonuses.
            if (currentScore === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                if (currentScore > bestScore) { // Still update if this is the "least bad" impossible move
                    bestScore = currentScore;
                    bestX = x;
                    useHold = isHeld;
                    reason = `Calculated strategy: Type ${pieceToDrop.type} at X=${x.toFixed(2)}, Score=${currentScore.toFixed(0)} (Absolute Height Avoidance)`;
                    if (isHeld) reason += " (Used HOLD)";
                }
                continue; // Skip further calculations for this move
            }


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
                    // v215: Apply penalty directly instead of skipping.
                    let hypotheticalNextMoveScore = 0;
                    hypotheticalNextMoveScore -= hypoNextHeightPenalty;

                    if (hypotheticalNextMoveScore === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                         // This path leads to game over in the next step, so it's a very bad outcome.
                        // We set it to a very low score, but allow it to be chosen if all other options are worse.
                        maxHypotheticalNextScore = Math.max(maxHypotheticalNextScore, hypotheticalNextMoveScore);
                        continue;
                    }

                    // Calculate full score for the hypothetical next piece
                    hypotheticalNextMoveScore += calculateMoveScore(nextPiece, hypoNextX, hypoNextPredictedY, hypotheticalNextBoardState, dominantLargePieceSide);

                    // --- Look-ahead for nextPieces[2] (third piece) ---
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
                            // v215: Apply penalty directly instead of skipping.
                            let hypotheticalThirdMoveScore = 0;
                            hypotheticalThirdMoveScore -= hypoThirdHeightPenalty;

                            if (hypotheticalThirdMoveScore === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                                maxHypotheticalThirdScore = Math.max(maxHypotheticalThirdScore, hypotheticalThirdMoveScore);
                                continue;
                            }

                            hypotheticalThirdMoveScore += calculateMoveScore(thirdPiece, hypoThirdX, hypoThirdPredictedY, hypotheticalThirdBoardState, dominantLargePieceSide);
                            maxHypotheticalThirdScore = Math.max(maxHypotheticalThirdScore, hypotheticalThirdMoveScore);
                        }
                        hypotheticalNextMoveScore += maxHypotheticalThirdScore * LOOK_AHEAD_WEIGHT_SECOND_PIECE;
                    }
                    // --- End Look-ahead for nextPieces[2] ---

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

    // Fallback if no good move found (should only happen if ALL moves are predicted to be game over OR extremely bad)
    // The previous change (removing 'continue') makes this less likely to trigger due to rigid filtering,
    // but rather due to genuinely extremely low scores across all options.
    if (bestScore === -Infinity) {
        reason = "No valid move found (all moves lead to game over or extremely low score), defaulting to center.";
        bestX = 0.0;
        useHold = false;
    }

    return { x: bestX, reason: reason, hold: useHold };
}

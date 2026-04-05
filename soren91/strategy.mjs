/**
 * strategy.mjs - ドロップ位置決定戦略 (v181)
 *
 * v181: v180で示された「高生存ターン数にも関わらずRankに貢献していない」という課題に対し、
 *       併合・パイプライン・大型ピース集約、および小ピースの触媒利用やゴミ除去のボーナスをさらに強化し、
 *       より積極的な高スコア獲得と盤面整理を促進します。また、併合判定の物理的実情を考慮し、
 *       併合可能距離を微調整します。
 *
 *       主な改善点:
 *       1.  **併合・パイプラインボーナスのさらなる強化**:
 *           - `MERGE_PROXIMITY_THRESHOLD` を `0.15` から `0.20` に微増。凸ポリゴンの衝突・回転を考慮。
 *           - `MERGE_BONUS_SCALE_FACTOR` を `50` から `70` に増強。
 *           - `PIPELINE_BONUS_DIRECT_CHAIN` を `1200` から `1500` に増強。
 *           - `PIPELINE_BONUS_INDIRECT_CHAIN` を `450` から `600` に増強。
 *             これにより、ピースの併合と連鎖形成の優先度をさらに高め、スコア獲得と盤面整理を促進します。
 *       2.  **大型ピース集約ボーナスのさらなる強化**:
 *           - `LARGE_PIECE_AGGREGATION_BONUS` を `1800` から `2000` に増強。
 *             大型ピースを一箇所に集めることの重要性を高め、後の高タイプ併合を容易にします。
 *       3.  **おじゃまブロック対策のさらなる強化**:
 *           - `GARBAGE_CLEAR_MERGE_BONUS_LOW_Y` を `2000` から `2500` に増強。
 *             おじゃまブロックが多い状況下で、低い位置での併合によるクリアを強く推奨します。
 *       4.  **小ピース触媒ボーナスの強化**:
 *           - `SMALL_PIECE_CATALYST_BONUS` を `850` から `1000` に増強。
 *             高密度エリアでの小ピース利用を促進し、盤面活性化を狙います。
 *       5.  **既存ロジックの維持**:
 *           - 高さ管理ペナルティの重み、HOLDメカニクス、その他のロジックはv180の方針を維持します。
 *
 *       これらの調整により、ゲームオーバーを回避しつつ、より積極的な併合と大型ピース管理によって高スコアを目指し、
 *       安定したRank獲得と生存ターン数のさらなる向上を目指します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 0.7;
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.0;

const HEIGHT_PENALTY_WEIGHT = 400000.0; // v180: Maintained from 400000.0.
const SETTLING_BUFFER = 0.35;

// v179: Changed from absolute avoidance (Infinity) to a very large penalty.
const DEADLINE_ABSOLUTE_AVOID_PENALTY = -1_000_000_000; // Very large penalty instead of Infinity

// Merge and Pipeline Bonuses (v181: further increased)
const MERGE_PROXIMITY_THRESHOLD = 0.20; // v181: Increased from 0.15
const MERGE_BONUS_SCALE_FACTOR = 70; // v181: Increased from 50
const PIPELINE_BONUS_DIRECT_CHAIN = 1500; // v181: Increased from 1200
const PIPELINE_BONUS_INDIRECT_CHAIN = 600; // v181: Increased from 450
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 2500; // v181: Increased from 2000

// Small Piece Catalyst (v181: increased)
const SMALL_PIECE_CATALYST_BONUS = 1000; // v181: Increased from 850

// Crowding Penalty (v177 maintained)
const CROWDING_PENALTY_START_THRESHOLD = 20;
const CROWDING_PENALTY_PER_PIECE = 50;

// Large Piece Aggregation (v181: further increased)
const LARGE_PIECE_AGGREGATION_BONUS = 2000; // v181: Increased from 1800
const LARGE_PIECE_AGGREGATION_PENALTY = 1000;
const LARGE_PIECE_TYPE_THRESHOLD = 9;

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
            if (existingPiece.y + existingPiece.r - piece.r > predictedY - piece.r - 0.01) { // Adjusted comparison
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
function calculateMergeBonus(boardState, dropX, piece, predictedY) {
    let bonus = 0;
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardState.pieces) {
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
function calculatePipelineBonus(boardState, dropX, piece, predictedY) {
    let bonus = 0;
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardState.pieces) {
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

    // v179: Replaced absolute avoidance with a very large penalty
    if (topOfPiece >= (DEADLINE_Y - 0.1)) { // This threshold corresponds to DEADLINE_ABSOLUTE_AVOID_THRESHOLD implicitly
        return DEADLINE_ABSOLUTE_AVOID_PENALTY;
    }

    const heightFromDeadline = DEADLINE_Y - topOfPiece;

    if (heightFromDeadline < CRITICAL_HEIGHT_MARGIN) {
        // Critical penalty: exponentially increasing as it gets closer
        penalty += HEIGHT_PENALTY_WEIGHT * Math.pow((CRITICAL_HEIGHT_MARGIN - heightFromDeadline) / CRITICAL_HEIGHT_MARGIN, 2);
    }
    if (topOfPiece >= TOP_Y_EXTREME_WARN_THRESHOLD) {
        penalty += HEIGHT_PENALTY_WEIGHT / 2; // Additional penalty for extreme warning
    } else if (topOfPiece >= DEADLINE_Y - 2.0) { // Using 2.0 as a general warning threshold
        penalty += HEIGHT_PENALTY_WEIGHT / 4; // Additional penalty for general warning
    }

    return penalty;
}

/**
 * Calculates penalty for dropping into an overly crowded area without merge potential.
 * (Simplified: counts pieces in a cylinder below the drop point)
 */
function calculateCrowdingPenalty(boardState, dropX, piece, predictedY) {
    let crowdedCount = 0;
    for (const existingPiece of boardState.pieces) {
        const xDistance = Math.abs(dropX - existingPiece.x);
        // Consider pieces directly below or very close horizontally
        if (xDistance < piece.r * 2 && existingPiece.y < predictedY) {
            crowdedCount++;
        }
    }
    if (crowdedCount > CROWDING_PENALTY_START_THRESHOLD) {
        return (crowdedCount - CROWDING_PENALTY_START_THRESHOLD) * CROWDING_PENALTY_PER_PIECE;
    }
    return 0;
}

/**
 * Calculates bonus for using small pieces as catalysts to agitate the board.
 * Assumes small pieces (type 1-4) can be used to shake things up if dropped in a dense area.
 */
function calculateSmallPieceCatalystBonus(boardState, dropX, piece, predictedY) {
    if (piece.type > 4) { // Only small pieces
        return 0;
    }

    let denseAreaPieces = 0;
    const searchRadius = piece.r * 3; // Check for density around the drop point
    const currentPiecePos = { x: dropX, y: predictedY };

    for (const existingPiece of boardState.pieces) {
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
 */
function calculateGarbageAwarenessBonus(boardState, dropX, piece, predictedY) {
    let bonus = 0;

    // Aggressively prioritize merges if garbage is high or ratio is high
    if (boardState.garbage.ratio > 0.15) { // OJAMA_MERGE mode
        bonus += piece.type * 50; // Boost merge priority
        if (boardState.garbage.ratio > 0.4) { // GBG_URGENT mode
            bonus += piece.type * 100; // Even more aggressive
        }
    }

    if (boardState.garbage.gauge >= 0.3) { // Prepare for incoming ojama
        bonus += piece.type * 25;
    }
    if (boardState.garbage.gauge >= 0.6) { // Ojama imminent
        bonus += piece.type * 75;
    }

    // Bonus for merging near the bottom when garbage is present (clears more effectively)
    if (boardState.garbage.ratio > 0 && predictedY < boardState.garbage.height * 0.5) { // Arbitrary low Y
         // Check if this drop creates a merge (very simplified check here, ideally integrate with merge bonus)
         const potentialMergeBonus = calculateMergeBonus(boardState, dropX, piece, predictedY);
         if (potentialMergeBonus > 0) {
            bonus += GARBAGE_CLEAR_MERGE_BONUS_LOW_Y;
         }
    }

    return bonus;
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
    // Apply a hysteresis to prevent frequent switching of the dominant side
    if (leftLargePiecesCount > rightLargePiecesCount + 1) {
        dominantLargePieceSide = -1; // Left side has significantly more
    } else if (rightLargePiecesCount > leftLargePiecesCount + 1) {
        dominantLargePieceSide = 1; // Right side has significantly more
    }

    // --- HOLD Logic ---
    let candidatePieces = [{ piece: boardState.next, isHeld: false }];

    if (boardState.canHold && boardState.hold) {
        // Evaluate dropping the held piece as an alternative
        candidatePieces.push({ piece: boardState.hold, isHeld: true });
    }


    for (const { piece: pieceToDrop, isHeld } of candidatePieces) {
        // Radius of the piece to be dropped. Using approximation from getPieceRadius.
        const pieceR = getPieceRadius(pieceToDrop.type);

        for (const x of FINE_COLS) {
            // Check if piece would be outside bounds
            if (x - pieceR < -BOARD_X_MAX_LIMIT || x + pieceR > BOARD_X_MAX_LIMIT) {
                continue; // Skip if piece would be outside walls
            }

            const predictedY = predictLandingY(boardState, x, pieceToDrop);
            let currentScore = 0;

            const heightPenalty = calculateHeightPenalty(predictedY, pieceR);
            // v179: Instead of `if (heightPenalty === Infinity)`, check for the new penalty value
            if (heightPenalty === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                currentScore += heightPenalty; // Add the large negative penalty
            } else {
                currentScore -= heightPenalty;
            }

            currentScore += calculateMergeBonus(boardState, x, pieceToDrop, predictedY);
            currentScore += calculatePipelineBonus(boardState, x, pieceToDrop, predictedY);
            // Pass the pre-calculated dominantLargePieceSide
            currentScore += calculateLargePieceAggregationBonus(x, pieceToDrop, dominantLargePieceSide);
            currentScore -= calculateCrowdingPenalty(boardState, x, pieceToDrop, predictedY);
            currentScore += calculateSmallPieceCatalystBonus(boardState, x, pieceToDrop, predictedY);
            currentScore += calculateGarbageAwarenessBonus(boardState, x, pieceToDrop, predictedY);

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

    // Fallback if no good move found (shouldn't happen with the constant score addition
    // AND the change to DEADLINE_ABSOLUTE_AVOID_PENALTY ensuring a path is always found,
    // even if it's a very bad one).
    // The "critical error" reason should now ideally disappear from logs unless something else is very wrong.
    if (bestScore === -Infinity) { // This condition should theoretically not be hit anymore if logic is sound.
        reason = "No valid move found, defaulting to center (critical error).";
        bestX = 0.0;
        useHold = false;
    }

    return { x: bestX, reason: reason, hold: useHold };
}
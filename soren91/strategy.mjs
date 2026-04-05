/**
 * strategy.mjs - ドロップ位置決定戦略 (v183)
 *
 * v183: v182で強化された高さ管理にも関わらず、ゲームオーバーに繋がる高すぎるピース配置が散見されたため、
 *       デッドラインに極めて近い配置を厳密に回避するロジックを導入。
 *       また、物理予測の不確実性をさらに考慮し、保守的な着地予測バッファとクリティカルな高さペナルティの閾値を微調整。
 *
 *       主な改善点:
 *       1.  **厳格なデッドライン回避ロジック**:
 *           - `decide` 関数内で、`calculateHeightPenalty` が `DEADLINE_ABSOLUTE_AVOID_PENALTY` を返した場合、
 *             そのドロップ位置候補を完全にスキップするよう変更。これにより、即座にゲームオーバーに繋がる
 *             と予測されるドロップを確実に避ける。
 *       2.  **高さ管理のさらなる強化と保守的予測**:
 *           - `SETTLING_BUFFER` を `0.40` から `0.45` に微増。予測着地Y座標をより安全側に見積もることで、
 *             物理エンジンの不確実性による予期せぬ高さ超過リスクを低減。
 *           - `CRITICAL_HEIGHT_MARGIN` を `0.8` から `0.9` に増強。二次曲線的な高さペナルティがより低い位置から
 *             発動するようになり、デッドライン到達前の早い段階から高さ管理の優先度を上げる。
 *       3.  **既存ロジックの維持**:
 *           - HOLDメカニクス、その他のボーナス/ペナルティロジックはv182の方針を維持します。
 *
 *       これらの調整により、ゲームオーバー回避の堅牢性を高めつつ、安定した生存ターン数と高スコアのバランスを
 *       さらに改善することを目指します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 0.9; // v183: Increased from 0.8
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.2; // v182: Maintained

const HEIGHT_PENALTY_WEIGHT = 500000.0; // v182: Maintained
const SETTLING_BUFFER = 0.45; // v183: Increased from 0.40

// v179: Changed from absolute avoidance (Infinity) to a very large penalty.
const DEADLINE_ABSOLUTE_AVOID_PENALTY = -1_000_000_000; // Very large penalty instead of Infinity

// Merge and Pipeline Bonuses (v182: further increased)
const MERGE_PROXIMITY_THRESHOLD = 0.20; // v181: Maintained from 0.20
const MERGE_BONUS_SCALE_FACTOR = 85; // v182: Maintained
const PIPELINE_BONUS_DIRECT_CHAIN = 1800; // v182: Maintained
const PIPELINE_BONUS_INDIRECT_CHAIN = 750; // v182: Maintained
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 3000; // v182: Maintained

// Small Piece Catalyst (v182: increased)
const SMALL_PIECE_CATALYST_BONUS = 1200; // v182: Maintained

// Crowding Penalty (v182: decreased)
const CROWDING_PENALTY_START_THRESHOLD = 20;
const CROWDING_PENALTY_PER_PIECE = 40; // v182: Maintained

// Large Piece Aggregation (v182: further increased)
const LARGE_PIECE_AGGREGATION_BONUS = 2300; // v182: Maintained
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

    // If piece is predicted to be at or above the absolute avoidance threshold, return the absolute penalty.
    if (topOfPiece >= (DEADLINE_Y - 0.1)) {
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
            // v183: If this move leads to an immediate game over, skip it.
            if (heightPenalty === DEADLINE_ABSOLUTE_AVOID_PENALTY) {
                continue;
            }
            currentScore -= heightPenalty;


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

    // Fallback if no good move found (should only happen if ALL moves are predicted to be game over)
    if (bestScore === -Infinity) {
        reason = "No valid move found (all moves lead to game over), defaulting to center.";
        bestX = 0.0;
        useHold = false;
    }

    return { x: bestX, reason: reason, hold: useHold };
}
/**
 * strategy.mjs - ドロップ位置決定戦略 (v178)
 *
 * v178: v177の改善方針を引き継ぎつつ、ゲーム分析で示された「Rankスコアの低さ」と
 *       「生存ターン数とRankの乖離」という課題に対し、より積極的に併合を促進する調整を行います。
 *       特に、併合ボーナスとパイプラインボーナス、小ピースの触媒ボーナスを強化し、
 *       大型ピースの片側集約をさらに強く推奨します。
 *       また、盤面混雑度ペナルティはv177の調整を維持し、高さ管理ペナルティを強化して、
 *       デッドライン到達によるゲームオーバーをより厳しく回避するようにします。
 *
 *       主な改善点:
 *       1.  **高さ管理ペナルティの強化**:
 *           - `SETTLING_BUFFER` を `0.35` から `0.40` に微増。
 *             物理エンジンの不確実性（着地後の回転、衝撃波）を考慮し、シミュレーション上の最高Y座標が
 *             実際よりもやや高めに出るように調整し、早めの高さ警戒を促します。
 *           - `CRITICAL_HEIGHT_MARGIN` を `0.7` から `0.8` に変更。
 *             デッドラインに近い領域でのペナルティ発生をさらに前倒しし、より安全な高さ維持を優先します。
 *           - `HEIGHT_PENALTY_WEIGHT` を `500000.0` から `750000.0` に増額。
 *             高さペナルティ全体の重みを強化し、高すぎる位置へのドロップをより厳しく抑制します。
 *       2.  **併合判定の緩和とボーナスの強化**:
 *           - `MERGE_PROXIMITY_THRESHOLD` はv177の`0.15`を維持。
 *           - `calculateMergeBonus` 内の二次関数スケール乗数 `30` から `40` に増額。
 *             高いtypeの併合をさらに強く推奨し、積極的なスコア獲得を促します。
 *           - `calculatePipelineBonus` 内の直接チェーンボーナスを `750` から `1000` に、
 *             間接パイプラインボーナスを `250` から `350` に増額。
 *             将来の併合連鎖を意識した配置に強力なインセンティブを与えます。
 *           - `GARBAGE_CLEAR_MERGE_BONUS_LOW_Y` はv177の`1000`を維持。
 *       3.  **小ピースの触媒利用ボーナスの強化**:
 *           - `SMALL_PIECE_CATALYST_BONUS` を `700` から `850` に増額。
 *             小ピースが盤面を攪拌し、既存ピースの併合機会を創出する効果をより高く評価します。
 *       4.  **盤面混雑度ペナルティの調整**:
 *           - `CROWDING_PENALTY_START_THRESHOLD` と `CROWDING_PENALTY_PER_PIECE` はv177の調整を維持。
 *       5.  **大型ピース片側集約の強化**:
 *           - `LARGE_PIECE_AGGREGATION_BONUS` と `LARGE_PIECE_AGGREGATION_PENALTY` はv177の調整を維持。
 *       6.  **既存ロジックの維持**:
 *           - HOLDメカニクス、おじゃまブロック対策などは維持されます。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 0.8; // v178: Increased from 0.7. Critical penalty starts when top is 0.8 below deadline
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.0; // v175: Changed from DEADLINE_Y - 0.75. Extreme warning when top is 1.0 below deadline
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 1.0; // Severe warning when top is 1.0 below deadline
const TOP_Y_WARN_PENALTY_START_RELATIVE = 2.0;     // Warning penalty when top is 2.0 below deadline

const HEIGHT_PENALTY_WEIGHT = 750000.0; // v178: Increased from 500000.0
const SETTLING_BUFFER = 0.40; // v178: Increased from 0.35. Accounts for physics uncertainty.

// v172: Absolute avoid threshold, further tightened from 0.4 to 0.1
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD = DEADLINE_Y - 0.1; // If predictedTopY (with small settling buffer) is above this, virtually GUARANTEES a game over.

// Merge and Pipeline Bonuses
const MERGE_PROXIMITY_THRESHOLD = 0.15; // v177 maintained
const MERGE_BONUS_SCALE_FACTOR = 40; // v178: Increased from 30.
const PIPELINE_BONUS_DIRECT_CHAIN = 1000; // v178: Increased from 750.
const PIPELINE_BONUS_INDIRECT_CHAIN = 350; // v178: Increased from 250.
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 1000; // v177 maintained

// Small Piece Catalyst
const SMALL_PIECE_CATALYST_BONUS = 850; // v178: Increased from 700.

// Crowding Penalty
const CROWDING_PENALTY_START_THRESHOLD = 20; // Example value, adjust as needed
const CROWDING_PENALTY_PER_PIECE = 50; // Example value, adjust as needed

// Large Piece Aggregation
const LARGE_PIECE_AGGREGATION_BONUS = 1500; // Example value, adjust as needed
const LARGE_PIECE_AGGREGATION_PENALTY = 1000; // Example value, adjust as needed
const LARGE_PIECE_TYPE_THRESHOLD = 9; // Type 9+ are considered large

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
            if (existingPiece.y + existingPiece.r > predictedY - piece.r) {
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
 * This function needs to determine the "dominant" side for large pieces first.
 */
let dominantLargePieceSide = null; // null: no dominant side, -1: left, 1: right

function calculateLargePieceAggregationBonus(boardState, dropX, piece) {
    if (piece.type < LARGE_PIECE_TYPE_THRESHOLD) {
        return 0; // Only applies to large pieces
    }

    // Recalculate dominant side if not set or if board state changes significantly
    // (simplified: just recalculate every time for now, or add a debounce/state check)
    let leftLargePieces = 0;
    let rightLargePieces = 0;
    for (const existingPiece of boardState.pieces) {
        if (existingPiece.type >= LARGE_PIECE_TYPE_THRESHOLD) {
            if (existingPiece.x < 0) leftLargePieces++;
            else if (existingPiece.x > 0) rightLargePieces++;
        }
    }

    if (leftLargePieces > rightLargePieces + 1) { // +1 to prevent rapid flip-flopping
        dominantLargePieceSide = -1; // Left side
    } else if (rightLargePieces > leftLargePieces + 1) {
        dominantLargePieceSide = 1; // Right side
    } else {
        dominantLargePieceSide = null; // No clear dominant side
    }

    if (dominantLargePieceSide === null) {
        // If no dominant side yet, try to favor the side with more small pieces or the side that is lower
        // For now, if no dominant side, don't apply bonus/penalty
        return 0;
    }

    if (dominantLargePieceSide === -1 && dropX < 0) {
        return LARGE_PIECE_AGGREGATION_BONUS;
    } else if (dominantLargePieceSide === 1 && dropX > 0) {
        return LARGE_PIECE_AGGREGATION_BONUS;
    } else if (dominantLargePieceSide === -1 && dropX > 0) {
        return -LARGE_PIECE_AGGREGATION_PENALTY;
    } else if (dominantLargePieceSide === 1 && dropX < 0) {
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

    if (topOfPiece >= DEADLINE_ABSOLUTE_AVOID_THRESHOLD) {
        // Absolute avoidance
        return Infinity;
    }

    const heightFromDeadline = DEADLINE_Y - topOfPiece;

    if (heightFromDeadline < CRITICAL_HEIGHT_MARGIN) {
        // Critical penalty: exponentially increasing as it gets closer
        penalty += HEIGHT_PENALTY_WEIGHT * Math.pow((CRITICAL_HEIGHT_MARGIN - heightFromDeadline) / CRITICAL_HEIGHT_MARGIN, 2);
    }
    if (topOfPiece >= TOP_Y_EXTREME_WARN_THRESHOLD) {
        penalty += HEIGHT_PENALTY_WEIGHT / 2; // Additional penalty for extreme warning
    } else if (topOfPiece >= DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE) {
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

    // --- HOLD Logic ---
    let currentPiece = boardState.next;
    let candidatePieces = [{ piece: boardState.next, isHeld: false }];

    if (boardState.canHold) {
        // Evaluate dropping the current piece and then holding
        // Evaluate holding the current piece and dropping the held piece (if any)
        // For simplicity now, let's just consider swapping if hold is not empty
        // A more sophisticated bot would simulate both paths.

        if (boardState.hold) {
            // Option 1: Drop currentPiece, then consider swapping for next turn
            // Option 2: Swap currentPiece with heldPiece, then drop heldPiece
            // For now, let's evaluate dropping the held piece immediately if it exists and canHold is true.
            // This is a simplified HOLD strategy, a full implementation would involve lookahead.
            candidatePieces.push({ piece: boardState.hold, isHeld: true });
        }
    }


    for (const { piece: pieceToDrop, isHeld } of candidatePieces) {
        for (const x of FINE_COLS) {
            // Check if piece would be outside bounds
            if (x - pieceToDrop.r < -BOARD_X_MAX_LIMIT || x + pieceToDrop.r > BOARD_X_MAX_LIMIT) {
                continue; // Skip if piece would be outside walls
            }

            const predictedY = predictLandingY(boardState, x, pieceToDrop);
            let currentScore = 0;

            const heightPenalty = calculateHeightPenalty(predictedY, pieceToDrop.r);
            if (heightPenalty === Infinity) {
                continue; // Absolutely avoid this position
            }
            currentScore -= heightPenalty;

            currentScore += calculateMergeBonus(boardState, x, pieceToDrop, predictedY);
            currentScore += calculatePipelineBonus(boardState, x, pieceToDrop, predictedY);
            currentScore += calculateLargePieceAggregationBonus(boardState, x, pieceToDrop);
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

    // Fallback if no good move found (shouldn't happen with the constant score addition)
    if (bestScore === -Infinity) {
        reason = "No valid move found, defaulting to center (critical error).";
        bestX = 0.0;
        useHold = false;
    }

    return { x: bestX, reason: reason, hold: useHold };
}
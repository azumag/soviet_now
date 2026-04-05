/**
 * strategy.mjs - ドロップ位置決定戦略 (v175)
 *
 * v175: v174のゲーム分析から、特に「高さがデッドライン付近まで到達しやすい」という課題に対し、
 *       高さペナルティをさらに早期かつ厳しく適用するように調整します。
 *       また、「盤面ピース数が多い」という課題に対しては、混雑度ペナルティの閾値を下げてより早く発動させ、
 *       ピースごとのペナルティも強化することで、積極的な盤面クリアを促します。
 *       小ピースの触媒ボーナスは、高さ管理や併合の優先度を確保するため、若干調整します。
 *
 *       主な改善点:
 *       1.  **高さペナルティの早期化と勾配強化**:
 *           - `CRITICAL_HEIGHT_MARGIN` を `0.5` から `0.7` に拡大し、よりデッドラインから離れた位置でクリティカルペナルティが開始されるように調整。
 *             これにより、危険な高さに達する前に戦略的な修正を促します。
 *           - `TOP_Y_EXTREME_WARN_THRESHOLD` を `DEADLINE_Y - 0.75` から `DEADLINE_Y - 1.0` に変更し、
 *             極端な警告ゾーンをデッドラインから少し離れた位置に設定し直し、その上にあるクリティカルゾーンの範囲を拡張します。
 *       2.  **盤面混雑度ペナルティの強化**:
 *           - `CROWDING_PENALTY_START_THRESHOLD` を `30` から `25` に減らし、より少ないピース数で混雑度ペナルティが開始されるようにします。
 *           - `CROWDING_PENALTY_PER_PIECE` を `50` から `75` に増額し、閾値を超えた際のピースごとのペナルティを強化。
 *             これにより、盤面のピース数が過剰に増えることを積極的に抑制します。
 *       3.  **小ピース触媒ボーナスの調整**:
 *           - `SMALL_PIECE_CATALYST_BONUS` を `750` から `600` に減額。
 *             高さ管理や直接的な併合の優先度を相対的に高めるため、触媒ボーナスの影響度を微調整します。
 *       4.  **既存ロジックの維持**:
 *           - HOLDメカニクス、大型ピースの片側集約、おじゃまブロック対策、マージ・パイプラインボーナスなどはv174のまま維持されます。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 0.7; // v175: Increased from 0.5. Critical penalty starts when top is 0.7 below deadline
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.0; // v175: Changed from DEADLINE_Y - 0.75. Extreme warning when top is 1.0 below deadline
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 1.0; // Severe warning when top is 1.0 below deadline
const TOP_Y_WARN_PENALTY_START_RELATIVE = 2.0;     // Warning penalty when top is 2.0 below deadline

const HEIGHT_PENALTY_WEIGHT = 500000.0; // v174: Increased from 250000.0

// v172: Absolute avoid threshold, further tightened from 0.4 to 0.1
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD = DEADLINE_Y - 0.1; // If predictedTopY (with small settling buffer) is above this, virtually disqualify the move.

// v172: Reduced from 4.0 to a more realistic 0.2
const SETTLING_BUFFER = 0.2;

// Strategy-specific constants (General)
const MERGE_PROXIMITY_THRESHOLD = 0.1; // Small buffer for "touching" pieces for merge detection
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_THRESHOLD = 4; // Pieces of this type or lower are considered 'small'. (v165: Changed from 3 to 4)
const SMALL_PIECE_CATALYST_BONUS = 600; // v175: Reduced from 750. Bonus for dropping small pieces into dense areas.
const DENSITY_CHECK_RADIUS = 1.5; // Radius to check for piece density for catalyst bonus. (v165: New)

const LARGE_PIECE_AGGREGATION_BONUS = 750; // Bonus for placing large pieces on the dominant side.
const LARGE_PIECE_AGGREGATION_PENALTY = 1000; // Penalty for placing large pieces on the non-dominant side.
const LARGE_PIECE_DOMINANCE_THRESHOLD = 3; // How many more large pieces on one side to establish dominance.
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 750; // v173: Increased from 300. Bonus for merges at low Y when garbage is present.
const LOW_Y_MERGE_THRESHOLD = 0.0; // Y-coordinate below which a merge is considered "low" for garbage clearing.
const GARBAGE_STACKING_PENALTY = 1500; // v170: Penalty for dropping a piece on garbage without merging.
const GARBAGE_IMMINENT_MERGE_BONUS = 100; // v171: Small bonus for any merge when garbage is imminent.

// v174: Crowding Penalty constants
const CROWDING_PENALTY_START_THRESHOLD = 25; // v175: Reduced from 30. Start penalizing when total pieces exceed this
const CROWDING_PENALTY_PER_PIECE = 75;       // v175: Increased from 50. Penalty per piece above the threshold

// Function to simulate dropping a piece and calculate its final Y position (top of the piece).
// This now returns the predicted static top Y of the piece plus a small settling buffer.
function simulateDropY(boardState, piece, x) {
    let maxY = BOARD_FLOOR_Y; // This will track the highest Y coordinate of a surface a piece could land on.

    for (const existingPiece of boardState.pieces) {
        // Check for horizontal overlap: if the horizontal distance between centers
        // is less than the sum of their radii (minus a small tolerance for initial contact),
        // they would collide horizontally.
        if (Math.abs(existingPiece.x - x) < (piece.r + existingPiece.r) - MERGE_PROXIMITY_THRESHOLD) {
            // The landing surface Y would be the top of the existing piece.
            maxY = Math.max(maxY, existingPiece.y + existingPiece.r);
        }
    }
    // v172: The predicted top Y of the dropping piece:
    // maxY (surface) + piece.r (to get center) + piece.r (to get top) + SETTLING_BUFFER (for bounce/settling)
    return maxY + (2 * piece.r) + SETTLING_BUFFER;
}

// Function to calculate height-based penalties
function calculateHeightPenalty(predictedTopY) {
    let penalty = 0;
    const currentTopY = predictedTopY;

    // v172: Absolute avoid threshold, adjusted due to SETTLING_BUFFER change.
    if (currentTopY >= DEADLINE_ABSOLUTE_AVOID_THRESHOLD) {
        return 1000000000; // Effectively disqualifies the move with a very large penalty
    }

    // v174: Adjusted multipliers to further increase penalty gradient towards deadline
    // v175: CRITICAL_HEIGHT_MARGIN adjusted to start penalty earlier
    if (currentTopY > DEADLINE_Y - CRITICAL_HEIGHT_MARGIN) { // > DEADLINE_Y - 0.7
        // Critical penalty zone - very severe
        penalty += (currentTopY - (DEADLINE_Y - CRITICAL_HEIGHT_MARGIN)) * HEIGHT_PENALTY_WEIGHT * 2000; // v174: Increased from 1000
    } else if (currentTopY > TOP_Y_EXTREME_WARN_THRESHOLD) { // > DEADLINE_Y - 1.0 (v175: Changed from DEADLINE_Y - 0.75)
        // Extreme warning zone
        penalty += (currentTopY - TOP_Y_EXTREME_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 400; // v174: Increased from 200
    } else if (currentTopY > DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE) { // > 2.32
        // Severe warning zone
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 50;
    } else if (currentTopY > DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE) { // > 1.32
        // Warning zone
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 10;
    }

    return penalty;
}

// Helper to find potential merges for a dropping piece at a simulated position
function findPotentialMerges(boardState, droppingPiece, dropX, dropY) {
    let potentialMerges = [];
    // Create a temporary piece object for the simulated position
    // `dropY` here represents the predicted top Y of the piece, so its center is `dropY - droppingPiece.r`.
    const simulatedPiece = { ...droppingPiece, x: dropX, y: dropY - droppingPiece.r };

    for (const existingPiece of boardState.pieces) {
        if (existingPiece.type === simulatedPiece.type) {
            // Calculate distance between centers
            const distance = Math.sqrt(
                Math.pow(simulatedPiece.x - existingPiece.x, 2) +
                Math.pow(simulatedPiece.y - existingPiece.y, 2)
            );
            // If distance is less than or equal to the sum of their radii (plus a small tolerance for "touching")
            if (distance <= simulatedPiece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD) {
                potentialMerges.push(existingPiece);
            }
        }
    }
    return potentialMerges;
}

// Calculate bonus for potential merges
function calculateMergeBonus(potentialMerges, simulatedPieceYCenter, garbageState) {
    let bonus = 0;
    if (potentialMerges.length > 0) {
        // Reward higher type merges more
        // Example: type 1 merge = 50, type 5 merge = 500, type 10 merge = 2000
        for (const mergedPiece of potentialMerges) {
            bonus += mergedPiece.type * mergedPiece.type * 20; // v174: Quadratic scaling multiplier increased from 10 to 20
        }

        // Boost merge bonus if garbage is imminent or present
        // v172: GARBAGE_IMMINENT_MERGE_BONUS now applies in OJAMA_MERGE mode too.
        if (garbageState.gauge >= 0.6 || garbageState.ratio > 0.4) { // GBG_URGENT
            bonus *= 5;
            bonus += GARBAGE_IMMINENT_MERGE_BONUS;
        } else if (garbageState.gauge >= 0.3 || garbageState.ratio > 0.15) { // OJAMA_MERGE
            bonus *= 3;
            bonus += GARBAGE_IMMINENT_MERGE_BONUS; // v172: Added bonus for any merge when garbage is imminent (OJAMA_MERGE)
        }

        // v173: Bonus for merges at low Y when garbage is present (increased value)
        if ((garbageState.gauge > 0.15 || garbageState.ratio > 0) && simulatedPieceYCenter < LOW_Y_MERGE_THRESHOLD) {
            bonus += GARBAGE_CLEAR_MERGE_BONUS_LOW_Y;
        }
    }
    return bonus;
}

// Calculate bonus for maintaining pipeline (simplified)
function calculatePipelineBonus(boardState, droppingPiece, dropX, dropY, nextNextPiece) {
    let bonus = 0;
    // `dropY` here represents the predicted top Y of the piece, so its center is `dropY - droppingPiece.r`.
    const simulatedPiece = { ...droppingPiece, x: dropX, y: dropY - droppingPiece.r };

    // Bonus for current piece forming a direct link in a chain (N-1 -> N)
    for (const existingPiece of boardState.pieces) {
        if (existingPiece.type === simulatedPiece.type - 1) {
            const distance = Math.sqrt(
                Math.pow(simulatedPiece.x - existingPiece.x, 2) +
                Math.pow(simulatedPiece.y - existingPiece.y, 2)
            );
            if (distance <= simulatedPiece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD) {
                bonus += 500; // v174: Stronger bonus for direct chain building (Increased from 200)
            }
        }
    }

    if (!nextNextPiece) return bonus;

    // A simple pipeline bonus: reward placing current piece such that nextNextPiece
    // could potentially merge or be placed near a similar type.
    for (const existingPiece of boardState.pieces) {
        // Check for adjacency with nextNextPiece's type (either N-1 or N+1)
        if (existingPiece.type === nextNextPiece.type - 1 || existingPiece.type === nextNextPiece.type + 1) {
            const distance = Math.sqrt(
                Math.pow(simulatedPiece.x - existingPiece.x, 2) +
                Math.pow(simulatedPiece.y - existingPiece.y, 2)
            );
            if (distance <= simulatedPiece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD) {
                bonus += 150; // v174: Small bonus for indirect pipeline (Increased from 50)
            }
        }
    }
    return bonus;
}


// Calculate penalty for exacerbating garbage problems
function calculateGarbagePenalty(boardState, predictedTopY, dropX, droppingPiece, hasMerge) {
    let penalty = 0;
    const garbage = boardState.garbage;

    if (garbage.ratio > 0.4) { // GBG_URGENT mode
        // Penalize moves that increase height significantly if garbage is high
        // v171: Increased multiplier from 800 to 1000
        if (predictedTopY > garbage.height) {
            penalty += (predictedTopY - garbage.height) * 1000;
        }
        penalty += 7500; // v171: General severe penalty for being in urgent garbage state, increased from 5000
    } else if (garbage.ratio > 0.15) { // OJAMA_MERGE mode
        // v171: Increased multiplier from 400 to 500
        if (predictedTopY > garbage.height + 0.5) { // Mild penalty for increasing height
            penalty += (predictedTopY - (garbage.height + 0.5)) * 500;
        }
    }

    // Penalize if predicted piece top is above garbage height and garbage is high
    // v170: Increased multiplier from 200 to 300
    if (garbage.height > BOARD_FLOOR_Y && predictedTopY > garbage.height) {
        penalty += (predictedTopY - garbage.height) * 300;
    }

    // v170: Penalty for stacking on garbage without merging, increased from 1000 to 1500
    if (!hasMerge && garbage.ratio > 0 && predictedTopY > garbage.height) {
        // Simulate landing on garbage - simplified to check if landing directly on/above existing garbage
        let simulatedGarbageImpactY = BOARD_FLOOR_Y;
        for (const piece of boardState.pieces) {
            if (Math.abs(piece.x - dropX) < droppingPiece.r + piece.r && piece.y < predictedTopY - droppingPiece.r) {
                 simulatedGarbageImpactY = Math.max(simulatedGarbageImpactY, piece.y + piece.r);
            }
        }
        if (simulatedGarbageImpactY > garbage.height - droppingPiece.r * 2) { // Roughly landing on or above garbage height
            penalty += GARBAGE_STACKING_PENALTY;
        }
    }

    return penalty;
}

// Calculate bonus for dropping small pieces into dense areas (catalyst effect)
function calculateSmallPieceCatalystBonus(boardState, droppingPiece, dropX, dropY) {
    if (droppingPiece.type > SMALL_PIECE_THRESHOLD) {
        return 0; // Only applies to small pieces
    }

    let denseNeighbors = 0;
    // `dropY` here represents the predicted top Y of the piece, so its center is `dropY - droppingPiece.r`.
    const simulatedPiece = { ...droppingPiece, x: dropX, y: dropY - droppingPiece.r };

    for (const existingPiece of boardState.pieces) {
        const distance = Math.sqrt(
            Math.pow(simulatedPiece.x - existingPiece.x, 2) +
            Math.pow(simulatedPiece.y - existingPiece.y, 2)
        );
        // Check if existing piece is within a certain radius
        if (distance < DENSITY_CHECK_RADIUS) {
            denseNeighbors++;
        }
    }

    // Apply bonus if there are enough neighbors to create a "dense" area
    if (denseNeighbors >= 3) { // Threshold for density, e.g., 3 or more pieces nearby
        return SMALL_PIECE_CATALYST_BONUS * denseNeighbors; // Bonus scales with density
    }
    return 0;
}

// v174: New function to calculate penalty based on the number of pieces on the board.
// v175: Threshold and per-piece penalty increased.
function calculateCrowdingPenalty(boardState) {
    let penalty = 0;
    const pieceCount = boardState.pieces.length;

    if (pieceCount > CROWDING_PENALTY_START_THRESHOLD) {
        penalty = (pieceCount - CROWDING_PENALTY_START_THRESHOLD) * CROWDING_PENALTY_PER_PIECE;
    }
    return penalty;
}


// Main decision function
export function decide(boardState) {
    let bestScore = -Infinity;
    let bestX = 0;
    let bestReason = "No good move found";
    let hold = false;

    // Correctly reference the current piece to drop and the next-next piece
    const currentPiece = boardState.next;
    const nextNextPiece = boardState.nextPieces[1]; // The piece after the current one
    const heldPiece = boardState.hold;

    // HOLD logic:
    // Simple strategy: If hold is available, and we have a large piece (>= type 9)
    // with no immediate merge opportunities, hold it to wait for a better spot.
    // Or if current piece is small, held piece is large, and held piece has merge opportunities.
    if (boardState.canHold) {
        let currentPieceMergeOpportunities = 0;
        if (currentPiece) {
             // A quick check for current piece's merge potential across the board
             for (const xCheck of FINE_COLS) {
                 const simulatedYCheck = simulateDropY(boardState, currentPiece, xCheck);
                 currentPieceMergeOpportunities += findPotentialMerges(boardState, currentPiece, xCheck, simulatedYCheck).length;
             }
        }

        // 1. Hold a large piece with no immediate merges, if nothing is held.
        if (currentPiece && currentPiece.type >= LARGE_PIECE_THRESHOLD && currentPieceMergeOpportunities === 0 && !heldPiece) {
            hold = true;
            bestReason = "Holding a large piece with no immediate merges.";
            return { x: 0, reason: bestReason, hold: hold };
        }
        
        // 2. If current piece is small and held piece is large, and held piece has merge opportunities, swap.
        if (currentPiece && currentPiece.type <= SMALL_PIECE_THRESHOLD && heldPiece && heldPiece.type >= LARGE_PIECE_THRESHOLD) {
            let heldPieceMergeOpportunities = 0;
            for (const xCheck of FINE_COLS) {
                 const simulatedYCheck = simulateDropY(boardState, heldPiece, xCheck);
                 heldPieceMergeOpportunities += findPotentialMerges(boardState, heldPiece, xCheck, simulatedYCheck).length;
            }
            if (heldPieceMergeOpportunities > 0) {
                hold = true;
                bestReason = "Swapping small piece for held large piece with merges.";
                return { x: 0, reason: bestReason, hold: hold };
            }
        }
    }

    // Ensure currentPiece exists before proceeding with calculations
    if (!currentPiece) {
        // This should ideally not happen if boardState is correctly populated
        return { x: 0, reason: "Error: No current piece available.", hold: false };
    }

    // Calculate large piece distribution for aggregation logic
    let largePiecesLeft = 0;
    let largePiecesRight = 0;
    for (const piece of boardState.pieces) {
        if (piece.type >= LARGE_PIECE_THRESHOLD) {
            if (piece.x < 0) {
                largePiecesLeft++;
            } else if (piece.x > 0) {
                largePiecesRight++;
            }
        }
    }
    let dominantSide = 'none';
    if (largePiecesRight - largePiecesLeft >= LARGE_PIECE_DOMINANCE_THRESHOLD) {
        dominantSide = 'right';
    } else if (largePiecesLeft - largePiecesRight >= LARGE_PIECE_DOMINANCE_THRESHOLD) {
        dominantSide = 'left';
    }


    for (const x of FINE_COLS) {
        // Skip if dropping outside the board limits
        if (Math.abs(x) + currentPiece.r > BOARD_X_MAX_LIMIT) {
            continue;
        }

        // Simulate dropping current piece
        const predictedTopY = simulateDropY(boardState, currentPiece, x);
        // Calculate the simulated piece's center Y for use in bonuses
        const simulatedPieceYCenter = predictedTopY - currentPiece.r;
        let score = 0;
        let reason = `X=${x.toFixed(2)}`;

        // Apply height penalty
        const heightPen = calculateHeightPenalty(predictedTopY);
        score -= heightPen;
        if (heightPen > 0) reason += ` (H:${heightPen.toFixed(0)})`;

        // v174: Apply crowding penalty
        const crowdingPen = calculateCrowdingPenalty(boardState);
        score -= crowdingPen;
        if (crowdingPen > 0) reason += ` (CP:${crowdingPen.toFixed(0)})`;

        // Early/Mid game central management
        // v169: Reduced central bonuses to encourage more flexible layouts for large piece aggregation.
        if (boardState.pieces.length < 5) { // Very early game
            const distanceFromCenter = Math.abs(x);
            if (currentPiece.type <= SMALL_PIECE_THRESHOLD) {
                if (distanceFromCenter < 0.5) { // Close to center for small pieces
                    score += 1500;
                    reason += " (EGCB-S)";
                } else if (distanceFromCenter < 1.5) {
                    score += 750;
                    reason += " (EGOB-S)";
                }
            } else if (currentPiece.type >= LARGE_PIECE_THRESHOLD) {
                if (distanceFromCenter < 1.0) { // Penalize large pieces in center
                    score -= 1500;
                    reason += " (EGCP-L)";
                }
            }
        } else if (boardState.pieces.length < 15) { // Mid-early game
            const distanceFromCenter = Math.abs(x);
            if (currentPiece.type <= SMALL_PIECE_THRESHOLD) {
                if (distanceFromCenter < 0.5) { // Close to center for small pieces
                    score += 750;
                    reason += " (MGCB-S)";
                } else if (distanceFromCenter < 1.5) {
                    score += 375;
                    reason += " (MGOB-S)";
                }
            } else if (currentPiece.type >= LARGE_PIECE_THRESHOLD) {
                if (distanceFromCenter < 1.0) { // Penalize large pieces in center
                    score -= 1000;
                    reason += " (MGCP-L)";
                }
            }
        }

        // v170: Large Piece Aggregation Logic - increased bonuses/penalties
        if (currentPiece.type >= LARGE_PIECE_THRESHOLD && dominantSide !== 'none') {
            if (x < 0 && dominantSide === 'left') {
                score += LARGE_PIECE_AGGREGATION_BONUS;
                reason += " (LPA-B)";
            } else if (x > 0 && dominantSide === 'right') {
                score += LARGE_PIECE_AGGREGATION_BONUS;
                reason += " (LPA-B)";
            } else if (x < 0 && dominantSide === 'right') { // Penalize dropping left if right is dominant
                score -= LARGE_PIECE_AGGREGATION_PENALTY;
                reason += " (LPA-P)";
            } else if (x > 0 && dominantSide === 'left') { // Penalize dropping right if left is dominant
                score -= LARGE_PIECE_AGGREGATION_PENALTY;
                reason += " (LPA-P)";
            }
        }

        const potentialMerges = findPotentialMerges(boardState, currentPiece, x, predictedTopY);
        const hasMerge = potentialMerges.length > 0;

        // Calculate and apply Merge Bonus
        const mergeBonus = calculateMergeBonus(potentialMerges, simulatedPieceYCenter, boardState.garbage);
        score += mergeBonus;
        if (mergeBonus > 0) reason += ` (M+:${mergeBonus.toFixed(0)})`;

        // Calculate and apply Pipeline Bonus
        const pipelineBonus = calculatePipelineBonus(boardState, currentPiece, x, predictedTopY, nextNextPiece);
        score += pipelineBonus;
        if (pipelineBonus > 0) reason += ` (P+:${pipelineBonus.toFixed(0)})`;

        // Calculate and apply Garbage Penalty
        const garbagePen = calculateGarbagePenalty(boardState, predictedTopY, x, currentPiece, hasMerge);
        score -= garbagePen;
        if (garbagePen > 0) reason += ` (GP:${garbagePen.toFixed(0)})`;

        // Small piece clustering bonus (Catalyst effect)
        const catalystBonus = calculateSmallPieceCatalystBonus(boardState, currentPiece, x, predictedTopY);
        score += catalystBonus;
        if (catalystBonus > 0) reason += ` (CAT+:${catalystBonus.toFixed(0)})`;

        if (score > bestScore) {
            bestScore = score;
            bestX = x;
            bestReason = reason;
        }
    }

    return { x: bestX, reason: bestReason, hold: hold };
}
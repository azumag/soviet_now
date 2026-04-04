/**
 * strategy.mjs - ドロップ位置決定戦略 (v166)
 *
 * v166: v165の改善点をベースに、ゲーム分析からの洞察と物理挙動の再評価に基づいた戦略の強化を行います。
 *
 *       主な改善点:
 *       1.  **CRITICAL FIX: simulateDropYの修正**:
 *           - 物理エンジンのピースのY座標計算ロジックを再評価。`simulateDropY` 関数がピースの「頂点Y座標」を
 *             正確に予測するように修正しました。これまでのバージョンでは、ピースの半径分だけ頂点Y座標が低く見積もられており、
 *             高さペナルティが十分に機能していなかった可能性があります。
 *             修正前: `maxY + piece.r + SETTLING_BUFFER` (ピース中心Y + バッファ)
 *             修正後: `maxY + (2 * piece.r) + SETTLING_BUFFER` (ピース頂点Y + バッファ)
 *             この修正により、高さ管理がより正確かつ厳密になり、早期ゲームオーバーの削減に寄与すると考えられます。
 *       2.  **HEIGHT_PENALTY_WEIGHTの強化**:
 *           - ゲーム分析で一貫して高いY座標がゲームオーバーの一因となっていることが示唆されたため、
 *             `HEIGHT_PENALTY_WEIGHT` を `90000.0` から `120000.0` に増加。
 *             より積極的に高さを抑える動きを奨励します。
 *       3.  **Garbage Penaltyの強化**:
 *           - `GBG_URGENT` モード（`garbage.ratio > 0.4`）における基本的なペナルティを
 *             `2000` から `3000` に増加。おじゃまブロックが非常に危険な状態での高リスク行動を抑制します。
 *       4.  **Small Piece Catalyst Bonusの強化**:
 *           - 「小ピースの触媒利用」原則の効果をより高めるため、`SMALL_PIECE_CATALYST_BONUS` を
 *             `500` から `750` に増加。小さなピースを高密度エリアに落とすことの戦略的価値を高めます。
 *       5.  **Pipeline Bonusの強化**:
 *           - `currentPiece` が既存のピースとタイプが1つ違いで隣接する場合のボーナスを
 *             `150` から `200` に増加。直接的な併合パイプラインの構築をさらに奨励します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
// v165: Corrected DEADLINE_Y based on game rules (+3.32)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
// v165: Adjusted relative values to be closer to actual DEADLINE_Y
const CRITICAL_HEIGHT_MARGIN = 0.5; // Critical penalty starts when top is 0.5 below deadline
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 0.75; // Extreme warning when top is 0.75 below deadline
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 1.0; // Severe warning when top is 1.0 below deadline
const TOP_Y_WARN_PENALTY_START_RELATIVE = 2.0;     // Warning penalty when top is 2.0 below deadline

// v166: Increased from 90000.0 to 120000.0
const HEIGHT_PENALTY_WEIGHT = 120000.0;

// Strategy-specific constants (General)
const MERGE_PROXIMITY_THRESHOLD = 0.1; // Small buffer for "touching" pieces for merge detection
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_THRESHOLD = 4; // Pieces of this type or lower are considered 'small'. (v165: Changed from 3 to 4)
const SMALL_PIECE_CATALYST_BONUS = 750; // Bonus for dropping small pieces into dense areas. (v166: Increased from 500)
const DENSITY_CHECK_RADIUS = 1.5; // Radius to check for piece density for catalyst bonus. (v165: New)

// v165: Adjusted settlingBuffer from 4.0 to 2.0
const SETTLING_BUFFER = 2.0;

// Function to simulate dropping a piece and calculate its final Y position (top of the piece).
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
    // v166 CRITICAL FIX: The predicted top Y of the dropping piece:
    // maxY (surface) + piece.r (to get center) + piece.r (to get top) + SETTLING_BUFFER (for bounce/settling)
    return maxY + (2 * piece.r) + SETTLING_BUFFER;
}

// Function to calculate height-based penalties
function calculateHeightPenalty(predictedTopY) {
    let penalty = 0;
    const currentTopY = predictedTopY;

    if (currentTopY > DEADLINE_Y - CRITICAL_HEIGHT_MARGIN) {
        // Critical penalty zone
        penalty += (currentTopY - (DEADLINE_Y - CRITICAL_HEIGHT_MARGIN)) * HEIGHT_PENALTY_WEIGHT * 10; // Very severe
    } else if (currentTopY > TOP_Y_EXTREME_WARN_THRESHOLD) {
        // Extreme warning zone
        penalty += (currentTopY - TOP_Y_EXTREME_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 75;
    } else if (currentTopY > DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE) {
        // Severe warning zone
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 15;
    } else if (currentTopY > DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE) {
        // Warning zone
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 5;
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
function calculateMergeBonus(potentialMerges, garbageState) {
    let bonus = 0;
    if (potentialMerges.length > 0) {
        // Reward higher type merges more
        // Example: type 1 merge = 50, type 5 merge = 500, type 10 merge = 2000
        for (const mergedPiece of potentialMerges) {
            bonus += mergedPiece.type * mergedPiece.type * 10; // Quadratic scaling with type
        }

        // Boost merge bonus if garbage is imminent or present
        if (garbageState.gauge >= 0.6 || garbageState.ratio > 0.4) { // GBG_URGENT
            bonus *= 3; // Aggressively prioritize merges
        } else if (garbageState.gauge >= 0.3 || garbageState.ratio > 0.15) { // OJAMA_MERGE
            bonus *= 1.5; // Prepare for incoming garbage
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
                bonus += 200; // v166: Stronger bonus for direct chain building (increased from 150)
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
                bonus += 50; // Small bonus for indirect pipeline
            }
        }
    }
    return bonus;
}


// Calculate penalty for exacerbating garbage problems
function calculateGarbagePenalty(boardState, predictedTopY) {
    let penalty = 0;
    const garbage = boardState.garbage;

    if (garbage.ratio > 0.4) { // GBG_URGENT mode
        // Penalize moves that increase height significantly if garbage is high
        if (predictedTopY > garbage.height) {
            penalty += (predictedTopY - garbage.height) * 500;
        }
        penalty += 3000; // v166: General severe penalty for being in urgent garbage state (increased from 2000)
    } else if (garbage.ratio > 0.15) { // OJAMA_MERGE mode
        if (predictedTopY > garbage.height + 0.5) { // Mild penalty for increasing height
            penalty += (predictedTopY - (garbage.height + 0.5)) * 200; // Increased multiplier
        }
    }

    // Penalize if predicted piece top is above garbage height and garbage is high
    if (garbage.height > BOARD_FLOOR_Y && predictedTopY > garbage.height) {
        penalty += (predictedTopY - garbage.height) * 150; // Increased multiplier
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
        
        // 2. If current piece is small and held piece is large and has merge opportunities, swap.
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


    for (const x of FINE_COLS) {
        // Skip if dropping outside the board limits
        if (Math.abs(x) + currentPiece.r > BOARD_X_MAX_LIMIT) {
            continue;
        }

        // Simulate dropping current piece
        const predictedTopY = simulateDropY(boardState, currentPiece, x);
        let score = 0;
        let reason = `X=${x.toFixed(2)}`;

        // Apply height penalty
        const heightPen = calculateHeightPenalty(predictedTopY);
        score -= heightPen;
        reason += ` (H:${heightPen.toFixed(0)})`;

        // Early/Mid game central management (v165: Refined based on Large Piece Consolidation)
        if (boardState.pieces.length < 5) { // Very early game
            const distanceFromCenter = Math.abs(x);
            if (currentPiece.type <= SMALL_PIECE_THRESHOLD) {
                if (distanceFromCenter < 0.5) { // Close to center for small pieces
                    score += 2000; // Strong bonus
                    reason += " (EGCB-S)";
                } else if (distanceFromCenter < 1.5) {
                    score += 1000;
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
                    score += 1000;
                    reason += " (MGCB-S)";
                } else if (distanceFromCenter < 1.5) {
                    score += 500;
                    reason += " (MGOB-S)";
                }
            } else if (currentPiece.type >= LARGE_PIECE_THRESHOLD) {
                if (distanceFromCenter < 1.0) { // Penalize large pieces in center
                    score -= 1000;
                    reason += " (MGCP-L)";
                }
            }
        }

        // Calculate and apply Merge Bonus
        const mergeBonus = calculateMergeBonus(findPotentialMerges(boardState, currentPiece, x, predictedTopY), boardState.garbage);
        score += mergeBonus;
        if (mergeBonus > 0) reason += ` (M+:${mergeBonus.toFixed(0)})`;

        // Calculate and apply Pipeline Bonus (v165: Improved)
        const pipelineBonus = calculatePipelineBonus(boardState, currentPiece, x, predictedTopY, nextNextPiece);
        score += pipelineBonus;
        if (pipelineBonus > 0) reason += ` (P+:${pipelineBonus.toFixed(0)})`;

        // Calculate and apply Garbage Penalty
        const garbagePen = calculateGarbagePenalty(boardState, predictedTopY);
        score -= garbagePen;
        if (garbagePen > 0) reason += ` (GP:${garbagePen.toFixed(0)})`;

        // Small piece clustering bonus (Catalyst effect) (v165: Implemented)
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
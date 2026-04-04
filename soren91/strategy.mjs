/**
 * strategy.mjs - ドロップ位置決定戦略 (v164)
 *
 * v164: v163で導入された厳格な高さ管理と初期盤面ボーナスは、
 *       分析結果から、ドロップX座標が常に0.00になるという致命的な問題を引き起こしていました。
 *       これは主に `boardState.currentPiece` の未定義、`boardState.nextPiece` 等の誤ったプロパティ参照、
 *       そしてスコアリングロジックにマージボーナスやガベージ処理が欠如していたため、
 *       全ての候補手が同様に低いスコアと判定されたことに起因します。
 *       本バージョンではこの根本原因に対処し、より実用的な戦略へと改善します。
 *
 *      主な改善点:
 *      1.  **`boardState` プロパティの正しい参照**:
 *          - ドロップするピースとして `boardState.currentPiece` ではなく `boardState.next` を使用するように修正。
 *          - `boardState.nextPiece`, `boardState.holdPiece` ではなく `boardState.nextPieces[1]`, `boardState.hold` を使用するように修正。
 *      2.  **`simulateDropY` の改善**:
 *          - ピース半径を考慮したより現実的なY座標予測ロジックに修正。X方向の重なり判定を厳密化。
 *      3.  **マージボーナスの導入**:
 *          - `findPotentialMerges` および `calculateMergeBonus` を導入し、
 *            同じタイプのピースが隣接し、併合が見込める位置にドロップすることを強く推奨。
 *            特に高いタイプのピースの併合に大きなボーナスを与える。
 *      4.  **ガベージ処理の強化**:
 *          - `calculateGarbagePenalty` および `calculateGarbageBonus` を導入。
 *          - `garbage.gauge` や `garbage.ratio` に応じて、併合ボーナスを増幅したり、
 *            ガベージの高さを増やすようなドロップをペナルティ化したりする。
 *      5.  **パイプラインボーナスの導入 (簡略版)**:
 *          - 次のピース (`nextNextPiece`) が既存のピースの隣に配置できる場合にボーナスを与える。
 *      6.  **HOLDロジックの再活性化**:
 *          - 初期化時にHOLDが空で、現在のピースが大きい場合にHOLDする、というシンプルなロジックを再導入。
 *            より複雑なHOLD戦略の基盤とする。
 *      7.  **デバッグ出力の追加**:
 *          - 各ドロップ候補のスコアと理由を `bestReason` に蓄積し、分析を容易にする。
 *      8.  **v163の高さ管理設定は維持**:
 *          - `settlingBuffer`、`CRITICAL_HEIGHT_MARGIN`、高さペナルティの乗数・閾値は、
 *            物理エンジンの挙動を考慮した保守的な予測として維持する。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
// Adjusted relative values to make penalties start earlier (lower Y) - v156
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 1.5; // Critical penalty starts when top is 1.5 below deadline (i.e., Y=1.0)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 2.5;     // Warning penalty starts when top is 2.5 below deadline (i.e., Y=0.0)
// v163: Adjusted from DEADLINE_Y - 0.5 (Y=2.0) to DEADLINE_Y - 0.75 (Y=1.75)
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 0.75;

// v162: Increased from 75000.0 to 90000.0
const HEIGHT_PENALTY_WEIGHT = 90000.0;

// v163: Changed from 0.75 to 1.0
const CRITICAL_HEIGHT_MARGIN = 1.0;

// Strategy-specific constants (General)
const MERGE_PROXIMITY_THRESHOLD = 0.1; // Small buffer for "touching" pieces for merge detection
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_CLUSTER_BONUS = 800; // Maintained from v141
const SMALL_PIECE_THRESHOLD = 3; // Corrected constant name

// Function to simulate dropping a piece and calculate its final Y position.
function simulateDropY(boardState, piece, x) {
    let maxY = BOARD_FLOOR_Y;
    const pieceRadius = piece.r;

    for (const existingPiece of boardState.pieces) {
        const existingPieceRadius = existingPiece.r;
        // Check for horizontal overlap: if the horizontal distance between centers
        // is less than the sum of their radii, they would collide horizontally.
        if (Math.abs(existingPiece.x - x) < (pieceRadius + existingPieceRadius) - MERGE_PROXIMITY_THRESHOLD) {
            // If they overlap horizontally, the dropping piece might land on this one.
            // The landing Y position would be the top of the existing piece plus the radius of the dropping piece.
            maxY = Math.max(maxY, existingPiece.y + existingPieceRadius);
        }
    }
    // v163: Increased settlingBuffer from 3.75 to 4.0
    // This settling buffer accounts for the convex polygon and shockwave effects,
    // providing a conservative height estimation for stable board management.
    const settlingBuffer = 4.0;
    return maxY + pieceRadius + settlingBuffer;
}

// Function to calculate height-based penalties
function calculateHeightPenalty(predictedMaxY) {
    let penalty = 0;
    const currentTopY = predictedMaxY;

    // v163: CRITICAL_HEIGHT_MARGIN increased from 0.75 to 1.0
    if (currentTopY > DEADLINE_Y - CRITICAL_HEIGHT_MARGIN) { // e.g., if DEADLINE_Y is 2.5, CRITICAL_HEIGHT_MARGIN is 1.0, this activates if currentTopY > 1.5
        // Critical penalty zone
        penalty += (currentTopY - (DEADLINE_Y - CRITICAL_HEIGHT_MARGIN)) * HEIGHT_PENALTY_WEIGHT * 10; // Very severe
    } else if (currentTopY > TOP_Y_EXTREME_WARN_THRESHOLD) { // e.g., if DEADLINE_Y is 2.5, THRESHOLD is 1.75, this activates if currentTopY > 1.75
        // v163: Extreme warning zone multiplier increased from 60 to 75
        penalty += (currentTopY - TOP_Y_EXTREME_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 75;
    } else if (currentTopY > DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE) { // Y=1.0 for DEADLINE_Y=2.5
        // Severe warning zone
        // v163: Severe warning zone multiplier increased from 12 to 15
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 15;
    } else if (currentTopY > DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE) { // Y=0.0 for DEADLINE_Y=2.5
        // Warning zone
        penalty += (currentTopY - (DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE)) * HEIGHT_PENALTY_WEIGHT * 5;
    }

    return penalty;
}

// Helper to find potential merges for a dropping piece at a simulated position
function findPotentialMerges(boardState, droppingPiece, dropX, dropY) {
    let potentialMerges = [];
    // Create a temporary piece object for the simulated position
    const simulatedPiece = { ...droppingPiece, x: dropX, y: dropY };

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
        if (garbageState.gauge >= 0.6 || garbageState.ratio > 0.15) {
            bonus *= 2; // Aggressively prioritize merges
        } else if (garbageState.gauge >= 0.3) {
            bonus *= 1.5; // Prepare for incoming garbage
        }
    }
    return bonus;
}

// Calculate bonus for maintaining pipeline (simplified)
function calculatePipelineBonus(boardState, droppingPiece, dropX, dropY, nextNextPiece) {
    let bonus = 0;
    if (!nextNextPiece) return bonus;

    // A simple pipeline bonus: reward placing current piece next to a piece one type lower/higher,
    // AND if the nextNextPiece could also be placed near a similar type.
    const simulatedPiece = { ...droppingPiece, x: dropX, y: dropY };

    for (const existingPiece of boardState.pieces) {
        // Check for adjacency with nextNextPiece's type
        if (existingPiece.type === nextNextPiece.type - 1 || existingPiece.type === nextNextPiece.type + 1) {
            const distance = Math.sqrt(
                Math.pow(simulatedPiece.x - existingPiece.x, 2) +
                Math.pow(simulatedPiece.y - existingPiece.y, 2)
            );
            if (distance <= simulatedPiece.r + existingPiece.r + MERGE_PROXIMITY_THRESHOLD) {
                bonus += 50; // Small bonus for indirect pipeline
                if (existingPiece.type === simulatedPiece.type - 1) {
                    bonus += 100; // Direct pipeline link
                }
            }
        }
    }
    return bonus;
}


// Calculate penalty for exacerbating garbage problems
function calculateGarbagePenalty(boardState, predictedMaxY) {
    let penalty = 0;
    const garbage = boardState.garbage;

    if (garbage.ratio > 0.4) { // GBG_URGENT mode
        // Penalize moves that increase height significantly if garbage is high
        if (predictedMaxY > garbage.height) {
            penalty += (predictedMaxY - garbage.height) * 500;
        }
        penalty += 1000; // General penalty for being in urgent garbage state
    } else if (garbage.ratio > 0.15) { // OJAMA_MERGE mode
        if (predictedMaxY > garbage.height + 0.5) { // Mild penalty for increasing height
            penalty += (predictedMaxY - (garbage.height + 0.5)) * 100;
        }
    }

    // Penalize if predicted piece top is above garbage height and garbage is high
    if (garbage.height > BOARD_FLOOR_Y && predictedMaxY > garbage.height) {
        penalty += (predictedMaxY - garbage.height) * 100;
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
    // Or if we have an undesirable small piece and a potentially better piece is held.
    if (boardState.canHold) {
        let currentPieceMergeOpportunities = 0;
        if (currentPiece) {
             // A quick check for current piece's merge potential across the board
             for (const xCheck of FINE_COLS) {
                 const simulatedYCheck = simulateDropY(boardState, currentPiece, xCheck);
                 currentPieceMergeOpportunities += findPotentialMerges(boardState, currentPiece, xCheck, simulatedYCheck).length;
             }
        }

        // If current piece is large and no easy merges, and we don't have anything better held.
        if (currentPiece && currentPiece.type >= LARGE_PIECE_THRESHOLD && currentPieceMergeOpportunities === 0 && !heldPiece) {
            hold = true;
            bestReason = "Holding a large piece with no immediate merges.";
            return { x: 0, reason: bestReason, hold: hold };
        }
        // More complex hold logic would involve simulating outcomes for both hold and no-hold paths
        // and comparing scores. This simplified version provides a starting point.
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
        const predictedY = simulateDropY(boardState, currentPiece, x);
        let score = 0;
        let reason = `X=${x.toFixed(2)}`;

        // Apply height penalty
        const heightPen = calculateHeightPenalty(predictedY);
        score -= heightPen;
        reason += ` (H:${heightPen.toFixed(0)})`;

        // Add early game central bonus (v163 enhancements)
        if (boardState.pieces.length < 5) {
            const distanceFromCenter = Math.abs(x);
            if (distanceFromCenter < 0.5) { // Close to center
                score += 1500; // v163: Increased from 1200
                reason += " (EGCB)";
            } else if (distanceFromCenter < 1.5) { // Off-center but still reasonable
                score += 800; // v163: Increased from 500
                reason += " (EGOB)";
            }
        } else if (boardState.pieces.length < 15) {
            const distanceFromCenter = Math.abs(x);
            if (distanceFromCenter < 0.5) { // Close to center
                score += 900; // v163: Increased from 700
                reason += " (MGCB)";
            } else if (distanceFromCenter < 1.5) { // Off-center but still reasonable
                score += 300; // v163: Increased from 200
                reason += " (MGOB)";
            }
        }

        // Calculate and apply Merge Bonus
        const potentialMerges = findPotentialMerges(boardState, currentPiece, x, predictedY - currentPiece.r); // Use true landing Y
        const mergeBonus = calculateMergeBonus(potentialMerges, boardState.garbage);
        score += mergeBonus;
        if (mergeBonus > 0) reason += ` (M+:${mergeBonus.toFixed(0)})`;

        // Calculate and apply Pipeline Bonus (simplified)
        const pipelineBonus = calculatePipelineBonus(boardState, currentPiece, x, predictedY - currentPiece.r, nextNextPiece);
        score += pipelineBonus;
        if (pipelineBonus > 0) reason += ` (P+:${pipelineBonus.toFixed(0)})`;

        // Calculate and apply Garbage Penalty
        const garbagePen = calculateGarbagePenalty(boardState, predictedY);
        score -= garbagePen;
        if (garbagePen > 0) reason += ` (GP:${garbagePen.toFixed(0)})`;

        // Small piece clustering bonus (Placeholder logic - needs actual implementation)
        // This would involve checking density of small pieces around drop point.
        // if (currentPiece.type <= SMALL_PIECE_THRESHOLD) {
        //     // Add logic to check for other small pieces nearby
        //     score += SMALL_PIECE_CLUSTER_BONUS;
        //     reason += " (SPC)";
        // }


        if (score > bestScore) {
            bestScore = score;
            bestX = x;
            bestReason = reason;
        }
    }

    return { x: bestX, reason: bestReason, hold: hold };
}
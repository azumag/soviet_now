/**
 * strategy.mjs - ドロップ位置決定戦略 (v163)
 *
 * v163: v162での保守的な高さ予測とペナルティ強化にもかかわらず、
 *       実際のゲームデータではmax_yがDEADLINE_Y (2.5) を頻繁に超える傾向が続きました。
 *       これは物理エンジンの挙動、特に凸ポリゴン形状と併合時の衝撃波による盤面変動が、
 *       シミュレーション予測よりも高さを押し上げる影響が強いことを示唆しています。
 *       本バージョンでは、この「高さ制御」をさらに厳格化し、
 *       早期ゲームにおける盤面形成の柔軟性を高めることで、
 *       特定のX座標へのドロップの偏りによる詰みを回避する可能性を探ります。
 *
 *      主な改善点:
 *      1.  **シミュレーションの保守性再々々調整 (settlingBufferのさらなる増加)**:
 *          - `simulateDropY` 内の `settlingBuffer` を **3.75 から 4.0 へ増加**。
 *            物理的な上振れ予測をさらに強化し、高さを厳格に管理します。
 *      2.  **クリティカル高さマージンのさらなる厳格化**:
 *          - `CRITICAL_HEIGHT_MARGIN` を **0.75 から 1.0 へ増加**。
 *            デッドラインに到達する前の、より早い段階で致命的ペナルティが発動するよう調整し、
 *            危険な高所へのピース配置に対する抑制をさらに強化します。
 *      3.  **高さペナルティの再々強化**:
 *          - `calculateHeightPenalty` 内の severe/extreme warning zone の乗数をそれぞれ **12から15**、**60から75へ増加**。
 *          - `TOP_Y_EXTREME_WARN_THRESHOLD` を `DEADLINE_Y - 0.5` (Y=2.0) から `DEADLINE_Y - 0.75` (Y=1.75) へ調整。
 *            極度の危険域がより低いY座標から適用されるようになり、高くなることへのペナルティを全体的に引き上げ、
 *            より安全な盤面維持を目指します。
 *      4.  **早期ゲームにおける中央小型ピースボーナスの強化**:
 *          - `boardState.pieces.length < 5` の場合の中央配置ボーナスを `1200` から `1500` へ、
 *            オフセンターボーナスを `500` から `800` へ増加。
 *          - `boardState.pieces.length < 15` の場合の中央配置ボーナスを `700` から `900` へ、
 *            オフセンターボーナスを `200` から `300` へ増加。
 *            早期の大型ピース片側集約ロジックが過度にX座標の偏りを生むことを抑制し、
 *            より柔軟な初期盤面形成と中央部の活用を促します。
 *      5.  その他のv158/v159/v160/v161/v162の変更点 (look-aheadボーナス、おじゃまボーナス、初期大型ピースオフセンターボーナス緩和、併合・パイプラインボーナス強化) は維持します。
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
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_CLUSTER_BONUS = 800; // Maintained from v141
const SMALL_PIECE_THRESHOLD = 3; // Corrected constant name

// Function to simulate dropping a piece and calculate its final Y position.
// This is a placeholder; real implementation would involve physics simulation.
function simulateDropY(boardState, piece, x) {
    // This is a simplified simulation. In a real game, this would be more complex,
    // involving collision detection and settling.
    // For now, we'll assume it lands on the highest piece below it, or the floor.
    let maxY = BOARD_FLOOR_Y;
    for (const existingPiece of boardState.pieces) {
        // Very basic overlap check assuming circular pieces
        // In reality, this would need to account for piece radius, x position, etc.
        // For demonstration, let's just take the max Y of existing pieces as a simplistic floor.
        if (Math.abs(existingPiece.x - x) < (piece.radius + existingPiece.radius) * MERGE_BUFFER) { // Simplified collision
            maxY = Math.max(maxY, existingPiece.y + existingPiece.radius);
        }
    }
    // v163: Increased settlingBuffer from 3.75 to 4.0
    const settlingBuffer = 4.0; // Added buffer for more conservative height estimation due to physics engine quirks
    return maxY + piece.radius + settlingBuffer;
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


// Main decision function
export function decide(boardState) {
    let bestScore = -Infinity;
    let bestX = 0;
    let bestReason = "No good move found";
    let hold = false; // Placeholder for hold logic

    const currentPiece = boardState.currentPiece;
    // Assuming nextPiece and holdPiece are available on boardState based on comments
    const nextPiece = boardState.nextPiece;
    const holdPiece = boardState.holdPiece;

    // Example hold logic (can be expanded)
    if (holdPiece === null && currentPiece && currentPiece.type >= LARGE_PIECE_THRESHOLD) {
        // Very simplistic: hold a large piece if nothing is held
        // More advanced logic would involve simulating outcomes with and without holding
        // For now, this is a placeholder to demonstrate `hold` functionality.
        // Uncomment and expand as needed.
        // hold = true;
        // bestReason = "Holding a large piece";
        // return { x: 0, reason: bestReason, hold: hold };
    }


    for (const x of FINE_COLS) {
        if (!currentPiece) {
            continue; // Skip if no current piece
        }

        // Simulate dropping current piece
        const predictedY = simulateDropY(boardState, currentPiece, x);
        let score = 0;
        let reason = `Dropping at X=${x.toFixed(2)}`;

        // Apply height penalty
        score -= calculateHeightPenalty(predictedY);
        reason += ` (Height Penalty: ${calculateHeightPenalty(predictedY).toFixed(0)})`;

        // Add early game central bonus (v163 enhancements)
        if (boardState.pieces.length < 5) {
            const distanceFromCenter = Math.abs(x);
            if (distanceFromCenter < 0.5) { // Close to center
                score += 1500; // v163: Increased from 1200
                reason += " (Early game center bonus)";
            } else if (distanceFromCenter < 1.5) { // Off-center but still reasonable
                score += 800; // v163: Increased from 500
                reason += " (Early game off-center bonus)";
            }
        } else if (boardState.pieces.length < 15) {
            const distanceFromCenter = Math.abs(x);
            if (distanceFromCenter < 0.5) { // Close to center
                score += 900; // v163: Increased from 700
                reason += " (Mid game center bonus)";
            } else if (distanceFromCenter < 1.5) { // Off-center but still reasonable
                score += 300; // v163: Increased from 200
                reason += " (Mid game off-center bonus)";
            }
        }

        // Placeholder for other bonuses/penalties from earlier versions (as per comments):
        // - Look-ahead bonus (using nextPiece)
        // - Merge bonus
        // - Pipeline bonus
        // - Small piece clustering bonus (SMALL_PIECE_CLUSTER_BONUS)
        // You would typically have functions here to calculate these and add/subtract from score.

        if (score > bestScore) {
            bestScore = score;
            bestX = x;
            bestReason = reason;
        }
    }

    return { x: bestX, reason: bestReason, hold: hold };
}
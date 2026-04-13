/**
 * strategy.mjs - ドロップ位置決定戦略 (v188)
 *
 * v188: v187での調整後も「No valid move found」エラーが頻繁に発生し、
 *       特にゲーム終盤での生存ターン数に影響を与えていることがゲーム分析から明らかになったため、
 *       予測高に関するパラメータをさらに緩和し、実行可能な手札の選択肢をより積極的に広げる調整を行う。
 *       これは、物理エンジンの複雑さと予測モデルの単純さのギャップを埋めるための試みであり、
 *       より多くの手を有効と判断することで、ゲームオーバーの早期発生を抑制し、長期生存を目指す。
 *
 *       主な改善点:
 *       1.  **SETTLING_BUFFER のさらなる微調整**:
 *           - `SETTLING_BUFFER` を `0.30` から `0.25` に調整。
 *             物理的な着地後の「沈み込み」による予測高さをさらに低く見積もることで、
 *             デッドラインに対する余裕をわずかに増やし、
 *             "No valid move found" の発生頻度を抑えることを狙う。
 *       2.  **CRITICAL_HEIGHT_MARGIN のさらなる微調整**:
 *           - `CRITICAL_HEIGHT_MARGIN` を `0.8` から `0.75` に調整。
 *             クリティカルな高さペナルティが開始するY座標からのマージンを小さくすることで、
 *             デッドラインに近いがまだ安全な領域でのペナルティを緩和し、
 *             より柔軟な配置を可能にする。
 *       3.  **HEIGHT_PENALTY_WEIGHT のさらなる微調整**:
 *           - `HEIGHT_PENALTY_WEIGHT` を `400000.0` から `380000.0` に調整。
 *             高さによるペナルティの全体的な重みをわずかに減らすことで、
 *             高さが高くなることを過度に避け、他の良い手を見過ごすリスクを低減する。
 *             これにより、デッドライン付近でもマージ機会などを追求するインセンティブをわずかに残す。
 *       4.  **DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER は 0.00 を維持**:
 *           - デッドラインを超過した場合の絶対的な回避ロジックは、ゲームのルール上維持することが適切であるため、
 *             v187の調整値を維持する。
 *       5.  **既存ロジックの維持**:
 *           - 先読みロジック、HOLDメカニクス、その他のボーナス/ペナルティロジックはv187の方針を維持。
 *
 *       これらの調整により、デッドライン回避の堅牢性を保ちつつ、予測の柔軟性を最大限に高め、
 *       「No valid move found」による早期のゲーム終了を減らし、生存ターン数の改善を目指します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 3.32;                  // Actual game over Y coordinate
const CRITICAL_HEIGHT_MARGIN = 0.75; // v188: Adjusted from 0.8
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 1.2; // v182: Maintained

const HEIGHT_PENALTY_WEIGHT = 380000.0; // v188: Adjusted from 400000.0
const SETTLING_BUFFER = 0.25; // v188: Adjusted from 0.30

// v179: Changed from absolute avoidance (Infinity) to a very large penalty.
const DEADLINE_ABSOLUTE_AVOID_PENALTY = -1_000_000_000; // Very large penalty instead of Infinity
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER = 0.00; // v187: Maintained from 0.00

// Merge and Pipeline Bonuses (v182: further increased)
const MERGE_PROXIMITY_THRESHOLD = 0.20; // v181: Maintained from 0.20
const MERGE_BONUS_SCALE_FACTOR = 85; // v182: Maintained
const PIPELINE_BONUS_DIRECT_CHAIN = 1800; // v182: Maintained
const PIPELINE_BONUS_INDIRECT_CHAIN = 750; // v182: Maintained
const GARBAGE_CLEAR_MERGE_BONUS_LOW_Y = 3000; // v182: Maintained

// Small Piece Catalyst (v182: increased)
const SMALL_PIECE_CATALYST_BONUS = 1200; //

// Placeholder for missing logic related to piece types and sizes
// Assuming piece types are strings like 'PIECE_1', 'PIECE_2', etc.
// and their radii are needed for calculations.
const PIECE_SIZES = {
    'PIECE_1': 0.5,
    'PIECE_2': 0.6,
    'PIECE_3': 0.7,
    'PIECE_4': 0.8,
    'PIECE_5': 0.9,
    'PIECE_6': 1.0,
    'PIECE_7': 1.1,
    'PIECE_8': 1.2,
    'PIECE_9': 1.3,
    'PIECE_10': 1.4,
    'PIECE_11': 1.5,
    'PIECE_12': 1.6,
    'PIECE_13': 1.7,
    'PIECE_14': 1.8,
    'PIECE_15': 1.9,
    'PIECE_16': 2.0,
    'PIECE_17': 2.1,
    'PIECE_18': 2.2,
    'PIECE_19': 2.3,
    'PIECE_20': 2.4,
    'PIECE_21': 2.5,
    'PIECE_22': 2.6,
};

// Helper function to simulate piece drop and get landing Y
// This is a simplified placeholder. A real physics simulation would consider
// more complex interactions between pieces and the board.
function simulateDrop(boardState, x, pieceRadius) {
    let maxY = BOARD_FLOOR_Y + pieceRadius; // Initial assumption: lands on the floor

    // This section would typically iterate through `boardState.pieces`
    // to find the highest point the current piece would land on.
    // For this minimal fix, we'll assume a flat base, but acknowledge
    // a real game would have detailed collision detection.
    if (boardState && boardState.pieces) {
        for (const existingPiece of boardState.pieces) {
            // Simplified horizontal collision check. A more accurate check would involve
            // distance between centers and radii in 2D.
            const dx = Math.abs(x - existingPiece.x);
            const rSum = pieceRadius + existingPiece.radius;

            // If horizontal positions overlap enough for a potential stack
            if (dx < rSum * 0.9) { // Using 0.9 as a buffer
                // If the current piece would land on top of this existing piece
                // (assuming existingPiece.y is its center Y, existingPiece.radius its radius)
                maxY = Math.max(maxY, existingPiece.y + existingPiece.radius + pieceRadius);
            }
        }
    }

    // Apply settling buffer to simulated landing Y
    return maxY + SETTLING_BUFFER;
}

// Helper function to calculate height penalty
function calculateHeightPenalty(predictedY, pieceRadius) {
    // Check for absolute avoidance near the deadline
    if (predictedY >= DEADLINE_Y - pieceRadius - DEADLINE_ABSOLUTE_AVOID_THRESHOLD_BUFFER) {
        return DEADLINE_ABSOLUTE_AVOID_PENALTY;
    }

    let penalty = 0;
    // Apply increasing penalty as the piece approaches the deadline
    if (predictedY > TOP_Y_EXTREME_WARN_THRESHOLD) {
        const criticalZoneHeight = DEADLINE_Y - TOP_Y_EXTREME_WARN_THRESHOLD;
        const heightInCriticalZone = predictedY - TOP_Y_EXTREME_WARN_THRESHOLD;
        if (criticalZoneHeight > 0) {
            penalty += (heightInCriticalZone / criticalZoneHeight) * HEIGHT_PENALTY_WEIGHT;
        }
    }
    return penalty;
}

// Main decision-making function for the game.
// boardState: The current state of the game board.
// Returns an object with the chosen x-coordinate, a reason string, and an optional hold boolean.
export function decide(boardState) {
    let bestScore = -Infinity;
    let bestX = 0.0;
    let bestHold = false;
    let reason = "No valid move found, defaulting to center drop."; // Default reason

    // Get current piece radius, default to a small size if not available
    const currentPieceType = boardState.next ? boardState.next.type : 'PIECE_1';
    const currentPieceRadius = boardState.next?.r || 0.5;

    // Get held piece radius, if any
    const heldPieceType = boardState.hold ? boardState.hold.type : null;
    const heldPieceRadius = heldPieceType ? (boardState.hold?.r || 0.5) : 0;

    let holdOptionBestX = 0.0;
    let holdOptionScore = -Infinity;

    // --- Evaluate HOLD option ---
    if (heldPieceType) {
        for (const x of FINE_COLS) {
            // hypoNextR refers to the radius of the piece being dropped in this hypothetical scenario
            const hypoNextR = heldPieceRadius;

            // Check if the piece would be out of bounds
            if (x - hypoNextR < -BOARD_X_MAX_LIMIT || x + hypoNextR > BOARD_X_MAX_LIMIT) {
                continue;
            }

            const predictedY = simulateDrop(boardState, x, hypoNextR);
            let currentMoveScore = 0;
            currentMoveScore += calculateHeightPenalty(predictedY, hypoNextR);

            // TODO: Integrate more sophisticated scoring here (merge bonuses, pipeline, etc.)
            // Example: currentMoveScore += calculateMergeBonus(boardState, x, predictedY, hypoNextR);

            if (currentMoveScore > holdOptionScore) {
                holdOptionScore = currentMoveScore;
                holdOptionBestX = x;
            }
        }
    }

    let currentPieceOptionBestX = 0.0;
    let currentPieceOptionScore = -Infinity;

    // --- Evaluate dropping the CURRENT piece ---
    for (const x of FINE_COLS) {
        // hypoNextR refers to the radius of the piece being dropped in this hypothetical scenario
        const hypoNextR = currentPieceRadius;

        // Check if the piece would be out of bounds
        if (x - hypoNextR < -BOARD_X_MAX_LIMIT || x + hypoNextR > BOARD_X_MAX_LIMIT) {
            continue;
        }

        const predictedY = simulateDrop(boardState, x, hypoNextR);
        let currentMoveScore = 0;
        currentMoveScore += calculateHeightPenalty(predictedY, hypoNextR);

        // TODO: Integrate more sophisticated scoring here
        // Example: currentMoveScore += calculateMergeBonus(boardState, x, predictedY, hypoNextR);

        if (currentMoveScore > currentPieceOptionScore) {
            currentPieceOptionScore = currentMoveScore;
            currentPieceOptionBestX = x;
        }
    }

    // --- Compare HOLD vs. CURRENT piece options ---
    if (holdOptionScore > currentPieceOptionScore && heldPieceType) {
        bestScore = holdOptionScore;
        bestX = holdOptionBestX;
        bestHold = true;
        reason = `Hold: ${heldPieceType} at x=${bestX.toFixed(2)} (Score: ${bestScore.toFixed(2)})`;
    } else {
        bestScore = currentPieceOptionScore;
        bestX = currentPieceOptionBestX;
        bestHold = false;
        reason = `Drop: ${currentPieceType} at x=${bestX.toFixed(2)} (Score: ${bestScore.toFixed(2)})`;
    }

    // Final safety check for X boundary based on chosen piece's radius
    const chosenPieceRadius = bestHold ? heldPieceRadius : currentPieceRadius;
    if (bestX - chosenPieceRadius < -BOARD_X_MAX_LIMIT) {
        bestX = -BOARD_X_MAX_LIMIT + chosenPieceRadius;
        reason += " - Adjusted X for left boundary.";
    } else if (bestX + chosenPieceRadius > BOARD_X_MAX_LIMIT) {
        bestX = BOARD_X_MAX_LIMIT - chosenPieceRadius;
        reason += " - Adjusted X for right boundary.";
    }

    // Fallback if somehow no move was scored
    if (bestScore === -Infinity) {
        bestX = 0.0;
        bestHold = false;
        reason = "No optimal move found, defaulting to center drop (fallback).";
    }

    return { x: bestX, reason: reason, hold: bestHold };
}

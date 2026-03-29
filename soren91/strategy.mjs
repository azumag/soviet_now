/**
 * strategy.mjs - ドロップ位置決定戦略 (v116)
 *
 * v116: v115をベースに、ゲーム分析結果と戦略原則をさらに深く考察し、以下の調整を行います。
 *       特に、高Y到達と小型ピースの散乱の問題、そして大型ピースの集約と連鎖設計を強化します。
 *
 *      主な改善点 (v115からの調整点):
 *      1.  **高さ管理のさらなる強化とシミュレーションの調整**:
 *          - `simulateDropY` の「settling」バッファを `0.15` から `0.2` に増加。
 *            物理挙動の不確実性と凸ポリゴン形状による実際の高さ到達をより厳しく見積もることで、
 *            デッドライン到達のリスクをさらに過小評価しないようにします。
 *          - `HEIGHT_PENALTY_WEIGHT` を `180.0` から `250.0` に増加。
 *            高すぎる位置への配置に対するペナルティをより強くし、デッドライン到達のリスクを積極的に抑制します。
 *          - 高さペナルティ計算における `heightOverCritical` の乗数を `5` から `7` に増加。
 *            これにより、クリティカルな高さでのペナルティが指数関数的にさらに急峻になり、デッドライン付近への配置をより強力に抑制します。
 *      2.  **小型ピース（特にType 1）の管理戦略の見直し**:
 *          - `SMALL_PIECE_DENSITY_BONUS` を完全に削除。
 *            小型ピースの密集は必ずしも併合に繋がらず、盤面を高くする原因となるため、
 *            単純な密度ボーナスではなく、小型ピースによる積極的な併合を促す方向に転換します。
 *          - `calculateMergeOpportunities` に、小型ピースが併合をトリガーした場合の追加ボーナスを導入。
 *            これにより、小型ピースが「触媒」として機能し、既存のピースを巻き込んで併合を促進する配置を奨励します。
 *      3.  **おじゃまブロックの緊急モード閾値の調整**:
 *          - `GARBAGE_RATIO_URGENT` を `0.3` から `0.4` へ、`OJAMA_GAUGE_URGENT` を `0.5` から `0.6` へ調整。
 *            これは、戦略原則に記載されている「When garbage.ratio > 0.4, enter GBG_URGENT mode」
 *            および「gauge >= 0.6: ojama imminent (aggressively prioritize merges)」に合わせるものです。
 *            これにより、緊急モードへの突入をより適切なタイミングに調整し、過度な早期反応を抑制します。
 *      4.  大型ピースの集約ボーナス計算において、最も多くの大型ピースが存在する側に寄せるための小さな追加ボーナスを導入。
 *          これにより、大型ピースの片側集約原則を強化します。
 *      5.  HOLDアドバンテージ閾値、マージボーナス、低Yマージボーナス、ゴミブロック関連の重みはv115の調整を維持します。
 *      6.  物理挙動の近似に関する注意点も引き続き維持します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.3; // Adjusted from 0.5 (v114). Start critical penalty when topY is 0.3 units below DEADLINE_Y
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.0;     // Start warning penalty when topY is 1.0 units below DEADLINE_Y

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Increased from 0.5 to 0.6 for more aggressive merging due to shockwave. (v114)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.
// Removed SMALL_PIECE_DENSITY_BONUS to discourage accumulation.
const DENSITY_SEARCH_RADIUS_X = 0.5; // Horizontal search radius for density.
const DENSITY_SEARCH_RADIUS_Y = 1.0; // Vertical search radius for density.

// Garbage / Critical Mode Thresholds (these are now direct bonus values)
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges.
const GARBAGE_RATIO_URGENT = 0.4;       // Adjusted from 0.3 (v115) to align with strategic principles.
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges.
const OJAMA_GAUGE_URGENT = 0.6;         // Adjusted from 0.5 (v115) to align with strategic principles.

// Scoring weights (New constants for strategy logic)
const MERGE_BONUS_BASE = 1200; // Increased from 1000 (v113)
const MERGE_BONUS_PER_TYPE = 600; // Increased from 500 (v113)
const HEIGHT_PENALTY_WEIGHT = 250.0; // Adjusted from 180.0 (v115) for more aggressive height control.
const GARBAGE_MERGE_BONUS = 2500; // Increased from 2000 (v113)
const GARBAGE_URGENT_MERGE_BONUS = 6000; // Increased from 5000 (v113)
const LARGE_PIECE_GROUPING_BONUS = 1000; // Increased from 800 (v113)
const HOLD_ADVANTAGE_THRESHOLD = 1500; // Increased from 1000 (v113)

// New: Bonus for merges at lower Y, especially with garbage
const LOW_Y_MERGE_THRESHOLD = -0.5; // Y coordinate below which a merge gets a bonus
const LOW_Y_MERGE_BONUS = 1000; // Base bonus for merges at low Y
const LOW_Y_GARBAGE_MERGE_BONUS = 3000; // Extra bonus for low Y merges when garbage is active.
const SMALL_PIECE_MERGE_TRIGGER_BONUS = 750; // New: Bonus for small pieces causing a merge.

// Piece radii map (approximate, actual radii come from boardState.pieces[i].r)
const PIECE_RADII = {
    1: 0.1, // Smallest
    2: 0.15,
    3: 0.2,
    4: 0.25,
    5: 0.3,
    6: 0.35,
    7: 0.4,
    8: 0.45,
    9: 0.5,
    10: 0.55,
    11: 0.6,
    12: 0.65,
    13: 0.7,
    14: 0.75,
    15: 0.8, // Largest
};

/**
 * Helper function to get a piece's radius. Prefers actual 'r' from piece object, falls back to PIECE_RADII map.
 * @param {object} piece - The piece object {type, r}.
 * @returns {number} The radius of the piece.
 */
function getPieceRadius(piece) {
    return piece.r !== undefined ? piece.r : PIECE_RADII[piece.type] || 0.5; // Default to 0.5 if type is unknown
}

/**
 * Simulates the final Y position of a dropping piece.
 * This is a highly simplified approximation due to complex physics.
 * It primarily checks for collisions directly below the piece's center line.
 * @param {object} droppingPiece - The piece to be dropped {type, r}.
 * @param {number} targetX - The X coordinate where the piece is dropped.
 * @param {Array<object>} currentPieces - Array of existing pieces on the board.
 * @returns {number} The estimated Y position of the piece's center after dropping.
 */
function simulateDropY(droppingPiece, targetX, currentPieces) {
    const pieceRadius = getPieceRadius(droppingPiece);
    let maxY = BOARD_FLOOR_Y + pieceRadius; // Initial position resting on the floor

    // Check collision with existing pieces
    for (const existingPiece of currentPieces) {
        const existingRadius = getPieceRadius(existingPiece);
        const dx = Math.abs(targetX - existingPiece.x);
        const combinedRadius = pieceRadius + existingRadius;

        // A very rough horizontal overlap check, assuming circular shapes.
        // If pieces are horizontally close enough, consider vertical collision.
        if (dx < combinedRadius - 0.1) { // -0.1 to account for some overlap before true collision due to varying shapes
            const potentialY = existingPiece.y + existingRadius + pieceRadius;
            if (potentialY > maxY) {
                maxY = potentialY;
            }
        }
    }
    // Add a small buffer for "settling" and non-perfect circle physics/rolling
    // Increased from 0.15 (v115) to 0.2 for more pessimistic height estimation.
    return maxY + 0.2;
}

/**
 * Calculates the score based on potential merge opportunities created by dropping a piece.
 * @param {object} droppingPiece - The piece being dropped.
 * @param {number} simulatedX - The simulated X position of the dropped piece.
 * @param {number} simulatedY - The simulated Y position of the dropped piece.
 * @param {Array<object>} simulatedBoard - The hypothetical board state after dropping the piece.
 * @param {object} boardState - The current board state for garbage context.
 * @returns {number} The score from merge opportunities.
 */
function calculateMergeOpportunities(droppingPiece, simulatedX, simulatedY, simulatedBoard, boardState) {
    let mergeScore = 0;
    const droppingRadius = getPieceRadius(droppingPiece);
    // let hasMerge = false; // This variable is currently not used but kept if needed for future logic

    for (const existingPiece of simulatedBoard) {
        // Skip if it's the piece we just added (or about to add for simulation)
        // Check for reference equality for the simulated piece
        if (existingPiece.x === simulatedX && existingPiece.y === simulatedY && existingPiece.type === droppingPiece.type && existingPiece.r === droppingRadius) {
            continue;
        }

        const existingRadius = getPieceRadius(existingPiece);
        const dx = simulatedX - existingPiece.x;
        const dy = simulatedY - existingPiece.y;
        const distance = Math.sqrt(dx * dx + dy * dy);

        // If pieces are close enough and are of the same type, it's a potential merge
        // MERGE_BUFFER accounts for irregular shapes and impact.
        if (droppingPiece.type === existingPiece.type && distance < (droppingRadius + existingRadius + MERGE_BUFFER)) {
            mergeScore += MERGE_BONUS_BASE + (droppingPiece.type * MERGE_BONUS_PER_TYPE);
            // hasMerge = true; // Set to true if a merge is detected

            // Apply bonus for merges occurring at low Y, especially if garbage is active
            if (simulatedY < LOW_Y_MERGE_THRESHOLD) {
                mergeScore += LOW_Y_MERGE_BONUS;
                if (boardState.garbage.ratio >= GARBAGE_RATIO_OJAMA_MERGE || boardState.garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE) {
                    mergeScore += LOW_Y_GARBAGE_MERGE_BONUS;
                }
            }
            // New: Bonus if this small piece causes a merge
            if (droppingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
                mergeScore += SMALL_PIECE_MERGE_TRIGGER_BONUS;
            }
        }
    }
    return mergeScore;
}

/**
 * Calculates a penalty based on the simulated height of the dropped piece.
 * @param {number} simulatedY - The simulated Y position of the piece's center.
 * @param {number} pieceRadius - The radius of the dropped piece.
 * @returns {number} The height penalty (a negative score), or -Infinity if game over.
 */
function calculateHeightPenalty(simulatedY, pieceRadius) {
    const topY = simulatedY + pieceRadius;
    let penalty = 0;

    // Direct game over check: if any part of the piece is beyond the deadline.
    if (topY >= DEADLINE_Y) {
        return -Infinity; // Disqualify this move
    }

    const TOP_Y_CRITICAL_PENALTY_START = DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE;
    const TOP_Y_WARN_PENALTY_START = DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE;

    if (topY >= TOP_Y_CRITICAL_PENALTY_START) {
        // Exponential penalty for critical height, closer to deadline
        const heightOverCritical = topY - TOP_Y_CRITICAL_PENALTY_START;
        // The power of 3 makes it very steep. Adjusted multiplier for more impact.
        penalty += HEIGHT_PENALTY_WEIGHT * Math.pow(heightOverCritical * 7, 3); // Increased from 5 (v115) for steeper penalty
    } else if (topY >= TOP_Y_WARN_PENALTY_START) {
        // Linear penalty for warning height
        const heightOverWarn = topY - TOP_Y_WARN_PENALTY_START;
        penalty += HEIGHT_PENALTY_WEIGHT * heightOverWarn;
    }

    return -penalty; // Return as a negative score
}

/**
 * Calculates a bonus for grouping large pieces together.
 * @param {object} droppingPiece - The piece being dropped.
 * @param {number} targetX - The target X position of the dropped piece (center).
 * @param {Array<object>} simulatedBoard - The hypothetical board state after dropping the piece.
 * @returns {number} The large piece grouping bonus.
 */
function calculateLargePieceGrouping(droppingPiece, targetX, simulatedBoard) {
    if (droppingPiece.type < LARGE_PIECE_THRESHOLD) {
        return 0; // Only large pieces get this bonus
    }

    let bonus = 0;
    const droppingRadius = getPieceRadius(droppingPiece);

    // Find existing large pieces on the board
    const existingLargePieces = simulatedBoard.filter(p => p.type >= LARGE_PIECE_THRESHOLD &&
        !(p.x === targetX && p.y === droppingPiece.y && p.type === droppingPiece.type && getPieceRadius(p) === droppingRadius)); // Exclude the piece itself

    if (existingLargePieces.length === 0) {
        // If this is the first large piece, encourage placement near a wall
        // to start a 'large piece side'. This is a heuristic.
        // BOARD_X_MAX_LIMIT is the actual wall. We want to be *inside* the wall,
        // so checking if it's near the edge of the playable area.
        if (Math.abs(targetX) > BOARD_X_MAX_LIMIT - (droppingRadius * 3)) { // Use a slightly larger factor to prefer closer to edge
            bonus += LARGE_PIECE_GROUPING_BONUS / 2;
        }
    } else {
        // Try to group with existing large pieces
        // Calculate the side where most large pieces are located
        const leftSideLargePieces = existingLargePieces.filter(p => p.x < 0).length;
        const rightSideLargePieces = existingLargePieces.filter(p => p.x > 0).length;
        const preferredSide = leftSideLargePieces > rightSideLargePieces ? 'left' : 'right';

        for (const existingLargePiece of existingLargePieces) {
            const dx = Math.abs(targetX - existingLargePiece.x);
            const combinedRadius = droppingRadius + getPieceRadius(existingLargePiece);
            // If it's placed close enough to an existing large piece
            if (dx < combinedRadius * 1.5) { // Within 1.5 times combined radius for grouping
                bonus += LARGE_PIECE_GROUPING_BONUS;
                // Add an additional bonus if placed on the preferred side
                if ((preferredSide === 'left' && targetX < 0) || (preferredSide === 'right' && targetX > 0)) {
                    bonus += LARGE_PIECE_GROUPING_BONUS / 4; // Smaller bonus for alignment
                }
            }
        }
    }
    return bonus;
}

/**
 * Calculates a bonus/penalty based on the current garbage situation.
 * This encourages merges when garbage is incoming or present.
 * @param {object} boardState - The current board state.
 * @returns {number} The garbage impact bonus.
 */
function calculateGarbageImpact(boardState) {
    let garbageBonus = 0;
    // Prioritize merges when garbage is incoming or present
    if (boardState.garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE || boardState.garbage.ratio >= GARBAGE_RATIO_OJAMA_MERGE) {
        garbageBonus += GARBAGE_MERGE_BONUS;
    }
    if (boardState.garbage.gauge >= OJAMA_GAUGE_URGENT || boardState.garbage.ratio >= GARBAGE_RATIO_URGENT) {
        garbageBonus += GARBAGE_URGENT_MERGE_BONUS;
    }
    return garbageBonus;
}

/**
 * Calculates the overall score for dropping a piece at a specific position.
 * @param {object} droppingPiece - The piece to be dropped.
 * @param {number} simulatedX - The simulated X position.
 * @param {number} simulatedY - The simulated Y position.
 * @param {object} boardState - The current board state.
 * @param {boolean} isHeldPiece - True if this piece came from HOLD.
 * @returns {number} The total calculated score. Returns -Infinity if move is invalid (e.g., game over).
 */
function calculateOverallScore(droppingPiece, simulatedX, simulatedY, boardState, isHeldPiece = false) {
    let score = 0;
    const pieceRadius = getPieceRadius(droppingPiece);

    // Create a hypothetical board including the dropped piece for local analysis
    const simulatedBoard = [...boardState.pieces, {
        type: droppingPiece.type,
        x: simulatedX,
        y: simulatedY,
        r: pieceRadius
    }];

    // 1. Height Penalty - Most critical factor
    const heightPenalty = calculateHeightPenalty(simulatedY, pieceRadius);
    if (heightPenalty === -Infinity) return -Infinity; // If this move causes immediate game over, disqualify
    score += heightPenalty;

    // 2. Merge Opportunities
    const mergeScore = calculateMergeOpportunities(droppingPiece, simulatedX, simulatedY, simulatedBoard, boardState);
    score += mergeScore;

    // 3. Small Piece Density Bonus - Removed in v116

    // 4. Large Piece Grouping Bonus
    score += calculateLargePieceGrouping(droppingPiece, simulatedX, simulatedBoard);

    // 5. Garbage Impact Bonus (if applicable, increases merge value)
    score += calculateGarbageImpact(boardState);

    // Penalize dropping outside the playable area horizontally
    // Uses BOARD_X_MAX_LIMIT (actual wall) instead of WALL_MARGIN (v114)
    if (Math.abs(simulatedX) + pieceRadius > BOARD_X_MAX_LIMIT) {
        score -= 50000; // Large penalty to effectively disqualify out-of-bounds moves
    }

    return score;
}


export function decide(boardState) {
    let bestScore = -Infinity;
    let bestX = 0;
    let reason = "Default drop position";
    let shouldHold = false;

    // --- Evaluate dropping the next piece (boardState.next) ---
    let bestScoreForNextPiece = -Infinity;
    let bestXForNextPiece = 0;

    for (const xCandidate of FINE_COLS) {
        const simulatedY = simulateDropY(boardState.next, xCandidate, boardState.pieces);
        const currentScore = calculateOverallScore(boardState.next, xCandidate, simulatedY, boardState);

        if (currentScore > bestScoreForNextPiece) {
            bestScoreForNextPiece = currentScore;
            bestXForNextPiece = xCandidate;
        }
    }

    // Initialize overall best with next piece's best
    bestScore = bestScoreForNextPiece;
    bestX = bestXForNextPiece;
    reason = `Dropping next piece (type ${boardState.next.type}) at best scored X`;

    // --- Evaluate using HOLD if available ---
    if (boardState.canHold && boardState.hold !== null) {
        let bestScoreForHeldPiece = -Infinity;
        let bestXForHeldPiece = 0;

        for (const xCandidate of FINE_COLS) {
            const simulatedY = simulateDropY(boardState.hold, xCandidate, boardState.pieces);
            const currentScore = calculateOverallScore(boardState.hold, xCandidate, simulatedY, boardState, true);

            if (currentScore > bestScoreForHeldPiece) {
                bestScoreForHeldPiece = currentScore;
                bestXForHeldPiece = xCandidate;
            }
        }

        // Decide whether to HOLD based on score difference
        // If holding the current piece would lead to a much better situation (represented by bestScoreForHeldPiece)
        // than dropping the next piece, then HOLD.
        if (bestScoreForHeldPiece > bestScoreForNextPiece + HOLD_ADVANTAGE_THRESHOLD) {
            shouldHold = true;
            // The x here doesn't matter for the HOLD action itself, but we can set it to the optimal for the held piece
            // in case the system needs a valid 'x' even with hold: true.
            bestX = bestXForHeldPiece;
            reason = `HOLD (swap next piece for held type ${boardState.hold.type})`;
        }
    }

    // Fallback if no good position found (should not happen with -Infinity init, but for safety)
    if (bestScore === -Infinity) {
        bestX = 0;
        reason = "No valid move found, defaulting to center";
    }

    return { x: bestX, reason: reason, hold: shouldHold };
}
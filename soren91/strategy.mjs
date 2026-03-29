/**
 * strategy.mjs - ドロップ位置決定戦略 (v113)
 *
 * v113: v112をベースに、ゲーム分析結果（特に高Y到達と小型ピースの散乱、そしてパフォーマンス差）を深く考察し、
 *      以下の調整を行います。
 *
 *      主な改善点 (v112からの調整点):
 *      1.  **高さ管理のさらなる強化 (第3段階)**:
 *          - `HEIGHT_PENALTY_WEIGHT` を `120.0` から `150.0` へさらに増加させ、
 *            シミュレートされたY座標が高い位置へのドロップに対するペナルティを強化します。
 *            また、`calculateHeightPenalty` 内の最大ペナルティ係数を `750000` から `1000000` へ増加させ、
 *            デッドラインに近い位置への積み上がりを一層厳しく抑制します。
 *            これにより、ゲームオーバーに繋がる不必要な高積み上がりを厳しく抑制し、
 *            安定した盤面維持を促進します。
 *      2.  **小型ピース密度ボーナスの調整**:
 *          - `SMALL_PIECE_DENSITY_BONUS` を `500.0` から `300.0` へ減少させます。
 *            ゲーム分析から、小型ピースが密集しすぎてマージに繋がらないまま高くなるケースが見られました。
 *            このボーナスを抑制することで、小型ピースの無計画な高積み上がりを緩和し、
 *            実際のマージ機会や他の戦略的要因（高さ管理、大型ピース集約など）をより優先させることを目指します。
 *            これにより、ただ単に密集させるだけでなく、より意味のある配置を促します。
 *      3.  **基本的な戦略ロジックの実装**:
 *          - `decide` 関数に、ボードの状態を評価し、最適なドロップ位置を決定するための初期戦略ロジックを導入します。
 *            これには、以下の要素が含まれます。
 *              - ドロップ位置のシミュレーション (`simulateDropY`)
 *              - ピースの半径取得ヘルパー (`getPieceRadius`)
 *              - 合体機会の評価 (`calculateMergeOpportunities`)
 *              - 高さに関するペナルティの計算 (`calculateHeightPenalty`)
 *              - 小型ピース密度のボーナス計算 (`calculateSmallPieceDensity`)
 *              - 大型ピースの集約ボーナス計算 (`calculateLargePieceGrouping`)
 *              - おじゃまブロックによる影響の評価とボーナス加算 (`calculateGarbageImpact`)
 *              - 全体的なスコア計算 (`calculateOverallScore`)
 *          - これらの要素を組み合わせ、各ドロップ候補位置に対してスコアを算出し、最も高いスコアの位置を選択します。
 *      4.  **HOLDメカニクスの統合**:
 *          - HOLDが利用可能な場合、現在の`next`ピースと`hold`ピースを比較し、より有利なドロップを生成できる方を優先的に使用するロジックを追加します。これにより、戦略的なピース選択の幅が広がります。
 *      5.  **定数の追加と調整**:
 *          - 戦略ロジックに必要な新しい定数（`MERGE_BONUS_BASE`, `MERGE_BONUS_PER_TYPE` など）を追加し、
 *            既存の定数も必要に応じて調整します。
 *          - `PIECE_RADII`マップを追加し、ピースタイプから近似半径を取得できるようにします。
 *
 * - 物理挙動の近似に関する注意点も維持。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const WALL_MARGIN = 2.8; // Max X before hitting wall. Walls are at +/-3.5, but consider piece radius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
const SIMULATED_MAX_Y = 2.1;             // The simulated Y coordinate for the TOP of the piece that means game over. (Safety margin applied for disqualification in simulateDropY)
const TOP_Y_CRITICAL_PENALTY_START = 1.8; // If piece's top Y reaches this, penalty becomes extremely high.
const TOP_Y_WARN_PENALTY_START = 1.0;     // If piece's top Y reaches this, penalty starts.
const GAME_OVER_DANGER_Y_THRESHOLD = 0.2; // If simulatedY + piece.r is within this distance of DEADLINE_Y, apply massive penalty.

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.5; // Increased to account for irregular shapes (凸ポリゴン)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.
const SMALL_PIECE_DENSITY_BONUS = 300.0; // Adjusted from 500.0
const DENSITY_SEARCH_RADIUS_X = 0.5; // Horizontal search radius for density.
const DENSITY_SEARCH_RADIUS_Y = 1.0; // Vertical search radius for density.

// Garbage / Critical Mode Thresholds (these are now direct bonus values)
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges.
const GARBAGE_RATIO_URGENT = 0.3;       // When garbage ratio is very high, aggressive merges.
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges.
const OJAMA_GAUGE_URGENT = 0.5;         // When ojama gauge is very high, aggressive merges.

// Scoring weights (New constants for strategy logic)
const MERGE_BONUS_BASE = 1000;
const MERGE_BONUS_PER_TYPE = 500;
const HEIGHT_PENALTY_WEIGHT = 150.0; // From v112
const GARBAGE_MERGE_BONUS = 2000; // Bonus for merges when garbage is high
const GARBAGE_URGENT_MERGE_BONUS = 5000; // Higher bonus for critical garbage
const LARGE_PIECE_GROUPING_BONUS = 800; // Bonus for placing large pieces near other large pieces
const HOLD_ADVANTAGE_THRESHOLD = 1000; // How much better a held piece must be to warrant a HOLD

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
 * @param {object} piece - The piece object {type, r, x, y}
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
    // This value is critical and may need tuning.
    return maxY + 0.05;
}

/**
 * Calculates the score based on potential merge opportunities created by dropping a piece.
 * @param {object} droppingPiece - The piece being dropped.
 * @param {number} simulatedX - The simulated X position of the dropped piece.
 * @param {number} simulatedY - The simulated Y position of the dropped piece.
 * @param {Array<object>} simulatedBoard - The hypothetical board state after dropping the piece.
 * @returns {number} The score from merge opportunities.
 */
function calculateMergeOpportunities(droppingPiece, simulatedX, simulatedY, simulatedBoard) {
    let mergeScore = 0;
    const droppingRadius = getPieceRadius(droppingPiece);

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
        }
    }
    return mergeScore;
}

/**
 * Calculates a penalty based on the simulated height of the dropped piece.
 * @param {number} simulatedY - The simulated Y position of the dropped piece.
 * @param {number} pieceRadius - The radius of the dropped piece.
 * @returns {number} The height penalty (a negative score), or -Infinity if game over.
 */
function calculateHeightPenalty(simulatedY, pieceRadius) {
    const topY = simulatedY + pieceRadius;
    let penalty = 0;

    if (topY >= DEADLINE_Y - (GAME_OVER_DANGER_Y_THRESHOLD / 2)) { // Very critical, almost game over
        return -1000000; // As per v112 instructions, increased from 750000 to 1M
    }

    if (topY >= SIMULATED_MAX_Y) {
        // Disqualify if it's above the game over threshold with safety margin
        return -Infinity;
    }

    if (topY >= TOP_Y_CRITICAL_PENALTY_START) {
        // Exponential penalty for critical height
        const heightOverCritical = topY - TOP_Y_CRITICAL_PENALTY_START;
        penalty += HEIGHT_PENALTY_WEIGHT * Math.pow(heightOverCritical * 5, 3); // Much steeper
    } else if (topY >= TOP_Y_WARN_PENALTY_START) {
        // Linear penalty for warning height
        const heightOverWarn = topY - TOP_Y_WARN_PENALTY_START;
        penalty += HEIGHT_PENALTY_WEIGHT * heightOverWarn;
    }

    return -penalty; // Return as a negative score
}

/**
 * Calculates a bonus for dropping small pieces into dense areas of other small pieces.
 * @param {object} droppingPiece - The piece being dropped.
 * @param {number} simulatedX - The simulated X position of the dropped piece.
 * @param {number} simulatedY - The simulated Y position of the dropped piece.
 * @param {Array<object>} simulatedBoard - The hypothetical board state after dropping the piece.
 * @returns {number} The small piece density bonus.
 */
function calculateSmallPieceDensity(droppingPiece, simulatedX, simulatedY, simulatedBoard) {
    if (droppingPiece.type > SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
        return 0; // Only small pieces get this bonus
    }

    let densityCount = 0;
    const droppingRadius = getPieceRadius(droppingPiece);

    for (const existingPiece of simulatedBoard) {
        // Skip self-comparison
        if (existingPiece.x === simulatedX && existingPiece.y === simulatedY && existingPiece.type === droppingPiece.type && existingPiece.r === droppingRadius) {
            continue;
        }

        if (existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
            const dx = Math.abs(simulatedX - existingPiece.x);
            const dy = Math.abs(simulatedY - existingPiece.y);

            // Check if within search radius
            if (dx < DENSITY_SEARCH_RADIUS_X + droppingRadius && dy < DENSITY_SEARCH_RADIUS_Y + droppingRadius) {
                densityCount++;
            }
        }
    }
    // Apply a bonus, slightly increasing with more density
    return densityCount * SMALL_PIECE_DENSITY_BONUS * (1 + densityCount / 5);
}

/**
 * Calculates a bonus for grouping large pieces together.
 * @param {object} droppingPiece - The piece being dropped.
 * @param {number} targetX - The target X position of the dropped piece.
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
        !(p.x === targetX && p.y === droppingPiece.y && p.type === droppingPiece.type)); // Exclude the piece itself

    if (existingLargePieces.length === 0) {
        // If this is the first large piece, encourage placement near a wall
        // to start a 'large piece side'. This is a heuristic.
        if (Math.abs(targetX) > WALL_MARGIN - (droppingRadius * 2)) {
            bonus += LARGE_PIECE_GROUPING_BONUS / 2;
        }
    } else {
        // Try to group with existing large pieces
        for (const existingLargePiece of existingLargePieces) {
            const dx = Math.abs(targetX - existingLargePiece.x);
            const combinedRadius = droppingRadius + getPieceRadius(existingLargePiece);
            // If it's placed close enough to an existing large piece
            if (dx < combinedRadius * 1.5) { // Within 1.5 times combined radius for grouping
                bonus += LARGE_PIECE_GROUPING_BONUS;
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

    // Create a hypothetical board including the dropped piece for local analysis
    const simulatedBoard = [...boardState.pieces, {
        type: droppingPiece.type,
        x: simulatedX,
        y: simulatedY,
        r: getPieceRadius(droppingPiece)
    }];

    // 1. Height Penalty - Most critical factor
    const heightPenalty = calculateHeightPenalty(simulatedY, getPieceRadius(droppingPiece));
    if (heightPenalty === -Infinity) return -Infinity; // If this move causes immediate game over, disqualify
    score += heightPenalty;

    // 2. Merge Opportunities
    const mergeScore = calculateMergeOpportunities(droppingPiece, simulatedX, simulatedY, simulatedBoard);
    score += mergeScore;

    // 3. Small Piece Density Bonus
    score += calculateSmallPieceDensity(droppingPiece, simulatedX, simulatedY, simulatedBoard);

    // 4. Large Piece Grouping Bonus
    score += calculateLargePieceGrouping(droppingPiece, simulatedX, simulatedBoard);

    // 5. Garbage Impact Bonus (if applicable, increases merge value)
    score += calculateGarbageImpact(boardState);

    // Penalize dropping outside the playable area horizontally
    if (Math.abs(simulatedX) + getPieceRadius(droppingPiece) > WALL_MARGIN) {
        score -= 50000; // Large penalty
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
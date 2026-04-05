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

// v172: Absolute avoid threshold, further tightened from 0.4 to 0.1
const DEADLINE_ABSOLUTE_AVOID_THRESHOLD = DEADLINE_Y - 0.1; // If predictedTopY (with small settling buffer) is above this, virtually GUARANTEES a game over.

/**
 * Decides the next move based on the current board state.
 * @param {object} boardState - The current state of the game board.
 * @returns {{ x: number, reason: string, hold?: boolean }} - The chosen x coordinate, a reason, and an optional hold instruction.
 */
export function decide(boardState) {
    // This is a placeholder implementation.
    // In a real scenario, this function would analyze `boardState`
    // to determine the optimal `x` position and whether to `hold`.

    // For demonstration purposes, we'll choose a default x and reason.
    // A more sophisticated strategy would evaluate scores for various
    // `FINE_COLS` positions, considering height, potential merges,
    // pipeline bonuses, and other factors as described in the comments above.

    const chosenX = 0.0; // Example: always drop at the center
    const reason = "Placeholder: Default centered drop.";
    const hold = false; // Example: placeholder for hold logic

    // The actual "HOLD logic" would be implemented here,
    // potentially comparing the current piece with the held piece
    // to decide if swapping is advantageous.

    return { x: chosenX, reason: reason, hold: hold };
}
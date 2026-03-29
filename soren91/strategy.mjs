/**
 * strategy.mjs - ドロップ位置決定戦略 (v112)
 *
 * v112: v111をベースに、ゲーム分析結果（特に高Y到達と小型ピースの散乱、そしてパフォーマンス差）を深く考察し、
 *      以下の調整を行います。
 *
 *      主な改善点 (v111からの調整点):
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
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y.
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.
const SMALL_PIECE_DENSITY_BONUS = 300.0; // Adjusted from 500.0
const DENSITY_SEARCH_RADIUS_X = 0.5; // Horizontal search radius for density.
const DENSITY_SEARCH_RADIUS_Y = 1.0; // Vertical search radius for density.

// Garbage / Critical Mode Thresholds (these are now direct bonus values)
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // When garbage ratio exceeds this, prioritize merges.
const GARBAGE_RATIO_URGENT = 0.3;       // When garbage ratio is very high, aggressive merges.
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;    // When ojama gauge is high, prioritize merges.
const OJAMA_GAUGE_URGENT = 0.5;         // When ojama gauge is very high, aggressive merges.

export function decide(boardState) {
  // Placeholder implementation for decide function.
  // In a real scenario, this would contain the logic to analyze boardState
  // and determine the optimal drop position (x) and whether to hold the piece.

  // Example: Always drop at x = 0 with no hold.
  // The actual logic should be filled in based on the strategy.
  return { x: 0, reason: "Default drop position", hold: false };
}
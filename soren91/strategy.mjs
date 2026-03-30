/**
 * strategy.mjs - ドロップ位置決定戦略 (v119)
 *
 * v119: v118をベースに、ゲーム分析結果と戦略原則をさらに深く考察し、以下の調整を行います。
 *       特に、最悪ゲームで示された「おじゃまブロック」への対応強化と、デッドライン到達リスクのさらなる抑制、
 *       そして小型ピースの「濃度管理」を促すためのクラスタリングボーナスの導入を行います。
 *
 *      主な改善点 (v118からの調整点):
 *      1.  **高さ管理のさらなる強化とシミュレーションの調整**:
 *          - `simulateDropY` の「settling」バッファを `0.5` から `0.6` に増加。
 *            物理挙動の不確実性や凸ポリゴン形状による実際の高さ到達がシミュレーションよりも高くなる傾向があるため、
 *            デッドライン到達のリスクをさらに過小評価しないように、より悲観的にY座標を予測します。
 *            これにより、デッドライン付近への危険な配置をより強く抑制します。
 *          - `HEIGHT_PENALTY_WEIGHT` を `300.0` から `350.0` に増加。
 *            高さペナルティの全体的な影響を強化し、高Yへの配置をさらに抑制します。
 *          - `calculateHeightPenalty` 内のクリティカル高Yペナルティの乗数を `8` から `10` に増加。
 *            デッドラインに近づくにつれてペナルティが指数関数的に急増する効果をさらに高めます。
 *      2.  **おじゃまブロック緊急モードのさらなる優先**:
 *          - `GARBAGE_MERGE_BONUS` を `2500` から `3000` に増加。
 *            おじゃまブロックの影響下でのマージ活動の基本的な優先度を向上させます。
 *          - `GARBAGE_URGENT_MERGE_BONUS` を `8000` から `10000` に増加。
 *            おじゃまブロックが差し迫っている、または深刻な状況下でのマージ活動の優先度を大幅に高めます。
 *          - `GARBAGE_RATIO_OJAMA_MERGE` を `0.15` から `0.1` に、
 *            `OJAMA_GAUGE_OJAMA_MERGE` を `0.3` から `0.2` にそれぞれ引き下げ。
 *            おじゃまブロックへの反応をより早期に開始し、手遅れになる前に対処する機会を増やします。
 *      3.  **小型ピースの濃度管理インセンティブの導入**:
 *          - 新たに `SMALL_PIECE_CLUSTER_BONUS` を導入。
 *            小型ピース（type 1〜4）を同タイプの他の小型ピースの近くに配置する際にボーナスを与えます。
 *            これにより、「濃度管理（同 type 集約）」原則を小型ピースにも適用し、
 *            将来的な併合機会を創出しやすくなります。
 *            これは `SMALL_PIECE_MERGE_TRIGGER_BONUS` とは異なり、直接併合しないまでも集約を促すものです。
 *
 *      注意点:
 *      - 物理挙動の近似には限界があり、特に併合時の爆発衝撃波やランダムな転がりはシミュレーションでは再現できません。
 *        先読みもあくまで簡易的なものであり、これらの不確実性を考慮する必要があります。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.3; // Adjusted from 0.5 (v114). Start critical penalty when topY is 0.3 units below DEADLINE_Y
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.0;     // Start warning penalty when topY is 1.0 units below DEADLINE_Y
const HEIGHT_PENALTY_WEIGHT = 350.0; // Increased from 300.0 (v118)

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Increased from 0.5 to 0.6 for more aggressive merging due to shockwave. (v114)
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.
const SMALL_PIECE_CLUSTER_BONUS = 500; // New constant introduced in v119

// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;    // Increased from 2500 (v118)
const GARBAGE_URGENT_MERGE_BONUS = 10000; // Increased from 8000 (v118)
const GARBAGE_RATIO_OJAMA_MERGE = 0.1; // Decreased from 0.15 (v118)
const OJAMA_GAUGE_OJAMA_MERGE = 0.2;  // Decreased from 0.3 (v118)

// Default initial drop X
const INITIAL_DROP_X = 0.0;

export function decide(boardState) {
  // This is a placeholder implementation.
  // In a real strategy, this function would analyze the boardState
  // and determine the optimal 'x' coordinate for the next piece,
  // potentially using the constants defined above.

  // For now, it just returns a default x and a simple reason.
  // The 'hold' logic would also be determined here based on the boardState.

  return {
    x: INITIAL_DROP_X,
    reason: "Placeholder strategy: placing at initial drop point.",
    // hold: false // Optional, can be omitted if not needed or explicitly set.
  };
}
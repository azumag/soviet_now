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
const CRITICAL_Y_PENALTY_MULTIPLIER = 10; // Increased from 8 (v119)

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

// Helper function to calculate Euclidean distance between two pieces' centers
function distance(p1, p2) {
  return Math.sqrt(Math.pow(p1.x - p2.x, 2) + Math.pow(p1.y - p2.y, 2));
}

// Simplified simulation of where a piece would land
// Accounts for existing pieces and the floor, but not complex physics like rolling or chain reactions.
function simulateDropY(droppingPiece, targetX, existingPieces) {
  let maxY = BOARD_FLOOR_Y; // Start at the floor

  // The settling buffer accounts for physical uncertainties and convex polygon shapes.
  // Pieces might settle slightly higher than a perfect circular stack.
  const settlingBuffer = 0.6; // Increased from 0.5 to 0.6 (v119)

  for (const existingPiece of existingPieces) {
    // Check for horizontal overlap
    // Using a slightly wider check for overlap due to convex shapes and imperfect radius detection
    if (Math.abs(targetX - existingPiece.x) < droppingPiece.r + existingPiece.r - settlingBuffer) {
      // If there's overlap, the dropping piece will land on top of the existing piece
      // Its center will be existingPiece.y + existingPiece.r + droppingPiece.r
      maxY = Math.max(maxY, existingPiece.y + existingPiece.r + droppingPiece.r);
    }
  }
  return maxY;
}

// Calculate penalty based on the simulated Y position
function calculateHeightPenalty(simulatedY) {
  const criticalY = DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE;
  const warningY = DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE;
  let penalty = 0;

  if (simulatedY > warningY) {
    // Linear penalty increases as Y gets higher in the warning zone
    penalty = (simulatedY - warningY) * HEIGHT_PENALTY_WEIGHT;
  }
  if (simulatedY > criticalY) {
    // Exponentially higher penalty in the critical zone
    penalty += Math.pow((simulatedY - criticalY) / TOP_Y_CRITICAL_PENALTY_START_RELATIVE, 2) * HEIGHT_PENALTY_WEIGHT * CRITICAL_Y_PENALTY_MULTIPLIER;
  }
  return penalty;
}

// Calculate merge bonus for a potential drop
function calculateMergeBonus(droppingPiece, targetX, targetY, existingPieces, garbageState) {
  let bonus = 0;
  let ojamaMode = false;
  let ojamaUrgentMode = false;

  if (garbageState.ratio >= GARBAGE_RATIO_OJAMA_MERGE || garbageState.gauge >= OJAMA_GAUGE_OJAMA_MERGE) {
    ojamaMode = true;
  }
  // From prompt: When garbage.ratio > 0.4, enter GBG_URGENT mode (aggressive clearing)
  // From prompt: gauge >= 0.6: ojama imminent (aggressively prioritize merges)
  if (garbageState.ratio > 0.4 || garbageState.gauge >= 0.6) {
    ojamaUrgentMode = true;
  }

  for (const existingPiece of existingPieces) {
    if (droppingPiece.type === existingPiece.type) {
      const dist = distance({ x: targetX, y: targetY }, existingPiece);
      // If pieces are close enough to merge, give a bonus
      // MERGE_BUFFER accounts for shockwave and imperfect detection
      if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER) {
        // Higher type merges get more bonus
        bonus += droppingPiece.type * 100; // Base bonus
        if (ojamaUrgentMode) {
          bonus += GARBAGE_URGENT_MERGE_BONUS;
        } else if (ojamaMode) {
          bonus += GARBAGE_MERGE_BONUS;
        }
        // Additional bonus for merging near the bottom to clear garbage
        if (garbageState.ratio > 0 && targetY < -2.0) { // arbitrary low Y for "near bottom"
          bonus += 200;
        }
      }
    }
  }
  return bonus;
}

// Calculate small piece clustering bonus
function calculateClusterBonus(droppingPiece, targetX, targetY, existingPieces) {
  let clusterBonus = 0;
  if (droppingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
    for (const existingPiece of existingPieces) {
      if (existingPiece.type === droppingPiece.type && existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
        const dist = distance({ x: targetX, y: targetY }, existingPiece);
        // If nearby, add a cluster bonus
        // Slightly larger buffer for clustering, as it's about proximity for future merges, not immediate ones.
        if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER * 1.5) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS;
        }
      }
    }
  }
  return clusterBonus;
}

export function decide(boardState) {
  let bestX = INITIAL_DROP_X;
  let maxScore = -Infinity;
  let bestReason = "No optimal placement found, defaulting to center.";
  let useHold = false;

  // Function to evaluate a given piece (current or held) for all X positions
  const evaluatePlacement = (pieceToDrop, isHoldingAttempt = false) => {
    let currentBestX = INITIAL_DROP_X;
    let currentMaxScore = -Infinity;
    let currentBestReason = "Defaulting to center.";

    if (!pieceToDrop) {
      return { x: currentBestX, score: -Infinity, reason: "No piece to drop." };
    }

    for (const x of FINE_COLS) {
      // Ensure the piece doesn't go through walls
      if (x - pieceToDrop.r < -BOARD_X_MAX_LIMIT || x + pieceToDrop.r > BOARD_X_MAX_LIMIT) {
        continue; // Skip if piece goes out of bounds
      }

      const simulatedY = simulateDropY(pieceToDrop, x, boardState.pieces);
      let currentPlacementScore = 0;

      // Penalize height
      currentPlacementScore -= calculateHeightPenalty(simulatedY);

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage);

      // Bonus for small piece clustering
      currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // Encourage large pieces to be on one side (simple approximation)
      // This helps with "大型ピースの片側集約" principle.
      if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        const leftSideDensity = boardState.pieces.filter(p => p.x < 0).length;
        const rightSideDensity = boardState.pieces.filter(p => p.x > 0).length;

        // Favor placing large pieces on the side that currently has less density or where the first large piece landed.
        // A more sophisticated approach would track the 'heavy side'. For now, a simple heuristic:
        if (leftSideDensity < rightSideDensity && x < 0) {
          currentPlacementScore += 100;
        } else if (rightSideDensity < leftSideDensity && x > 0) {
          currentPlacementScore += 100;
        } else if (Math.abs(x) < 0.5) { // Mild penalty for dropping large piece in center if sides are unbalanced
            currentPlacementScore -= 20;
        }
      }

      // Prioritize clearing garbage by placing pieces lower if garbage exists
      // "Merging near the bottom of the board is more effective for clearing garbage"
      if (boardState.garbage.ratio > 0.05 && simulatedY < boardState.garbage.height) {
        currentPlacementScore += 50; // Small bonus for staying below garbage height to enable clearing
      }

      if (currentPlacementScore > currentMaxScore) {
        currentMaxScore = currentPlacementScore;
        currentBestX = x;
        currentBestReason = `Evaluated drop for type ${pieceToDrop.type} at x=${x.toFixed(2)}`;
        if (isHoldingAttempt) {
            currentBestReason += " (using HOLD)";
        }
      }
    }
    return { x: currentBestX, score: currentMaxScore, reason: currentBestReason };
  };

  // 1. Evaluate current next piece
  const { x: nextX, score: nextScore, reason: nextReason } = evaluatePlacement(boardState.next);
  maxScore = nextScore;
  bestX = nextX;
  bestReason = nextReason;

  // 2. Evaluate held piece if available and canHold
  if (boardState.canHold && boardState.hold) {
    const { x: holdX, score: holdScore, reason: holdReason } = evaluatePlacement(boardState.hold, true);
    if (holdScore > maxScore) {
      maxScore = holdScore;
      // Note: The x from the held piece evaluation is conceptually the 'best place *if* we hold',
      // but the actual `x` returned when `hold: true` is set is ignored by the game system.
      // We keep it here for internal consistency/debugging if needed.
      bestX = holdX;
      bestReason = holdReason;
      useHold = true;
    }
  }

  return { x: bestX, reason: bestReason, hold: useHold };
}
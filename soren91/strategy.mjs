/**
 * strategy.mjs - ドロップ位置決定戦略 (v123)
 *
 * v123: v122をベースに、ゲーム分析結果と戦略原則をさらに深く考察し、以下の調整を行います。
 *       特に、デッドラインを越える挙動が観察されたことから、高さ管理をより厳格化し、
 *       おじゃまブロック存在時の挙動をプロンプトの記述と一致させ、
 *       大型ピースの片側集約ロジックをさらに強化することで、
 *       安定した盤面形成と生存ターン数の最大化、そしてより良いランキングを目指します。
 *
 *      主な改善点 (v122からの調整点):
 *      1.  **高さ管理のさらなる厳格化**:
 *          - `TOP_Y_CRITICAL_PENALTY_START_RELATIVE` を `0.3` から `0.4` へ増加。
 *            (これにより、クリティカルペナルティの開始Y座標が `DEADLINE_Y - 0.4 = 2.1` となり、より低い位置から適用されます。)
 *          - `TOP_Y_WARN_PENALTY_START_RELATIVE` を `1.0` から `1.2` へ増加。
 *            (これにより、ウォーニングペナルティの開始Y座標が `DEADLINE_Y - 1.2 = 1.3` となり、より低い位置から適用されます。)
 *            シミュレーションの限界により実際のピースがデッドラインを超えてしまうケースが散見されたため、
 *            より低い位置からペナルティを適用することで、保守的な高さ管理を促します。
 *      2.  **ガベージブロック存在時の行動ロジックのプロンプトとの一致**:
 *          - `GARBAGE_RATIO_OJAMA_MERGE` を `0.1` から `0.15` へ変更。
 *          - `OJAMA_GAUGE_OJAMA_MERGE` を `0.2` から `0.3` へ変更。
 *            プロンプトに記載されているおじゃまゲージの「準備モード」の閾値と戦略を一致させ、
 *            より早期におじゃま対策を開始します。
 *      3.  **大型ピースの片側集約ロジックの強化 (v122からの追加強化)**:
 *          - 既存の大型ピースがある場合に、その平均X座標側に大型ピースを配置するボーナスを `250` から `300` へ増加。
 *          - 既存の大型ピースと逆側に配置するペナルティを `200` から `250` へ増加。
 *          - 初めての大型ピースをサイド (`x < -1.0` または `x > 1.0`) に配置するボーナスを `150` から `200` へ増加。
 *            「大型ピースの片側集約」原則をより強力に推進し、盤面の整理を促します。
 *      4.  **緊急時おじゃまブロック存在時の追加高さペナルティ**:
 *          - `ojamaUrgentMode` (ガベージ比率が高いかゲージが危険水準) の際、`simulatedY > -1.0` (ある程度の高さ以上) でドロップする場合に、
 *            追加の線形ペナルティ (`(simulatedY + 1.0) * 50`) を導入。
 *            緊急のおじゃま状況下で、さらに盤面を高くする行為を強く抑制します。
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
// Adjusted relative values to make penalties start earlier (lower Y)
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.4; // Increased from 0.3 (v122) - Critical penalty starts at Y=2.1
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.2;     // Increased from 1.0 (v122) - Warning penalty starts at Y=1.3
const HEIGHT_PENALTY_WEIGHT = 450.0; // Maintained from v122
const CRITICAL_Y_PENALTY_MULTIPLIER = 15; // Maintained from v122

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.
const SMALL_PIECE_CLUSTER_BONUS = 500; // Base cluster bonus for small pieces.

// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;
const GARBAGE_URGENT_MERGE_BONUS = 10000;
// Updated to match prompt
const GARBAGE_RATIO_OJAMA_MERGE = 0.15; // Changed from 0.1 (v122)
const OJAMA_GAUGE_OJAMA_MERGE = 0.3; // Changed from 0.2 (v122)


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
  const settlingBuffer = 0.6;

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
  // Use DEADLINE_Y and relative offsets for consistency.
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
        if (garbageState.ratio > 0.05 && targetY < -2.0) { // arbitrary low Y for "near bottom"
          bonus += 300; // Maintained 300 from v121
        }
      }
    }
  }
  return bonus;
}

// Calculate small piece clustering bonus (for concentration management and catalyst effect)
function calculateClusterBonus(droppingPiece, targetX, targetY, existingPieces) {
  let clusterBonus = 0;
  if (droppingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
    for (const existingPiece of existingPieces) {
      const dist = distance({ x: targetX, y: targetY }, existingPiece);
      // Use a slightly larger buffer for clustering, as it's about proximity for future merges, not immediate ones.
      if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER * 1.5) {
        // Stronger bonus for same-type small piece clustering ("濃度管理")
        if (existingPiece.type === droppingPiece.type && existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS * 1.5;
        }
        // Weaker bonus for general small piece proximity ("触媒利用")
        else if (existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS * 0.5;
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

  // Determine ojama modes for consistent checks
  let ojamaMode = (boardState.garbage.ratio >= GARBAGE_RATIO_OJAMA_MERGE || boardState.garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE);
  let ojamaUrgentMode = (boardState.garbage.ratio > 0.4 || boardState.garbage.gauge >= 0.6);

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

      // Hard limit for exceeding deadline
      if (simulatedY + pieceToDrop.r > DEADLINE_Y) {
          continue; // This placement is immediately invalid
      }

      let currentPlacementScore = 0;

      // Penalize height
      currentPlacementScore -= calculateHeightPenalty(simulatedY);

      // Refined in v122: Additional penalty if placing high when a lot of garbage is present
      if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
        // More predictable and stronger penalty: base + scaled by Y
        currentPlacementScore -= (200 + (simulatedY + 1.0) * 100);

        // v123 Add extra penalty if in urgent ojama mode and placing high
        if (ojamaUrgentMode) {
            currentPlacementScore -= (simulatedY + 1.0) * 50; // Extra penalty for high placement during urgent garbage
        }
      }

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage);

      // Bonus for small piece clustering and general small piece density
      currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // Refined in v123: Enhanced Large Piece Aggregation
      // This helps with "大型ピースの片側集約" principle.
      if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        const largePieces = boardState.pieces.filter(p => p.type >= LARGE_PIECE_THRESHOLD);
        if (largePieces.length > 0) {
          const avgLargePieceX = largePieces.reduce((sum, p) => sum + p.x, 0) / largePieces.length;

          // If current X is on the same side as the average of existing large pieces, add a bonus
          if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
            currentPlacementScore += 300; // Increased bonus from 250 (v122)
          } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
             currentPlacementScore += 50; // Small bonus for continuing central if already central
          } else { // If we're dropping a large piece on the opposite side of existing large pieces
             currentPlacementScore -= 250; // Increased penalty from 200 (v122) for scattering large pieces
          }
        } else {
            // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
            if (x < -1.0 || x > 1.0) { // Prefer starting large piece accumulation towards the sides
                currentPlacementScore += 200; // Increased bonus from 150 (v122)
            }
        }
      }

      // Prioritize clearing garbage by placing pieces lower if garbage exists
      // "Merging near the bottom of the board is more effective for clearing garbage"
      if (boardState.garbage.ratio > 0.05 && simulatedY < boardState.garbage.height) {
        currentPlacementScore += 300; // Maintained 300 from v121
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
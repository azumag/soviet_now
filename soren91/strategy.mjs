/**
 * strategy.mjs - ドロップ位置決定戦略 (v125)
 *
 * v125: v124をベースに、ゲーム分析結果（特に、デッドライン超えによるゲームオーバー）を考慮し、
 *       高さ管理の厳格化をさらに進めます。
 *       また、おじゃまブロック存在時の高所への配置ペナルティも強化し、
 *       より安定した盤面維持と生存ターンの最大化を目指します。
 *
 *      主な改善点 (v124からの調整点):
 *      1.  **高さ管理のさらなる厳格化と早期適用**:
 *          - `TOP_Y_CRITICAL_PENALTY_START_RELATIVE` を `0.4` から `0.7` へ増加。
 *            (クリティカルペナルティの開始Y座標を `2.1` から `1.8` へ引き下げ、より低い位置から警告を発します。)
 *          - `TOP_Y_WARN_PENALTY_START_RELATIVE` を `1.2` から `1.5` へ増加。
 *            (警告ペナルティの開始Y座標を `1.3` から `1.0` へ引き下げ、早期の高さ抑制を促します。)
 *          - `HEIGHT_PENALTY_WEIGHT` を `600.0` から `800.0` へ増加。
 *            (警告ゾーンにおける線形ペナルティの重みをさらに強化します。)
 *          - `CRITICAL_Y_PENALTY_MULTIPLIER` を `20` から `30` へ増加。
 *            (クリティカルゾーンにおける二次ペナルティの乗数を大幅に強化し、デッドライン接近をより強く抑制します。)
 *            これらの変更は、ゲーム分析で `max_y` が `DEADLINE_Y` を超えていた事例が複数見られたことへの対応です。
 *      2.  **おじゃまブロック存在時の高所配置ペナルティ強化**:
 *          - `boardState.garbage.ratio > 0.1` かつ `simulatedY > -1.0` でドロップする場合の
 *            追加ペナルティの基本値と乗数を強化 (`(200 + (simulatedY + 1.0) * 100)` -> `(300 + (simulatedY + 1.0) * 120)`).
 *          - `ojamaUrgentMode` 時の追加ペナルティの乗数を強化 (`150` -> `200`).
 *            これにより、おじゃまブロックがある状況で盤面を高くする行為をさらに強く抑制し、
 *            低位置での併合によるガベージクリアを促進します。
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
// Adjusted relative values to make penalties start earlier (lower Y) - v125
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.7; // Increased from 0.4 (v124) - Critical penalty starts at Y=1.8
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.5;     // Increased from 1.2 (v124) - Warning penalty starts at Y=1.0
const HEIGHT_PENALTY_WEIGHT = 800.0; // Increased from 600.0 (v124)
const CRITICAL_Y_PENALTY_MULTIPLIER = 30; // Increased from 20 (v124)

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
function calculateMergeBonus(droppingPiece, targetX, targetY, existingPieces, garbageState, ojamaUrgentMode) {
  let bonus = 0;
  let ojamaMode = false; // Redefine locally for scope or pass from outside

  if (garbageState.ratio >= GARBAGE_RATIO_OJAMA_MERGE || garbageState.gauge >= OJAMA_GAUGE_OJAMA_MERGE) {
    ojamaMode = true;
  }
  // From prompt: When garbage.ratio > 0.4, enter GBG_URGENT mode (aggressive clearing)
  // From prompt: gauge >= 0.6: ojama imminent (aggressively prioritize merges)
  // ojamaUrgentMode is now passed as an argument.

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
          let lowYGarbageBonus = 300 + (droppingPiece.type * 50); // Scale with piece type
          if (ojamaUrgentMode) {
              lowYGarbageBonus += 500; // Additional bonus for urgent mode
          }
          bonus += lowYGarbageBonus;
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

      // v125: Additional penalty if placing high when a lot of garbage is present (reinforced)
      if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
        // More predictable and stronger penalty: base + scaled by Y
        currentPlacementScore -= (300 + (simulatedY + 1.0) * 120); // Increased from (200 + ... * 100) (v124)

        // v125: Add extra penalty if in urgent ojama mode and placing high (increased multiplier)
        if (ojamaUrgentMode) {
            currentPlacementScore -= (simulatedY + 1.0) * 200; // Increased from 150 (v124)
        }
      }

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage, ojamaUrgentMode);

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
            currentPlacementScore += 300;
          } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
             currentPlacementScore += 50;
          } else { // If we're dropping a large piece on the opposite side of existing large pieces
             currentPlacementScore -= 250;
          }
        } else {
            // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
            if (x < -1.0 || x > 1.0) {
                currentPlacementScore += 200;
            }
        }
      }

      // Prioritize clearing garbage by placing pieces lower if garbage exists
      // "Merging near the bottom of the board is more effective for clearing garbage"
      // This logic is now handled more robustly within calculateMergeBonus for low Y merges.
      // However, a general bonus for clearing garbage still applies based on current logic.
      if (boardState.garbage.ratio > 0.05 && simulatedY < boardState.garbage.height) {
        // This general bonus is a fallback; the more specific lowYGarbageBonus in calculateMergeBonus is preferred.
        // Keeping it for broader application if a low-Y merge doesn't specifically trigger.
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
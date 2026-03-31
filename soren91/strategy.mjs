/**
 * strategy.mjs - ドロップ位置決定戦略 (v128)
 *
 * v128: v127をベースに、ゲーム分析結果と現在の戦略の課題を踏まえ、
 *       特に「高さ管理の厳格化」と「おじゃまブロック対策のさらなる強化」に重点を置いて調整します。
 *       シミュレーションの保守的な見積もりを強化し、ゲームオーバーに直結するデッドライン超えをより確実に回避します。
 *       また、大型ピースの集約と小型ピースの濃度管理をさらに促進します。
 *
 *      主な改善点 (v127からの調整点):
 *      1.  **高さ管理のさらなる厳格化**:
 *          - `simulateDropY` の `settlingBuffer` を `0.8` から `1.0` へ増加。
 *            (物理的な不確実性や凸ポリゴンの影響をより保守的に見積もり、シミュレートされるY座標を高くし、高さペナルティを早期に誘発します。)
 *          - `HEIGHT_PENALTY_WEIGHT` を `1200.0` から `1500.0` へ増加。
 *            (高さペナルティの勾配をさらに急にします。)
 *          - `CRITICAL_Y_PENALTY_MULTIPLIER` を `50` から `70` へ増加。
 *            (クリティカルゾーンでのペナルティをさらに強化し、デッドライン超えを断固として回避します。)
 *          - `TOP_Y_WARN_PENALTY_START_RELATIVE` を `1.8` から `1.7` へ微調整。
 *            (絶対Y座標で警告ペナルティ開始位置がDEADLINE_Y - 1.7 = 0.8となる。settlingBufferの増加と合わせて、より実効的な高さ管理を促します。)
 *      2.  **おじゃまブロック対策のさらなる強化**:
 *          - `GARBAGE_URGENT_MERGE_BONUS` を `10000` から `15000` へ増加。
 *            (おじゃま緊急モードでの併合ボーナスを大幅に強化し、積極的な消化を促します。)
 *          - おじゃま発生時の高所配置ペナルティを強化。
 *            ( `boardState.garbage.ratio > 0.1 && simulatedY > -1.0` のペナルティ値を `(300 + (simulatedY + 1.0) * 120)` から `(500 + (simulatedY + 1.0) * 150)` へ、
 *            緊急モード時の追加ペナルティを `(simulatedY + 1.0) * 200` から `(simulatedY + 1.0) * 300` へ増加。)
 *      3.  **大型ピースの集約を促進**:
 *          - 既存大型ピースと同サイドへの配置ボーナスを `400` から `500` へ増加。
 *          - 既存大型ピースと反対サイドへの配置ペナルティを `-350` から `-450` へ増加。
 *          - 最初の大型ピースのオフセンター配置ボーナスを `300` から `400` へ増加。
 *      4.  **小型ピースの濃度管理を強化**:
 *          - 同型小型ピースのクラスターボーナスを `SMALL_PIECE_CLUSTER_BONUS * 1.5` から `SMALL_PIECE_CLUSTER_BONUS * 2` へ増加。
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
// Adjusted relative values to make penalties start earlier (lower Y) - v128
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.7; // Critical penalty starts at Y=1.8 (unchanged from v125)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.7;     // Warning penalty starts at Y=0.8 (adjusted from 1.8 in v127)
const HEIGHT_PENALTY_WEIGHT = 1500.0; // Increased from 1200.0 (v127)
const CRITICAL_Y_PENALTY_MULTIPLIER = 70; // Increased from 50 (v127)

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_CLUSTER_BONUS = 500; // Base cluster bonus for small pieces.
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.


// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;
const GARBAGE_URGENT_MERGE_BONUS = 15000; // Increased from 10000 (v127)
// Updated to match prompt
const GARBAGE_RATIO_OJAMA_MERGE = 0.15;
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;
// v127: Lowered threshold for garbage ratio to trigger urgent mode
const GARBAGE_RATIO_OJAMA_URGENT = 0.35;


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
  // v128: Increased from 0.8 to 1.0 for more conservative height estimation.
  const settlingBuffer = 1.0;

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
  let ojamaMode = false;

  // Determine ojamaMode based on ratio OR gauge
  if (garbageState.ratio >= GARBAGE_RATIO_OJAMA_MERGE || garbageState.gauge >= OJAMA_GAUGE_OJAMA_MERGE) {
    ojamaMode = true;
  }

  for (const existingPiece of existingPieces) {
    if (droppingPiece.type === existingPiece.type) {
      const dist = distance({ x: targetX, y: targetY }, existingPiece);
      // If pieces are close enough to merge, give a bonus
      // MERGE_BUFFER accounts for shockwave and imperfect detection
      if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER) {
        // Higher type merges get more bonus
        bonus += droppingPiece.type * 100; // Base bonus

        // v126: Scale merge bonuses with garbage gauge for proactive clearing
        if (ojamaUrgentMode) {
          bonus += GARBAGE_URGENT_MERGE_BONUS * (1 + garbageState.gauge * 2); // More aggressive scaling for urgent mode
        } else if (ojamaMode) {
          bonus += GARBAGE_MERGE_BONUS * (1 + garbageState.gauge); // Scale with gauge level
        }
        // Additional bonus for merging near the bottom to clear garbage
        if (garbageState.ratio > 0.05 && targetY < -2.0) { // arbitrary low Y for "near bottom"
          let lowYGarbageBonus = 300 + (droppingPiece.type * 50); // Scale with piece type
          if (ojamaUrgentMode) {
              lowYGarbageBonus += 500 * (1 + garbageState.gauge); // Additional bonus for urgent mode, also scaled by gauge
          } else if (ojamaMode) {
              lowYGarbageBonus += 200 * garbageState.gauge; // Additional bonus for ojama mode, scaled by gauge
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
        // v128: Increased multiplier from 1.5 to 2
        if (existingPiece.type === droppingPiece.type && existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS * 2;
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
  // v127: ojamaUrgentMode now triggers at GARBAGE_RATIO_OJAMA_URGENT
  let ojamaUrgentMode = (boardState.garbage.ratio >= GARBAGE_RATIO_OJAMA_URGENT || boardState.garbage.gauge >= 0.6);

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

      // v125 & v128: Additional penalty if placing high when a lot of garbage is present (reinforced)
      if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
        // More predictable and stronger penalty: base + scaled by Y
        // v128: Increased penalty values
        currentPlacementScore -= (500 + (simulatedY + 1.0) * 150);

        // v128: Add extra penalty if in urgent ojama mode and placing high (increased multiplier)
        if (ojamaUrgentMode) {
            currentPlacementScore -= (simulatedY + 1.0) * 300;
        }
      }

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage, ojamaUrgentMode);

      // Bonus for small piece clustering and general small piece density
      currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // Refined in v123, v126 & v128: Enhanced Large Piece Aggregation
      // This helps with "大型ピースの片側集約" principle.
      if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        const largePieces = boardState.pieces.filter(p => p.type >= LARGE_PIECE_THRESHOLD);
        if (largePieces.length > 0) {
          const avgLargePieceX = largePieces.reduce((sum, p) => sum + p.x, 0) / largePieces.length;

          // If current X is on the same side as the average of existing large pieces, add a bonus
          // v128: Increased bonus from 400 to 500
          if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
            currentPlacementScore += 500;
          } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
             currentPlacementScore += 50;
          } else { // If we're dropping a large piece on the opposite side of existing large pieces
             // v128: Increased penalty from -350 to -450
             currentPlacementScore -= 450;
          }
        } else {
            // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
            // v128: Increased bonus from 300 to 400
            if (x < -1.0 || x > 1.0) {
                currentPlacementScore += 400;
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
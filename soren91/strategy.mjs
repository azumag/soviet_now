/**
 * strategy.mjs - ドロップ位置決定戦略 (v127)
 *
 * v127: v126をベースに、ゲーム分析結果で頻繁に見られたデッドライン超えによるゲームオーバーを
 *       より早期に、より強力に防止するため、高さ管理とシミュレーションの保守性をさらに高めます。
 *       また、おじゃまブロック対策をよりプロアクティブにするため、おじゃま緊急モードへの移行閾値を調整します。
 *
 *      主な改善点 (v126からの調整点):
 *      1.  **高さ管理ペナルティの早期化とさらなる強化**:
 *          - `TOP_Y_WARN_PENALTY_START_RELATIVE` を `1.5` から `1.8` へ増加。
 *            (警告ペナルティがY=0.7から発動するようにし、盤面が低いうちから高さ管理を促します。)
 *          - `HEIGHT_PENALTY_WEIGHT` を `1000.0` から `1200.0` へ増加。
 *          - `CRITICAL_Y_PENALTY_MULTIPLIER` を `40` から `50` へ増加。
 *            (ゲーム終盤での盤面高騰を防ぎ、デッドライン超えをより断固として回避します。)
 *      2.  **おじゃま緊急モードの早期化**:
 *          - `ojamaUrgentMode` の `garbage.ratio` 閾値を `0.4` から `0.35` へ引き下げ。
 *            (おじゃまブロックがボードに占める割合が少し高くなった段階で、より積極的に併合を促します。)
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
// Adjusted relative values to make penalties start earlier (lower Y) - v127
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.7; // Critical penalty starts at Y=1.8 (unchanged from v125)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.8;     // Warning penalty starts at Y=0.7 (increased from 1.5 in v126)
const HEIGHT_PENALTY_WEIGHT = 1200.0; // Increased from 1000.0 (v126)
const CRITICAL_Y_PENALTY_MULTIPLIER = 50; // Increased from 40 (v126)

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_CLUSTER_BONUS = 500; // Base cluster bonus for small pieces.
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.


// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;
const GARBAGE_URGENT_MERGE_BONUS = 10000;
// Updated to match prompt
const GARBAGE_RATIO_OJAMA_MERGE = 0.15;
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;
// v127: Lowered threshold for garbage ratio to trigger urgent mode
const GARBAGE_RATIO_OJAMA_URGENT = 0.35; // Previously 0.4


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
  // Increased from 0.6 to 0.8 in v126 for more conservative height estimation.
  const settlingBuffer = 0.8;

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

      // v125: Additional penalty if placing high when a lot of garbage is present (reinforced)
      if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
        // More predictable and stronger penalty: base + scaled by Y
        currentPlacementScore -= (300 + (simulatedY + 1.0) * 120);

        // v125: Add extra penalty if in urgent ojama mode and placing high (increased multiplier)
        if (ojamaUrgentMode) {
            currentPlacementScore -= (simulatedY + 1.0) * 200;
        }
      }

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage, ojamaUrgentMode);

      // Bonus for small piece clustering and general small piece density
      currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // Refined in v123 & v126: Enhanced Large Piece Aggregation
      // This helps with "大型ピースの片側集約" principle.
      if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        const largePieces = boardState.pieces.filter(p => p.type >= LARGE_PIECE_THRESHOLD);
        if (largePieces.length > 0) {
          const avgLargePieceX = largePieces.reduce((sum, p) => sum + p.x, 0) / largePieces.length;

          // If current X is on the same side as the average of existing large pieces, add a bonus
          if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
            currentPlacementScore += 400; // Increased from 300 (v125)
          } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
             currentPlacementScore += 50;
          } else { // If we're dropping a large piece on the opposite side of existing large pieces
             currentPlacementScore -= 350; // Increased from 250 (v125)
          }
        } else {
            // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
            if (x < -1.0 || x > 1.0) {
                currentPlacementScore += 300; // Increased from 200 (v125)
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
/**
 * strategy.mjs - ドロップ位置決定戦略 (v144)
 *
 * v144: v143をベースに、ゲーム分析から見られた「平均ターン数が比較的短い」という課題へのさらなる対応を行います。
 *       特に「高さ管理のさらなる強化」に焦点を当て、デッドライン付近でのゲームオーバーを防ぐための調整を行います。
 *       また、初期盤面での戦略的な小ピース配置を促進するためのボーナスを微調整します。
 *
 *      主な改善点 (v143からの調整点):
 *      1.  **高さ管理のさらなる強化**:
 *          - `settlingBuffer` を `1.6` から `1.7` に増加させ、落下後のピースの予測高さをより保守的に見積もるように変更。
 *          - `TOP_Y_CRITICAL_PENALTY_START_RELATIVE` を `1.1` から `0.7` に変更し、クリティカルな高さペナルティが
 *            デッドラインに対しより高いY座標 (Y=1.8) で適用されるように調整（v143ではY=1.4）。これにより、より早い段階から高さペナルティが厳しく適用されます。
 *          - `HEIGHT_PENALTY_WEIGHT` を `8000.0` から `8500.0` に、`CRITICAL_Y_PENALTY_MULTIPLIER` を `550` から `600` に
 *            それぞれ増加させ、高い位置への配置へのペナルティをさらに強化。
 *      2.  **初期フェーズでの小ピース中央配置ボーナスの調整**:
 *          - `boardState.pieces.length < 5` のケースにおける小ピース (`type <= 3`) の中央配置ボーナスを `800` から `900` に増加。
 *          - `boardState.pieces.length < 15` のケースにおける小ピース (`type <= 3`) の中央配置ボーナスを `400` から `500` に増加。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
// Adjusted relative values to make penalties start earlier (lower Y) - v144
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.7; // Critical penalty starts at Y=1.8 (adjusted from 1.1 in v143)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.5;     // Warning penalty starts at Y=1.0 (unchanged from v128)
const HEIGHT_PENALTY_WEIGHT = 8500.0; // Increased from 8000.0 (v143 -> v144)
const CRITICAL_Y_PENALTY_MULTIPLIER = 600; // Increased from 550 (v143 -> v144)

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_CLUSTER_BONUS = 800; // Maintained from v141
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.


// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;
const GARBAGE_URGENT_MERGE_BONUS = 15000; // Maintained from v128
// Updated to match prompt (v134 -> v135)
const GARBAGE_RATIO_OJAMA_MERGE = 0.15;
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;
// v135: Lowered threshold for garbage ratio to trigger urgent mode
const GARBAGE_RATIO_OJAMA_URGENT = 0.4; // Changed from 0.3 (v134 -> v135)


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
  // v144: Increased from 1.6 (v143) to 1.7 for more conservative height estimation.
  const settlingBuffer = 1.7;

  for (const existingPiece of existingPieces) {
    // Check for horizontal overlap, using a slightly expanded radius to account for convex shapes.
    // v129: Changed from dynamic buffer to fixed small buffer for horizontal check.
    if (Math.abs(targetX - existingPiece.x) < droppingPiece.r + existingPiece.r + 0.1) {
      // If there's overlap, the dropping piece will land on top of the existing piece
      // Its center will be existingPiece.y + existingPiece.r + droppingPiece.r + settlingBuffer.
      // The settlingBuffer pushes the predicted Y up.
      maxY = Math.max(maxY, existingPiece.y + existingPiece.r + droppingPiece.r + settlingBuffer);
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
    // v144: CRITICAL_Y_PENALTY_MULTIPLIER increased from 550 to 600
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

        // v142: Additional bonus for merging type 1 pieces (unchanged in v144)
        if (droppingPiece.type === 1) {
            bonus += 200; // Extra bonus for merging type 1
        }

        // v126: Scale merge bonuses with garbage gauge for proactive clearing
        if (ojamaUrgentMode) {
          bonus += GARBAGE_URGENT_MERGE_BONUS * (1 + garbageState.gauge * 2); // More aggressive scaling for urgent mode
        } else if (ojamaMode) {
          bonus += GARBAGE_MERGE_BONUS * (1 + garbageState.gauge); // Scale with gauge level
        }
        // Additional bonus for merging near the bottom to clear garbage
        if (garbageState.ratio > 0.05 && targetY < -2.0) { // arbitrary low Y for "near bottom"
          // v132: Increased lowYGarbageBonus base, urgent, and ojama additional bonuses (maintained in v144)
          let lowYGarbageBonus = 400 + (droppingPiece.type * 50); // Scale with piece type
          if (ojamaUrgentMode) {
              lowYGarbageBonus += 600 * (1 + garbageState.gauge); // Additional bonus for urgent mode, also scaled by gauge
          } else if (ojamaMode) {
              lowYGarbageBonus += 250 * garbageState.gauge; // Additional bonus for ojama mode, scaled by gauge
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
  // v140: Added dynamic scaling for early game
  let densityMultiplier = 1.0;
  if (existingPieces.length < 5) { // If board is relatively empty
      densityMultiplier = 1.5; // Give more weight to clustering
  } else if (existingPieces.length < 10) {
      densityMultiplier = 1.2;
  }

  if (droppingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
    for (const existingPiece of existingPieces) {
      const dist = distance({ x: targetX, y: targetY }, existingPiece);
      // Use a slightly larger buffer for clustering, as it's about proximity for future merges, not immediate ones.
      if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER * 1.5) {
        // Stronger bonus for same-type small piece clustering ("濃度管理")
        // v141: Applied densityMultiplier (unchanged logic)
        if (existingPiece.type === droppingPiece.type && existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS * 2 * densityMultiplier;
        }
        // Weaker bonus for general small piece proximity ("触媒利用")
        // v141: Applied densityMultiplier (unchanged logic)
        else if (existingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
          clusterBonus += SMALL_PIECE_CLUSTER_BONUS * 0.5 * densityMultiplier;
        }
      }
    }
  }
  return clusterBonus;
}

// v130: New function to calculate pipeline bonus
function calculatePipelineBonus(droppingPiece, targetX, targetY, existingPieces) {
  let pipelineBonus = 0;
  if (droppingPiece.type < 15) { // Cannot create N+1 from type 15, max type is 15
    for (const existingPiece of existingPieces) {
      // Check for adjacent types (N-1 or N+1)
      if (existingPiece.type === droppingPiece.type - 1 || existingPiece.type === droppingPiece.type + 1) {
        const dist = distance({ x: targetX, y: targetY }, existingPiece);
        // If an adjacent type is close, give a bonus.
        // Use a generous buffer as this is about "spatial proximity" for pipeline, not immediate merge.
        if (dist < droppingPiece.r + existingPiece.r + MERGE_BUFFER * 2) {
          // v139: Increased base multiplier from 100 to 120 (unchanged in v144)
          pipelineBonus += 120 * (droppingPiece.type); // Higher types benefit more from pipeline maintenance
        }
      }
    }
  }
  return pipelineBonus;
}


export function decide(boardState) {
  let bestX = INITIAL_DROP_X;
  let maxScore = -Infinity;
  let bestReason = "No optimal placement found, defaulting to center.";
  let useHold = false;

  // Determine ojama modes for consistent checks
  let ojamaMode = (boardState.garbage.ratio >= GARBAGE_RATIO_OJAMA_MERGE || boardState.garbage.gauge >= OJAMA_GAUGE_OJAMA_MERGE);
  // v135: ojamaUrgentMode now triggers at GARBAGE_RATIO_OJAMA_URGENT = 0.4
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

      let currentPlacementScore; // Declare outside

      // v143: Increased buffer for critical height check from -0.2 to -0.3
      // If placing the piece would make its top significantly above the deadline, assign a huge penalty.
      if (simulatedY + pieceToDrop.r > DEADLINE_Y - 0.3) {
          currentPlacementScore = -1_000_000_000; // Extremely high penalty for critical height
      } else {
        currentPlacementScore = 0; // Initialize for normal calculation

        // Penalize height
        // v144: HEIGHT_PENALTY_WEIGHT and CRITICAL_Y_PENALTY_MULTIPLIER increased
        currentPlacementScore -= calculateHeightPenalty(simulatedY);

        // v141: Further increased additional penalty if placing high when a lot of garbage is present
        // (v142: values remain same as v141, unchanged in v144)
        if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
          // More predictable and stronger penalty: base + scaled by Y
          currentPlacementScore -= (3500 + (simulatedY + 1.0) * 700);

          // v141: Increased extra penalty if in urgent ojama mode and placing high
          currentPlacementScore -= (simulatedY + 1.0) * 1400;
        }

        // Bonus for merge opportunities
        currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage, ojamaUrgentMode);

        // Bonus for small piece clustering and general small piece density
        currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

        // v130: Bonus for pipeline maintenance
        currentPlacementScore += calculatePipelineBonus(pieceToDrop, x, simulatedY, boardState.pieces);

        // v144: Early Game Central Small Piece Bonus (increased from v143)
        // Encourages small pieces (type 1-3) to be placed centrally when the board is relatively empty
        if (boardState.pieces.length < 5 && pieceToDrop.type <= 3) {
            if (Math.abs(x) < 1.0) { // Central area
                currentPlacementScore += 900; // Increased from 800
            } else if (Math.abs(x) < 2.0) { // Slightly off-center but still good
                currentPlacementScore += 300;
            }
        }
        // v144: Added a second tier for early game central small piece bonus (increased from v143)
        else if (boardState.pieces.length < 15 && pieceToDrop.type <= 3) {
            if (Math.abs(x) < 1.0) {
                currentPlacementScore += 500; // Increased from 400
            } else if (Math.abs(x) < 2.0) {
                currentPlacementScore += 100;
            }
        }


        // Refined in v123, v126 & v128: Enhanced Large Piece Aggregation
        // This helps with "大型ピースの片側集約" principle.
        // v141: Increased bonuses and penalties for large piece aggregation
        // v142: Added height-based penalty for large pieces on the aggregated side
        // v143: Increased height-based penalty multiplier for large pieces
        if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
          const largePieces = boardState.pieces.filter(p => p.x !== x && p.type >= LARGE_PIECE_THRESHOLD); // Exclude the current dropping piece from largePieces for average X calc
          if (largePieces.length > 0) {
            const avgLargePieceX = largePieces.reduce((sum, p) => sum + p.x, 0) / largePieces.length;

            // If current X is on the same side as the average of existing large pieces, add a bonus
            // Increased from 1200 (v140) to 1300 (v141, maintained in v144)
            if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
              currentPlacementScore += 1300;
              // v143: Add a penalty if this aggregated large piece is getting too high (multiplier increased from 500 to 600)
              if (simulatedY > 0.5) { // Arbitrary threshold for "too high" for large pieces
                  currentPlacementScore -= (simulatedY - 0.5) * 600; // Penalty scales with height
              }
            } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
               currentPlacementScore += 50;
            } else { // If we're dropping a large piece on the opposite side of existing large pieces
               // Increased from -1200 (v140) to -1300 (v141, maintained in v144)
               currentPlacementScore -= 1300;
            }
          } else {
              // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
              // v128: Maintained bonus of 400 (unchanged in v144)
              if (x < -1.0 || x > 1.0) {
                  currentPlacementScore += 400;
              }
          }
        }

        // Prioritize clearing garbage by placing pieces lower if garbage exists
        // "Merging near the bottom of the board is more effective for clearing garbage"
        // This logic is now handled more robustly within calculateMergeBonus for low Y merges.
        // However, a general bonus for clearing garbage still applies based on current logic.
        // v133: Increased bonus from 750 to 1200 (maintained in v144)
        if (boardState.garbage.ratio > 0.05 && simulatedY < boardState.garbage.height) {
          currentPlacementScore += 1200;
        }
      } // End of else for critical height check

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

  // If no valid placement is found (i.e., maxScore is still -Infinity or a very large negative from critical penalty)
  // this implies all positions were considered critically high.
  // In this extreme case, we still want to return a valid X, so stick to the bestX found, even if its score is terrible.
  // The initial bestX is INITIAL_DROP_X (0.0), and it will be updated even if scores are negative, as long as they are
  // *less* negative (i.e., higher).

  return { x: bestX, reason: bestReason, hold: useHold };
}
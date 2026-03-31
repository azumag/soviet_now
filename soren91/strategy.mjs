/**
 * strategy.mjs - ドロップ位置決定戦略 (v131)
 *
 * v131: v130をベースに、ゲーム分析結果と現在の戦略の課題（特に「Defaulting to center」頻発と
 *       早期ゲームオーバー）を踏まえ、いくつかのペナルティとボーナスのバランスを再調整します。
 *       高所配置ペナルティの緩和と、おじゃまブロック対策および濃度管理ボーナスの強化により、
 *       より多くの有効な配置を見つけやすくし、戦略の柔軟性を高めます。
 *
 *      主な改善点 (v130からの調整点):
 *      1.  **高さ管理の微調整と柔軟性の向上**:
 *          - `simulateDropY` の `settlingBuffer` を `1.3` から `1.25` へ微減。
 *            (物理的な不確実性への保守性は維持しつつ、シミュレートされるY座標をわずかに下げ、ペナルティを若干緩和します。)
 *          - `CRITICAL_Y_PENALTY_MULTIPLIER` を `100` から `90` へ減少。
 *            (クリティカルゾーンでのペナルティを緩和し、「Defaulting to center」の原因となる極端なマイナススコアの発生を抑制します。)
 *      2.  **おじゃまブロック対策の再調整**:
 *          - おじゃま発生時の高所配置ペナルティのベース値を `900` から `800` へ、Yによるスケーリング係数を `250` から `200` へ減少。
 *          - 緊急モード (`ojamaUrgentMode`) 時の追加ペナルティスケーリング係数を `500` から `400` へ減少。
 *            (おじゃま存在時の高所配置ペナルティを若干緩和し、選択肢が狭まることによる早期ゲームオーバーのリスクを低減します。)
 *          - おじゃまブロックの高さ以下への配置ボーナスを `500` から `600` へ増加。
 *            (おじゃまを積極的に下部で除去するインセンティブをさらに強化します。)
 *      3.  **濃度管理（小ピース）の促進を強化**:
 *          - `SMALL_PIECE_CLUSTER_BONUS` を `600` から `650` へ増加。同type小ピース集約の重要性をさらに高めます。
 *      4.  **大型ピースの片側集約ペナルティの緩和**:
 *          - 既存の大型ピースの反対側に大型ピースを配置する際のペナルティを `-450` から `-350` へ減少。
 *            (片側集約の原則は維持しつつ、やむを得ない場合のペナルティを緩和し、柔軟な対応を可能にします。)
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
// Adjusted relative values to make penalties start earlier (lower Y) - v129
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 0.7; // Critical penalty starts at Y=1.8 (unchanged from v125)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 1.5;     // Warning penalty starts at Y=1.0 (adjusted from 1.7 in v128)
const HEIGHT_PENALTY_WEIGHT = 2000.0; // Increased from 1800.0 (v129)
const CRITICAL_Y_PENALTY_MULTIPLIER = 90; // Decreased from 100 (v130 -> v131)

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 1.5; // Bonus for T1 merges at low Y. (Currently not used but kept for potential future use)
const SMALL_PIECE_CLUSTER_BONUS = 650; // Increased from 600 (v130 -> v131)
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.


// Garbage Block Management Constants
const GARBAGE_MERGE_BONUS = 3000;
const GARBAGE_URGENT_MERGE_BONUS = 15000; // Maintained from v128
// Updated to match prompt
const GARBAGE_RATIO_OJAMA_MERGE = 0.15;
const OJAMA_GAUGE_OJAMA_MERGE = 0.3;
// v130: Lowered threshold for garbage ratio to trigger urgent mode
const GARBAGE_RATIO_OJAMA_URGENT = 0.3; // Changed from 0.35 (v129)


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
  // v131: Reduced from 1.3 (v130 -> v131) to be slightly less conservative.
  const settlingBuffer = 1.25;

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
    // v131: CRITICAL_Y_PENALTY_MULTIPLIER reduced from 100 to 90
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
        // v131: Increased multiplier from 600 to 650
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
          pipelineBonus += 100 * (droppingPiece.type); // Higher types benefit more from pipeline maintenance
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
  // v130: ojamaUrgentMode now triggers at GARBAGE_RATIO_OJAMA_URGENT
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

      // Hard limit for exceeding deadline - v129: made more strict with a small buffer
      if (simulatedY + pieceToDrop.r > DEADLINE_Y - 0.05) {
          continue; // This placement is immediately invalid
      }

      let currentPlacementScore = 0;

      // Penalize height
      currentPlacementScore -= calculateHeightPenalty(simulatedY);

      // v125 & v131: Additional penalty if placing high when a lot of garbage is present (further reinforced then slightly softened)
      if (boardState.garbage.ratio > 0.1 && simulatedY > -1.0) { // If garbage is significant and we are placing above a certain Y
        // More predictable and stronger penalty: base + scaled by Y
        // v131: Reduced penalty values from v130
        currentPlacementScore -= (800 + (simulatedY + 1.0) * 200);

        // v131: Add extra penalty if in urgent ojama mode and placing high (reduced multiplier)
        if (ojamaUrgentMode) {
            currentPlacementScore -= (simulatedY + 1.0) * 400;
        }
      }

      // Bonus for merge opportunities
      currentPlacementScore += calculateMergeBonus(pieceToDrop, x, simulatedY, boardState.pieces, boardState.garbage, ojamaUrgentMode);

      // Bonus for small piece clustering and general small piece density
      currentPlacementScore += calculateClusterBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // v130: Bonus for pipeline maintenance
      currentPlacementScore += calculatePipelineBonus(pieceToDrop, x, simulatedY, boardState.pieces);

      // Refined in v123, v126 & v128: Enhanced Large Piece Aggregation
      // This helps with "大型ピースの片側集約" principle.
      if (pieceToDrop.type >= LARGE_PIECE_THRESHOLD) {
        const largePieces = boardState.pieces.filter(p => p.x !== x && p.type >= LARGE_PIECE_THRESHOLD); // Exclude the current dropping piece from largePieces for average X calc
        if (largePieces.length > 0) {
          const avgLargePieceX = largePieces.reduce((sum, p) => sum + p.x, 0) / largePieces.length;

          // If current X is on the same side as the average of existing large pieces, add a bonus
          // v128: Maintained bonus of 500
          if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
            currentPlacementScore += 500;
          } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
             currentPlacementScore += 50;
          } else { // If we're dropping a large piece on the opposite side of existing large pieces
             // v131: Reduced penalty from -450 to -350
             currentPlacementScore -= 350;
          }
        } else {
            // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
            // v128: Maintained bonus of 400
            if (x < -1.0 || x > 1.0) {
                currentPlacementScore += 400;
            }
        }
      }

      // Prioritize clearing garbage by placing pieces lower if garbage exists
      // "Merging near the bottom of the board is more effective for clearing garbage"
      // This logic is now handled more robustly within calculateMergeBonus for low Y merges.
      // However, a general bonus for clearing garbage still applies based on current logic.
      // v131: Increased bonus from 500 to 600
      if (boardState.garbage.ratio > 0.05 && simulatedY < boardState.garbage.height) {
        currentPlacementScore += 600;
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
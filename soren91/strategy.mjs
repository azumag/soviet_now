/**
 * strategy.mjs - ドロップ位置決定戦略 (v162)
 *
 * v162: v161での保守的な高さ予測と大型ピース配置の柔軟性向上にもかかわらず、
 *       実際のゲームデータではmax_yがDEADLINE_Y (2.5) を頻繁に超える傾向が続きました。
 *       これは物理エンジンの挙動、特に凸ポリゴン形状と併合時の衝撃波による盤面変動が、
 *       シミュレーション予測よりも高さを押し上げる影響が強いことを示唆しています。
 *       また、特定のX座標へのドロップの偏りも依然として見られます。
 *       本バージョンでは、この「高さ制御」と「X座標分布の改善」を最優先とし、
 *       さらに併合とパイプラインのボーナスを強化して積極的なスコア獲得を促します。
 *
 *      主な改善点:
 *      1.  **シミュレーションの保守性再々調整 (settlingBufferのさらなる増加)**:
 *          - `simulateDropY` 内の `settlingBuffer` を **3.0 から 3.75 へ増加**。
 *            v161の変更後もmax_yがデッドラインを超える問題が頻発したため、
 *            より強力に物理的な上振れを予測に織り込み、高さを厳格に管理します。
 *      2.  **クリティカル高さマージンのさらなる厳格化**:
 *          - `CRITICAL_HEIGHT_MARGIN` を **0.5 から 0.75 へ増加**。
 *            デッドラインに到達する前の、より早い段階で致命的ペナルティが発動するよう調整し、
 *            危険な高所へのピース配置に対する抑制をさらに強化します。
 *      3.  **高さペナルティの再強化**:
 *          - `HEIGHT_PENALTY_WEIGHT` を **75000.0 から 90000.0 へ増加**。
 *          - `calculateHeightPenalty` 内の severe/extreme warning zone の乗数をそれぞれ **10から12**、**50から60へ増加**。
 *            高さが危険域に達した際のペナルティを全体的に引き上げ、より安全な盤面維持を目指します。
 *      4.  **マージボーナスの再強化**:
 *          - `calculateMergeBonus` 内の基本ボーナスを `droppingPiece.type * 150` から `droppingPiece.type * 180` へ増加。
 *            積極的な併合による盤面整理とスコアアップを促進します。
 *          - `GARBAGE_MERGE_BONUS` を **4000 から 5000 へ増加**。
 *          - `GARBAGE_URGENT_MERGE_BONUS` を **20000 から 25000 へ増加**。
 *            おじゃま発生時の緊急マージの優先度をさらに高めます。
 *      5.  **パイプラインボーナスの再強化**:
 *          - `calculatePipelineBonus` 内の基本ボーナスを `180 * (droppingPiece.type)` から `220 * (droppingPiece.type)` へ増加。
 *          - 小型ピースへの追加ボーナスを `300` から `400` へ増加。
 *            連鎖の維持と形成をさらに奨励し、長期的なゲーム進行を支援します。
 *      6.  **大型ピースの片側集約ロジックの再調整**:
 *          - 既存大型ピースと逆側への配置ペナルティを **700 から 800 へ増加**。
 *            片側集中の原則を再度強化し、盤面全体の高さバランスを考慮しつつも、
 *            大型ピースの分散による後期の合流困難を避けるため、集中を促します。
 *      7.  **初回大型ピース配置ボーナスの緩和**:
 *          - 初回大型ピースのオフセンター配置ボーナスを **500 から 400 へ減少**。
 *            過度な初期のX座標偏りを抑制し、より柔軟な序盤の盤面形成を可能にします。
 *      8.  **先読みボーナスの強化**:
 *          - `calculateLookAheadBonus` の乗数を **0.4 から 0.5 へ増加**。
 *            次手以降の併合機会の価値を高く評価し、より戦略的なドロップを促進します。
 *      9.  その他のv158/v159/v160/v161の変更点 (look-aheadボーナス、おじゃまボーナス、初期大型ピースオフセンターボーナス緩和、早期ゲームにおける中央小型ピースボーナス強化) は維持します。
 */

// Expanded FINE_COLS to increase granularity for X-axis placement
const FINE_COLS = [-2.75, -2.5, -2.25, -2.0, -1.75, -1.5, -1.25, -1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75];
const BOARD_FLOOR_Y = -5.0; // The lowest Y coordinate for pieces.
const BOARD_X_MAX_LIMIT = 3.5; // Actual wall boundary. Max X a piece's *center* can be at is 3.5 - pieceRadius.

// Strategy-specific constants (Height Management)
const DEADLINE_Y = 2.5;                  // Actual game over Y coordinate
// Adjusted relative values to make penalties start earlier (lower Y) - v156
const TOP_Y_CRITICAL_PENALTY_START_RELATIVE = 1.5; // Critical penalty starts when top is 1.5 below deadline (i.e., Y=1.0)
const TOP_Y_WARN_PENALTY_START_RELATIVE = 2.5;     // Warning penalty starts when top is 2.5 below deadline (i.e., Y=0.0)
// v158: Adjusted from DEADLINE_Y - 1.0 (Y=1.5) to DEADLINE_Y - 0.5 (Y=2.0)
const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 0.5;                               // Top is 0.5 below deadline (i.e., Y=2.0)

// v162: Increased from 75000.0 to 90000.0
const HEIGHT_PENALTY_WEIGHT = 90000.0;

// v162: Changed from 0.5 to 0.75
const CRITICAL_HEIGHT_MARGIN = 0.75;

// Strategy-specific constants (General)
const MERGE_BUFFER = 0.6; // Maintained from v114
const LARGE_PIECE_THRESHOLD = 9; // Pieces of this type or higher are considered 'large'.
const SMALL_PIECE_CLUSTER_BONUS = 800; // Maintained from v141
const SMALL_PIECE_THRESHOLD_FOR_DENSITY = 4; // Pieces of this type or lower are considered 'small' for density bonus.

// v152: Adjusted from 0.0 to 1.0
const LARGE_PIECE_HIGH_PENALTY_START_Y = 1.0; // Start penalizing large pieces getting high at Y=1.0 (was 0.0)
// v153: Multiplier increased to 5000
const LARGE_PIECE_HIGH_PENALTY_MULTIPLIER = 5000;

// Garbage Block Management Constants
// v162: Increased from 4000 to 5000
const GARBAGE_MERGE_BONUS = 5000;
// v162: Increased from 20000 to 25000
const GARBAGE_URGENT_MERGE_BONUS = 25000; // Maintained from v128
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
  // The piece's center would be at BOARD_FLOOR_Y + its radius if dropped on an empty floor.
  // This is the baseline lowest possible center Y.
  let simulatedCenterY = BOARD_FLOOR_Y + droppingPiece.r; // v155: Corrected baseline

  // The settling buffer accounts for physical uncertainties and convex polygon shapes.
  // Pieces might settle slightly higher than a perfect circular stack.
  // v162: Increased from 3.0 to 3.75 to more conservatively predict maximum height.
  const settlingBuffer = 3.75;

  for (const existingPiece of existingPieces) {
    // Check for horizontal overlap, using a slightly expanded radius to account for convex shapes.
    // v129: Changed from dynamic buffer to fixed small buffer for horizontal check.
    if (Math.abs(targetX - existingPiece.x) < droppingPiece.r + existingPiece.r + 0.1) {
      // If there's overlap, the dropping piece will land on top of the existing piece
      // Its center will be existingPiece.y + existingPiece.r + droppingPiece.r + settlingBuffer.
      // The settlingBuffer pushes the predicted Y up.
      simulatedCenterY = Math.max(simulatedCenterY, existingPiece.y + existingPiece.r + droppingPiece.r + settlingBuffer);
    }
  }
  return simulatedCenterY;
}

// Calculate penalty based on the simulated Y position (v152: now based on piece's top Y)
function calculateHeightPenalty(simulatedY_center, pieceRadius) {
  const simulatedY_top = simulatedY_center + pieceRadius;

  // Define penalty thresholds for the TOP of the piece
  // v156: Thresholds adjusted for earlier and more severe penalties
  const TOP_Y_MILD_WARN_THRESHOLD = DEADLINE_Y - TOP_Y_WARN_PENALTY_START_RELATIVE;     // Top is 2.5 below deadline (i.e., Y=0.0)
  const TOP_Y_SEVERE_WARN_THRESHOLD = DEADLINE_Y - TOP_Y_CRITICAL_PENALTY_START_RELATIVE; // Top is 1.5 below deadline (i.e., Y=1.0)
  // v158: Adjusted from DEADLINE_Y - 1.0 (Y=1.5) to DEADLINE_Y - 0.5 (Y=2.0)
  const TOP_Y_EXTREME_WARN_THRESHOLD = DEADLINE_Y - 0.5;                               // Top is 0.5 below deadline (i.e., Y=2.0)

  let penalty = 0;

  // Penalize getting close to the deadline. The absolute -1_000_000_000 penalty handles exceeding DEADLINE_Y.
  if (simulatedY_top > TOP_Y_MILD_WARN_THRESHOLD) {
    // Mild warning zone: Top is above Y=0.0
    penalty += (simulatedY_top - TOP_Y_MILD_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 0.5;
  }
  if (simulatedY_top > TOP_Y_SEVERE_WARN_THRESHOLD) {
    // Severe warning zone: Top is above Y=1.0
    // v162: Multiplier increased from 10 to 12
    penalty += (simulatedY_top - TOP_Y_SEVERE_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 12;
  }
  if (simulatedY_top > TOP_Y_EXTREME_WARN_THRESHOLD) {
    // Extreme warning zone: Top is above Y=2.0
    // v162: Multiplier increased from 50 to 60
    penalty += (simulatedY_top - TOP_Y_EXTREME_WARN_THRESHOLD) * HEIGHT_PENALTY_WEIGHT * 60;
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
        // v162: Increased from 150 to 180
        bonus += droppingPiece.type * 180; // Base bonus

        // v142: Additional bonus for merging type 1 pieces (unchanged in v146)
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
          // v158: Increased lowYGarbageBonus base and scaled by piece type, and urgent additional bonus
          let lowYGarbageBonus = 600 + (droppingPiece.type * 75); // Scale with piece type
          if (ojamaUrgentMode) {
              lowYGarbageBonus += 900 * (1 + garbageState.gauge); // Additional bonus for urgent mode, also scaled by gauge
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
          // v162: Increased base multiplier from 180 to 220
          pipelineBonus += 220 * (droppingPiece.type); // Higher types benefit more from pipeline maintenance
          // v162: Increased additional bonus for small pieces from 300 to 400
          if (droppingPiece.type <= SMALL_PIECE_THRESHOLD_FOR_DENSITY) {
              pipelineBonus += 400;
          }
        }
      }
    }
  }
  return pipelineBonus;
}

// v162: Adjusted look-ahead bonus multiplier from 0.4 to 0.5
function calculateLookAheadBonus(currentPiece, currentX, currentY, boardPieces, nextPiece) {
  if (!nextPiece) {
    return 0;
  }

  let lookAheadMaxBonus = 0;

  // Create a hypothetical board state after the current piece drops
  // We're adding currentPiece at its simulated landing Y, but for look-ahead, we can use currentY (the calculated landing Y)
  // as the piece's center for simplicity. Its radius is currentPiece.r.
  const hypotheticalPieces = [...boardPieces, { type: currentPiece.type, x: currentX, y: currentY, r: currentPiece.r }];

  for (const nextX of FINE_COLS) {
    // Simplified simulation for next piece's landing Y on the hypothetical board
    const simulatedNextY = simulateDropY(nextPiece, nextX, hypotheticalPieces);

    // Only look for merge bonuses for the next piece in this simplified look-ahead
    // Ignore garbage state for look-ahead for simplicity and to prevent recursion or complex state management.
    // The main decision logic handles garbage.
    const nextMergeBonus = calculateMergeBonus(nextPiece, nextX, simulatedNextY, hypotheticalPieces, { ratio: 0, height: 0, gauge: 0 }, false);

    if (nextMergeBonus > lookAheadMaxBonus) {
      lookAheadMaxBonus = nextMergeBonus;
    }
  }
  // Apply a discount factor as look-ahead is less certain than immediate outcomes
  // v162: Increased from 0.4 to 0.5 for stronger weighting of future merges
  return lookAheadMaxBonus * 0.5;
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
  // nextPieceForLookAhead: The piece that would come *after* pieceToDrop if it were dropped
  const evaluatePlacement = (pieceToDrop, isHoldingAttempt = false, nextPieceForLookAhead = null) => {
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

      // v162: If placing the piece would make its predicted top significantly close to or above the deadline, assign a huge penalty.
      if (simulatedY + pieceToDrop.r > DEADLINE_Y - CRITICAL_HEIGHT_MARGIN) {
          currentPlacementScore = -1_000_000_000; // Extremely high penalty for critical height
      } else {
        currentPlacementScore = 0; // Initialize for normal calculation

        // Penalize height (v158: thresholds adjusted for new settlingBuffer)
        currentPlacementScore -= calculateHeightPenalty(simulatedY, pieceToDrop.r);

        // v141: Further increased additional penalty if placing high when a lot of garbage is present
        // (v142: values remain same as v141, unchanged in v147)
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

        // v157: Bonus for pipeline maintenance, with stronger bonus for small pieces
        currentPlacementScore += calculatePipelineBonus(pieceToDrop, x, simulatedY, boardState.pieces);

        // v158: Add look-ahead bonus for the next piece in queue (if available) - weight adjusted
        if (nextPieceForLookAhead) {
            currentPlacementScore += calculateLookAheadBonus(pieceToDrop, x, simulatedY, boardState.pieces, nextPieceForLookAhead);
        }

        // v161: Early Game Central Small Piece Bonus
        // Encourages small pieces (type 1-3) to be placed centrally when the board is relatively empty
        if (boardState.pieces.length < 5 && pieceToDrop.type <= 3) {
            if (Math.abs(x) < 1.0) { // Central area
                currentPlacementScore += 1200;
            } else if (Math.abs(x) < 2.0) { // Slightly off-center but still good
                currentPlacementScore += 500;
            }
        }
        // v161: Added a second tier for early game central small piece bonus
        else if (boardState.pieces.length < 15 && pieceToDrop.type <= 3) {
            if (Math.abs(x) < 1.0) {
                currentPlacementScore += 700;
            } else if (Math.abs(x) < 2.0) {
                currentPlacementScore += 200;
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
            // v149: Reduced from 1300 to 1000 to balance with stronger height penalties (maintained in v150)
            if ((avgLargePieceX < 0 && x < 0) || (avgLargePieceX > 0 && x > 0)) {
              currentPlacementScore += 1000;
              // v152: LARGE_PIECE_HIGH_PENALTY_START_Y adjusted to 1.0
              // v153: LARGE_PIECE_HIGH_PENALTY_MULTIPLIER increased from 3000 to 5000
              if (simulatedY > LARGE_PIECE_HIGH_PENALTY_START_Y) {
                  currentPlacementScore -= (simulatedY - LARGE_PIECE_HIGH_PENALTY_START_Y) * LARGE_PIECE_HIGH_PENALTY_MULTIPLIER;
              }
            } else if (Math.abs(avgLargePieceX) < 0.5 && Math.abs(x) < 0.5) { // If large pieces are mostly centered, and we're dropping centrally
               currentPlacementScore += 50;
            } else { // If we're dropping a large piece on the opposite side of existing large pieces
               // v162: Increased from 700 to 800
               currentPlacementScore -= 800;
            }
          } else {
              // If this is the first large piece, try to place it significantly off-center to encourage starting a stack
              // v162: Reduced bonus from 500 to 400.
              if (x < -1.0 || x > 1.0) {
                  currentPlacementScore += 400;
              }
          }
        }

        // Prioritize clearing garbage by placing pieces lower if garbage exists
        // "Merging near the bottom of the board is more effective for clearing garbage"
        // This logic is now handled more robustly within calculateMergeBonus for low Y merges.
        // However, a general bonus for clearing garbage still applies based on current logic.
        // v133: Increased bonus from 750 to 1200 (maintained in v147)
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
  const nextPieceLookAhead = boardState.nextPieces.length > 1 ? boardState.nextPieces[1] : null;
  const { x: nextX, score: nextScore, reason: nextReason } = evaluatePlacement(boardState.next, false, nextPieceLookAhead);
  maxScore = nextScore;
  bestX = nextX;
  bestReason = nextReason;

  // 2. Evaluate held piece if available and canHold
  if (boardState.canHold && boardState.hold) {
    // If we perform a HOLD, the original boardState.next becomes the "next" piece for look-ahead
    const holdPieceLookAhead = boardState.next;
    const { x: holdX, score: holdScore, reason: holdReason } = evaluatePlacement(boardState.hold, true, holdPieceLookAhead);
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
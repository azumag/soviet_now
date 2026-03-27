/**
 * strategy.mjs - ドロップ位置決定戦略 (v83)
 *
 * v83: 物理エンジン挙動の複雑さを考慮し、併合条件の判定をよりロバスト化。
 *      ピースが円形と仮定した場合の2D中心間距離に基づく併合判定を導入し、
 *      併合の厳密性を調整するための `MERGE_BUFFER` 定数を追加。
 *      `simulateDropY` は既存の垂直スタックモデルを維持するが、これはポリゴン特性による
 *      回転や転がりを完全に予測できないという限界を考慮したもの。
 *      全体的な戦略の優先順位とHOLDロジックはv82から維持。
 *
 * v82: ログで観察された「ドロップX=0.00固定」問題の解決、HOLDロジックの強化、
 *      DEFAULT戦略におけるマージ優先・高さ管理・大型ピース片側集約の導入。
 *      ダミーだったヘルパー関数 (`simulateDropY`, `findT1LowMerge`, `findAggressiveCriticalMerge`) の実装。
 * - 【全体】デフォルトのドロップ位置が中央(0.0)に固定される問題を解決。
 *   - 優先順位に基づき、HOLD、CRITICAL、ULTRA、DEFAULTの各モードで適切なX座標を決定する。
 * - 【HOLDモード強化】
 *   - 現在のピースにマージ先がないがHOLD中のピースにマージ先がある場合にHOLDを使用。
 *   - 大型ピース(type 10+)が来た際に、HOLDスロットが空いていれば一時的にHOLDするロジックを追加。
 *   - 小ピース(type 1-3)が来た際、HOLD中の大型ピースがあれば入れ替えるロジックを追加。
 * - 【CRITICALモード強化】
 *   - `findAggressiveCriticalMerge` を実装。高いピース数、ガベージ割合、ガベージゲージレベルを考慮し、
 *     可能な限り多くの同typeピースと併合できる位置、次いで低いY座標を優先して探索。
 * - 【ULTRAモード強化】
 *   - `findT1LowMerge` を実装。T1ピースが過密で高所に位置する場合に、
 *     最も低いY座標でT1を併合できる位置を優先して探索。
 * - 【DEFAULT戦略の改善】
 *   - 最も優先度の低いDEFAULTモードでも、単純な0.0ドロップではなく、以下の順でX座標を決定。
 *     1. 現在のピース (`next`) と同typeのピースに、最も低いY座標で即時併合できる位置を探す。
 *     2. 即時併合先がない場合、ドロップ後のY座標が最も低くなる位置を探す（高さ管理）。
 *     3. 大型ピース (type 9+) の場合、左側 (`LEFT_SIDE_X_MAX`) に寄せて配置し、大型ピースの片側集約を促す。
 *     4. 全ての戦略が適用できない場合の最終手段として、`findLeastOccupiedX` (空いている列) または中央(0.0)を使用。
 * - 【ヘルパー関数実装】
 *   - `simulateDropY`: ピースを特定のX座標にドロップした際のY座標を、既存ピースと半径を考慮して推定する。
 *   - `findMergeOpportunity`: 指定されたtypeのピースが併合可能となる最も適切なX座標を探索。
 *   - `computeColHeights`: 各FINE_COLSにおけるピースの最高到達Y座標を計算。
 * - 【定数調整】
 *   - `T1_LOW_MERGE_HEIGHT_ADVANTAGE`: v81からの変更を維持 (0.55 -> 0.6)。
 *   - `LARGE_PIECE_THRESHOLD`, `LEFT_SIDE_X_MAX`: 大型ピースの片側集約のために新規導入。
 * - 継承: v81のT1過密時の低位置マージ優先度強化とCRITICALモードの微調整
 * - 継承: v80のCRITICAL/ULTRAモードでのT1管理とアグレッシブマージ戦略強化
 * - 継承: v79のCRITICALモードのマージ探索強化とT1過密時の処理改善
 * - 継承: v78のガベージ・緊急時のT1低位置マージ優先度とT1過密時の処理、HOLD戦略の強化
 * - 継承: v77のガベージ・高typeT1の低位置マージ優先度調整とボード全体高さペナルティ強化
 * - 継承: v76の高typeピース/T1管理の改善と高さペナルティ強化
 * - 継承: v75の高typeピース活用改善 + EXTREME閾値調整
 * - 継承: v74のEMERGENCY修正 (findEmergencyMergeRelaxed, hold最終手段)
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 85;
const ULTRA_MASS_THRESHOLD = 70;
const EXTREME_T1_FLOOD_THRESHOLD = 30;
const SURVIVAL_PIECE_THRESHOLD = 78;
const LOW_MASS_CRITICAL_RELIEF_PIECE_THRESHOLD = 32;
const LOW_MASS_CRITICAL_RELIEF_AVG_HEIGHT = 1.45;
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.6; // Changed from 0.55 to 0.6
const GARBAGE_MODERATE_RATIO = 0.25;
const GARBAGE_MODERATE_HEIGHT = 0.3;
const EXTREME_T1_WALL_PIECE_THRESHOLD = 75;
const T1_PREFLOOD_THRESHOLD = 12;
const T1_PREFLOOD_DENSE_THRESHOLD = 10;
const T1_RATIO_PURGE_THRESHOLD = 0.62;

// Custom constants for new strategy
const LARGE_PIECE_THRESHOLD = 9; // Pieces of type 9 or larger
const LEFT_SIDE_X_MAX = -1.0; // Defines the "left side" area for large piece segregation
const MERGE_BUFFER = 0.01; // Small buffer for merge proximity (e.g., 0.01 for slight overlap)

// Helper function to find the least occupied x-coordinate
function findLeastOccupiedX(pieces) {
  const occupancy = {};
  FINE_COLS.forEach(col => {
    occupancy[col] = 0;
  });

  pieces.forEach(p => {
    const closestCol = FINE_COLS.reduce((prev, curr) =>
      Math.abs(curr - p.x) < Math.abs(prev - p.x) ? curr : prev
    );
    occupancy[closestCol]++;
  });

  let minOccupancy = Infinity;
  let leastOccupiedCol = FINE_COLS[0]; // Default to the first column

  for (const col of FINE_COLS) {
    if (occupancy[col] < minOccupancy) {
      minOccupancy = occupancy[col];
      leastOccupiedCol = col;
    }
  }
  return leastOccupiedCol;
}

// Helper to estimate the Y position if a piece is dropped at X
// This function aims to find the highest point a piece of dropRadius would rest on
// if dropped at dropX, considering other pieces and the floor.
function simulateDropY(pieces, dropX, dropRadius) {
  let highestRestY = -5.0 + dropRadius; // Start from the floor

  pieces.forEach(p => {
    // Check if the dropped piece would land on or interact with this piece in the X-axis
    // The horizontal distance between centers must be less than the sum of their radii for them to touch.
    const horizontalDistance = Math.abs(p.x - dropX);
    const minOverlapDistance = p.r + dropRadius;

    if (horizontalDistance < minOverlapDistance) {
      // If there's horizontal overlap, the dropped piece could land on 'p'.
      // Calculate the potential resting Y for the *center* of the dropped piece
      // assuming it stacks directly on top, using Pythagoras for circular collision.
      // sqrt((r1+r2)^2 - dx^2) gives the vertical distance between centers.
      const dy = Math.sqrt(Math.max(0, minOverlapDistance * minOverlapDistance - horizontalDistance * horizontalDistance));
      const potentialRestY = p.y + dy;

      if (potentialRestY > highestRestY) {
        highestRestY = potentialRestY;
      }
    }
  });

  // Ensure the piece does not go above the deadline.
  // The center of the piece cannot be higher than DEADLINE_Y - dropRadius.
  return Math.min(highestRestY, DEADLINE_Y - dropRadius);
}


// Helper function to compute column heights
function computeColHeights(pieces) {
  const colHeights = {};
  for (const col of FINE_COLS) {
    colHeights[col] = -5.0; // Initialize to floor
  }

  pieces.forEach(p => {
    // For each piece, find the column(s) it "occupies" or influences
    const affectedCols = FINE_COLS.filter(col => Math.abs(col - p.x) < p.r);

    if (affectedCols.length === 0) { // If it doesn't align with any FINE_COLS, find the closest
        const closestCol = FINE_COLS.reduce((prev, curr) =>
            Math.abs(curr - p.x) < Math.abs(prev - p.x) ? curr : prev
        );
        affectedCols.push(closestCol);
    }

    affectedCols.forEach(col => {
      // The height should be the top of the piece
      if (p.y + p.r > colHeights[col]) {
        colHeights[col] = p.y + p.r;
      }
    });
  });
  return colHeights;
}


// Helper to find a merge opportunity for a given type, prioritizing low Y
function findMergeOpportunity(pieces, typeToMerge, dropRadius, priorityLowY = true) {
  let bestX = null;
  let bestY = Infinity;
  let bestMergePartnerCount = 0;

  for (const targetX of FINE_COLS) {
    const simulatedY = simulateDropY(pieces, targetX, dropRadius);

    // Look for same-type pieces near the simulated drop spot
    const mergePartners = pieces.filter(p => {
      if (p.type === typeToMerge) {
        // Calculate 2D distance between centers
        const distance = Math.sqrt(
          Math.pow(p.x - targetX, 2) + Math.pow(p.y - simulatedY, 2)
        );
        // Merge if centers are close enough (within sum of radii, minus a small buffer for overlap)
        return distance < (p.r + dropRadius - MERGE_BUFFER);
      }
      return false;
    });

    if (mergePartners.length > 0) {
      if (bestX === null ||
          (priorityLowY && simulatedY < bestY) ||
          (!priorityLowY && mergePartners.length > bestMergePartnerCount) ||
          (mergePartners.length === bestMergePartnerCount && simulatedY < bestY)
         ) {
        bestX = targetX;
        bestY = simulatedY;
        bestMergePartnerCount = mergePartners.length;
      }
    }
  }
  return bestX;
}


// Implement findT1LowMerge to prioritize merging type 1 pieces at lower Y coordinates
function findT1LowMerge(activePieces, nextType, dropRadius) {
  // Specifically look for type 1 merges at low positions
  if (nextType !== 1) return null; // Only for nextType 1

  let bestX = null;
  let lowestMergeY = Infinity;

  for (const targetX of FINE_COLS) {
    const simulatedY = simulateDropY(activePieces, targetX, dropRadius);

    const mergeCandidates = activePieces.filter(p => {
      if (p.type === 1) {
        // Calculate 2D distance between centers
        const distance = Math.sqrt(
          Math.pow(p.x - targetX, 2) + Math.pow(p.y - simulatedY, 2)
        );
        // Merge if centers are close enough (within sum of radii, minus a small buffer for overlap)
        return distance < (p.r + dropRadius - MERGE_BUFFER);
      }
      return false;
    });

    if (mergeCandidates.length > 0) {
      if (simulatedY < lowestMergeY) {
        lowestMergeY = simulatedY;
        bestX = targetX;
      }
    }
  }
  return bestX;
}

// Implement findAggressiveCriticalMerge for critical situations
function findAggressiveCriticalMerge(activePieces, nextType, dropRadius) {
  let bestX = null;
  let maxMergePartners = 0;
  let lowestYForMaxPartners = Infinity;

  for (const targetX of FINE_COLS) {
    const simulatedY = simulateDropY(activePieces, targetX, dropRadius);

    const mergePartners = activePieces.filter(p => {
      if (p.type === nextType) {
        // Calculate 2D distance between centers for aggressive merge (slightly more permissive buffer)
        const distance = Math.sqrt(
          Math.pow(p.x - targetX, 2) + Math.pow(p.y - simulatedY, 2)
        );
        // Aggressive merge, so use a slightly larger or no buffer, or even a positive one for more leniency
        return distance < (p.r + dropRadius + MERGE_BUFFER); // Increased leniency for aggressive merge
      }
      return false;
    });

    if (mergePartners.length > 0) {
      if (mergePartners.length > maxMergePartners) {
        maxMergePartners = mergePartners.length;
        lowestYForMaxPartners = simulatedY;
        bestX = targetX;
      } else if (mergePartners.length === maxMergePartners && simulatedY < lowestYForMaxPartners) {
        lowestYForMaxPartners = simulatedY;
        bestX = targetX;
      }
    }
  }
  return bestX;
}


export function decide(boardState) {
  const { pieces, next, nextPieces, confidence, garbage, hold, canHold, score } = boardState;
  const nextType = next ? next.type : 1;
  // Fallback radius if next.r is not available (should ideally always be there)
  const nextRadius = next ? next.r : 0.15 + (nextType - 1) * 0.05;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  let activePieces = pieces.filter(p => Math.abs(p.x) <= 3.2);
  const rawPieceCount = activePieces.length;

  if (activePieces.length > MAX_ACTIVE_PIECES) {
    const mergeCandidates = activePieces.filter(p => p.type === nextType);
    const highPieces = activePieces.filter(p => p.y > WARN_Y && p.type !== nextType);
    const rest = activePieces
      .filter(p => p.y <= WARN_Y && p.type !== nextType)
      .sort((a, b) => b.y - a.y);
    const combined = [...mergeCandidates, ...highPieces, ...rest];
    const seen = new Set();
    const deduped = combined.filter(p => { if (seen.has(p)) return false; seen.add(p); return true; });
    activePieces = deduped.slice(0, MAX_ACTIVE_PIECES);
  }

  const unreliable = confidence < 0.3;
  if (unreliable) {
    const safeX = findLeastOccupiedX(activePieces);
    return { x: safeX, reason: `SPREAD_UNRELIABLE_X${safeX.toFixed(1)}` };
  }

  const colHeights = computeColHeights(activePieces);
  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageGauge = garbage ? (garbage.gauge || 0) : 0;

  let bestX = 0.0;
  let reason = 'DEFAULT';
  let shouldHold = false;

  // --- HOLD Logic ---
  if (canHold) {
    const currentPieceMergeTargetX = findMergeOpportunity(activePieces, nextType, nextRadius, true);
    let holdPieceMergeTargetX = null;
    if (hold) {
      holdPieceMergeTargetX = findMergeOpportunity(activePieces, hold.type, hold.r, true);
    }

    // Heuristic: If current piece has no merge target, but held piece does, swap.
    if (currentPieceMergeTargetX === null && holdPieceMergeTargetX !== null) {
      shouldHold = true;
      reason = 'HOLD_FOR_MERGE_TARGET';
      return { x: 0, reason: reason, hold: shouldHold };
    }
    // Heuristic: If current piece is very large (e.g., type 10+) and not immediately useful, hold it for later.
    if (nextType >= 10 && currentPieceMergeTargetX === null && hold === null) { // Only hold if slot is empty
      shouldHold = true;
      reason = 'HOLD_LARGE_PIECE';
      return { x: 0, reason: reason, hold: shouldHold };
    }
    // Heuristic: If current piece is a small piece and we have a large piece held, maybe swap to get rid of small one first.
    if (nextType <= 3 && hold && hold.type >= 8 && currentPieceMergeTargetX === null) {
      shouldHold = true;
      reason = 'HOLD_SWAP_SMALL_FOR_LARGE';
      return { x: 0, reason: reason, hold: shouldHold };
    }
  }


  // --- CRITICAL Mode ---
  const isCritical = (rawPieceCount > ULTRA_MASS_THRESHOLD || garbageRatio > GARBAGE_MODERATE_RATIO || garbageGauge >= 0.6);
  if (isCritical) {
    const aggressiveMergeX = findAggressiveCriticalMerge(activePieces, nextType, nextRadius);
    if (aggressiveMergeX !== null) {
      bestX = aggressiveMergeX;
      reason = 'CRITICAL_AGGRESSIVE_MERGE';
      return { x: bestX, reason: reason, hold: shouldHold };
    }
  }

  // --- ULTRA Mode (T1 flood and high T1 position) ---
  const t1Pieces = activePieces.filter(p => p.type === 1);
  const extremeT1Flood = t1Pieces.length > EXTREME_T1_FLOOD_THRESHOLD;
  const highestT1Y = t1Pieces.reduce((maxY, p) => Math.max(maxY, p.y + p.r), -Infinity);

  if (extremeT1Flood && highestT1Y > WARN_Y) {
    const t1LowMergeCandidate = findT1LowMerge(activePieces, nextType, nextRadius);
    if (t1LowMergeCandidate !== null) {
      bestX = t1LowMergeCandidate;
      reason = 'ULTRA_T1_LOW_MERGE';
      return { x: bestX, reason: reason, hold: shouldHold };
    }
  }

  // --- DEFAULT Strategy ---
  // 1. Try to find an immediate merge for the current piece (nextType) at the lowest possible Y.
  let mergeX = findMergeOpportunity(activePieces, nextType, nextRadius, true);
  if (mergeX !== null) {
    bestX = mergeX;
    reason = 'DEFAULT_IMMEDIATE_MERGE';
    return { x: bestX, reason: reason, hold: shouldHold };
  }

  // 2. If no immediate merge, try to place the piece to keep the board low.
  //    Prioritize large piece segregation.
  let lowestY = Infinity;
  let candidateX = null;

  // First, check for large piece segregation
  if (nextType >= LARGE_PIECE_THRESHOLD) {
      let lowestYOnLeftSide = Infinity;
      let leftSideCandidateX = null;
      for (const targetX of FINE_COLS) {
          // If the column is on the left side (or close to it)
          if (targetX <= LEFT_SIDE_X_MAX + nextRadius) { // Allow some leeway based on piece size
              const simulatedY = simulateDropY(activePieces, targetX, nextRadius);
              if (simulatedY < lowestYOnLeftSide) {
                  lowestYOnLeftSide = simulatedY;
                  leftSideCandidateX = targetX;
              }
          }
      }
      if (leftSideCandidateX !== null) {
          bestX = leftSideCandidateX;
          reason = 'DEFAULT_LARGE_PIECE_LEFT_SIDE';
          return { x: bestX, reason: reason, hold: shouldHold };
      }
  }

  // If not a large piece, or couldn't place on left side, find generally lowest Y
  for (const targetX of FINE_COLS) {
    const simulatedY = simulateDropY(activePieces, targetX, nextRadius);
    if (simulatedY < lowestY) {
      lowestY = simulatedY;
      candidateX = targetX;
    }
  }

  if (candidateX !== null) {
    bestX = candidateX;
    reason = 'DEFAULT_LOWEST_Y';
    return { x: bestX, reason: reason, hold: shouldHold };
  }


  // Final fallback (should ideally not be reached often if logic is robust)
  // Use findLeastOccupiedX to spread pieces or a simple center drop.
  if (rawPieceCount > SURVIVAL_PIECE_THRESHOLD || highestT1Y > WARN_Y + 0.5) {
      bestX = findLeastOccupiedX(activePieces);
      reason = 'DEFAULT_SPREAD_HIGH_BOARD';
  } else {
      bestX = 0.0;
      reason = 'DEFAULT_CENTER_FALLBACK';
  }


  return { x: bestX, reason: reason, hold: shouldHold };
}
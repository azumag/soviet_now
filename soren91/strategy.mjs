/**
 * strategy.mjs - ドロップ位置決定戦略 (v10)
 *
 * v10改善点 (v9からの修正):
 * - Game #46: score=0, 89ターン全CRITICAL の根本原因修正
 * - CRITICAL閾値を2列→3列に緩和 (91人対戦のガベージ環境に適応)
 * - garbageUrgent時に即マージ優先 (CRITICAL判定前にガベージ対応)
 * - CRITICAL時フォールバック: 最低列ドロップ→クラスタリング優先
 * - avgHeight: 中央値ベースに変更 (ガベージ層による外れ値除外)
 * - findClusterDrop: 即マージなくても同タイプ近くに置いて将来マージ準備
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 60;

export function decide(boardState) {
  const { pieces, next, nextPieces, confidence, garbage, hold, canHold } = boardState;
  const nextType = next ? next.type : 1;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  let activePieces = pieces.filter(p => Math.abs(p.x) <= 3.2);
  if (activePieces.length > MAX_ACTIVE_PIECES) {
    activePieces = [...activePieces].sort((a, b) => b.y - a.y).slice(0, MAX_ACTIVE_PIECES);
  }

  const unreliable = confidence < 0.3;
  if (unreliable) {
    const safeX = findLeastOccupiedX(activePieces);
    return { x: safeX, reason: `SPREAD_UNRELIABLE_X${safeX.toFixed(1)}` };
  }

  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  const garbageUrgent = garbageRatio > 0.3 || garbageHeight > 0.5;
  const garbageCritical = garbageRatio > 0.55 || garbageHeight > 1.8;

  const boardPressure = activePieces.length > 45;

  const colHeights = computeColHeights(activePieces);
  const avgHeight = computeMedianHeight(colHeights);

  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < -0.3).length;
  const rightDanger = dangerPieces.filter(p => p.x > 0.3).length;
  let dangerBias = 0;
  if (leftDanger > rightDanger + 3) dangerBias = -1;
  else if (rightDanger > leftDanger + 3) dangerBias = 1;

  // CRITICAL判定: ガベージが多い時は閾値引き上げ (3列以上near-deadline or 1列over)
  const nearDeadlineCount = colHeights.filter(h => h > DEADLINE_Y - 0.3).length;
  const overDeadlineCount = colHeights.filter(h => h > DEADLINE_Y + 0.1).length;
  const critThreshold = garbageCritical ? 4 : 3;
  const isCritical = nearDeadlineCount >= critThreshold || overDeadlineCount >= 1;
  const isWarn = colHeights.some(h => h > WARN_Y + 0.5);

  // --- おじゃま緊急: CRITICAL判定前にマージを最優先 ---
  if (garbageUrgent) {
    const anyMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, true, 0);
    if (anyMerge) return { ...anyMerge, reason: `GBG_MERGE_T${nextType}` };
    // HOLDにマージ先があれば使用
    if (canHold && hold && hold.type) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1
      ).length;
      if (holdMergeCount > 0) {
        return { x: 0, reason: `GBG_HOLD_SWAP_T${hold.type}`, hold: true };
      }
    }
    // マージ不可 → 通常フローへ fall through
  }

  // --- CRITICAL時の処理 ---
  if (isCritical) {
    // HOLDスワップ: HOLDピースの方が安全マージ多い場合
    if (canHold && hold && hold.type) {
      const holdSafeMerge = countSafeMerge(activePieces, hold.type, colHeights, avgHeight);
      const nextSafeMerge = countSafeMerge(activePieces, nextType, colHeights, avgHeight);
      if (holdSafeMerge > nextSafeMerge) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // 安全列でのマージ
    const criticalMerge = findSafeColMerge(activePieces, nextType, colHeights, avgHeight, dangerBias, garbageUrgent);
    if (criticalMerge) return { ...criticalMerge, reason: `CRITICAL_MERGE_T${nextType}` };

    // HOLD保存: マージ先なし + 次ピースに安全マージあり
    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0 && nextPieceType !== nextType) {
        const nextHasSafeMerge = countSafeMerge(activePieces, nextPieceType, colHeights, avgHeight) > 0;
        if (nextHasSafeMerge) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_FOR_T${nextPieceType}`, hold: true };
        }
      }
    }

    // クラスタリング: 同タイプ近くに置いて将来マージ準備
    const cluster = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
    if (cluster) return { ...cluster, reason: `CRITICAL_CLUSTER_T${nextType}` };

    // 最後の手段: 最低列
    const bestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: bestDrop.x, reason: `CRITICAL_DROP${bestDrop.idx}_Y${colHeights[bestDrop.idx].toFixed(1)}` };
  }

  // --- HOLD判定 (非CRITICAL) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces, isWarn);
    if (holdResult) return holdResult;
  }

  // ボード圧迫時
  if (boardPressure) {
    if (nextType >= 5) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  // --- 1. 大型ピース(type>=6)優先マージ ---
  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  // --- 2. チェーン期待値マージ ---
  const chainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  // --- 3. 通常マージ ---
  const normalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  // --- 4. クラスタリング ---
  const clusterPlace = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
  if (clusterPlace) return { ...clusterPlace, reason: `CLUSTER_T${nextType}` };

  // --- 5. 高さバランス ---
  return findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

/** 安全マージ候補数 (CRITICAL時チェック用) */
function countSafeMerge(pieces, type, colHeights, avgHeight) {
  const heightLimit = Math.min(Math.max(avgHeight + 0.8, 0.5), DEADLINE_Y - 0.3);
  return pieces.filter(p => {
    const ci = nearestColIdx(p.x);
    return p.type === type &&
           Math.abs(p.x) < WALL_MARGIN &&
           p.y < DEADLINE_Y - 0.1 &&
           colHeights[ci] <= heightLimit;
  }).length;
}

/** クラスタリング: 同タイプ近くに置いて将来マージ準備 */
function findClusterDrop(pieces, nextType, colHeights, dangerBias) {
  const sameType = pieces.filter(p =>
    p.type === nextType && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y - 0.1
  );
  if (sameType.length === 0) return null;

  let bestScore = -Infinity;
  let bestX = null;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] >= DEADLINE_Y) continue;

    const nearSame = sameType.filter(p => Math.abs(p.x - cx) < 1.5).length;
    if (nearSame === 0) continue;

    let s = nearSame * 5.0;
    s -= colHeights[i] * 3.0;
    s -= Math.abs(cx) * 0.3;
    if (dangerBias < 0 && cx < -0.5) s -= 5;
    if (dangerBias > 0 && cx > 0.5) s -= 5;

    if (s > bestScore) { bestScore = s; bestX = cx; }
  }

  if (bestX === null) return null;
  return { x: clampX(bestX), reason: `CLUSTER_X${bestX.toFixed(1)}` };
}

/** 中央値ベースの高さ (外れ値除外) */
function computeMedianHeight(colHeights) {
  const valid = colHeights.filter(h => h > -4.5).sort((a, b) => a - b);
  if (valid.length === 0) return -3.0;
  const mid = Math.floor(valid.length / 2);
  return valid.length % 2 === 0 ? (valid[mid - 1] + valid[mid]) / 2 : valid[mid];
}

/** HOLD判定 */
function evaluateHold(pieces, nextType, hold, nextPieces, isWarn) {
  const safeY = DEADLINE_Y - 0.3;
  const nextMergeCount = pieces.filter(p => p.type === nextType && p.y < safeY).length;

  if (hold && hold.type) {
    const holdMergeCount = pieces.filter(p => p.type === hold.type && p.y < safeY).length;
    if (holdMergeCount > nextMergeCount && nextMergeCount === 0) {
      return { x: 0, reason: `HOLD_SWAP_T${hold.type}vs${nextType}`, hold: true };
    }
    if (hold.type >= 5 && nextType <= 3 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_BIGTYPE_T${hold.type}`, hold: true };
    }
  } else {
    if (nextMergeCount === 0 && pieces.length > 15 && !isWarn) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      const nextHasMerge = nextPieceType > 0 &&
        pieces.filter(p => p.type === nextPieceType && p.y < safeY).length > 0;
      if (nextHasMerge) {
        return { x: 0, reason: `HOLD_SAVE_T${nextType}_FOR_T${nextPieceType}`, hold: true };
      }
    }
  }
  return null;
}

/** CRITICAL時: 安全高さ範囲内マージ */
function findSafeColMerge(pieces, nextType, colHeights, avgHeight, dangerBias, garbageUrgent) {
  const heightLimit = garbageUrgent
    ? Math.min(DEADLINE_Y - 0.2, avgHeight + 1.5)
    : Math.min(Math.max(avgHeight + 0.8, 0.5), DEADLINE_Y - 0.3);

  const candidates = pieces.filter(p => {
    const ci = nearestColIdx(p.x);
    return p.type === nextType &&
           Math.abs(p.x) < WALL_MARGIN &&
           p.y < DEADLINE_Y - 0.1 &&
           colHeights[ci] <= heightLimit;
  });
  if (candidates.length === 0) return null;

  let bestTarget = null;
  let bestScore = -Infinity;

  for (const t of candidates) {
    const ci = nearestColIdx(t.x);
    const colH = colHeights[ci];
    let s = -colH * 5.0 + nextType * 1.5;
    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 4;
    s += countNear(pieces, t.x, nextType + 1, 1.8) * 4;
    s -= Math.abs(t.x) * 0.2;
    if (dangerBias < 0 && t.x < -0.5) s -= 6;
    if (dangerBias > 0 && t.x > 0.5) s -= 6;
    if (s > bestScore) { bestScore = s; bestTarget = t; }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: 'SAFE_MERGE' };
}

/** 最低列へドロップ (CRITICALフォールバック) */
function findLowestSafeDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] >= DEADLINE_Y + 0.2) continue;
    let s = -colHeights[i] * 6.0;
    s -= Math.abs(FINE_COLS[i]) * 0.15;
    if (dangerBias < 0 && FINE_COLS[i] < -1.0) s -= 8;
    if (dangerBias > 0 && FINE_COLS[i] > 1.0) s -= 8;
    if (s > bestScore) { bestScore = s; bestIdx = i; }
  }

  if (bestScore === -Infinity) bestIdx = findLowestColIdx(colHeights);
  return { x: clampX(FINE_COLS[bestIdx]), idx: bestIdx };
}

/** 列高さ計算 */
function computeColHeights(pieces) {
  return FINE_COLS.map(cx => {
    const col = pieces.filter(p => Math.abs(p.x - cx) < 0.4);
    if (col.length === 0) return -5.0;
    return Math.max(...col.map(p => p.y + (p.r || 0.3)));
  });
}

/** 最も低い列のインデックス */
function findLowestColIdx(colHeights) {
  let minH = Infinity, minIdx = 5;
  for (let i = 0; i < colHeights.length; i++) {
    if (colHeights[i] < minH) { minH = colHeights[i]; minIdx = i; }
  }
  return minIdx;
}

/** 最良マージ位置 */
function findBestMerge(pieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, minChainScore) {
  const candidates = pieces.filter(p =>
    p.type === nextType &&
    Math.abs(p.x) < WALL_MARGIN &&
    p.y < DEADLINE_Y - 0.1
  );
  if (candidates.length === 0) return null;

  let bestTarget = null;
  let bestScore = -Infinity;

  for (const t of candidates) {
    const ci = nearestColIdx(t.x);
    const colH = colHeights[ci];
    if (colH > DEADLINE_Y) continue;

    let s = -colH * 2.5;
    if (colH > DEADLINE_Y - 0.4) s -= 10;
    if (colH > avgHeight + 0.8) s -= 3;

    s += nextType * 1.5;

    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    const c3 = countNear(pieces, t.x, nextType + 3, 2.6);
    const chainScore = c1 * 6 + c2 * 3 + c3 * 1.5;
    if (chainScore < minChainScore) continue;
    s += chainScore;

    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 3;

    if (dangerBias < 0 && t.x < -0.5) s -= 5;
    if (dangerBias > 0 && t.x > 0.5) s -= 5;
    s -= Math.abs(t.x) * 0.5;

    if (garbageUrgent) s += 15;

    if (s > bestScore) { bestScore = s; bestTarget = t; }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
}

/** 高さバランス着弾位置 */
function findBestHeightDrop(pieces, nextType, colHeights, dangerBias, avgHeight) {
  let bestIdx = -1;
  let bestScore = -Infinity;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i] * 3.0;
    const nearSame = pieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.2 && p.y < DEADLINE_Y
    ).length;
    s += nearSame * 2.5;

    const leftH = i > 0 ? colHeights[i - 1] : colHeights[i];
    const rightH = i < FINE_COLS.length - 1 ? colHeights[i + 1] : colHeights[i];
    const gap = Math.max(leftH, rightH) - colHeights[i];
    if (gap > 1.0) s -= (gap - 1.0) * 2.0;

    s -= Math.abs(cx) * 0.4;
    if (dangerBias < 0 && cx < -0.5) s -= 4;
    if (dangerBias > 0 && cx > 0.5) s -= 4;

    if (s > bestScore) { bestScore = s; bestIdx = i; }
  }

  if (bestIdx < 0) {
    const lowestIdx = findLowestColIdx(colHeights);
    return { x: clampX(FINE_COLS[lowestIdx]), reason: `CRITICAL_COL${lowestIdx}` };
  }
  return { x: clampX(FINE_COLS[bestIdx]), reason: `HEIGHT_COL${bestIdx}_Y${colHeights[bestIdx].toFixed(1)}` };
}

/** 指定タイプが cx 付近にいくつあるか */
function countNear(pieces, cx, type, radius) {
  if (type > 15) return 0;
  return pieces.filter(p =>
    p.type === type && Math.abs(p.x - cx) < radius && p.y < DEADLINE_Y
  ).length;
}

/** x に最も近い FINE_COLS のインデックス */
function nearestColIdx(x) {
  let minDist = Infinity, minIdx = 0;
  for (let i = 0; i < FINE_COLS.length; i++) {
    const d = Math.abs(FINE_COLS[i] - x);
    if (d < minDist) { minDist = d; minIdx = i; }
  }
  return minIdx;
}

/** 最もピースが少ない列のX座標 */
function findLeastOccupiedX(pieces) {
  const counts = FINE_COLS.map(cx => ({
    x: cx,
    n: pieces.filter(p => Math.abs(p.x - cx) < 0.4).length,
  }));
  const minN = Math.min(...counts.map(c => c.n));
  return counts
    .filter(c => c.n === minN)
    .reduce((a, b) => Math.abs(a.x) <= Math.abs(b.x) ? a : b).x;
}

function clampX(x) {
  return Math.max(-3.0, Math.min(3.0, x));
}
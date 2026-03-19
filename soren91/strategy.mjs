/**
 * strategy.mjs - ドロップ位置決定戦略 (v11)
 *
 * v11改善点 (v10からの修正):
 * - Game #48: 55ターン中52ターンCRITICAL / score=0の根本原因対処
 * - CRITICAL閾値を大幅緩和: nearDeadline>=2→>=3, overDeadline>=1→>=2
 *   (v10は厳しすぎてほぼ全ターンCRITICALになっていた)
 * - MAX_ACTIVE_PIECES: 45→65 (解析用ピース数を増やして精度向上)
 * - boardPressure閾値: 40→55 (不必要な圧迫判定を軽減)
 * - ガベージ緊急モードをCRITICAL前に追加
 * - CRITICAL時のHOLD活用: lowLimitを緩和して選択肢を増やす
 * - 非CRITICAL時: クラスタリングステップを追加 (マージ不可時の準備)
 * - look-ahead: nextPieces[1,2]の活用を改善
 * - コーナー列(±2.5)へのドロップに軽いペナルティを追加
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 65;

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

  const boardPressure = activePieces.length > 55;

  const colHeights = computeColHeights(activePieces);
  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < -0.3).length;
  const rightDanger = dangerPieces.filter(p => p.x > 0.3).length;
  let dangerBias = 0;
  if (leftDanger > rightDanger + 3) dangerBias = -1;
  else if (rightDanger > leftDanger + 3) dangerBias = 1;

  // CRITICAL判定: 緩和 (nearDeadline>=3 or overDeadline>=2)
  const nearDeadlineCount = colHeights.filter(h => h > DEADLINE_Y - 0.3).length;
  const overDeadlineCount = colHeights.filter(h => h > DEADLINE_Y + 0.1).length;
  const isCritical = nearDeadlineCount >= 3 || overDeadlineCount >= 2;
  const isWarn = colHeights.some(h => h > WARN_Y + 0.5);

  // --- ガベージ緊急: CRITICAL判定前に最優先でマージ ---
  if (garbageUrgent) {
    const gbgMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, true, 0);
    if (gbgMerge) return { ...gbgMerge, reason: `GBG_MERGE_T${nextType}` };
    if (canHold && hold && hold.type) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1
      ).length;
      if (holdMergeCount > 0) {
        return { x: 0, reason: `GBG_HOLD_SWAP_T${hold.type}`, hold: true };
      }
    }
  }

  // --- CRITICAL: 最低列優先 + 低列限定マージ ---
  if (isCritical) {
    // 全列deadline超過の緊急時: 無条件最低列ドロップ
    if (overDeadlineCount >= FINE_COLS.length - 3) {
      const emergencyIdx = findLowestColIdx(colHeights);
      return { x: clampX(FINE_COLS[emergencyIdx]), reason: 'EMERGENCY_ALL_DANGER' };
    }

    // HOLDスワップ: 低列でHOLDがマージできて現ピースができない場合
    if (canHold && hold && hold.type) {
      const lowLimit = Math.min(avgHeight + 0.3, DEADLINE_Y - 0.4);
      const holdLowMerge = countLowColMerge(activePieces, hold.type, colHeights, lowLimit);
      const nextLowMerge = countLowColMerge(activePieces, nextType, colHeights, lowLimit);
      if (holdLowMerge > 0 && nextLowMerge === 0) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // 最低列を特定
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    const lowestColH = colHeights[lowestDrop.idx];

    // 低列(avgHeight+0.2以下)でのマージを許可 (v10より緩和: -0.5→+0.2)
    const lowMergeLimit = Math.min(avgHeight + 0.2, DEADLINE_Y - 0.4);
    const lowColMerge = findMergeInLowCol(activePieces, nextType, colHeights, lowMergeLimit, dangerBias);

    if (lowColMerge) {
      const mergeColH = colHeights[nearestColIdx(lowColMerge.x)];
      // マージ先より最低列が0.6以上低ければ最低列を優先 (v10: 0.5)
      if (lowestColH < mergeColH - 0.6) {
        return { x: lowestDrop.x, reason: `CRITICAL_LOW_COL_Y${lowestColH.toFixed(1)}` };
      }
      return { x: lowColMerge.x, reason: `CRITICAL_MERGE_T${nextType}` };
    }

    // HOLD保存: マージ不可 + 次ピースにマージあり
    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0) {
        const nextHasLowMerge = countLowColMerge(activePieces, nextPieceType, colHeights, lowMergeLimit) > 0;
        if (nextHasLowMerge) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_T${nextType}_FOR_T${nextPieceType}`, hold: true };
        }
      }
    }

    // マージなし: 最低列へドロップ
    return { x: lowestDrop.x, reason: `CRITICAL_DROP${lowestDrop.idx}_Y${lowestColH.toFixed(1)}` };
  }

  // --- HOLD判定 (非CRITICAL時) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces, isWarn);
    if (holdResult) return holdResult;
  }

  // ボード圧迫時
  if (boardPressure) {
    if (nextType >= 4) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  // 大型ピース優先マージ
  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  // チェーン期待値の高いマージ
  const chainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  // 通常マージ
  const normalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  // クラスタリング: マージ不可時は同タイプ近くに配置して将来マージを準備
  const clusterPlace = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
  if (clusterPlace) return { ...clusterPlace, reason: `CLUSTER_T${nextType}` };

  // 高さバランス
  return findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

/** 低列(h <= heightLimit)でのマージ先件数 */
function countLowColMerge(pieces, type, colHeights, heightLimit) {
  return pieces.filter(p => {
    const ci = nearestColIdx(p.x);
    return p.type === type &&
           Math.abs(p.x) < WALL_MARGIN &&
           p.y < DEADLINE_Y - 0.1 &&
           colHeights[ci] <= heightLimit;
  }).length;
}

/** 低列(colH <= heightLimit)でのマージ先を探す - CRITICAL用 */
function findMergeInLowCol(pieces, nextType, colHeights, heightLimit, dangerBias) {
  const candidates = pieces.filter(p => {
    const ci = nearestColIdx(p.x);
    return p.type === nextType &&
           Math.abs(p.x) < WALL_MARGIN &&
           p.y < DEADLINE_Y - 0.1 &&
           colHeights[ci] <= heightLimit;
  });

  if (candidates.length === 0) return null;

  let best = null;
  let bestScore = -Infinity;

  for (const t of candidates) {
    const ci = nearestColIdx(t.x);
    const colH = colHeights[ci];
    let s = -colH * 8.0;
    s += nextType * 1.5;

    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 4;

    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    s += c1 * 5;

    s -= Math.abs(t.x) * 0.3;
    if (Math.abs(t.x) > 2.2) s -= 4; // コーナーペナルティ

    if (dangerBias < 0 && t.x < -0.5) s -= 6;
    if (dangerBias > 0 && t.x > 0.5) s -= 6;

    if (s > bestScore) {
      bestScore = s;
      best = t;
    }
  }

  return best ? { x: clampX(best.x) } : null;
}

/** HOLD判定: 現ピースとHOLDの有利な方を使う */
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
      // look-ahead: nextPieces[1] or [2] にマージがあればHOLD保存
      const futurePieces = [
        nextPieces && nextPieces[1] ? nextPieces[1].type : 0,
        nextPieces && nextPieces[2] ? nextPieces[2].type : 0,
      ].filter(t => t > 0 && t !== nextType);
      for (const futureType of futurePieces) {
        const futureHasMerge = pieces.filter(p => p.type === futureType && p.y < safeY).length > 0;
        if (futureHasMerge) {
          return { x: 0, reason: `HOLD_SAVE_T${nextType}_FOR_T${futureType}`, hold: true };
        }
      }
    }
  }
  return null;
}

/** クラスタリング: 同タイプ近くに配置して将来マージを準備 */
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
    s -= Math.abs(cx) * 0.4;
    if (Math.abs(cx) > 2.2) s -= 3; // コーナーペナルティ

    if (dangerBias < 0 && cx < -0.5) s -= 5;
    if (dangerBias > 0 && cx > 0.5) s -= 5;

    if (s > bestScore) { bestScore = s; bestX = cx; }
  }

  return bestX !== null ? { x: clampX(bestX) } : null;
}

/** 列高さを計算 */
function computeColHeights(pieces) {
  return FINE_COLS.map(cx => {
    const col = pieces.filter(p => Math.abs(p.x - cx) < 0.45);
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

/** 最低かつ安全な列へドロップ (CRITICAL時) */
function findLowestSafeDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] >= DEADLINE_Y + 0.2) continue;

    let s = -colHeights[i] * 8.0;
    s -= Math.abs(FINE_COLS[i]) * 0.15;
    if (Math.abs(FINE_COLS[i]) > 2.2) s -= 2; // コーナーペナルティ

    if (dangerBias < 0 && FINE_COLS[i] < -1.0) s -= 8;
    if (dangerBias > 0 && FINE_COLS[i] > 1.0) s -= 8;

    if (s > bestScore) {
      bestScore = s;
      bestIdx = i;
    }
  }

  if (bestScore === -Infinity) {
    bestIdx = findLowestColIdx(colHeights);
  }

  return { x: clampX(FINE_COLS[bestIdx]), idx: bestIdx };
}

/**
 * 最良マージ位置を探す
 * minChainScore: 0=全マージ, 4=チェーン期待が高いもののみ
 */
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
    const colIdx = nearestColIdx(t.x);
    const colH = colHeights[colIdx];
    if (colH > DEADLINE_Y) continue;

    let s = 0;
    s -= colH * 4.0;
    if (colH > DEADLINE_Y - 0.4) s -= 10;
    if (colH > avgHeight + 0.8) s -= 5;

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
    if (Math.abs(t.x) > 2.2) s -= 3; // コーナーペナルティ

    if (garbageUrgent) s += 15;

    if (s > bestScore) {
      bestScore = s;
      bestTarget = t;
    }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
}

/** 高さバランスを優先した着弾位置 */
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
    if (Math.abs(cx) > 2.2) s -= 2; // コーナーペナルティ

    if (dangerBias < 0 && cx < -0.5) s -= 4;
    if (dangerBias > 0 && cx > 0.5) s -= 4;

    if (s > bestScore) {
      bestScore = s;
      bestIdx = i;
    }
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
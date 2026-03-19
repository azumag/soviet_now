/**
 * strategy.mjs - ドロップ位置決定戦略 (v9)
 *
 * v9改善点 (v8からの修正):
 * - 根本問題修正: v8はCRITICAL_MERGE_T1ループに陥りscore=0 (62ターン全てCRITICAL)
 * - CRITICAL判定を厳格化: 単一列でなく「2列以上deadline近接 OR 1列以上deadline超過」のみCRITICAL
 * - CRITICAL時マージ制限: 高い列のマージ先を除外 (高さ上限 = avgHeight+0.8)
 *   → 高列T1マージ→T2も高い→CRITICAL継続 のデスループを断ち切る
 * - 最低列ドロップ: 中央バイアスを極小化し純粋に高さ優先
 * - 左偏り修正: dangerBias閾値を3に引き上げ、中央を基準に判定
 * - evaluateHold: isWarnフラグ追加、警戒時は不要なHOLD保存を抑制
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 50;

export function decide(boardState) {
  const { pieces, next, nextPieces, confidence, garbage, hold, canHold } = boardState;
  const nextType = next ? next.type : 1;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  // X範囲でフィルタ後、Y座標上位50件に絞る (他ボードの混入対策)
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

  const boardPressure = activePieces.length > 50;

  const colHeights = computeColHeights(activePieces);
  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  // 危険側バイアス (閾値を3に引き上げ左偏り修正、中央±0.3を中立ゾーンに)
  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < -0.3).length;
  const rightDanger = dangerPieces.filter(p => p.x > 0.3).length;
  let dangerBias = 0;
  if (leftDanger > rightDanger + 3) dangerBias = -1;
  else if (rightDanger > leftDanger + 3) dangerBias = 1;

  // CRITICAL判定を厳格化: 2列以上deadline近接 OR 1列以上deadline超過
  const nearDeadlineCount = colHeights.filter(h => h > DEADLINE_Y - 0.3).length;
  const overDeadlineCount = colHeights.filter(h => h > DEADLINE_Y + 0.1).length;
  const isCritical = nearDeadlineCount >= 2 || overDeadlineCount >= 1;
  const isWarn = colHeights.some(h => h > WARN_Y + 0.5);

  // --- CRITICAL: 高さ制限付きマージ or 最低列 ---
  if (isCritical) {
    // HOLDスワップ: HOLDピースに安全列マージ先があり現ピースにない場合
    if (canHold && hold && hold.type) {
      const holdSafeMerge = activePieces.filter(p => {
        const ci = nearestColIdx(p.x);
        return p.type === hold.type &&
               p.y < DEADLINE_Y - 0.1 &&
               Math.abs(p.x) < WALL_MARGIN &&
               colHeights[ci] < avgHeight + 1.0;
      }).length;
      const nextSafeMerge = activePieces.filter(p => {
        const ci = nearestColIdx(p.x);
        return p.type === nextType &&
               p.y < DEADLINE_Y - 0.1 &&
               Math.abs(p.x) < WALL_MARGIN &&
               colHeights[ci] < avgHeight + 1.0;
      }).length;
      if (holdSafeMerge > nextSafeMerge) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // 安全列でのマージ (高さ上限付き: 高列T1マージループを防ぐ)
    const criticalMerge = findSafeColMerge(activePieces, nextType, colHeights, avgHeight, dangerBias, garbageUrgent);
    if (criticalMerge) return { ...criticalMerge, reason: `CRITICAL_MERGE_T${nextType}` };

    // HOLD保存: マージ先なし + 次ピースに安全マージあり
    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0) {
        const nextHasSafeMerge = activePieces.filter(p => {
          const ci = nearestColIdx(p.x);
          return p.type === nextPieceType &&
                 p.y < DEADLINE_Y - 0.1 &&
                 Math.abs(p.x) < WALL_MARGIN &&
                 colHeights[ci] < avgHeight + 1.0;
        }).length > 0;
        if (nextHasSafeMerge) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_FOR_T${nextPieceType}`, hold: true };
        }
      }
    }

    // マージなし: 最低列へ直接ドロップ (中央バイアス最小化)
    const bestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: bestDrop.x, reason: `CRITICAL_DROP${bestDrop.idx}_Y${colHeights[bestDrop.idx].toFixed(1)}` };
  }

  // --- HOLD判定 (非CRITICAL時) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces, isWarn);
    if (holdResult) return holdResult;
  }

  // ボード圧迫時: 大型マージ → 高さ管理
  if (boardPressure) {
    if (nextType >= 5) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  // --- 1. 大型ピース(type>=6)の優先マージ ---
  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  // --- 2. チェーン期待値の高いマージ ---
  const chainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  // --- 3. 通常マージ ---
  const normalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  // --- 4. 高さバランス ---
  return findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

/** HOLD判定: 現ピースとHOLDの有利な方を使う (isWarn時は保存を抑制) */
function evaluateHold(pieces, nextType, hold, nextPieces, isWarn) {
  const safeY = DEADLINE_Y - 0.3;
  const nextMergeCount = pieces.filter(p => p.type === nextType && p.y < safeY).length;

  if (hold && hold.type) {
    const holdMergeCount = pieces.filter(p => p.type === hold.type && p.y < safeY).length;
    // HOLDの方が明らかにマージ有利で現ピースはマージ無し
    if (holdMergeCount > nextMergeCount && nextMergeCount === 0) {
      return { x: 0, reason: `HOLD_SWAP_T${hold.type}vs${nextType}`, hold: true };
    }
    // HOLDが大型で現ピースが小型かつHOLDにマージ先あり
    if (hold.type >= 5 && nextType <= 3 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_BIGTYPE_T${hold.type}`, hold: true };
    }
  } else {
    // HOLDが空: マージ先なし + 盤面余裕あり + 警戒中でない + 次ピースにマージ先あり
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

/** CRITICAL時: 安全な高さ範囲の列にいるマージ先のみ探す */
function findSafeColMerge(pieces, nextType, colHeights, avgHeight, dangerBias, garbageUrgent) {
  // おじゃま緊急時は高さ制限を緩める (マージでおじゃま消去優先)
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
    const colIdx = nearestColIdx(t.x);
    const colH = colHeights[colIdx];

    let s = -colH * 5.0;  // 高さを強く優先
    s += nextType * 1.5;

    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 4;

    // チェーン評価
    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    s += c1 * 4;

    s -= Math.abs(t.x) * 0.2;

    if (dangerBias < 0 && t.x < -0.5) s -= 6;
    if (dangerBias > 0 && t.x > 0.5) s -= 6;

    if (s > bestScore) {
      bestScore = s;
      bestTarget = t;
    }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `SAFE_MERGE` };
}

/** 最低かつ安全な列へドロップ (CRITICAL時) */
function findLowestSafeDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] >= DEADLINE_Y + 0.2) continue;  // 超過列は除外

    let s = -colHeights[i] * 6.0;  // 高さ最優先 (旧: 3.0)
    s -= Math.abs(FINE_COLS[i]) * 0.15;  // 極めて弱い中央バイアス (旧: 0.4)

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

/** 列高さを計算 */
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

    s -= colH * 2.5;
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
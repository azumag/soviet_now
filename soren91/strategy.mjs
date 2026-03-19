/**
 * strategy.mjs - ドロップ位置決定戦略 (v8)
 *
 * v8改善点:
 * - CRITICAL モードでマージ最優先: マージでピース数を減らすことが最重要
 *   (旧: 即座に最低列へ → マージゼロ → 常にCRITICAL → score=0 のデスループ)
 * - CRITICAL時のHOLD活用: HOLDピースにマージ先があればスワップ
 * - CRITICAL時の着地選択: 純粋な最低列ではなく高さ+中央バイアスの複合スコア
 * - HOLD判定をCRITICAL分岐の後に移動 (CRITICAL時は専用HOLD判断を優先)
 * - nextPieces先読みをHOLD保存判断に追加
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.5;
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

  // おじゃま状態
  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  const garbageUrgent = garbageRatio > 0.3 || garbageHeight > 0.5;

  // ボード圧迫度
  const boardPressure = activePieces.length > 50;

  // 細粒度列高さ
  const colHeights = computeColHeights(activePieces);

  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  // 危険側バイアス
  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;
  let dangerBias = 0;
  if (leftDanger > rightDanger + 2) dangerBias = -1;
  else if (rightDanger > leftDanger + 2) dangerBias = 1;

  // --- 緊急: deadline近接列がある場合 ---
  const criticalCount = colHeights.filter(h => h > DEADLINE_Y - 0.3).length;
  if (criticalCount > 0) {
    // v8 FIX: マージを最優先 (マージでピース数↓→高さ↓→CRITICAL脱出)

    // HOLD スワップ: HOLDピースにマージ先があり現ピースにない場合
    if (canHold && hold && hold.type) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1 && Math.abs(p.x) < WALL_MARGIN
      ).length;
      const nextMergeCount = activePieces.filter(p =>
        p.type === nextType && p.y < DEADLINE_Y - 0.1 && Math.abs(p.x) < WALL_MARGIN
      ).length;
      if (holdMergeCount > nextMergeCount) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // マージ先を探す (これが核心: マージでピース数削減→高さ低下)
    const criticalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (criticalMerge) return { ...criticalMerge, reason: `CRITICAL_MERGE_T${nextType}` };

    // HOLD保存: マージ先なし + 次ピースにマージ先あり → 保存して次を使う
    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0) {
        const nextNextMerge = activePieces.filter(p =>
          p.type === nextPieceType && p.y < DEADLINE_Y - 0.1 && Math.abs(p.x) < WALL_MARGIN
        ).length;
        if (nextNextMerge > 0) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_FOR_T${nextPieceType}`, hold: true };
        }
      }
    }

    // マージなし: 高さ+中央バイアスで最良列を選択 (純粋最低列よりバランス良い)
    const bestDrop = findBestCriticalDrop(colHeights, dangerBias);
    return { x: bestDrop.x, reason: `CRITICAL_COL${bestDrop.idx}_Y${colHeights[bestDrop.idx].toFixed(1)}` };
  }

  // --- HOLD判定 (非CRITICAL時) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces);
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

/** HOLD判定: 現ピースとHOLDの有利な方を使う */
function evaluateHold(pieces, nextType, hold, nextPieces) {
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
    // HOLDが空: マージ先なし + 盤面余裕あり → 保存
    if (nextMergeCount === 0 && pieces.length > 15 && pieces.length < 50) {
      // 次ピースにマージ先があれば保存する価値あり
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

/** CRITICAL時の最良ドロップ位置: 高さ+中央バイアスの複合スコア */
function findBestCriticalDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5; // デフォルト中央

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] > DEADLINE_Y) continue; // 超過列は除外

    let s = -colHeights[i] * 3.0; // 低い列を強く優先
    s -= Math.abs(FINE_COLS[i]) * 0.4; // 中央寄りバイアス

    if (dangerBias < 0 && FINE_COLS[i] < -0.5) s -= 5;
    if (dangerBias > 0 && FINE_COLS[i] > 0.5) s -= 5;

    if (s > bestScore) {
      bestScore = s;
      bestIdx = i;
    }
  }

  // 全列がdeadline超過の場合
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

    // 着弾列の安全度
    s -= colH * 2.5;
    if (colH > DEADLINE_Y - 0.4) s -= 10;
    if (colH > avgHeight + 0.8) s -= 3;

    // 大型マージの価値
    s += nextType * 1.5;

    // 3段チェーン評価
    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    const c3 = countNear(pieces, t.x, nextType + 3, 2.6);
    const chainScore = c1 * 6 + c2 * 3 + c3 * 1.5;
    if (chainScore < minChainScore) continue;
    s += chainScore;

    // 近接同タイプ: 多重マージ期待
    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 3;

    // 危険側スコアペナルティ
    if (dangerBias < 0 && t.x < -0.5) s -= 5;
    if (dangerBias > 0 && t.x > 0.5) s -= 5;

    // 中央寄りボーナス
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
/**
 * strategy.mjs - ドロップ位置決定戦略 (v9)
 *
 * v9改善点:
 * - CRITICAL閾値を大幅引き上げ: DEADLINE_Y-0.3(=2.2) → DEADLINE_Y(=2.5) のみ
 *   旧閾値が低すぎてゲーム開始直後から常にCRITICALになるデスループを防止
 * - WARN_MODE追加: DEADLINE_Y-0.8 で3列以上→マージ優先の警戒モード
 * - ピース重複除去: 近接ピース(dist<0.25)は同一として処理 (ノイズ対策)
 * - 過多ピース(>80)を信頼性低として扱いスプレッド戦略
 * - CRITICAL時のマージ探索を寛容化: Y制約を緩めてdeadline付近のピースもマージ対象に
 * - HOLD機能は完全保持
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const CRITICAL_Y = DEADLINE_Y;       // v9: 実際にdeadlineを超えた時のみCRITICAL
const WARN_Y = DEADLINE_Y - 0.8;     // v9: 早期警戒閾値
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 50;
const NOISE_THRESHOLD = 80;

export function decide(boardState) {
  const { pieces, next, nextPieces, confidence, garbage, hold, canHold } = boardState;
  const nextType = next ? next.type : 1;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  // X範囲フィルタ
  const rawFiltered = pieces.filter(p => Math.abs(p.x) <= 3.2);

  // 重複除去 (近接ピースは同一として処理)
  let activePieces = deduplicatePieces(rawFiltered);

  // 過多ピース or 低信頼度 = ノイズとして扱いスプレッド
  const tooManyPieces = rawFiltered.length > NOISE_THRESHOLD;
  if (confidence < 0.3 || tooManyPieces) {
    const safeX = findLeastOccupiedX(activePieces);
    return { x: safeX, reason: `SPREAD_${tooManyPieces ? 'NOISE' : 'UNRELIABLE'}` };
  }

  // Y座標上位50件に絞る
  if (activePieces.length > MAX_ACTIVE_PIECES) {
    activePieces = [...activePieces].sort((a, b) => b.y - a.y).slice(0, MAX_ACTIVE_PIECES);
  }

  // おじゃま状態
  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  const garbageUrgent = garbageRatio > 0.3 || garbageHeight > 0.5;

  // ボード圧迫度
  const boardPressure = activePieces.length > 50;

  // 列高さ計算
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

  // --- CRITICAL判定 (v9: 実際にdeadline超過のみ) ---
  const criticalCount = colHeights.filter(h => h > CRITICAL_Y).length;
  if (criticalCount > 0) {
    // HOLD スワップ: HOLDピースにマージ先があり現ピースにない場合
    if (canHold && hold && hold.type) {
      const holdMerges = activePieces.filter(p =>
        p.type === hold.type && Math.abs(p.x) < WALL_MARGIN
      ).length;
      const nextMerges = activePieces.filter(p =>
        p.type === nextType && Math.abs(p.x) < WALL_MARGIN
      ).length;
      if (holdMerges > nextMerges) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // CRITICAL時: より寛容なマージ探索 (Y制約を緩めてdeadline付近もOK)
    const criticalMerge = findBestMergeCritical(activePieces, nextType, colHeights, dangerBias);
    if (criticalMerge) return criticalMerge;

    // HOLD保存: マージ先なし + 次ピースにマージ先あり
    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0) {
        const nextNextMerges = activePieces.filter(p =>
          p.type === nextPieceType && Math.abs(p.x) < WALL_MARGIN
        ).length;
        if (nextNextMerges > 0) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_T${nextPieceType}`, hold: true };
        }
      }
    }

    // マージなし: 最も安全な列に落とす
    const bestDrop = findBestCriticalDrop(colHeights, dangerBias);
    return { x: bestDrop.x, reason: `CRITICAL_COL${bestDrop.idx}_Y${colHeights[bestDrop.idx].toFixed(1)}` };
  }

  // --- WARN_MODE: 3列以上が警戒ラインを超えたらマージ強化 ---
  const warnCount = colHeights.filter(h => h > WARN_Y).length;
  if (warnCount >= 3) {
    const warnMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (warnMerge) return { ...warnMerge, reason: `WARN_MERGE_T${nextType}` };
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

/**
 * CRITICAL時のマージ探索 (寛容版)
 * - Y制約を緩めてdeadline付近のピースも対象に
 * - 低い位置のマージ先を最優先
 */
function findBestMergeCritical(pieces, nextType, colHeights, dangerBias) {
  const candidates = pieces.filter(p =>
    p.type === nextType &&
    Math.abs(p.x) < WALL_MARGIN
    // NOTE: Y制約なし — deadline超えのピースにもマージを試みる
  );
  if (candidates.length === 0) return null;

  let bestTarget = null;
  let bestScore = -Infinity;

  for (const t of candidates) {
    const colIdx = nearestColIdx(t.x);
    const colH = colHeights[colIdx];

    let s = 0;
    s -= t.y * 2.0;     // 低い位置のマージ先を優先
    s -= colH * 1.5;    // 低い列を優先
    s -= Math.abs(t.x) * 0.3;  // 中央寄り

    // 近接同タイプボーナス (複数同時マージ期待)
    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.5).length;
    s += nearSame * 5;

    // チェーン評価
    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    s += c1 * 4 + c2 * 2;

    if (dangerBias < 0 && t.x < -0.5) s -= 3;
    if (dangerBias > 0 && t.x > 0.5) s -= 3;

    if (s > bestScore) {
      bestScore = s;
      bestTarget = t;
    }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `CRITICAL_MERGE_T${nextType}` };
}

/** ピース重複除去: dist < 0.25 の同一位置ピースを除去 */
function deduplicatePieces(pieces) {
  const result = [];
  const used = new Set();
  for (let i = 0; i < pieces.length; i++) {
    if (used.has(i)) continue;
    result.push(pieces[i]);
    for (let j = i + 1; j < pieces.length; j++) {
      if (used.has(j)) continue;
      const dx = pieces[i].x - pieces[j].x;
      const dy = pieces[i].y - pieces[j].y;
      if (dx * dx + dy * dy < 0.0625) { // 0.25^2
        used.add(j);
      }
    }
  }
  return result;
}

/** HOLD判定: 現ピースとHOLDの有利な方を使う */
function evaluateHold(pieces, nextType, hold, nextPieces) {
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
    if (nextMergeCount === 0 && pieces.length > 15 && pieces.length < 50) {
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
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i] * 3.0;
    s -= Math.abs(FINE_COLS[i]) * 0.4;
    if (dangerBias < 0 && FINE_COLS[i] < -0.5) s -= 5;
    if (dangerBias > 0 && FINE_COLS[i] > 0.5) s -= 5;

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
 * 最良マージ位置を探す (通常用)
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
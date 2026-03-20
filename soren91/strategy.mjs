/**
 * strategy.mjs - ドロップ位置決定戦略 (v24)
 *
 * v24改善点 (v23からの改善):
 * - 【CRITICAL T1フラッド対応】CRITICAL+T1フラッド時にfindT1StackColumn/findT1ChainAnchorを試行
 *   v23ではultraMassModeのみ適用だったがcriticalモードでも同様に有効
 *   garbageFloodMode時は除外 (T1マージ無効フラグと競合するため)
 * - 【CRITICAL HOLD upgrade】hold.type >= nextType+2 かつ lowColMergeあり → 積極的HOLD swap
 *   既存比較ロジックはcount比較のみ; 高タイプのHOLDはchain反応が大きいため count≤でも優先
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 65;
const ULTRA_MASS_THRESHOLD = 45;        // v23: 50→45 早期対応
const EXTREME_T1_FLOOD_THRESHOLD = 30;  // v22: T1極端洪水しきい値
const SURVIVAL_PIECE_THRESHOLD = 72;    // v23: 78→72 生存モード早期化

export function decide(boardState) {
  const { pieces, next, nextPieces, confidence, garbage, hold, canHold, score } = boardState;
  const nextType = next ? next.type : 1;

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

  // v22: T1フラッド検出 (extremeT1Flood追加)
  const t1Count = activePieces.filter(p => p.type === 1).length;
  const t1FloodMode = t1Count > 15;
  const extremeT1Flood = t1Count > EXTREME_T1_FLOOD_THRESHOLD;

  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  const garbageUrgent = garbageRatio > 0.4 || garbageHeight > 1.2;

  const colHeights = computeColHeights(activePieces);
  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  const garbageFloodMode = rawPieceCount > 55 && avgHeight > 1.3;

  const ultraMassMode = rawPieceCount >= ULTRA_MASS_THRESHOLD;
  const massMode = activePieces.length >= MAX_ACTIVE_PIECES - 5;
  const boardPressure = activePieces.length > 55;

  const balanceBias = computeBalanceBias(activePieces, colHeights);

  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < -0.3).length;
  const rightDanger = dangerPieces.filter(p => p.x > 0.3).length;
  let dangerBias = balanceBias;
  if (leftDanger > rightDanger + 3) dangerBias = Math.min(dangerBias - 1, -1);
  else if (rightDanger > leftDanger + 3) dangerBias = Math.max(dangerBias + 1, 1);

  const nearDeadlineCount = colHeights.filter(h => h > DEADLINE_Y - 0.3).length;
  const overDeadlineCount = colHeights.filter(h => h > DEADLINE_Y + 0.1).length;
  const isCritical = nearDeadlineCount >= 3 || overDeadlineCount >= 2;
  const isWarn = colHeights.some(h => h > WARN_Y + 0.5);

  // --- v23: 生存最優先モード (ピース数が極端に多い時、CRITICAL除外) ---
  if (rawPieceCount >= SURVIVAL_PIECE_THRESHOLD && !isCritical) {
    // HOLDに高タイプがあれば積極的に使う
    if (canHold && hold && hold.type >= 2) {
      const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
      if (holdMerge !== null) return { x: 0, reason: `SURVIVE_HOLD_T${hold.type}`, hold: true };
    }
    const survivalMerge = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
    if (survivalMerge !== null) return { x: survivalMerge, reason: `SURVIVE_MERGE_T${nextType}_PC${rawPieceCount}` };
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `SURVIVE_LOW_PC${rawPieceCount}` };
  }

  // --- ULTRAマスモード ---
  if (ultraMassMode) {
    if (Math.abs(balanceBias) >= 2) {
      const balanceDrop = findLowestSafeDrop(colHeights, dangerBias);
      return { x: balanceDrop.x, reason: `ULTRA_BALANCE_BIAS${balanceBias > 0 ? 'R' : 'L'}` };
    }

    if (canHold && hold && hold.type >= 2 && hold.type > nextType) {
      const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
      if (holdMerge !== null) return { x: 0, reason: `ULTRA_HOLD_T${hold.type}`, hold: true };
    }

    if (nextType === 1) {
      // v23: extremeT1Flood時: findT1StackColumn → findT1ChainAnchor の順で試行
      if (extremeT1Flood) {
        // HOLDが空でnext2/3がT2+ならT1をHOLD
        if (canHold && !hold) {
          const next2Type = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
          const next3Type = nextPieces && nextPieces[2] ? nextPieces[2].type : 0;
          const bestNext = Math.max(next2Type, next3Type);
          if (bestNext >= 2) {
            return { x: 0, reason: `ULTRA_EXTREME_HOLD_T1_FOR_T${bestNext}`, hold: true };
          }
        }
        // HOLDにT2+があれば使う
        if (canHold && hold && hold.type >= 2) {
          const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
          if (holdMerge !== null) return { x: 0, reason: `ULTRA_EXTREME_SWAP_T${hold.type}`, hold: true };
        }
        // v23: 縦積みT1列を最優先で探す → 物理マージが発生しやすい
        const stackCol = findT1StackColumn(activePieces, colHeights, dangerBias);
        if (stackCol !== null) return { x: stackCol, reason: 'ULTRA_EXTREME_T1_STACK' };
        // チェーンアンカーにフォールバック
        const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
        if (chainAnchor !== null) return { x: chainAnchor, reason: 'ULTRA_EXTREME_T1_ANCHOR' };
        const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
        return { x: lowestDrop.x, reason: `ULTRA_EXTREME_T1_LOWEST` };
      }

      // 通常T1フラッドモード (T1<=30)
      if (t1FloodMode) {
        const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
        if (denseX !== null) return { x: denseX, reason: 'ULTRA_T1_DENSE' };
      }
      const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, t1FloodMode);
      if (chainAnchor !== null) return { x: chainAnchor, reason: `ULTRA_CHAIN_ANCHOR_T1` };
    }

    const ultraMergeX = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
    if (ultraMergeX !== null) return { x: ultraMergeX, reason: `ULTRA_MERGE_T${nextType}` };

    if (canHold && nextType === 1 && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType >= 2) {
        return { x: 0, reason: `ULTRA_HOLD_T1_NEXT_T${nextPieceType}`, hold: true };
      }
    }
    const ultraCluster = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
    if (ultraCluster) return { x: ultraCluster.x, reason: `ULTRA_CLUSTER_T${nextType}` };
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `ULTRA_LOWEST_Y${colHeights[lowestDrop.idx].toFixed(1)}` };
  }

  // --- 大量ピースモード: 非CRITICAL時のみ (マージ優先で生存) ---
  if (massMode && !isCritical) {
    const anyMergeX = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
    if (anyMergeX !== null) return { x: anyMergeX, reason: `MASS_MERGE_T${nextType}` };
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `MASS_LOW_COL_Y${colHeights[lowestDrop.idx].toFixed(1)}` };
  }

  // --- CRITICAL を garbageUrgent より先に処理 (高さ管理最優先) ---
  if (isCritical) {
    if (overDeadlineCount >= FINE_COLS.length - 3) {
      const emergencyIdx = findLowestColIdx(colHeights);
      return { x: clampX(FINE_COLS[emergencyIdx]), reason: 'EMERGENCY_ALL_DANGER' };
    }

    const critMergeLimit = DEADLINE_Y - 0.1;

    const critGbgMinType = 2;
    if (garbageUrgent && nextType >= critGbgMinType) {
      const critGbgMerge = findMergeInLowCol(activePieces, nextType, colHeights, critMergeLimit, dangerBias);
      if (critGbgMerge) {
        return { x: critGbgMerge.x, reason: `CRITICAL_GBG_T${nextType}` };
      }
    }

    if (garbageFloodMode && nextType === 1 && canHold && hold && hold.type >= 2) {
      return { x: 0, reason: `FLOOD_CRIT_SWAP_T${hold.type}`, hold: true };
    }

    if (canHold && hold && hold.type) {
      const holdLowMerge = countLowColMerge(activePieces, hold.type, colHeights, critMergeLimit);
      const nextLowMerge = countLowColMerge(activePieces, nextType, colHeights, critMergeLimit);
      if (holdLowMerge > nextLowMerge && holdLowMerge > 0) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    // v24: HOLD upgrade — hold が nextType+2以上で低列マージがある場合は count比較なしで優先
    // 高タイプのマージはチェーン反応が大きくゲームボード圧力を解消しやすい
    if (canHold && hold && hold.type >= nextType + 2) {
      const holdUpgrade = countLowColMerge(activePieces, hold.type, colHeights, critMergeLimit);
      if (holdUpgrade > 0) {
        return { x: 0, reason: `CRITICAL_HOLD_UPGRADE_T${hold.type}`, hold: true };
      }
    }

    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    const lowestColH = colHeights[lowestDrop.idx];

    // v24: CRITICAL + T1フラッドモード時はstack列 → chain anchorを試行
    // garbageFloodMode時はT1マージ無効(minMergeType=99)と競合するため除外
    if (nextType === 1 && t1FloodMode && !garbageFloodMode) {
      const critT1Stack = findT1StackColumn(activePieces, colHeights, dangerBias);
      if (critT1Stack !== null) return { x: critT1Stack, reason: 'CRITICAL_T1_STACK' };
      const critT1Chain = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (critT1Chain !== null) return { x: critT1Chain, reason: 'CRITICAL_T1_ANCHOR' };
    }

    const minMergeType = (garbageFloodMode && nextType === 1) ? 99 : 1;
    const lowColMerge = nextType >= minMergeType
      ? findMergeInLowCol(activePieces, nextType, colHeights, critMergeLimit, dangerBias)
      : null;

    if (lowColMerge) {
      return { x: lowColMerge.x, reason: `CRITICAL_MERGE_T${nextType}` };
    }

    if (nextType >= 1) {
      const critFallbackMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0);
      if (critFallbackMerge) {
        const mergeColH = colHeights[nearestColIdx(critFallbackMerge.x)];
        if (mergeColH < DEADLINE_Y + 0.1) {
          return { x: critFallbackMerge.x, reason: `CRITICAL_ANY_MERGE_T${nextType}` };
        }
      }
    }

    if (canHold && !hold) {
      const nextPieceType = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
      if (nextPieceType > 0) {
        const nextHasLowMerge = countLowColMerge(activePieces, nextPieceType, colHeights, critMergeLimit) > 0;
        if (nextHasLowMerge) {
          return { x: 0, reason: `CRITICAL_HOLD_SAVE_T${nextType}_FOR_T${nextPieceType}`, hold: true };
        }
      }
    }

    return { x: lowestDrop.x, reason: `CRITICAL_DROP${lowestDrop.idx}_Y${lowestColH.toFixed(1)}` };
  }

  // --- ガベージ緊急 (非CRITICAL時のみ到達) ---
  if (garbageUrgent) {
    if (canHold && hold && hold.type && hold.type > nextType) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1
      ).length;
      const nextMergeCount = activePieces.filter(p =>
        p.type === nextType && p.y < DEADLINE_Y - 0.1
      ).length;
      if (holdMergeCount > 0 && holdMergeCount >= nextMergeCount) {
        return { x: 0, reason: `GBG_HOLD_UPGRADE_T${hold.type}`, hold: true };
      }
    }

    if (garbageFloodMode && nextType === 1 && canHold && !hold) {
      return { x: 0, reason: `FLOOD_GBG_HOLD_T1`, hold: true };
    }

    const gbgMinType = garbageFloodMode ? 2 : 1;
    if (nextType >= gbgMinType) {
      const gbgMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, true, 0);
      if (gbgMerge) return { ...gbgMerge, reason: `GBG_MERGE_T${nextType}` };
    }

    if (canHold && hold && hold.type) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1
      ).length;
      if (holdMergeCount > 0) {
        return { x: 0, reason: `GBG_HOLD_SWAP_T${hold.type}`, hold: true };
      }
    }
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `GBG_LOW_COL_Y${colHeights[lowestDrop.idx].toFixed(1)}` };
  }

  // --- HOLD判定 (非CRITICAL時) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces, isWarn);
    if (holdResult) return holdResult;
  }

  // T1ピースは早期にT1フラッドチェック
  if (nextType === 1) {
    if (t1FloodMode && !extremeT1Flood) {
      const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
      if (denseX !== null) return { x: denseX, reason: 'T1_FLOOD_DENSE' };
    }
    if (extremeT1Flood) {
      // 通常モードでもextremeT1Flood時は積み列 → 最低列優先
      const stackCol = findT1StackColumn(activePieces, colHeights, dangerBias);
      if (stackCol !== null) return { x: stackCol, reason: 'T1_EXTREME_STACK' };
      const t1Chain = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (t1Chain !== null) return { x: t1Chain, reason: 'T1_EXTREME_ANCHOR' };
      const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
      return { x: lowestDrop.x, reason: `T1_EXTREME_LOWEST` };
    }
    const t1Chain = findT1ChainAnchor(activePieces, colHeights, dangerBias, t1FloodMode);
    if (t1Chain !== null) return { x: t1Chain, reason: 'T1_CHAIN_ANCHOR' };
    const t1Merge = findAnyMerge(activePieces, 1, colHeights, dangerBias);
    if (t1Merge !== null) return { x: t1Merge, reason: 'T1_MERGE' };
  }

  if (boardPressure) {
    if (nextType >= 4) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  const chainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  const normalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  const clusterPlace = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
  if (clusterPlace) return { ...clusterPlace, reason: `CLUSTER_T${nextType}` };

  return findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

function computeBalanceBias(pieces, colHeights) {
  const leftMass = pieces.filter(p => p.x < -0.3).reduce((s, p) => s + p.type, 0);
  const rightMass = pieces.filter(p => p.x > 0.3).reduce((s, p) => s + p.type, 0);
  const leftH = colHeights.slice(0, 4).filter(h => h > -4.5);
  const rightH = colHeights.slice(7).filter(h => h > -4.5);
  const avgLeftH = leftH.length > 0 ? leftH.reduce((a, b) => a + b, 0) / leftH.length : -3;
  const avgRightH = rightH.length > 0 ? rightH.reduce((a, b) => a + b, 0) / rightH.length : -3;
  const combined = (rightMass - leftMass) * 0.02 + (avgRightH - avgLeftH) * 0.5;
  if (combined > 1.2) return 2;
  if (combined > 0.5) return 1;
  if (combined < -1.2) return -2;
  if (combined < -0.5) return -1;
  return 0;
}

/**
 * T1フラッドモード専用: T1が最も密集した列に誘導してT1→T2チェーン促進
 * v22: 高さペナルティ強化(5.0→10.0)、WARN_Y以上の列を除外
 */
function findT1DenseColumn(pieces, colHeights, dangerBias) {
  const t1Pieces = pieces.filter(p =>
    p.type === 1 && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y
  );
  if (t1Pieces.length < 2) return null;

  let bestScore = -Infinity;
  let bestCol = null;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    // v22: WARN_Y以上の列は除外 (高い列にT1を積まない)
    if (colHeights[i] >= WARN_Y) continue;
    if (colHeights[i] >= DEADLINE_Y + 0.1) continue;
    const nearT1 = t1Pieces.filter(p => Math.abs(p.x - cx) < 0.8).length;
    if (nearT1 < 2) continue;
    const nearT2 = countNear(pieces, cx, 2, 1.5);
    const nearT3 = countNear(pieces, cx, 3, 2.0);
    let s = nearT1 * 20;
    s += nearT2 * 10;
    s += nearT3 * 5;
    s -= colHeights[i] * 10.0;  // v22: 5.0→10.0 高さペナルティ強化
    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 8;
    if (dangerBias >= 2 && cx > 0) s -= 15;
    if (dangerBias <= -2 && cx < 0) s -= 15;
    if (dangerBias >= 1 && cx > 0.5) s -= 6;
    if (dangerBias <= -1 && cx < -0.5) s -= 6;
    if (s > bestScore) { bestScore = s; bestCol = i; }
  }

  return bestCol !== null ? clampX(FINE_COLS[bestCol]) : null;
}

/**
 * v23新機能: T1が縦に積み重なった列を検出
 * 縦積みのT1群の上にドロップすると物理的にマージが発生しやすい
 */
function findT1StackColumn(pieces, colHeights, dangerBias) {
  const t1Pieces = pieces.filter(p =>
    p.type === 1 && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y
  );
  if (t1Pieces.length < 3) return null;

  let bestScore = -Infinity;
  let bestCol = null;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] >= DEADLINE_Y) continue;

    // この列のT1ピース (水平範囲を狭くして縦積みを確認)
    const colT1 = t1Pieces.filter(p => Math.abs(p.x - cx) < 0.55);
    if (colT1.length < 2) continue;

    // 縦密度: T1ピースが縦に詰まっているか
    const yVals = colT1.map(p => p.y).sort((a, b) => a - b);
    const yRange = yVals[yVals.length - 1] - yVals[0];
    const density = colT1.length / (yRange + 0.5); // ピース数/Y範囲

    let s = density * 20;          // 縦密度を最優先
    s += colT1.length * 5;         // ピース数も加算
    s -= colHeights[i] * 8.0;      // 低い列を優先
    s -= Math.abs(cx) * 2.0;       // 中央寄りを優先
    if (Math.abs(cx) > 2.2) s -= 8;

    // 最上部のT1が列トップ付近なら更に優遇 (ドロップで直接マージが起きやすい)
    const topT1Y = Math.max(...colT1.map(p => p.y));
    if (Math.abs(topT1Y - colHeights[i]) < 0.5) s += 15;

    if (dangerBias >= 2 && cx > 0) s -= 15;
    if (dangerBias <= -2 && cx < 0) s -= 15;
    if (dangerBias >= 1 && cx > 0.5) s -= 6;
    if (dangerBias <= -1 && cx < -0.5) s -= 6;

    if (s > bestScore) { bestScore = s; bestCol = i; }
  }

  return bestCol !== null ? clampX(FINE_COLS[bestCol]) : null;
}

/**
 * T1専用チェーンアンカー
 * v21: t1FloodModeパラメータ追加
 */
function findT1ChainAnchor(pieces, colHeights, dangerBias, t1Flood = false) {
  const t1Pieces = pieces.filter(p =>
    p.type === 1 && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y
  );
  if (t1Pieces.length === 0) return null;

  let best = null;
  let bestScore = -Infinity;

  for (const t1 of t1Pieces) {
    const ci = nearestColIdx(t1.x);
    if (colHeights[ci] >= DEADLINE_Y + 0.2) continue;

    const nearT1 = t1Pieces.filter(p => p !== t1 && Math.abs(p.x - t1.x) < 1.2).length;
    const nearT2 = countNear(pieces, t1.x, 2, 2.0);
    const nearT3 = countNear(pieces, t1.x, 3, 2.2);
    const nearT4 = countNear(pieces, t1.x, 4, 2.4);

    if (!t1Flood && nearT2 === 0) continue;
    if (t1Flood && nearT2 === 0 && nearT1 < 2) continue;

    let s = nearT2 * 16;
    s += nearT3 * 8;
    s += nearT4 * 4;
    if (t1Flood) s += nearT1 * 10;
    s -= colHeights[ci] * 4.0;
    s -= Math.abs(t1.x) * 2.0;
    if (Math.abs(t1.x) > 2.2) s -= 8;
    if (dangerBias < 0 && t1.x < -0.5) s -= 8;
    if (dangerBias > 0 && t1.x > 0.5) s -= 8;
    if (dangerBias >= 2 && t1.x > 0) s -= 15;
    if (dangerBias <= -2 && t1.x < 0) s -= 15;

    if (s > bestScore) { bestScore = s; best = t1; }
  }

  return best ? clampX(best.x) : null;
}

function findAnyMerge(pieces, nextType, colHeights, dangerBias) {
  const candidates = pieces.filter(p =>
    p.type === nextType && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y
  );
  if (candidates.length === 0) return null;

  let best = null;
  let bestScore = -Infinity;

  for (const t of candidates) {
    const ci = nearestColIdx(t.x);
    if (colHeights[ci] >= DEADLINE_Y + 0.2) continue;
    let s = -colHeights[ci] * 6.0;
    s -= Math.abs(t.x) * 2.0;
    if (Math.abs(t.x) > 2.2) s -= 6;
    if (dangerBias <= -1 && t.x < -0.5) s -= 6;
    if (dangerBias >= 1 && t.x > 0.5) s -= 6;
    if (dangerBias <= -2 && t.x < 0) s -= 10;
    if (dangerBias >= 2 && t.x > 0) s -= 10;
    s += countNear(pieces, t.x, nextType + 1, 1.8) * 10;
    s += countNear(pieces, t.x, nextType + 2, 2.2) * 5;
    s += countNear(pieces, t.x, nextType + 3, 2.6) * 3;
    if (s > bestScore) { bestScore = s; best = t; }
  }

  return best ? clampX(best.x) : null;
}

function countLowColMerge(pieces, type, colHeights, heightLimit) {
  return pieces.filter(p => {
    const ci = nearestColIdx(p.x);
    return p.type === type &&
           Math.abs(p.x) < WALL_MARGIN &&
           p.y < DEADLINE_Y - 0.1 &&
           colHeights[ci] <= heightLimit;
  }).length;
}

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
    s += candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length * 4;
    s += countNear(pieces, t.x, nextType + 1, 1.8) * 6;
    s += countNear(pieces, t.x, nextType + 2, 2.2) * 3;
    s -= Math.abs(t.x) * 2.0;
    if (Math.abs(t.x) > 2.2) s -= 4;
    if (dangerBias <= -1 && t.x < -0.5) s -= 6;
    if (dangerBias >= 1 && t.x > 0.5) s -= 6;
    if (s > bestScore) { bestScore = s; best = t; }
  }

  return best ? { x: clampX(best.x) } : null;
}

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
    if (hold.type >= 7 && nextType <= 4 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_HUGE_T${hold.type}`, hold: true };
    }
  } else {
    if (nextMergeCount === 0 && pieces.length > 15 && !isWarn) {
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
      if (nextType >= 5 && !isWarn) {
        return { x: 0, reason: `HOLD_SAVE_BIG_T${nextType}`, hold: true };
      }
    }
  }
  return null;
}

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
    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 3;
    if (dangerBias <= -1 && cx < -0.5) s -= 5;
    if (dangerBias >= 1 && cx > 0.5) s -= 5;
    if (s > bestScore) { bestScore = s; bestX = cx; }
  }

  return bestX !== null ? { x: clampX(bestX) } : null;
}

function computeColHeights(pieces) {
  return FINE_COLS.map(cx => {
    const col = pieces.filter(p => Math.abs(p.x - cx) < 0.45);
    if (col.length === 0) return -5.0;
    return Math.max(...col.map(p => p.y + (p.r || 0.3)));
  });
}

function findLowestColIdx(colHeights) {
  let minH = Infinity, minIdx = 5;
  for (let i = 0; i < colHeights.length; i++) {
    if (colHeights[i] < minH) { minH = colHeights[i]; minIdx = i; }
  }
  return minIdx;
}

function findLowestSafeDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] >= DEADLINE_Y + 0.2) continue;
    let s = -colHeights[i] * 8.0;
    s -= Math.abs(FINE_COLS[i]) * 1.0;
    if (Math.abs(FINE_COLS[i]) > 2.2) s -= 3;
    if (dangerBias <= -1 && FINE_COLS[i] < -1.0) s -= 10;
    if (dangerBias >= 1 && FINE_COLS[i] > 1.0) s -= 10;
    if (dangerBias <= -2 && FINE_COLS[i] < 0) s -= 8;
    if (dangerBias >= 2 && FINE_COLS[i] > 0) s -= 8;
    if (s > bestScore) { bestScore = s; bestIdx = i; }
  }

  if (bestScore === -Infinity) bestIdx = findLowestColIdx(colHeights);
  return { x: clampX(FINE_COLS[bestIdx]), idx: bestIdx };
}

function findBestMerge(pieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, minChainScore) {
  const candidates = pieces.filter(p =>
    p.type === nextType && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y - 0.1
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
    if (colH > avgHeight + 0.8) s -= 2;
    s += nextType * 1.5;

    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    const c3 = countNear(pieces, t.x, nextType + 3, 2.6);
    const c4 = countNear(pieces, t.x, nextType + 4, 3.0);
    const chainScore = c1 * 8 + c2 * 4 + c3 * 2 + c4 * 1;
    if (chainScore < minChainScore) continue;
    s += chainScore;

    s += candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length * 3;

    if (dangerBias <= -1 && t.x < -0.5) s -= 6;
    if (dangerBias >= 1 && t.x > 0.5) s -= 6;
    if (dangerBias <= -2 && t.x < 0) s -= 8;
    if (dangerBias >= 2 && t.x > 0) s -= 8;
    s -= Math.abs(t.x) * 2.0;
    if (Math.abs(t.x) > 2.2) s -= 4;
    if (garbageUrgent) s += 15;

    if (s > bestScore) { bestScore = s; bestTarget = t; }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
}

function findBestHeightDrop(pieces, nextType, colHeights, dangerBias, avgHeight) {
  let bestIdx = -1;
  let bestScore = -Infinity;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i] * 3.0;
    s += pieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.2 && p.y < DEADLINE_Y
    ).length * 2.5;

    const leftH = i > 0 ? colHeights[i - 1] : colHeights[i];
    const rightH = i < FINE_COLS.length - 1 ? colHeights[i + 1] : colHeights[i];
    const gap = Math.max(leftH, rightH) - colHeights[i];
    if (gap > 1.0) s -= (gap - 1.0) * 2.0;

    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 3;
    if (dangerBias <= -1 && cx < -0.5) s -= 5;
    if (dangerBias >= 1 && cx > 0.5) s -= 5;
    if (dangerBias <= -2 && cx < 0) s -= 8;
    if (dangerBias >= 2 && cx > 0) s -= 8;

    if (s > bestScore) { bestScore = s; bestIdx = i; }
  }

  if (bestIdx < 0) {
    const lowestIdx = findLowestColIdx(colHeights);
    return { x: clampX(FINE_COLS[lowestIdx]), reason: `CRITICAL_COL${lowestIdx}` };
  }
  return { x: clampX(FINE_COLS[bestIdx]), reason: `HEIGHT_COL${bestIdx}_Y${colHeights[bestIdx].toFixed(1)}` };
}

function countNear(pieces, cx, type, radius) {
  if (type > 15) return 0;
  return pieces.filter(p =>
    p.type === type && Math.abs(p.x - cx) < radius && p.y < DEADLINE_Y
  ).length;
}

function nearestColIdx(x) {
  let minDist = Infinity, minIdx = 0;
  for (let i = 0; i < FINE_COLS.length; i++) {
    const d = Math.abs(FINE_COLS[i] - x);
    if (d < minDist) { minDist = d; minIdx = i; }
  }
  return minIdx;
}

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
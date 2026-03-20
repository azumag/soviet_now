/**
 * strategy.mjs - ドロップ位置決定戦略 (v20)
 *
 * v20改善点 (v19からの改善):
 * - 【根本修正】CRITICAL mode merge/drop決定バグ修正:
 *   v19問題: lowestColH=-5.0(空列)があると条件 lowestColH < mergeColH-1.2 が常にtrue
 *            → マージが全くされずピースが増加し続けてゲームオーバー
 *   v20修正: 安全マージ(DEADLINE_Y未満の列)を常に優先、空列より合体を選ぶ
 * - 通常モードにT1チェーンアンカー探索を追加 (ULTRAモード専用→全モード拡張)
 * - 通常モード早期T1マージ探索: T1ピースは積極的にT1+T1→T2を促進
 * - CRITICAL HOLD評価改善: HOLDがnextより多くのマージ機会を持つ場合も交換
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 65;
const ULTRA_MASS_THRESHOLD = 60;

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
      const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias);
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
      // v20: HOLDがnextより多くマージできる場合も交換 (v19: nextMerge===0の時のみ)
      if (holdLowMerge > nextLowMerge && holdLowMerge > 0) {
        return { x: 0, reason: `CRITICAL_HOLD_T${hold.type}`, hold: true };
      }
    }

    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    const lowestColH = colHeights[lowestDrop.idx];

    const minMergeType = (garbageFloodMode && nextType === 1) ? 99 : 1;
    const lowColMerge = nextType >= minMergeType
      ? findMergeInLowCol(activePieces, nextType, colHeights, critMergeLimit, dangerBias)
      : null;

    if (lowColMerge) {
      // v20: 【根本修正】常にマージを優先
      // v19: lowestColH=-5.0(空列)があると常に空列を選んでいた → マージ0でピース増加
      // v20: 安全な列にマージがあれば必ず実行 → ピース削減・チェーン反応を促進
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

  // v20: T1ピースは早期にチェーンアンカーを探す (ULTRA専用→通常モードにも拡張)
  if (nextType === 1) {
    const t1Chain = findT1ChainAnchor(activePieces, colHeights, dangerBias);
    if (t1Chain !== null) return { x: t1Chain, reason: 'T1_CHAIN_ANCHOR' };
    // T1同士のシンプルマージも早期に探す
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

/**
 * ボードバランスバイアス計算
 * 左右の加重質量差から強制バイアスを返す
 * 正=右が重い(左寄りに打て), 負=左が重い(右寄りに打て)
 * 0=バランス, ±1=軽微, ±2=強制
 */
function computeBalanceBias(pieces, colHeights) {
  const leftMass = pieces.filter(p => p.x < -0.3).reduce((s, p) => s + p.type, 0);
  const rightMass = pieces.filter(p => p.x > 0.3).reduce((s, p) => s + p.type, 0);
  const leftH = colHeights.slice(0, 4).filter(h => h > -4.5);
  const rightH = colHeights.slice(7).filter(h => h > -4.5);
  const avgLeftH = leftH.length > 0 ? leftH.reduce((a, b) => a + b, 0) / leftH.length : -3;
  const avgRightH = rightH.length > 0 ? rightH.reduce((a, b) => a + b, 0) / rightH.length : -3;

  const massDiff = rightMass - leftMass;
  const heightDiff = avgRightH - avgLeftH;

  const combined = massDiff * 0.02 + heightDiff * 0.5;
  if (combined > 1.2) return 2;   // 右重い → 強制左へ
  if (combined > 0.5) return 1;   // 右重い → 左寄り
  if (combined < -1.2) return -2; // 左重い → 強制右へ
  if (combined < -0.5) return -1; // 左重い → 右寄り
  return 0;
}

/**
 * T1専用チェーンアンカー
 * T1+T1=T2 の着地位置が T2クラスタ近くになる T1ターゲットを探す
 */
function findT1ChainAnchor(pieces, colHeights, dangerBias) {
  const t1Pieces = pieces.filter(p =>
    p.type === 1 && Math.abs(p.x) < WALL_MARGIN && p.y < DEADLINE_Y
  );
  if (t1Pieces.length === 0) return null;

  let best = null;
  let bestScore = -Infinity;

  for (const t1 of t1Pieces) {
    const ci = nearestColIdx(t1.x);
    if (colHeights[ci] >= DEADLINE_Y + 0.2) continue;

    const nearT2 = countNear(pieces, t1.x, 2, 2.0);
    const nearT3 = countNear(pieces, t1.x, 3, 2.2);
    const nearT4 = countNear(pieces, t1.x, 4, 2.4);

    if (nearT2 === 0) continue;

    let s = nearT2 * 16;
    s += nearT3 * 8;
    s += nearT4 * 4;
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

/** 大量ピースモード用 - 安全列のマージを探す */
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
    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    const c3 = countNear(pieces, t.x, nextType + 3, 2.6);
    s += c1 * 10;
    s += c2 * 5;
    s += c3 * 3;
    if (s > bestScore) { bestScore = s; best = t; }
  }

  return best ? clampX(best.x) : null;
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
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    s += c1 * 6;
    s += c2 * 3;
    s -= Math.abs(t.x) * 2.0;
    if (Math.abs(t.x) > 2.2) s -= 4;
    if (dangerBias <= -1 && t.x < -0.5) s -= 6;
    if (dangerBias >= 1 && t.x > 0.5) s -= 6;
    if (s > bestScore) { bestScore = s; best = t; }
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
    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 3;
    if (dangerBias <= -1 && cx < -0.5) s -= 5;
    if (dangerBias >= 1 && cx > 0.5) s -= 5;
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

/** 最低かつ安全な列へドロップ */
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

/**
 * 最良マージ位置を探す
 * minChainScore: 0=全マージ, 4=チェーン期待が高いもののみ
 */
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

    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.2).length;
    s += nearSame * 3;

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
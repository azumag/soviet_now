/**
 * strategy.mjs - ドロップ位置決定戦略 (v43)
 *
 * v43: v42ベース + 序盤ゲージ対応・EMERGENCY改善
 * - 【gaugeLevel早期計算】序盤保護モード前に移動して早期参照可能に
 * - 【序盤ゲージ対応】rawPieceCount<10でgauge>=0.6時、X範囲を±1.5に制限
 *   大型チェーンクリア後のおじゃまフラッド到来直前に壁付近に置くリスクを軽減
 * - 【EMERGENCY マージ優先】EMERGENCY_ALL_DANGERでT2+マージが可能なら先に実行
 *   ピース削減でチェーン機会を作り即死を1手でも先延ばし
 * - v42以前の全改善を維持
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 85;
const ULTRA_MASS_THRESHOLD = 62;
const EXTREME_T1_FLOOD_THRESHOLD = 30;
const SURVIVAL_PIECE_THRESHOLD = 78;
const LOW_MASS_CRITICAL_RELIEF_PIECE_THRESHOLD = 32;
const LOW_MASS_CRITICAL_RELIEF_AVG_HEIGHT = 1.45;
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.55;
const GARBAGE_MODERATE_RATIO = 0.20;
const GARBAGE_MODERATE_HEIGHT = 0.3;

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

  // colHeights & garbageRatioを早期に計算 (序盤保護モードで使用)
  const colHeights = computeColHeights(activePieces);
  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  // [v43] gaugeLevel を序盤保護モード前に計算
  const gaugeLevel = garbage ? (garbage.gauge || 0) : 0;

  // 序盤保護モード (rawPieceCount < 10 かつガベージ少ない)
  // ガベージが多い場合は通常フローでガベージ処理
  if (rawPieceCount < 10 && garbageRatio < 0.1) {
    if (nextType > 1) {
      const earlyMerge = findAnyMerge(activePieces, nextType, colHeights, 0);
      if (earlyMerge !== null) return { x: earlyMerge, reason: `EARLY_MERGE_T${nextType}` };
    }
    const earlyDrop = findLowestSafeDrop(colHeights, 0);
    // [v43] ゲージ高い場合は中央寄りに (おじゃまフラッド対策)
    const earlyXLimit = gaugeLevel >= 0.6 ? 1.5 : 2.0;
    const earlyX = Math.max(-earlyXLimit, Math.min(earlyXLimit, earlyDrop.x));
    return { x: earlyX, reason: `EARLY_SAFE` };
  }

  const t1Count = activePieces.filter(p => p.type === 1).length;
  const t1FloodMode = t1Count > 12;
  const extremeT1Flood = t1Count > EXTREME_T1_FLOOD_THRESHOLD;

  const garbageUrgent = garbageRatio > 0.4 || garbageHeight > 1.2;
  const garbageModerate = !garbageUrgent && (
    garbageRatio > GARBAGE_MODERATE_RATIO ||
    (garbageHeight > GARBAGE_MODERATE_HEIGHT && garbageRatio > 0.05)
  );
  const garbagePresent = garbageRatio > 0.05;
  // gaugeLevel already computed above

  // おじゃまマージブースト: ガベージ状態/ゲージに応じてマージ優先度を加算
  let ojamaBoost = 0;
  if (garbageModerate) ojamaBoost = 12;
  else if (garbagePresent) ojamaBoost = 8;
  else if (gaugeLevel >= 0.6) ojamaBoost = 10;
  else if (gaugeLevel >= 0.3) ojamaBoost = 5;

  // colHeights already computed above
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
  const hardCritical = nearDeadlineCount >= 3 || overDeadlineCount >= 2;
  const isCritical = hardCritical && !shouldRelieveLowMassCritical(
    nearDeadlineCount,
    overDeadlineCount,
    rawPieceCount,
    avgHeight,
    garbageHeight,
  );
  const isWarn = colHeights.some(h => h > WARN_Y + 0.5);

  // next2 type for look-ahead
  const next2Type = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;

  // --- 生存最優先モード ---
  if (rawPieceCount >= SURVIVAL_PIECE_THRESHOLD && !isCritical) {
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
    if (nextType > 1) {
      if (canHold && hold && hold.type >= nextType + 2) {
        const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
        if (holdMerge !== null) return { x: 0, reason: `ULTRA_HOLD_UPGRADE_T${hold.type}`, hold: true };
      }

      // T4+はfindBestMergeでチェーン反応考慮の最適マージ位置を選択
      if (nextType >= 4) {
        const bestMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0);
        if (bestMerge) return { ...bestMerge, reason: `ULTRA_BEST_T${nextType}` };
        // T5+はマージなければHOLD保護
        if (nextType >= 5 && canHold && !hold && !isWarn) {
          return { x: 0, reason: `ULTRA_HOLD_PROTECT_T${nextType}`, hold: true };
        }
      } else {
        const earlyMergeX = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
        if (earlyMergeX !== null) return { x: earlyMergeX, reason: `ULTRA_MERGE_T${nextType}` };
      }
    }

    // extremeT1Flood時はバランス補正より先にT1処理
    if (nextType === 1 && extremeT1Flood) {
      if (canHold && !hold) {
        const next2T = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
        const next3T = nextPieces && nextPieces[2] ? nextPieces[2].type : 0;
        const bestNext = Math.max(next2T, next3T);
        // v41維持: T2のためにHOLDするのはT1即時マージがない場合のみ。T3+は無条件HOLD
        if (bestNext >= 3) {
          return { x: 0, reason: `ULTRA_EXTREME_HOLD_T1_FOR_T${bestNext}`, hold: true };
        }
        if (bestNext === 2) {
          const hasImmediateMerge = findT1ImmediateMerge(activePieces, colHeights, dangerBias) !== null;
          if (!hasImmediateMerge) {
            return { x: 0, reason: `ULTRA_EXTREME_HOLD_T1_FOR_T${bestNext}`, hold: true };
          }
        }
      }
      if (canHold && hold && hold.type >= 2) {
        const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
        if (holdMerge !== null) return { x: 0, reason: `ULTRA_EXTREME_SWAP_T${hold.type}`, hold: true };
      }
      // extremeT1Flood時、held T3+(mergeあり or T5+)があればスワップして活用
      if (canHold && hold && hold.type >= 3) {
        const upgradeMerge = findBestMerge(activePieces, hold.type, colHeights, dangerBias, avgHeight, false, 0);
        if (upgradeMerge || hold.type >= 5) {
          return { x: 0, reason: `ULTRA_EXTREME_UPGRADE_T${hold.type}`, hold: true };
        }
      }
      // immediateをdense前に (top=T1は物理的に確実なマージ)
      const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias);
      if (immediateX !== null) return { x: immediateX, reason: 'ULTRA_EXTREME_T1_IMMEDIATE' };
      const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
      if (denseX !== null) return { x: denseX, reason: 'ULTRA_EXTREME_T1_DENSE' };
      const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (chainAnchor !== null) return { x: chainAnchor, reason: 'ULTRA_EXTREME_T1_ANCHOR' };
      const stackCol = findT1StackColumn(activePieces, colHeights, dangerBias);
      if (stackCol !== null) return { x: stackCol, reason: 'ULTRA_EXTREME_T1_STACK' };
      const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
      return { x: lowestDrop.x, reason: `ULTRA_EXTREME_T1_LOWEST` };
    }

    // バランス補正 (T1フラッド時は閾値を3に引き上げて振動抑制)
    const balanceThreshold = t1FloodMode ? 3 : 2;
    if (Math.abs(balanceBias) >= balanceThreshold) {
      if (nextType > 1) {
        const targetRight = balanceBias < 0;
        const balanceDrop = findLowestOnTargetSide(colHeights, targetRight);
        return { x: balanceDrop, reason: `ULTRA_BALANCE_BIAS${balanceBias > 0 ? 'R' : 'L'}` };
      }
      const balanceDrop = findLowestSafeDrop(colHeights, dangerBias);
      return { x: balanceDrop.x, reason: `ULTRA_BALANCE_BIAS${balanceBias > 0 ? 'R' : 'L'}` };
    }

    if (canHold && hold && hold.type >= 2 && hold.type > nextType) {
      const holdMerge = findAnyMerge(activePieces, hold.type, colHeights, dangerBias);
      if (holdMerge !== null) return { x: 0, reason: `ULTRA_HOLD_T${hold.type}`, hold: true };
    }

    if (nextType === 1) {
      // 通常T1フラッドモード (12 < t1Count <= 30)
      if (t1FloodMode) {
        const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias);
        if (immediateX !== null) return { x: immediateX, reason: 'ULTRA_T1_IMMEDIATE' };
        const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
        if (denseX !== null) return { x: denseX, reason: 'ULTRA_T1_DENSE' };
      }
      const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, t1FloodMode);
      if (chainAnchor !== null) return { x: chainAnchor, reason: `ULTRA_CHAIN_ANCHOR_T1` };
    }

    // T1のマージ
    if (nextType === 1) {
      const ultraMergeX = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
      if (ultraMergeX !== null) return { x: ultraMergeX, reason: `ULTRA_MERGE_T${nextType}` };
    }

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

  // --- 大量ピースモード: 非CRITICAL時のみ ---
  if (massMode && !isCritical) {
    const anyMergeX = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
    if (anyMergeX !== null) return { x: anyMergeX, reason: `MASS_MERGE_T${nextType}` };
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `MASS_LOW_COL_Y${colHeights[lowestDrop.idx].toFixed(1)}` };
  }

  // --- CRITICAL を garbageUrgent より先に処理 ---
  if (isCritical) {
    if (overDeadlineCount >= FINE_COLS.length - 3) {
      // [v43] T2+マージが可能なら先に実行 (ピース削減でチェーン機会)
      if (nextType >= 2) {
        const emergMerge = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
        if (emergMerge !== null) return { x: emergMerge, reason: `EMERGENCY_MERGE_T${nextType}` };
      }
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

    if (canHold && hold && hold.type >= nextType + 2) {
      const holdUpgrade = countLowColMerge(activePieces, hold.type, colHeights, critMergeLimit);
      if (holdUpgrade > 0) {
        return { x: 0, reason: `CRITICAL_HOLD_UPGRADE_T${hold.type}`, hold: true };
      }
    }

    // v37: T1の場合、T1フラッド処理より前に早期HOLDセーブを実行
    // T1→T1→T1の連鎖ループをHOLDで中断し、次のT2+ピースのマージを優先
    // nextPieces[1]に機会がない場合はnextPieces[2]も確認
    if (canHold && !hold && nextType === 1) {
      const next2Piece = nextPieces && nextPieces[1];
      const next3Piece = nextPieces && nextPieces[2];
      if (next2Piece && next2Piece.type > 1) {
        const next2LowMerge = countLowColMerge(activePieces, next2Piece.type, colHeights, critMergeLimit);
        if (next2LowMerge > 0) {
          return { x: 0, reason: `CRITICAL_T1_EARLY_HOLD_FOR_T${next2Piece.type}`, hold: true };
        }
      }
      if (next3Piece && next3Piece.type > 1) {
        const next3LowMerge = countLowColMerge(activePieces, next3Piece.type, colHeights, critMergeLimit);
        if (next3LowMerge > 0) {
          return { x: 0, reason: `CRITICAL_T1_EARLY_HOLD_FOR_T${next3Piece.type}_3RD`, hold: true };
        }
      }
    }

    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    const lowestColH = colHeights[lowestDrop.idx];
    const critLowT1Merge = nextType === 1
      ? findMergeInLowCol(activePieces, 1, colHeights, critMergeLimit, dangerBias)
      : null;

    if (nextType === 1 && t1FloodMode && !garbageFloodMode) {
      const critT1Immediate = findT1ImmediateMerge(activePieces, colHeights, dangerBias);
      if (shouldPreferLowT1CriticalMerge(
        colHeights,
        critT1Immediate,
        critLowT1Merge ? critLowT1Merge.x : null,
        garbageHeight,
        garbageRatio,
        lowestColH,
      )) {
        return { x: critLowT1Merge.x, reason: 'CRITICAL_T1_LOW_MERGE' };
      }
      if (critT1Immediate !== null) return { x: critT1Immediate, reason: 'CRITICAL_T1_IMMEDIATE' };
      const critT1Stack = findT1StackColumn(activePieces, colHeights, dangerBias);
      if (critT1Stack !== null) return { x: critT1Stack, reason: 'CRITICAL_T1_STACK' };
      const critT1Chain = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (critT1Chain !== null) return { x: critT1Chain, reason: 'CRITICAL_T1_ANCHOR' };
    }

    const minMergeType = (garbageFloodMode && nextType === 1) ? 99 : 1;
    const lowColMerge = nextType >= minMergeType
      ? (nextType === 1 ? critLowT1Merge : findMergeInLowCol(activePieces, nextType, colHeights, critMergeLimit, dangerBias))
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

  // --- おじゃまブロック中程度 → マージ優先モード ---
  if (garbageModerate) {
    // HOLDスワップ: 高タイプのマージ候補があればスワップ
    if (canHold && hold && hold.type && hold.type > nextType) {
      const holdMergeCount = activePieces.filter(p =>
        p.type === hold.type && p.y < DEADLINE_Y - 0.1
      ).length;
      if (holdMergeCount > 0) {
        return { x: 0, reason: `OJAMA_HOLD_T${hold.type}`, hold: true };
      }
    }
    // マージ優先 (ojamaBoost付き)
    const ojamaMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
    if (ojamaMerge) return { ...ojamaMerge, reason: `OJAMA_MERGE_T${nextType}` };
    // T1もマージ優先
    if (nextType === 1) {
      const t1Merge = findAnyMerge(activePieces, 1, colHeights, dangerBias);
      if (t1Merge !== null) return { x: t1Merge, reason: 'OJAMA_T1_MERGE' };
    }
    // マージなければ低い列へ
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `OJAMA_LOW_COL` };
  }

  // --- おじゃまゲージ警告 → マージ準備 ---
  if (gaugeLevel >= 0.3 && !garbagePresent) {
    // ゲージが充填中: おじゃまが来る前にマージ可能な配置を優先
    if (nextType > 1) {
      const gaugeMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
      if (gaugeMerge) return { ...gaugeMerge, reason: `GAUGE_MERGE_T${nextType}` };
    }
    if (nextType === 1) {
      const t1Merge = findAnyMerge(activePieces, 1, colHeights, dangerBias);
      if (t1Merge !== null) return { x: t1Merge, reason: 'GAUGE_T1_MERGE' };
    }
    // マージなければ通常フローへ (ojamaBoost付きで下のfindBestMergeが効く)
  }

  // --- T1フラッド予防HOLD (t1Count>=8, isWarnでない場合) ---
  // T1が溜まり始めたらHOLDで次のT2+ピースを先取りしてフラッドを防ぐ
  if (canHold && !hold && nextType === 1 && t1Count >= 8 && !isWarn) {
    const next2T = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
    const next3T = nextPieces && nextPieces[2] ? nextPieces[2].type : 0;
    const bestNextT = Math.max(next2T, next3T);
    if (bestNextT >= 3) {
      return { x: 0, reason: `T1_FLOOD_HOLD_T${bestNextT}`, hold: true };
    }
    if (bestNextT === 2) {
      const t2Merge = findAnyMerge(activePieces, 2, colHeights, dangerBias);
      if (t2Merge !== null) return { x: 0, reason: 'T1_FLOOD_HOLD_T2', hold: true };
    }
  }

  // --- HOLD判定 (非CRITICAL時) ---
  if (canHold) {
    const holdResult = evaluateHold(activePieces, nextType, hold, nextPieces, isWarn, t1Count);
    if (holdResult) return holdResult;
  }

  // isWarn時のpre-criticalマージ強化 (T2+のみ)
  // HOLD評価後にT2+ピースがあれば積極的マージでCRITICAL到達を抑制
  if (isWarn && !isCritical && nextType > 1) {
    const warnMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
    if (warnMerge) return { ...warnMerge, reason: `PREWARN_MERGE_T${nextType}` };
  }

  // T1ピースは早期にT1フラッドチェック
  if (nextType === 1) {
    // v36: T1プレフラッド対策 (t1Count 8-11): flood閾値到達前から密集誘導+チェーン
    if (t1Count >= 8 && !t1FloodMode && !extremeT1Flood) {
      const preFloodDense = findT1DenseColumn(activePieces, colHeights, dangerBias);
      if (preFloodDense !== null) return { x: preFloodDense, reason: 'T1_PREFLOOD_DENSE' };
      // v36: チェーンアンカーも追加してより効果的にT1を集中
      const preFloodAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (preFloodAnchor !== null) return { x: preFloodAnchor, reason: 'T1_PREFLOOD_ANCHOR' };
    }
    if (t1FloodMode && !extremeT1Flood) {
      const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
      if (denseX !== null) return { x: denseX, reason: 'T1_FLOOD_DENSE' };
    }
    if (extremeT1Flood) {
      const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias);
      if (immediateX !== null) return { x: immediateX, reason: 'T1_EXTREME_IMMEDIATE' };
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
    if (nextType >= 2) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0, ojamaBoost);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight, next2Type);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0, ojamaBoost);
    if (bigMerge) return bigMerge;
  }

  const chainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 4, ojamaBoost);
  if (chainMerge) return chainMerge;

  const normalMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, 0, ojamaBoost);
  if (normalMerge) return normalMerge;

  const clusterPlace = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
  if (clusterPlace) return { ...clusterPlace, reason: `CLUSTER_T${nextType}` };

  return findBestHeightDrop(activePieces, nextType, colHeights, dangerBias, avgHeight, next2Type)
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

function shouldRelieveLowMassCritical(nearDeadlineCount, overDeadlineCount, rawPieceCount, avgHeight, garbageHeight) {
  if (overDeadlineCount !== 2) return false;
  if (nearDeadlineCount > 2) return false;
  if (rawPieceCount >= LOW_MASS_CRITICAL_RELIEF_PIECE_THRESHOLD) return false;
  if (avgHeight >= LOW_MASS_CRITICAL_RELIEF_AVG_HEIGHT) return false;
  return garbageHeight >= DEADLINE_Y;
}

function shouldPreferLowT1CriticalMerge(colHeights, immediateX, lowMergeX, garbageHeight, garbageRatio, lowestColH) {
  if (lowMergeX === null) return false;
  if (immediateX === null) return true;

  const immediateH = colHeights[nearestColIdx(immediateX)];
  const lowMergeH = colHeights[nearestColIdx(lowMergeX)];

  if (garbageHeight >= DEADLINE_Y + 0.5 && lowMergeH <= immediateH - T1_LOW_MERGE_HEIGHT_ADVANTAGE) {
    return true;
  }
  if (garbageRatio >= 0.3 && lowMergeH <= lowestColH + 0.3 && immediateH >= lowMergeH + 0.35) {
    return true;
  }
  return false;
}

/**
 * バランス回復用に軽い側の最低列を直接選択
 */
function findLowestOnTargetSide(colHeights, targetRight) {
  let bestScore = -Infinity;
  let bestX = 0;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] >= DEADLINE_Y) continue;
    const inTargetSide = targetRight ? cx >= -0.5 : cx <= 0.5;
    if (!inTargetSide) continue;
    let s = -colHeights[i] * 8.0;
    s -= Math.abs(cx) * 1.5;
    if (s > bestScore) { bestScore = s; bestX = cx; }
  }

  if (bestScore === -Infinity) {
    const idx = findLowestColIdx(colHeights);
    return clampX(FINE_COLS[idx]);
  }
  return clampX(bestX);
}

/**
 * T1フラッドモード専用: T1が最も密集した列に誘導してT1→T2チェーン促進
 * WARN_Y硬直cutoff廃止→softペナルティ化
 *   全列がWARN_Y超えでもnullを返さずに最善列を返す
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
    // DEADLINE_Y hard cutoffは維持
    if (colHeights[i] >= DEADLINE_Y + 0.1) continue;
    const nearT1 = t1Pieces.filter(p => Math.abs(p.x - cx) < 0.8).length;
    if (nearT1 < 2) continue;
    const nearT2 = countNear(pieces, cx, 2, 1.5);
    const nearT3 = countNear(pieces, cx, 3, 2.0);
    let s = nearT1 * 20;
    s += nearT2 * 20;
    s += nearT3 * 10;
    s -= colHeights[i] * 10.0;
    // WARN_Y超えには追加ペナルティ (soft penalty)
    if (colHeights[i] > WARN_Y) s -= (colHeights[i] - WARN_Y) * 15;
    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 8;
    if (dangerBias >= 2 && cx > 0) s -= 15;
    if (dangerBias <= -2 && cx < 0) s -= 15;
    if (dangerBias >= 1 && cx > 0.5) s -= 8;
    if (dangerBias <= -1 && cx < -0.5) s -= 8;
    if (s > bestScore) { bestScore = s; bestCol = i; }
  }

  return bestCol !== null ? clampX(FINE_COLS[bestCol]) : null;
}

/**
 * T1が縦に積み重なった列を検出
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

    const colT1 = t1Pieces.filter(p => Math.abs(p.x - cx) < 0.55);
    if (colT1.length < 2) continue;

    const yVals = colT1.map(p => p.y).sort((a, b) => a - b);
    const yRange = yVals[yVals.length - 1] - yVals[0];
    const density = colT1.length / (yRange + 0.5);

    let s = density * 20;
    s += colT1.length * 5;
    s -= colHeights[i] * 8.0;
    s -= Math.abs(cx) * 2.0;
    if (Math.abs(cx) > 2.2) s -= 8;

    const colPiecesNarrow = pieces.filter(p => Math.abs(p.x - cx) < 0.45 && p.y < DEADLINE_Y);
    if (colPiecesNarrow.length > 0) {
      const topNarrow = colPiecesNarrow.reduce((a, b) =>
        (b.y + (b.r || 0.3)) > (a.y + (a.r || 0.3)) ? b : a
      );
      if (topNarrow.type === 1) s += 40;
    }

    if (dangerBias >= 2 && cx > 0) s -= 15;
    if (dangerBias <= -2 && cx < 0) s -= 15;
    if (dangerBias >= 1 && cx > 0.5) s -= 6;
    if (dangerBias <= -1 && cx < -0.5) s -= 6;

    if (s > bestScore) { bestScore = s; bestCol = i; }
  }

  return bestCol !== null ? clampX(FINE_COLS[bestCol]) : null;
}

/**
 * 列トップがT1の列を検出して即時T1→T2マージを狙う
 * tall列への追加ペナルティ (WARN_Y+0.5超えで×30)
 * 低Y位置ボーナス追加 (colH<0で+20、colH<WARN_Yで+10)
 *   高い列のT1をさらに積むことを抑制し、低い位置のマージを優先
 */
function findT1ImmediateMerge(pieces, colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestCol = null;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] >= DEADLINE_Y) continue;

    const colPieces = pieces.filter(p => Math.abs(p.x - cx) < 0.45 && p.y < DEADLINE_Y);
    if (colPieces.length === 0) continue;

    const topPiece = colPieces.reduce((a, b) =>
      (b.y + (b.r || 0.3)) > (a.y + (a.r || 0.3)) ? b : a
    );
    if (topPiece.type !== 1) continue;

    let s = 50;
    s -= colHeights[i] * 8.0;
    // tall列への追加ペナルティ (WARN_Y+0.5超えで急激に増加)
    if (colHeights[i] > WARN_Y + 0.5) s -= (colHeights[i] - WARN_Y - 0.5) * 30;
    // 低Y位置ボーナス - 低い列のT1マージはT2を安全な高さに生成
    if (colHeights[i] < 0) s += 20;
    else if (colHeights[i] < WARN_Y) s += 10;
    s -= Math.abs(cx) * 2.0;

    const nearT2 = countNear(pieces, cx, 2, 2.0);
    const nearT3 = countNear(pieces, cx, 3, 2.4);
    s += nearT2 * 35;
    s += nearT3 * 18;

    // wall抑制
    if (Math.abs(cx) > 2.0) s -= 12;
    if (Math.abs(cx) > 2.4) s -= 20;

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

// T1蓄積時(t1Count>=5)のfuture-save HOLD抑制
function evaluateHold(pieces, nextType, hold, nextPieces, isWarn, t1Count = 0) {
  const safeY = DEADLINE_Y - 0.3;
  const nextMergeCount = pieces.filter(p => p.type === nextType && p.y < safeY).length;

  if (hold && hold.type) {
    const holdMergeCount = pieces.filter(p => p.type === hold.type && p.y < safeY).length;
    if (holdMergeCount > nextMergeCount && nextMergeCount === 0) {
      return { x: 0, reason: `HOLD_SWAP_T${hold.type}vs${nextType}`, hold: true };
    }
    if (hold.type >= 4 && nextType <= 2 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_BIGTYPE_T${hold.type}`, hold: true };
    }
    if (hold.type >= 6 && nextType <= 3 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_HUGE_T${hold.type}`, hold: true };
    }
  } else {
    if (nextMergeCount === 0 && !isWarn) {
      if (nextType >= 5 && pieces.length > 15) {
        return { x: 0, reason: `HOLD_SAVE_BIG_T${nextType}`, hold: true };
      }
      if (nextType >= 4 && pieces.length > 20) {
        return { x: 0, reason: `HOLD_SAVE_T4_T${nextType}`, hold: true };
      }
      // T1蓄積時(t1Count>=5)はT1のfuture-save HOLDをスキップ
      if (pieces.length > 15 && (nextType !== 1 || t1Count < 5)) {
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

/**
 * wall penalty強化 (|x|>2.2: -8, |x|>2.4: 追加-15)
 * 端列への意図しない落下を防止
 */
function findLowestSafeDrop(colHeights, dangerBias) {
  let bestScore = -Infinity;
  let bestIdx = 5;

  for (let i = 0; i < FINE_COLS.length; i++) {
    if (colHeights[i] >= DEADLINE_Y + 0.2) continue;
    let s = -colHeights[i] * 8.0;
    s -= Math.abs(FINE_COLS[i]) * 1.0;
    if (Math.abs(FINE_COLS[i]) > 2.2) s -= 8;
    if (Math.abs(FINE_COLS[i]) > 2.4) s -= 15;
    if (dangerBias <= -1 && FINE_COLS[i] < -1.0) s -= 10;
    if (dangerBias >= 1 && FINE_COLS[i] > 1.0) s -= 10;
    if (dangerBias <= -2 && FINE_COLS[i] < 0) s -= 8;
    if (dangerBias >= 2 && FINE_COLS[i] > 0) s -= 8;
    if (s > bestScore) { bestScore = s; bestIdx = i; }
  }

  if (bestScore === -Infinity) bestIdx = findLowestColIdx(colHeights);
  return { x: clampX(FINE_COLS[bestIdx]), idx: bestIdx };
}

function findBestMerge(pieces, nextType, colHeights, dangerBias, avgHeight, garbageUrgent, minChainScore, ojamaBoost = 0) {
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
    s += nextType * 2.0;

    const c1 = countNear(pieces, t.x, nextType + 1, 1.8);
    const c2 = countNear(pieces, t.x, nextType + 2, 2.2);
    const c3 = countNear(pieces, t.x, nextType + 3, 2.6);
    const c4 = countNear(pieces, t.x, nextType + 4, 3.0);
    const chainScore = c1 * 12 + c2 * 6 + c3 * 3 + c4 * 2;
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
    s += ojamaBoost;
    // おじゃまブロック時: ボード下部のマージを優先 (おじゃまは下に溜まるため)
    if (ojamaBoost > 0 && t.y < -1.0) s += 5;

    if (s > bestScore) { bestScore = s; bestTarget = t; }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
}

/**
 * 高さベースのドロップ位置決定 (look-ahead: next2Type のマージ準備も加点)
 */
function findBestHeightDrop(pieces, nextType, colHeights, dangerBias, avgHeight, next2Type = 0) {
  let bestIdx = -1;
  let bestScore = -Infinity;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i] * 3.0;
    s += pieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.2 && p.y < DEADLINE_Y
    ).length * 2.5;

    // look-ahead: next2ピースのマージ準備位置に加点
    if (next2Type > 0 && next2Type !== nextType) {
      s += pieces.filter(p =>
        p.type === next2Type && Math.abs(p.x - cx) < 1.5 && p.y < DEADLINE_Y
      ).length * 1.2;
    }

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
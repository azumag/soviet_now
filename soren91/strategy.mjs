/**
 * strategy.mjs - ドロップ位置決定戦略 (v55)
 *
 * v55: v54ベース + T1過剰即時マージの抑制
 * - 【garbageUrgent + T1で低列マージを優先】
 *   緊急ガベージ時に高い列のT1即時マージへ吸われすぎるのを抑え、
 *   低い列の安全マージを選ぶ分岐を追加
 * - 【ULTRA_EXTREME/T1_EXTREMEのimmediateを高密度時に軽くスロットル】
 *   immediate先の列が高い/壁寄りで、dense/stackの代替が十分安全ならそちらを優先
 * - v54の全改善を維持
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 85;
const ULTRA_MASS_THRESHOLD = 70; // [v54] 62→70: 早期サバイバルモード抑制
const EXTREME_T1_FLOOD_THRESHOLD = 30;
const SURVIVAL_PIECE_THRESHOLD = 78;
const LOW_MASS_CRITICAL_RELIEF_PIECE_THRESHOLD = 32;
const LOW_MASS_CRITICAL_RELIEF_AVG_HEIGHT = 1.45;
const T1_LOW_MERGE_HEIGHT_ADVANTAGE = 0.55;
const GARBAGE_MODERATE_RATIO = 0.22;
const GARBAGE_MODERATE_HEIGHT = 0.3;
const EXTREME_T1_WALL_PIECE_THRESHOLD = 75; // [v48] 壁ペナルティ強化閾値
const T1_PREFLOOD_THRESHOLD = 8; // [v54] 10→8: v52実績値に戻す (早期フラッド予防強化)

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
  else if (gaugeLevel >= 0.2) ojamaBoost = 3; // [v51] 早期ゲージ反応

  // colHeights already computed above
  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  // [v51] garbageFloodMode height threshold lowered from 1.3 to 1.1
  const garbageFloodMode = rawPieceCount > 55 && avgHeight > 1.1;

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
  // [v53] heavyGarbageStart: 開幕大量ガベージでピースが押し上げられた状態を検出
  const heavyGarbageStart = garbageRatio > 0.25 && avgHeight > 1.5;
  // [v54] ガベージ開幕時はCRITICAL閾値を緩和 (>=7に引き上げて誤緩和を削減)
  const hardCritical = heavyGarbageStart
    ? (nearDeadlineCount >= 7 || overDeadlineCount >= 3)
    : (nearDeadlineCount >= 4 || overDeadlineCount >= 2);
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
        // [v54] T3+HOLDはボード上にマージ対象がある場合のみ (無駄HOLD防止)
        if (bestNext >= 3) {
          const hasMergeForNext = findAnyMerge(activePieces, bestNext, colHeights, dangerBias) !== null;
          if (hasMergeForNext) {
            return { x: 0, reason: `ULTRA_EXTREME_HOLD_T1_FOR_T${bestNext}`, hold: true };
          }
        }
        if (bestNext === 2) {
          const hasImmediateMerge = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount) !== null;
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
      // [v48] 壁ペナルティ動的強化: pieces>75の場合、壁落下ループを抑制
      const t1WallMult = rawPieceCount > EXTREME_T1_WALL_PIECE_THRESHOLD ? 2.5 : 1.0;
      const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias, t1WallMult, rawPieceCount);
      const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias, t1WallMult);
      const stackCol = findT1StackColumn(activePieces, colHeights, dangerBias);
      const saferT1Alt = pickSaferT1Alternative(
        colHeights,
        immediateX,
        [
          { x: denseX, reason: 'ULTRA_EXTREME_T1_DENSE_SAFE' },
          { x: stackCol, reason: 'ULTRA_EXTREME_T1_STACK_SAFE' },
        ],
        rawPieceCount,
        gaugeLevel,
      );
      if (saferT1Alt) return saferT1Alt;
      // immediateをdense前に維持しつつ、高密度では明らかに安全な代替を許可
      if (immediateX !== null) return { x: immediateX, reason: 'ULTRA_EXTREME_T1_IMMEDIATE' };
      if (denseX !== null) return { x: denseX, reason: 'ULTRA_EXTREME_T1_DENSE' };
      const chainAnchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, true);
      if (chainAnchor !== null) return { x: chainAnchor, reason: 'ULTRA_EXTREME_T1_ANCHOR' };
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
        const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount);
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
      // [v53] T1もEMERGENCY_ALL_DANGERより前にimmediate mergeを試みる
      if (nextType === 1) {
        const emergT1 = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount);
        if (emergT1 !== null) return { x: emergT1, reason: 'EMERGENCY_T1_IMMEDIATE' };
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
      const critT1Immediate = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount);
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
        // [v49] 列制限を DEADLINE_Y+0.1 → +0.3 に緩和してより多くのマージ機会を確保
        if (mergeColH < DEADLINE_Y + 0.3) {
          return { x: critFallbackMerge.x, reason: `CRITICAL_ANY_MERGE_T${nextType}` };
        }
      }
    }

    // [v49] CRITICAL最終マージ: T2+のfindAnyMergeをHOLDセーブより前に試みる
    // 列高さ制限は findAnyMerge 内部の DEADLINE_Y+0.2 のみ
    if (nextType >= 2) {
      const critLastMerge = findAnyMerge(activePieces, nextType, colHeights, dangerBias);
      if (critLastMerge !== null) {
        return { x: critLastMerge, reason: `CRITICAL_LAST_MERGE_T${nextType}` };
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

    if (nextType === 1) {
      const gbgImmediateT1 = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount);
      const gbgLowT1Merge = findMergeInLowCol(activePieces, 1, colHeights, DEADLINE_Y - 0.2, dangerBias);
      const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
      const lowestColH = colHeights[lowestDrop.idx];

      if (shouldPreferLowT1GarbageMerge(
        colHeights,
        gbgImmediateT1,
        gbgLowT1Merge ? gbgLowT1Merge.x : null,
        garbageHeight,
        garbageRatio,
        lowestColH,
        rawPieceCount,
        t1Count,
      )) {
        return { x: gbgLowT1Merge.x, reason: 'GBG_T1_LOW_MERGE' };
      }

      if (gbgImmediateT1 !== null) {
        return { x: gbgImmediateT1, reason: 'GBG_T1_IMMEDIATE' };
      }
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
    // [v46] クラスター配置を最低列フォールバックの前に試みる
    const gbgCluster = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
    if (gbgCluster) return { ...gbgCluster, reason: `GBG_CLUSTER_T${nextType}` };
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
    // [v46] T2+: チェーン優先マージ (minChainScore=4に緩和: より多くのマージ機会を捉える)
    if (nextType >= 2) {
      const ojamaChainMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 4, ojamaBoost);
      if (ojamaChainMerge) return { ...ojamaChainMerge, reason: `OJAMA_CHAIN_T${nextType}` };
    }
    // マージ優先 (ojamaBoost付き)
    const ojamaMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
    if (ojamaMerge) return { ...ojamaMerge, reason: `OJAMA_MERGE_T${nextType}` };
    // T1もマージ優先
    if (nextType === 1) {
      const t1Merge = findAnyMerge(activePieces, 1, colHeights, dangerBias);
      if (t1Merge !== null) return { x: t1Merge, reason: 'OJAMA_T1_MERGE' };
    }
    // [v45] マージなければ同type集約 (最低列ランダム積みを防いで将来マージ機会を温存)
    const ojamaCluster = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
    if (ojamaCluster) return { ...ojamaCluster, reason: `OJAMA_CLUSTER_T${nextType}` };
    // マージなければ低い列へ
    const lowestDrop = findLowestSafeDrop(colHeights, dangerBias);
    return { x: lowestDrop.x, reason: `OJAMA_LOW_COL` };
  }

  // --- おじゃまゲージ警告 → マージ準備 ---
  // [v51] threshold lowered from 0.3 to 0.2 for earlier gauge response
  if (gaugeLevel >= 0.2 && !garbagePresent) {
    // ゲージが充填中: おじゃまが来る前にマージ可能な配置を優先
    if (nextType > 1) {
      const gaugeMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
      if (gaugeMerge) return { ...gaugeMerge, reason: `GAUGE_MERGE_T${nextType}` };
      // [v47] ゲージ中間以上: マージなければクラスター集約でおじゃま着弾後の連鎖準備
      if (gaugeLevel >= 0.45) {
        const gaugeCluster = findClusterDrop(activePieces, nextType, colHeights, dangerBias);
        if (gaugeCluster) return { ...gaugeCluster, reason: `GAUGE_CLUSTER_T${nextType}` };
      }
    }
    if (nextType === 1) {
      const t1Merge = findAnyMerge(activePieces, 1, colHeights, dangerBias);
      if (t1Merge !== null) return { x: t1Merge, reason: 'GAUGE_T1_MERGE' };
      // [v47] ゲージ中間以上: T1はchainAnchorで次のT2連鎖を仕込む
      if (gaugeLevel >= 0.45) {
        const gaugeT1Anchor = findT1ChainAnchor(activePieces, colHeights, dangerBias, false);
        if (gaugeT1Anchor !== null) return { x: gaugeT1Anchor, reason: 'GAUGE_T1_ANCHOR' };
      }
    }
    // マージなければ通常フローへ (ojamaBoost付きで下のfindBestMergeが効く)
  }

  // [v53] ゲージ満タン直前HOLDセーブ: おじゃま発動前にT2以下を退避し次のT3+で反撃
  if (gaugeLevel >= 0.85 && canHold && !hold && nextType <= 2 && !isWarn && !isCritical) {
    const next2T = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
    const next3T = nextPieces && nextPieces[2] ? nextPieces[2].type : 0;
    if (Math.max(next2T, next3T) >= 3) {
      return { x: 0, reason: `PRE_OJAMA_HOLD_T${nextType}`, hold: true };
    }
  }

  // --- T1フラッド予防HOLD (t1Count>=T1_PREFLOOD_THRESHOLD, isWarnでない場合) ---
  // [v54] 閾値をT1_PREFLOOD_THRESHOLD=8に戻す + T3+HOLDはマージ対象確認
  if (canHold && !hold && nextType === 1 && t1Count >= T1_PREFLOOD_THRESHOLD && !isWarn) {
    const next2T = nextPieces && nextPieces[1] ? nextPieces[1].type : 0;
    const next3T = nextPieces && nextPieces[2] ? nextPieces[2].type : 0;
    const bestNextT = Math.max(next2T, next3T);
    if (bestNextT >= 3) {
      // [v54] マージ対象が実際に存在する場合のみHOLD (無駄なHOLDを回避)
      const hasMergeForBestNext = findAnyMerge(activePieces, bestNextT, colHeights, dangerBias) !== null;
      if (hasMergeForBestNext) {
        return { x: 0, reason: `T1_FLOOD_HOLD_T${bestNextT}`, hold: true };
      }
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

  // [v51] 中盤プリエンプティブマージ: 高さが上昇する前にマージを実行してCRITICAL到達を防止
  // 非警戒・非緊急・T2+・ピース20-55・avgHeight > 0.6 の条件で積極マージ
  if (!isWarn && !isCritical && !garbageUrgent && !garbageModerate &&
      nextType >= 2 && rawPieceCount >= 20 && rawPieceCount <= 55 && avgHeight > 0.6) {
    const preHeightMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, 0);
    if (preHeightMerge) return { ...preHeightMerge, reason: `HPREMERGE_T${nextType}` };
  }

  // [v46] 中盤チェーンファースト: 非WARN・T3+・ピース15-60でチェーン優先
  // [v50] T3に拡張 (minChainScore=6), T4+は従来通り (minChainScore=8)
  if (!isWarn && !isCritical && nextType >= 3 && rawPieceCount >= 15 && rawPieceCount < 60) {
    const chainThreshold = nextType >= 4 ? 8 : 6;
    const midChain = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, chainThreshold, 0);
    if (midChain) return { ...midChain, reason: `MID_CHAIN_T${nextType}` };
  }

  // isWarn時のpre-criticalマージ強化 (T2+のみ)
  // HOLD評価後にT2+ピースがあれば積極的マージでCRITICAL到達を抑制
  if (isWarn && !isCritical && nextType > 1) {
    const warnMerge = findBestMerge(activePieces, nextType, colHeights, dangerBias, avgHeight, false, 0, ojamaBoost);
    if (warnMerge) return { ...warnMerge, reason: `PREWARN_MERGE_T${nextType}` };
  }

  // T1ピースは早期にT1フラッドチェック
  if (nextType === 1) {
    // [v50] T1プレフラッド対策 (t1Count T1_PREFLOOD_THRESHOLD-11): 閾値8から12の間
    if (t1Count >= T1_PREFLOOD_THRESHOLD && !t1FloodMode && !extremeT1Flood) {
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
      const immediateX = findT1ImmediateMerge(activePieces, colHeights, dangerBias, 1.0, rawPieceCount);
      const denseX = findT1DenseColumn(activePieces, colHeights, dangerBias);
      const stackCol = findT1StackColumn(activePieces, colHeights, dangerBias);
      const saferT1Alt = pickSaferT1Alternative(
        colHeights,
        immediateX,
        [
          { x: denseX, reason: 'T1_EXTREME_DENSE_SAFE' },
          { x: stackCol, reason: 'T1_EXTREME_STACK_SAFE' },
        ],
        rawPieceCount,
        gaugeLevel,
      );
      if (saferT1Alt) return saferT1Alt;
      if (immediateX !== null) return { x: immediateX, reason: 'T1_EXTREME_IMMEDIATE' };
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

function shouldPreferLowT1GarbageMerge(
  colHeights,
  immediateX,
  lowMergeX,
  garbageHeight,
  garbageRatio,
  lowestColH,
  rawPieceCount,
  t1Count,
) {
  if (lowMergeX === null) return false;
  if (immediateX === null) return true;

  const immediateH = colHeights[nearestColIdx(immediateX)];
  const lowMergeH = colHeights[nearestColIdx(lowMergeX)];

  if (garbageHeight >= DEADLINE_Y + 0.5 && lowMergeH <= immediateH - 0.3) {
    return true;
  }
  if (garbageRatio >= 0.28 && t1Count >= 12 && lowMergeH <= lowestColH + 0.35 && immediateH >= lowMergeH + 0.25) {
    return true;
  }
  if (rawPieceCount >= 45 && lowMergeH <= immediateH - 0.5) {
    return true;
  }
  return false;
}

function pickSaferT1Alternative(colHeights, immediateX, candidates, rawPieceCount, gaugeLevel) {
  if (immediateX === null) return null;
  if (rawPieceCount < 90 && gaugeLevel < 0.65) return null;

  const immediateH = colHeights[nearestColIdx(immediateX)];
  const immediateWall = Math.abs(immediateX) >= 2.0;
  const viable = candidates
    .filter(candidate => candidate && candidate.x !== null)
    .map(candidate => ({
      ...candidate,
      h: colHeights[nearestColIdx(candidate.x)],
    }))
    .sort((a, b) => (a.h - b.h) || (Math.abs(a.x) - Math.abs(b.x)));

  if (viable.length === 0) return null;

  const best = viable[0];
  if (immediateWall && best.h <= immediateH + 0.15) {
    return { x: best.x, reason: best.reason };
  }
  if (immediateH > WARN_Y + 0.35 && best.h <= immediateH - 0.35) {
    return { x: best.x, reason: best.reason };
  }
  if (rawPieceCount >= 105 && best.h <= immediateH + 0.2 && Math.abs(best.x) <= Math.abs(immediateX)) {
    return { x: best.x, reason: best.reason };
  }
  return null;
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
 * [v48] wallPenaltyMult追加: ULTRA_EXTREMEでpieces>75の時に壁ペナルティを強化
 */
function findT1DenseColumn(pieces, colHeights, dangerBias, wallPenaltyMult = 1.0) {
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
    if (Math.abs(cx) > 2.2) s -= Math.round(8 * wallPenaltyMult);
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
 * [v44] nearT2Close (r=0.8): T2が極近傍にある列を大幅優先
 *   T1→T2マージ直後にT2+T2→T3の即時連鎖を誘発する
 * [v48] wallPenaltyMult追加: ULTRA_EXTREMEでpieces>75の時に壁ペナルティを強化
 *   ベースペナルティも12→15 / 20→25に引き上げ
 * [v52] nearT3検出半径2.4→2.8、nearT4追加、nearT2Closeペアボーナス、nearT3スコア18→22
 * [v54] pieceCount引数追加: 高密度ボードでは高さペナルティ強化・チェーンボーナス削減
 *   nearT4ボーナス: 10→15 (3段連鎖設定を積極的に評価)
 */
function findT1ImmediateMerge(pieces, colHeights, dangerBias, wallPenaltyMult = 1.0, pieceCount = 0) {
  // [v54] ピース数に応じた動的重み付け
  // 高密度時は高さペナルティを強化し、チェーンボーナスを抑制して生存優先
  const chainMult = pieceCount > 105 ? 0.35 : pieceCount > 85 ? 0.65 : 1.0;
  const heightMult = pieceCount > 105 ? 1.8 : pieceCount > 85 ? 1.35 : 1.0;

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
    s -= colHeights[i] * 8.0 * heightMult;
    // tall列への追加ペナルティ (WARN_Y+0.5超えで急激に増加)
    if (colHeights[i] > WARN_Y + 0.5) s -= (colHeights[i] - WARN_Y - 0.5) * 30 * heightMult;
    // 低Y位置ボーナス - 低い列のT1マージはT2を安全な高さに生成
    if (colHeights[i] < 0) s += 20;
    else if (colHeights[i] < WARN_Y) s += 10;
    s -= Math.abs(cx) * 2.0;

    // [v44] 近接T2 (r=0.8): T1→T2後の即時T2+T2連鎖を強く優先
    // [v52] nearT3検出半径2.4→2.8、nearT4追加 (3段連鎖ルックアヘッド)
    // [v54] chainMultで高密度時に抑制
    const nearT2Close = countNear(pieces, cx, 2, 0.8);
    const nearT2 = countNear(pieces, cx, 2, 2.0);
    const nearT3 = countNear(pieces, cx, 3, 2.8);
    const nearT4 = countNear(pieces, cx, 4, 3.0);
    if (nearT2Close >= 2) s += Math.round(40 * chainMult);   // [v52] T2 pair bonus
    s += Math.round(nearT2Close * 55 * chainMult);
    s += Math.round((nearT2 - nearT2Close) * 25 * chainMult);
    s += Math.round(nearT3 * 22 * chainMult);
    s += Math.round(nearT4 * 15 * chainMult);                // [v54] 10→15

    // wall抑制 [v48: ベースペナルティ引き上げ + 動的乗数]
    if (Math.abs(cx) > 2.0) s -= Math.round(15 * wallPenaltyMult);
    if (Math.abs(cx) > 2.4) s -= Math.round(25 * wallPenaltyMult);

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
 * [v44] nearT2Close (r=0.8): T2が極近傍の位置を強く優先
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
    // [v44] 近接T2 (r=0.8) を分離評価
    const nearT2Close = countNear(pieces, t1.x, 2, 0.8);
    const nearT2 = countNear(pieces, t1.x, 2, 2.0);
    const nearT3 = countNear(pieces, t1.x, 3, 2.2);
    const nearT4 = countNear(pieces, t1.x, 4, 2.4);

    if (!t1Flood && nearT2 === 0) continue;
    if (t1Flood && nearT2 === 0 && nearT1 < 2) continue;

    // [v44] 近接T2に大ボーナス、通常T2も維持
    let s = nearT2Close * 28 + (nearT2 - nearT2Close) * 12;
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
    // [v46] T5+保有時にT3以下が来たらスワップしてT5を即活用 (T5→T6チェーン機会を優先)
    if (hold.type >= 5 && nextType <= 3 && holdMergeCount >= 1) {
      return { x: 0, reason: `HOLD_SWAP_T5PLUS_T${hold.type}`, hold: true };
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
    // [v47] let に変更してカスケードボーナスを追加
    let chainScore = c1 * 16 + c2 * 6 + c3 * 3 + c4 * 2;
    // [v47] 2段連鎖確定ボーナス: N+N→N+1後にN+1がN+1と隣接してN+2連鎖
    if (c1 > 0 && c2 > 0) chainScore += 15;
    // [v50] 3段連鎖ボーナス: より深い連鎖設定を強く優先
    if (c1 > 0 && c2 > 0 && c3 > 0) chainScore += 8;
    // [v47] 多重T_{N+1}ボーナス: 複数の即時チェーンターゲット = 確実連鎖
    if (c1 > 1) chainScore += (c1 - 1) * 8;
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

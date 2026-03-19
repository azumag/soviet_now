/**
 * strategy.mjs - ドロップ位置決定戦略 (v7)
 *
 * AI改善ループにより、このファイルは自動的に更新される。
 * インターフェースは固定: decide(boardState) -> { x, reason }
 *
 * v7改善点:
 * - ピースノイズ対策: 密集デデュプ + 物理的不可能位置の除外
 * - 列高さ計算: 幅を0.5に拡大し取りこぼし防止
 * - 超緊急モード: pieces>90またはmaxHeight>2.0 で低列強制ドロップ
 * - HOLD: 大型ピース温存 + より積極的なswap
 * - 近接同タイプ密集ボーナス強化で将来チェーン準備
 * - avoidSide閾値を3差に緩和（過剰回避防止）
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.2;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 80;

export function decide(boardState) {
  const { pieces, next, confidence, garbage, hold, canHold } = boardState;
  const nextType = next ? next.type : 1;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  // 物理範囲外を先に除外
  let inBound = pieces.filter(p =>
    p.x >= -3.5 && p.x <= 3.5 && p.y >= -6.0 && p.y <= 3.5
  );

  // 密集デデュプ: 同タイプが0.3以内に複数ある場合はY高いもの1つだけ残す
  inBound = deduplicatePieces(inBound);

  // 上位80件にフィルタ
  let activePieces = inBound;
  if (inBound.length > MAX_ACTIVE_PIECES) {
    activePieces = [...inBound].sort((a, b) => b.y - a.y).slice(0, MAX_ACTIVE_PIECES);
  }

  const unreliable = confidence < 0.3;
  if (unreliable) {
    const safeX = findLeastOccupiedX(activePieces);
    return { x: safeX, reason: `SPREAD_UNRELIABLE_X${safeX.toFixed(1)}` };
  }

  // 列高さ計算 (幅0.5で取りこぼし防止)
  const colHeights = computeColHeights(activePieces);

  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;
  const maxHeight = validH.length > 0 ? Math.max(...validH) : -3.0;

  // 危険側判定（閾値3差に緩和）
  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;
  let avoidSide = null;
  if (leftDanger > rightDanger + 3) avoidSide = 'left';
  else if (rightDanger > leftDanger + 3) avoidSide = 'right';

  // おじゃま状態
  const garbageRatio = garbage ? (garbage.ratio || 0) : 0;
  const garbageHeight = garbage ? (garbage.height || -5) : -5;
  const garbageUrgent = garbageRatio > 0.3 || garbageHeight > 0.5;

  // 圧迫度
  const boardPressure = activePieces.length > 55;
  const boardCritical = activePieces.length > 90 || maxHeight > 2.0;

  // --- HOLD判定 ---
  if (canHold) {
    const holdDecision = evaluateHold(activePieces, nextType, hold, avgHeight, boardCritical);
    if (holdDecision) return holdDecision;
  }

  // 超緊急: 最低列に大型マージを試み、無ければ強制低列ドロップ
  if (boardCritical) {
    if (nextType >= 4) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, null, avgHeight, garbageUrgent, 0);
      if (bigMerge) return { ...bigMerge, reason: `CRITICAL_${bigMerge.reason}` };
    }
    const drop = findLowestDrop(colHeights, avoidSide);
    return { ...drop, reason: `CRITICAL_${drop.reason}` };
  }

  // ボード圧迫時は高さ管理優先（大型はマージ優先）
  if (boardPressure) {
    if (nextType >= 5) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
      if (bigMerge) return { ...bigMerge, reason: `PRESSURE_${bigMerge.reason}` };
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, avoidSide);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  // 大型ピース(type>=6)優先マージ
  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  // チェーン期待値の高いマージ
  const chainMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  // 通常マージ
  const normalMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  // 高さバランス
  return findBestHeightDrop(activePieces, nextType, colHeights, avoidSide)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

/** 密集デデュプ: 同タイプが0.3以内なら上位Y座標のもの1つに絞る */
function deduplicatePieces(pieces) {
  const kept = [];
  const used = new Array(pieces.length).fill(false);
  // Y降順で処理
  const sorted = [...pieces].sort((a, b) => b.y - a.y);
  for (let i = 0; i < sorted.length; i++) {
    if (used[i]) continue;
    kept.push(sorted[i]);
    for (let j = i + 1; j < sorted.length; j++) {
      if (used[j]) continue;
      if (sorted[j].type === sorted[i].type &&
          Math.hypot(sorted[j].x - sorted[i].x, sorted[j].y - sorted[i].y) < 0.3) {
        used[j] = true;
      }
    }
  }
  return kept;
}

/** HOLD使用を評価 */
function evaluateHold(pieces, nextType, hold, avgHeight, boardCritical) {
  const nextMergeTargets = pieces.filter(p =>
    p.type === nextType && p.y < DEADLINE_Y - 0.3
  );

  if (hold && hold.type) {
    const holdType = hold.type;
    const holdMergeTargets = pieces.filter(p =>
      p.type === holdType && p.y < DEADLINE_Y - 0.3
    );

    // HOLDの方がマージ機会が多い
    if (holdMergeTargets.length >= 2 && nextMergeTargets.length === 0) {
      return { x: 0, reason: `HOLD_SWAP_T${holdType}`, hold: true };
    }
    // 大型HOLDピースにマージ先があり現ピースが小型
    if (holdType >= 6 && holdMergeTargets.length >= 1 && nextType <= 3) {
      return { x: 0, reason: `HOLD_BIGSWAP_T${holdType}`, hold: true };
    }
  } else {
    // HOLDが空: 現ピースのマージ先がなく盤面が十分ならsave
    if (nextMergeTargets.length === 0 && pieces.length > 8 && !boardCritical && nextType >= 3) {
      return { x: 0, reason: `HOLD_SAVE_T${nextType}`, hold: true };
    }
  }
  return null;
}

/** 列高さ計算 (幅±0.5で取りこぼし防止) */
function computeColHeights(pieces) {
  return FINE_COLS.map(cx => {
    const col = pieces.filter(p => Math.abs(p.x - cx) < 0.5);
    if (col.length === 0) return -5.0;
    return Math.max(...col.map(p => p.y + (p.r || 0.3)));
  });
}

/** 緊急時: 最も低い列を返す */
function findLowestDrop(colHeights, avoidSide) {
  let bestIdx = -1;
  let bestH = Infinity;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (avoidSide === 'left' && cx < -0.5) continue;
    if (avoidSide === 'right' && cx > 0.5) continue;
    if (colHeights[i] >= DEADLINE_Y) continue;
    if (colHeights[i] < bestH) {
      bestH = colHeights[i];
      bestIdx = i;
    }
  }

  if (bestIdx < 0) {
    const minH = Math.min(...colHeights);
    bestIdx = colHeights.indexOf(minH);
  }

  return { x: clampX(FINE_COLS[bestIdx]), reason: `LOWEST_COL${bestIdx}_Y${colHeights[bestIdx].toFixed(1)}` };
}

/** 最良マージ位置を探す */
function findBestMerge(pieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, minChainScore) {
  const candidates = pieces.filter(p =>
    p.type === nextType &&
    Math.abs(p.x) < WALL_MARGIN &&
    p.y < DEADLINE_Y - 0.1 &&
    !(avoidSide === 'left' && p.x < -0.5) &&
    !(avoidSide === 'right' && p.x > 0.5)
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

    // マージ後のtype+1が近くにいれば追加ボーナス
    const nextMergeReady = countNear(pieces, t.x, nextType + 1, 1.5);
    s += nextMergeReady * 4;

    // 近接同タイプ: 多重マージ期待
    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.0).length;
    s += nearSame * 3;

    if (garbageUrgent) s += 12;

    if (s > bestScore) {
      bestScore = s;
      bestTarget = t;
    }
  }

  if (!bestTarget) return null;
  return { x: clampX(bestTarget.x), reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
}

/** 高さバランスを優先した着弾位置 */
function findBestHeightDrop(pieces, nextType, colHeights, avoidSide) {
  let bestIdx = -1;
  let bestScore = -Infinity;

  for (let i = 0; i < FINE_COLS.length; i++) {
    const cx = FINE_COLS[i];
    if (avoidSide === 'left' && cx < -0.5) continue;
    if (avoidSide === 'right' && cx > 0.5) continue;
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i] * 2.5;

    // 同タイプ近傍: 将来マージ準備
    const nearSame = pieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.2 && p.y < DEADLINE_Y
    ).length;
    s += nearSame * 2.5;

    // 隣接列との高さ差ペナルティ
    const leftH = i > 0 ? colHeights[i - 1] : colHeights[i];
    const rightH = i < FINE_COLS.length - 1 ? colHeights[i + 1] : colHeights[i];
    const gap = Math.max(leftH, rightH) - colHeights[i];
    if (gap > 1.5) s -= 2.0;
    if (gap > 2.5) s -= 3.5;

    // 中央付近を若干好む
    s -= Math.abs(cx) * 0.15;

    if (s > bestScore) {
      bestScore = s;
      bestIdx = i;
    }
  }

  if (bestIdx < 0) {
    const minH = Math.min(...colHeights);
    const minIdx = colHeights.indexOf(minH);
    return { x: clampX(FINE_COLS[minIdx]), reason: `CRITICAL_COL${minIdx}` };
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
    n: pieces.filter(p => Math.abs(p.x - cx) < 0.5).length,
  }));
  const minN = Math.min(...counts.map(c => c.n));
  return counts
    .filter(c => c.n === minN)
    .reduce((a, b) => Math.abs(a.x) <= Math.abs(b.x) ? a : b).x;
}

function clampX(x) {
  return Math.max(-3.0, Math.min(3.0, x));
}
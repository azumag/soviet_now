/**
 * strategy.mjs - ドロップ位置決定戦略 (v6)
 *
 * AI改善ループにより、このファイルは自動的に更新される。
 * インターフェースは固定: decide(boardState) -> { x, reason }
 *
 * 重要: strategy.mjs は毎ターン動的importされるため、
 * モジュールレベルの可変状態は毎ターンリセットされる。
 * すべての判断は boardState のみに基づく純粋関数として実装すること。
 *
 * v6改善点:
 * - 細粒度グリッド(11列)でより正確な着弾位置
 * - マージ対象はピース実座標に着弾(列センタースナップ廃止)
 * - 3段チェーン評価強化
 * - 大型ピース(type>=6)優先マージ
 * - ボード圧迫時(60ピース超)の早期高さ管理
 * - 右寄りバイアス修正
 */

const FINE_COLS = [-2.5, -2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0, 2.5];
const DEADLINE_Y = 2.5;
const WARN_Y = 1.5;
const WALL_MARGIN = 2.8;
const MAX_ACTIVE_PIECES = 70;

export function decide(boardState) {
  const { pieces, next, confidence, garbage } = boardState;
  const nextType = next ? next.type : 1;

  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  // 多すぎる場合はY座標上位70件にフィルタ (他ボードの誤混入対策)
  let activePieces = pieces;
  if (pieces.length > MAX_ACTIVE_PIECES) {
    activePieces = [...pieces].sort((a, b) => b.y - a.y).slice(0, MAX_ACTIVE_PIECES);
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
  const boardPressure = activePieces.length > 60;

  // 細粒度列高さ
  const colHeights = FINE_COLS.map(cx => {
    const col = activePieces.filter(p => Math.abs(p.x - cx) < 0.35);
    if (col.length === 0) return -5.0;
    return Math.max(...col.map(p => p.y + (p.r || 0.3)));
  });

  const validH = colHeights.filter(h => h > -4.5);
  const avgHeight = validH.length > 0
    ? validH.reduce((a, b) => a + b, 0) / validH.length
    : -3.0;

  // 危険側判定 (早期・ソフト)
  const dangerPieces = activePieces.filter(p => p.y > WARN_Y);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;
  let avoidSide = null;
  if (leftDanger > rightDanger + 1) avoidSide = 'left';
  else if (rightDanger > leftDanger + 1) avoidSide = 'right';

  // ボード圧迫時は高さ管理を優先 (大型マージは例外)
  if (boardPressure) {
    if (nextType >= 5) {
      const bigMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
      if (bigMerge) return bigMerge;
    }
    const heightDrop = findBestHeightDrop(activePieces, nextType, colHeights, avoidSide);
    if (heightDrop) return { ...heightDrop, reason: `PRESSURE_${heightDrop.reason}` };
  }

  // --- 1. 大型ピース(type>=6)の優先マージ ---
  if (nextType >= 6) {
    const bigMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
    if (bigMerge) return bigMerge;
  }

  // --- 2. チェーン期待値の高いマージ ---
  const chainMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 4);
  if (chainMerge) return chainMerge;

  // --- 3. 通常マージ ---
  const normalMerge = findBestMerge(activePieces, nextType, colHeights, avoidSide, avgHeight, garbageUrgent, 0);
  if (normalMerge) return normalMerge;

  // --- 4. 高さバランス ---
  return findBestHeightDrop(activePieces, nextType, colHeights, avoidSide)
    || { x: 0.0, reason: 'CENTER_FALLBACK' };
}

/**
 * 最良マージ位置を探す
 * minChainScore: 0=全マージ, 4=チェーン期待が高いもののみ
 */
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
    const chainScore = c1 * 5 + c2 * 2.5 + c3 * 1.0;
    if (chainScore < minChainScore) continue;
    s += chainScore;

    // 近接同タイプ: 多重マージ期待
    const nearSame = candidates.filter(p => p !== t && Math.abs(p.x - t.x) < 1.0).length;
    s += nearSame * 2;

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
    s += nearSame * 2.0;

    // 隣接列との高さ差ペナルティ
    const leftH = i > 0 ? colHeights[i - 1] : colHeights[i];
    const rightH = i < FINE_COLS.length - 1 ? colHeights[i + 1] : colHeights[i];
    const gap = Math.max(leftH, rightH) - colHeights[i];
    if (gap > 1.5) s -= 2.0;
    if (gap > 2.5) s -= 3.0;

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
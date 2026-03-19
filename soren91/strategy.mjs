/**
 * strategy.mjs - ドロップ位置決定戦略 (v3 改善版)
 *
 * AI改善ループにより、このファイルは自動的に更新される。
 * インターフェースは固定: decide(boardState) -> { x, reason }
 */

const SPREAD_POSITIONS = [-2.0, -1.0, 0.0, 1.0, 2.0];
let dropCounter = 0;
let lastDropX = 0;
let recentDrops = []; // 直近N回のdropXを記憶して分散を促す

const COLUMNS = [-2.0, -1.0, 0.0, 1.0, 2.0];
const COL_WIDTH = 1.0;

const DEADLINE_Y = 2.5;
const WALL_MARGIN = 2.6;
const RECENT_WINDOW = 4;

export function decide(boardState) {
  const { pieces, next, score, confidence } = boardState;
  dropCounter++;

  const nextType = next ? next.type : 1;
  const suspiciousRead = pieces && pieces.length > 80;

  if (!pieces || pieces.length < 2 || confidence < 0.35 || suspiciousRead) {
    const pos = SPREAD_POSITIONS[dropCounter % SPREAD_POSITIONS.length];
    recordDrop(pos);
    return { x: pos, reason: `SPREAD_UNRELIABLE` };
  }

  const dangerPieces = pieces.filter(p => p.y > DEADLINE_Y - 0.8);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;

  let avoidX = null;
  if (leftDanger >= 2 && rightDanger === 0) avoidX = 'left';
  else if (rightDanger >= 2 && leftDanger === 0) avoidX = 'right';

  const colHeights = COLUMNS.map(cx => {
    const colPieces = pieces.filter(p => Math.abs(p.x - cx) < COL_WIDTH / 2);
    if (colPieces.length === 0) return -5.0;
    return Math.max(...colPieces.map(p => p.y + (p.r || 0.3)));
  });

  // 直近ドロップの左右バイアスを計算
  const recentBias = recentDrops.length > 0
    ? recentDrops.reduce((a, b) => a + b, 0) / recentDrops.length
    : 0;

  // --- 1. 同タイプ併合狙い ---
  const sameType = pieces.filter(p =>
    p.type === nextType &&
    Math.abs(p.x) < WALL_MARGIN &&
    p.y < DEADLINE_Y - 0.3
  );

  if (sameType.length > 0) {
    const safeTargets = avoidX
      ? sameType.filter(p => avoidX === 'left' ? p.x >= -0.5 : p.x <= 0.5)
      : sameType;

    const candidates = safeTargets.length > 0 ? safeTargets : sameType;

    let bestTarget = null;
    let bestScore = -Infinity;

    for (const t of candidates) {
      let s = -t.y;

      // チェーン評価
      const mergedType = nextType + 1;
      if (mergedType <= 15) {
        const chainPieces = pieces.filter(p =>
          p.type === mergedType && Math.abs(p.x - t.x) < 1.5
        );
        s += chainPieces.length * 2;
      }

      // 危険列ペナルティ
      const colIdx = COLUMNS.findIndex(cx => Math.abs(cx - t.x) < COL_WIDTH / 2);
      if (colIdx >= 0 && colHeights[colIdx] > DEADLINE_Y) s -= 10;

      // 同方向への集中にペナルティ (右集中なら右ターゲットを避ける)
      if (recentBias > 0.8 && t.x > 0.5) s -= 2;
      else if (recentBias < -0.8 && t.x < -0.5) s -= 2;

      // 直前と同じX位置への連続ドロップにペナルティ
      if (Math.abs(t.x - lastDropX) < 0.2) s -= 1.5;

      if (s > bestScore) {
        bestScore = s;
        bestTarget = t;
      }
    }

    if (bestTarget) {
      const dropX = clampX(bestTarget.x);
      recordDrop(dropX);
      return { x: dropX, reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
    }
  }

  // --- 2. 高さバランス戦略 ---
  let bestColIdx = -1;
  let bestScore = -Infinity;

  for (let i = 0; i < COLUMNS.length; i++) {
    const cx = COLUMNS[i];
    if (avoidX === 'left' && cx < -0.5) continue;
    if (avoidX === 'right' && cx > 0.5) continue;
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i];

    // 直近バイアスへの反対方向にボーナス
    if (recentBias > 0.8 && cx < 0) s += 1.5;
    else if (recentBias < -0.8 && cx > 0) s += 1.5;

    if (Math.abs(cx - lastDropX) < 0.3) s -= 0.5;

    if (s > bestScore) {
      bestScore = s;
      bestColIdx = i;
    }
  }

  if (bestColIdx >= 0) {
    const dropX = clampX(COLUMNS[bestColIdx]);
    recordDrop(dropX);
    return { x: dropX, reason: `HEIGHT_COL${bestColIdx}_Y${colHeights[bestColIdx].toFixed(1)}` };
  }

  // --- 3. 緊急: 最も低い列 ---
  const minHeight = Math.min(...colHeights);
  const minIdx = colHeights.indexOf(minHeight);
  const dropX = clampX(COLUMNS[minIdx]);
  recordDrop(dropX);
  return { x: dropX, reason: `EMERGENCY_COL${minIdx}` };
}

function recordDrop(x) {
  lastDropX = x;
  recentDrops.push(x);
  if (recentDrops.length > RECENT_WINDOW) recentDrops.shift();
}

function clampX(x) {
  return Math.max(-3.0, Math.min(3.0, x));
}
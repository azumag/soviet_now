/**
 * strategy.mjs - ドロップ位置決定戦略 (v5 高ピース数対応版)
 *
 * AI改善ループにより、このファイルは自動的に更新される。
 * インターフェースは固定: decide(boardState) -> { x, reason }
 *
 * 重要: strategy.mjs は毎ターン動的importされるため、
 * モジュールレベルの可変状態は毎ターンリセットされる。
 * すべての判断は boardState のみに基づく純粋関数として実装すること。
 */

const COLUMNS = [-2.0, -1.0, 0.0, 1.0, 2.0];
const COL_WIDTH = 1.0;
const DEADLINE_Y = 2.5;
const WALL_MARGIN = 2.8;

// 91人対戦ではスクリーンショットに複数ボードが映り込む可能性がある。
// 多すぎる場合は上位ピース（デッドライン付近＝実際の自分のボード）にフォーカスする。
const MAX_ACTIVE_PIECES = 70;

export function decide(boardState) {
  const { pieces, next, confidence, garbage } = boardState;
  const nextType = next ? next.type : 1;

  // ピースが全くない場合のみ中央にドロップ
  if (!pieces || pieces.length === 0) {
    return { x: 0.0, reason: 'NO_PIECES' };
  }

  // ピース数が多い場合、Y座標の高いもの（デッドライン寄り＝自ボードの有効領域）を優先サンプリング
  // これにより他プレイヤーのボードや誤検出を排除しやすくなる
  let activePieces = pieces;
  if (pieces.length > MAX_ACTIVE_PIECES) {
    activePieces = [...pieces]
      .sort((a, b) => b.y - a.y)
      .slice(0, MAX_ACTIVE_PIECES);
  }

  // おじゃまブロックが多い場合は積極的に併合を狙う
  const garbageUrgent = garbage && garbage.ratio > 0.4;

  // 危険ゾーン (デッドライン付近) のピース検出
  const dangerPieces = activePieces.filter(p => p.y > DEADLINE_Y - 0.8);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;

  let avoidSide = null;
  if (leftDanger >= 2 && rightDanger === 0) avoidSide = 'left';
  else if (rightDanger >= 2 && leftDanger === 0) avoidSide = 'right';

  // 各列の最高到達点を計算
  const colHeights = COLUMNS.map(cx => {
    const colPieces = activePieces.filter(p => Math.abs(p.x - cx) < COL_WIDTH / 2);
    if (colPieces.length === 0) return -5.0;
    return Math.max(...colPieces.map(p => p.y + (p.r || 0.3)));
  });

  // 全体の平均高さ (バランス評価用)
  const validHeights = colHeights.filter(h => h > -5.0);
  const avgHeight = validHeights.length > 0
    ? validHeights.reduce((a, b) => a + b, 0) / validHeights.length
    : -3.0;

  // --- 1. 同タイプ併合狙い ---
  const sameType = activePieces.filter(p =>
    p.type === nextType &&
    Math.abs(p.x) < WALL_MARGIN &&
    p.y < DEADLINE_Y - 0.2 &&
    !(avoidSide === 'left' && p.x < -0.5) &&
    !(avoidSide === 'right' && p.x > 0.5)
  );

  if (sameType.length > 0) {
    let bestTarget = null;
    let bestMergeScore = -Infinity;

    for (const t of sameType) {
      let s = -t.y * 0.5;

      const mergedType = nextType + 1;
      if (mergedType <= 15) {
        const chainPieces = activePieces.filter(p =>
          p.type === mergedType && Math.abs(p.x - t.x) < 1.5
        );
        s += chainPieces.length * 3;

        const chain2Type = mergedType + 1;
        if (chain2Type <= 15) {
          const chain2Pieces = activePieces.filter(p =>
            p.type === chain2Type && Math.abs(p.x - t.x) < 2.0
          );
          s += chain2Pieces.length * 1.5;
        }
      }

      s += nextType * 0.8;

      const colIdx = COLUMNS.findIndex(cx => Math.abs(cx - t.x) < COL_WIDTH / 2);
      if (colIdx >= 0) {
        if (colHeights[colIdx] > DEADLINE_Y) s -= 20;
        else if (colHeights[colIdx] > DEADLINE_Y - 0.5) s -= 5;
      }

      if (t.y > avgHeight + 0.5) s -= 2;
      if (garbageUrgent) s += 10;

      if (s > bestMergeScore) {
        bestMergeScore = s;
        bestTarget = t;
      }
    }

    if (bestTarget) {
      const dropX = clampX(bestTarget.x);
      return { x: dropX, reason: `MERGE_T${nextType}_X${bestTarget.x.toFixed(1)}` };
    }
  }

  // --- 2. 高さバランス戦略 ---
  let bestColIdx = -1;
  let bestHeightScore = -Infinity;

  for (let i = 0; i < COLUMNS.length; i++) {
    const cx = COLUMNS[i];
    if (avoidSide === 'left' && cx < -0.5) continue;
    if (avoidSide === 'right' && cx > 0.5) continue;
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i];

    const nearSameType = activePieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.5 && p.y < DEADLINE_Y
    );
    s += nearSameType.length * 1.0;

    const leftH = i > 0 ? colHeights[i - 1] : colHeights[i];
    const rightH = i < COLUMNS.length - 1 ? colHeights[i + 1] : colHeights[i];
    const maxNeighbor = Math.max(leftH, rightH);
    if (maxNeighbor - colHeights[i] > 1.5) s -= 1.5;

    if (s > bestHeightScore) {
      bestHeightScore = s;
      bestColIdx = i;
    }
  }

  if (bestColIdx >= 0) {
    const dropX = clampX(COLUMNS[bestColIdx]);
    return { x: dropX, reason: `HEIGHT_COL${bestColIdx}_Y${colHeights[bestColIdx].toFixed(1)}` };
  }

  // --- 3. 緊急 ---
  const safeHeights = colHeights.map((h, i) => ({ h, i })).filter(c => c.h <= DEADLINE_Y);
  if (safeHeights.length > 0) {
    const best = safeHeights.reduce((a, b) => a.h < b.h ? a : b);
    return { x: clampX(COLUMNS[best.i]), reason: `EMERGENCY_COL${best.i}` };
  }

  // --- 4. 最終手段 ---
  const minHeight = Math.min(...colHeights);
  const minIdx = colHeights.indexOf(minHeight);
  return { x: clampX(COLUMNS[minIdx]), reason: `CRITICAL_COL${minIdx}` };
}

function clampX(x) {
  return Math.max(-3.0, Math.min(3.0, x));
}
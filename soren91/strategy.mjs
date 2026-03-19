/**
 * strategy.mjs - ドロップ位置決定戦略 (v4 ステートレス版)
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

export function decide(boardState) {
  const { pieces, next, confidence, garbage } = boardState;
  const nextType = next ? next.type : 1;

  // ピース数が非常に多い場合は解析精度が低下しているが、
  // 91人対戦では多くのピースが正常。120超で過負荷と判断。
  const overloaded = pieces && pieces.length > 120;
  const unreliable = !pieces || pieces.length < 2 || confidence < 0.35 || overloaded;

  if (unreliable) {
    // dropCounter は毎ターンリセットされるため使用不可。
    // ボード状態から最もピースが少ない列を選ぶ。
    const safeX = findLeastOccupiedX(pieces || []);
    return { x: safeX, reason: `SPREAD_UNRELIABLE_X${safeX.toFixed(1)}` };
  }

  // おじゃまブロックが多い場合は積極的に併合を狙う
  const garbageUrgent = garbage && garbage.ratio > 0.4;

  // 危険ゾーン (デッドライン付近) のピース検出
  const dangerPieces = pieces.filter(p => p.y > DEADLINE_Y - 0.8);
  const leftDanger = dangerPieces.filter(p => p.x < 0).length;
  const rightDanger = dangerPieces.filter(p => p.x >= 0).length;

  let avoidSide = null;
  if (leftDanger >= 2 && rightDanger === 0) avoidSide = 'left';
  else if (rightDanger >= 2 && leftDanger === 0) avoidSide = 'right';

  // 各列の最高到達点を計算
  const colHeights = COLUMNS.map(cx => {
    const colPieces = pieces.filter(p => Math.abs(p.x - cx) < COL_WIDTH / 2);
    if (colPieces.length === 0) return -5.0;
    return Math.max(...colPieces.map(p => p.y + (p.r || 0.3)));
  });

  // 全体の平均高さ (バランス評価用)
  const validHeights = colHeights.filter(h => h > -5.0);
  const avgHeight = validHeights.length > 0
    ? validHeights.reduce((a, b) => a + b, 0) / validHeights.length
    : -3.0;

  // --- 1. 同タイプ併合狙い ---
  const sameType = pieces.filter(p =>
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
      // 基本スコア: より低い位置のピースを優先 (上に積まれにくい)
      let s = -t.y * 0.5;

      // チェーン評価: 併合後タイプが近くにあればボーナス
      const mergedType = nextType + 1;
      if (mergedType <= 15) {
        const chainPieces = pieces.filter(p =>
          p.type === mergedType && Math.abs(p.x - t.x) < 1.5
        );
        s += chainPieces.length * 3;

        // 2段チェーン評価
        const chain2Type = mergedType + 1;
        if (chain2Type <= 15) {
          const chain2Pieces = pieces.filter(p =>
            p.type === chain2Type && Math.abs(p.x - t.x) < 2.0
          );
          s += chain2Pieces.length * 1.5;
        }
      }

      // 大型ピースの併合はより価値が高い
      s += nextType * 0.8;

      // デッドライン超過列は厳しくペナルティ
      const colIdx = COLUMNS.findIndex(cx => Math.abs(cx - t.x) < COL_WIDTH / 2);
      if (colIdx >= 0) {
        if (colHeights[colIdx] > DEADLINE_Y) s -= 20;
        else if (colHeights[colIdx] > DEADLINE_Y - 0.5) s -= 5;
      }

      // 平均より高い列はペナルティ
      if (t.y > avgHeight + 0.5) s -= 2;

      // おじゃま緊急時は無条件に最初の有効ターゲット
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

  // --- 2. 高さバランス戦略: 最も低い安全な列に積む ---
  let bestColIdx = -1;
  let bestHeightScore = -Infinity;

  for (let i = 0; i < COLUMNS.length; i++) {
    const cx = COLUMNS[i];
    if (avoidSide === 'left' && cx < -0.5) continue;
    if (avoidSide === 'right' && cx > 0.5) continue;
    if (colHeights[i] > DEADLINE_Y) continue;

    let s = -colHeights[i];

    // 近くに同タイプがあれば将来の併合チャンスとしてボーナス
    const nearSameType = pieces.filter(p =>
      p.type === nextType && Math.abs(p.x - cx) < 1.5 && p.y < DEADLINE_Y
    );
    s += nearSameType.length * 1.0;

    // 隣接列との高さ差が大きいとペナルティ (崩れやすい)
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

  // --- 3. 緊急: デッドラインを超えていない最も低い列 ---
  const safeHeights = colHeights.map((h, i) => ({ h, i })).filter(c => c.h <= DEADLINE_Y);
  if (safeHeights.length > 0) {
    const best = safeHeights.reduce((a, b) => a.h < b.h ? a : b);
    return { x: clampX(COLUMNS[best.i]), reason: `EMERGENCY_COL${best.i}` };
  }

  // --- 4. 最終手段: 全列危険、最も低い列 ---
  const minHeight = Math.min(...colHeights);
  const minIdx = colHeights.indexOf(minHeight);
  return { x: clampX(COLUMNS[minIdx]), reason: `CRITICAL_COL${minIdx}` };
}

/**
 * ピース配置から最もピースが少ない列のX座標を返す
 * SPREAD_UNRELIABLE時のフォールバック用 (dropCounterは使えない)
 */
function findLeastOccupiedX(pieces) {
  const colCounts = COLUMNS.map(cx => ({
    x: cx,
    count: pieces.filter(p => Math.abs(p.x - cx) < COL_WIDTH / 2).length,
  }));
  const minCount = Math.min(...colCounts.map(c => c.count));
  // 同数の場合は中央寄りを優先
  const best = colCounts
    .filter(c => c.count === minCount)
    .reduce((a, b) => Math.abs(a.x) <= Math.abs(b.x) ? a : b);
  return best.x;
}

function clampX(x) {
  return Math.max(-3.0, Math.min(3.0, x));
}
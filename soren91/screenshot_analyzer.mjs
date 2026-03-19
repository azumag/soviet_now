/**
 * screenshot_analyzer.mjs - スクリーンショット → 盤面状態抽出
 *
 * Sharp でスクリーンショットを処理し、ゲーム状態を推定する。
 * - ゲーム状態検出: MOVE / GAMEOVER / DROP
 * - ピース検出: 色セグメンテーション + サイズ分類
 * - 次のピース検出
 * - スコア検出
 */

import sharp from 'sharp';

// ピースタイプごとの半径 (ゲーム座標単位)
// 型番号 → 半径のマッピング (ゲーム内データから抽出)
const TYPE_RADII = {
  1: 0.207,
  2: 0.259,
  3: 0.316,
  4: 0.380,
  5: 0.414,
  6: 0.470,
  7: 0.559,
  8: 0.660,
  9: 0.746,
  10: 0.846,
  11: 0.982,
  12: 1.068,
  13: 1.207,
  14: 1.385,
  15: 1.600,
};

// ピースタイプごとの代表色 (HSV/RGB近似) - 実際のゲーム画面で調整が必要
// これは初期推定値。実際のスクリーンショットで色をキャリブレーションする
const TYPE_COLORS = {
  1:  { r: 200, g: 50,  b: 50,  name: 'red-small' },
  2:  { r: 220, g: 100, b: 50,  name: 'orange-small' },
  3:  { r: 220, g: 180, b: 50,  name: 'yellow' },
  4:  { r: 50,  g: 180, b: 50,  name: 'green-small' },
  5:  { r: 50,  g: 200, b: 200, name: 'cyan' },
  6:  { r: 50,  g: 100, b: 200, name: 'blue' },
  7:  { r: 150, g: 50,  b: 200, name: 'purple' },
  8:  { r: 200, g: 50,  b: 150, name: 'pink' },
  9:  { r: 180, g: 180, b: 50,  name: 'olive' },
  10: { r: 100, g: 150, b: 100, name: 'sage' },
  11: { r: 200, g: 150, b: 100, name: 'tan' },
  12: { r: 150, g: 100, b: 50,  name: 'brown' },
  13: { r: 100, g: 50,  b: 50,  name: 'maroon' },
  14: { r: 200, g: 200, b: 200, name: 'silver' },
  15: { r: 220, g: 50,  b: 50,  name: 'red-large' },
};

/**
 * スクリーンショットからゲーム盤面を解析する
 *
 * @param {string} screenshotPath - スクリーンショットのパス
 * @param {object} calibration - キャリブレーションデータ
 * @returns {object} 盤面状態
 */
export async function analyzeScreenshot(screenshotPath, calibration) {
  const image = sharp(screenshotPath);
  const metadata = await image.metadata();
  const { data } = await image.raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const { width, height } = metadata;
  const { board } = calibration;

  // 1. ゲーム状態検出
  const state = detectGameState(data, width, height, board);

  // 2. ピース検出
  const pieces = state === 'MOVE' || state === 'DROP'
    ? detectPieces(data, width, height, calibration)
    : [];

  // 3. 次のピース検出
  const next = state === 'MOVE'
    ? detectNextPiece(data, width, height, board)
    : null;

  // 4. おじゃまブロック量を測定 (灰色領域の割合)
  const garbage = state === 'MOVE'
    ? measureGarbage(data, width, height, calibration)
    : { ratio: 0, height: 0 };

  // 5. スコア検出 (簡易版 - 後でOCR/テンプレートマッチに改良)
  const score = 0; // TODO: スコア検出実装

  return {
    state,
    score,
    pieces,
    next,
    garbage, // { ratio: 0-1, height: ゲーム座標でのおじゃまの高さ }
    confidence: pieces.length > 0 ? 0.5 : 0.3,
  };
}

/**
 * ゲーム状態を検出する
 *
 * ゲーム画面構造:
 *   左側(~30%): 他プレイヤーのミニボード (黄色/オレンジ背景)
 *   中央(~40%): 自分のプレイエリア (暗い背景 + ピース)
 *   右側(~30%): 他プレイヤーのミニボード
 *   上部: UI (YOUR score, HOLD, NEXT)
 *
 * 状態:
 *   MOVE: ゲームボード表示中（ドロップ操作可能）
 *   WAITING: ランキング/接続/マッチング画面
 *   GAMEOVER: ゲームオーバー
 */
function detectGameState(data, width, height, board) {
  // 画面中央列 (自分のプレイエリア) のみ分析
  // ゲームボード: 中央30-70%が暗い背景
  const centerLeft = Math.floor(width * 0.35);
  const centerRight = Math.floor(width * 0.65);
  const topArea = Math.floor(height * 0.1);  // UI上部を除外

  let centerDark = 0;   // 暗いピクセル (ゲームボード背景)
  let centerRed = 0;    // 赤 (接続画面)
  let centerYellow = 0; // 黄色 (ランキング画面)
  let centerSamples = 0;

  const step = 8;
  for (let y = topArea; y < height; y += step) {
    for (let x = centerLeft; x < centerRight; x += step) {
      const idx = (y * width + x) * 4;
      const r = data[idx], g = data[idx + 1], b = data[idx + 2];
      const brightness = (r + g + b) / 3;
      centerSamples++;

      if (brightness < 50) centerDark++;
      if (r > 180 && g < 80 && b < 80) centerRed++;
      if (r > 180 && g > 120 && b < 80) centerYellow++;
    }
  }

  if (centerSamples === 0) return 'WAITING';

  const darkRatio = centerDark / centerSamples;
  const redRatio = centerRed / centerSamples;
  const yellowRatio = centerYellow / centerSamples;

  // ゲームボード判定: 暗い部分(ボード背景)が十分あればMOVE
  // ピースやおじゃまに赤・黄色があってもボード背景の暗さでゲーム中と判別
  if (darkRatio > 0.1) {
    return 'MOVE';
  }

  // 接続中/タイトル画面: 赤が支配的で暗い部分がほぼない
  if (redRatio > 0.08) {
    return 'WAITING';
  }

  // ランキング画面: 黄色/オレンジが支配的
  if (yellowRatio > 0.10) {
    return 'WAITING';
  }

  return 'WAITING';
}

/**
 * ピースを検出する (v1: 色ベースblob検出)
 * 精度は低いが、AI改善ループで戦略がノイズ耐性方向に進化する前提
 */
function detectPieces(data, width, height, calibration) {
  const { board } = calibration;
  const pieces = [];

  // ボード領域内をグリッドスキャンし、色付きブロブを検出
  const gridStep = 4; // ピクセル単位のスキャン間隔 (91人対戦ではピースが小さい)
  const visited = new Set();

  for (let y = board.top; y < board.bottom; y += gridStep) {
    for (let x = board.left; x < board.right; x += gridStep) {
      const key = `${Math.floor(x / gridStep)}_${Math.floor(y / gridStep)}`;
      if (visited.has(key)) continue;

      const idx = (y * width + x) * 4;
      const r = data[idx], g = data[idx + 1], b = data[idx + 2], a = data[idx + 3];

      // 背景色を除外
      // ボード背景: rgb(50,50,49) brightness≈50, 壁: brightness≈155-247
      // デッドライン等のUI: 灰色の横線
      const brightness = (r + g + b) / 3;
      if (brightness < 60 || a < 200) continue; // ボード背景(≈50)を除外

      // 彩度チェック - ピースは色がついている
      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      const saturation = max > 0 ? (max - min) / max : 0;
      // 灰色(壁、UIライン)を除外: 低彩度 かつ 非白
      if (saturation < 0.15 && brightness < 220) continue;
      // 壁の色(155-190)を除外
      if (saturation < 0.1 && brightness > 100 && brightness < 200) continue;

      // この色が新しいblobの開始点か確認
      const blob = floodFillEstimate(data, width, height, x, y, r, g, b, gridStep, visited, board);
      if (blob && blob.pixelCount > 5) { // 最小ブロブサイズ (ゲームの小ピースは小さい)
        // ブロブサイズからピースタイプを推定
        const piece = classifyBlob(blob, calibration);
        if (piece) {
          pieces.push(piece);
        }
      }
    }
  }

  return pieces;
}

/**
 * 簡易フラッドフィル: 同色領域のサイズと中心を推定
 */
function floodFillEstimate(data, width, height, startX, startY, targetR, targetG, targetB, gridStep, visited, board) {
  const colorThreshold = 50; // RGB差の許容値
  const queue = [{ x: startX, y: startY }];
  let totalX = 0, totalY = 0, count = 0;
  let minX = startX, maxX = startX, minY = startY, maxY = startY;

  while (queue.length > 0) {
    const { x, y } = queue.shift();
    const key = `${Math.floor(x / gridStep)}_${Math.floor(y / gridStep)}`;
    if (visited.has(key)) continue;
    if (x < board.left || x >= board.right || y < board.top || y >= board.bottom) continue;

    const idx = (y * width + x) * 4;
    const r = data[idx], g = data[idx + 1], b = data[idx + 2];
    const diff = Math.abs(r - targetR) + Math.abs(g - targetG) + Math.abs(b - targetB);
    if (diff > colorThreshold) continue;

    visited.add(key);
    totalX += x;
    totalY += y;
    count++;
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;

    // 4方向に拡大
    queue.push({ x: x + gridStep, y });
    queue.push({ x: x - gridStep, y });
    queue.push({ x, y: y + gridStep });
    queue.push({ x, y: y - gridStep });
  }

  if (count === 0) return null;

  return {
    centerX: totalX / count,
    centerY: totalY / count,
    pixelCount: count,
    bboxWidth: maxX - minX,
    bboxHeight: maxY - minY,
    avgColor: { r: targetR, g: targetG, b: targetB },
  };
}

/**
 * ブロブのサイズからピースタイプを推定
 */
function classifyBlob(blob, calibration) {
  const { board } = calibration;

  // ブロブの平均半径（ピクセル）
  const blobRadius = Math.sqrt(blob.bboxWidth * blob.bboxHeight) / 2;

  // ピクセル→ゲーム座標の変換スケール
  const pixelsPerGameUnit = board.width / 7.0;

  // ゲーム座標での半径
  const gameRadius = blobRadius / pixelsPerGameUnit;

  // 最も近いタイプを見つける
  let bestType = 1;
  let bestDiff = Infinity;

  for (const [type, radius] of Object.entries(TYPE_RADII)) {
    const diff = Math.abs(gameRadius - radius);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestType = parseInt(type);
    }
  }

  // ゲーム座標での中心位置
  const normalizedX = (blob.centerX - board.left) / board.width;
  const gameX = -3.5 + normalizedX * 7.0;

  const normalizedY = (board.bottom - blob.centerY) / board.height;
  const gameY = -5.0 + normalizedY * 8.32; // total range: -5.0 to 3.32

  return {
    type: bestType,
    x: Math.round(gameX * 100) / 100,
    y: Math.round(gameY * 100) / 100,
    r: TYPE_RADII[bestType],
  };
}

/**
 * 次のピースを検出する
 * 画面上部のNEXT表示領域から次のピースのタイプを推定
 */
function detectNextPiece(data, width, height, board) {
  // NEXT表示は通常、ゲームエリアの上部に表示される
  // ボード上部の上方の領域をサンプリング
  const nextAreaTop = Math.max(0, board.top - 80);
  const nextAreaBottom = board.top;
  const nextAreaLeft = board.left + Math.floor(board.width * 0.3);
  const nextAreaRight = board.left + Math.floor(board.width * 0.7);

  // この領域で最も大きい色つきブロブを探す
  let maxBlobSize = 0;
  let bestColor = null;

  const gridStep = 4;
  const visited = new Set();

  for (let y = nextAreaTop; y < nextAreaBottom; y += gridStep) {
    for (let x = nextAreaLeft; x < nextAreaRight; x += gridStep) {
      const key = `${Math.floor(x / gridStep)}_${Math.floor(y / gridStep)}`;
      if (visited.has(key)) continue;

      const idx = (y * width + x) * 4;
      const r = data[idx], g = data[idx + 1], b = data[idx + 2];
      const brightness = (r + g + b) / 3;
      if (brightness < 40) continue;

      const max = Math.max(r, g, b);
      const min = Math.min(r, g, b);
      const saturation = max > 0 ? (max - min) / max : 0;
      if (saturation < 0.15) continue;

      const blob = floodFillEstimate(data, width, height, x, y, r, g, b, gridStep, visited,
        { left: nextAreaLeft, right: nextAreaRight, top: nextAreaTop, bottom: nextAreaBottom });

      if (blob && blob.pixelCount > maxBlobSize) {
        maxBlobSize = blob.pixelCount;
        bestColor = blob.avgColor;
      }
    }
  }

  if (!bestColor) {
    // デフォルト: 小さいピース
    return { type: 1, r: TYPE_RADII[1] };
  }

  // 色から最も近いタイプを推定
  let bestType = 1;
  let bestDiff = Infinity;
  for (const [type, color] of Object.entries(TYPE_COLORS)) {
    const diff = Math.abs(bestColor.r - color.r) + Math.abs(bestColor.g - color.g) + Math.abs(bestColor.b - color.b);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestType = parseInt(type);
    }
  }

  // ドロップ可能なピースはtype 1-5程度（小さいもの）
  if (bestType > 5) bestType = Math.min(bestType, 5);

  return { type: bestType, r: TYPE_RADII[bestType] };
}

/**
 * おじゃまブロック(灰色)の量を測定
 * ボード内の灰色(低彩度, 中輝度)ピクセルの割合と、最高到達Y座標を返す
 */
function measureGarbage(data, width, height, calibration) {
  const { board } = calibration;
  let garbageCount = 0;
  let totalCount = 0;
  let highestGarbageY = board.bottom; // ピクセルY (小さい = 高い位置)

  const step = 6;
  for (let y = board.top; y < board.bottom; y += step) {
    for (let x = board.left; x < board.right; x += step) {
      const idx = (y * width + x) * 4;
      const r = data[idx], g = data[idx + 1], b = data[idx + 2];
      const brightness = (r + g + b) / 3;
      const sat = Math.max(r, g, b) > 0 ? (Math.max(r, g, b) - Math.min(r, g, b)) / Math.max(r, g, b) : 0;

      // ボード背景(brightness<60)を除外
      if (brightness < 60) continue;

      totalCount++;

      // おじゃまブロック: 灰色 (中輝度, 低彩度)
      // brightness 100-200, saturation < 0.1
      if (brightness > 100 && brightness < 200 && sat < 0.1) {
        garbageCount++;
        if (y < highestGarbageY) highestGarbageY = y;
      }
    }
  }

  const ratio = totalCount > 0 ? garbageCount / totalCount : 0;

  // ピクセルY → ゲーム座標Y
  const totalGameHeight = 3.32 - (-5.0); // 8.32
  const normalizedY = (board.bottom - highestGarbageY) / board.height;
  const garbageGameY = -5.0 + normalizedY * totalGameHeight;

  return {
    ratio: Math.round(ratio * 100) / 100, // 0-1
    height: garbageCount > 0 ? Math.round(garbageGameY * 10) / 10 : -5.0, // ゲーム座標
    pixelCount: garbageCount,
  };
}

export { TYPE_RADII };

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

  // 3. 次のピース検出 (3つ)
  const nextPieces = state === 'MOVE'
    ? detectNextPieces(data, width, height, board)
    : [];
  const next = nextPieces.length > 0 ? nextPieces[0] : null;

  // 4. おじゃまブロック量を測定 (灰色領域の割合)
  const garbage = state === 'MOVE'
    ? measureGarbage(data, width, height, calibration)
    : { ratio: 0, height: 0 };

  // 4b. おじゃまゲージ検出 (左壁の予告ゲージ)
  if (state === 'MOVE') {
    const gauge = detectOjamaGauge(data, width, height, calibration);
    garbage.gauge = gauge.level;
  } else {
    garbage.gauge = 0;
  }

  // 5. HOLD ピース検出
  const hold = state === 'MOVE'
    ? detectHoldPiece(data, width, height, board)
    : null;

  // 6. 順位検出 — リアルタイムOCRは精度不足のため無効化
  // ランキング画面からの検出 (detectRankingScreen) に委ねる
  const rank = null;

  return {
    state,
    rank,  // 現在の順位 (1-91) or null
    pieces,
    next,             // { type, r } — 1つ目 (後方互換)
    nextPieces,       // [{ type, r }, ...] — 最大3つ
    hold,             // { type, r } or null
    garbage,          // { ratio: 0-1, height: ゲーム座標でのおじゃまの高さ, gauge: 0-1 おじゃまゲージレベル }
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
 * HOLD領域のピースを検出する
 * 画面上部のHOLD表示領域(YOURとNEXTの間)からピースのタイプを推定
 */
function detectHoldPiece(data, width, height, board) {
  // HOLD領域: ボード上部左寄り (YOURの右、NEXTの左)
  const holdAreaTop = 0;
  const holdAreaBottom = board.top + 30;
  const holdAreaLeft = board.left + Math.floor(board.width * 0.15);
  const holdAreaRight = board.left + Math.floor(board.width * 0.45);

  return detectPieceInArea(data, width, height, holdAreaTop, holdAreaBottom, holdAreaLeft, holdAreaRight);
}

/**
 * NEXT領域から最大3つのピースを検出する
 * NEXT表示は縦に3つ並んでいる
 */
function detectNextPieces(data, width, height, board) {
  const nextAreaLeft = board.left + Math.floor(board.width * 0.55);
  const nextAreaRight = board.right;
  // NEXT領域は上部UIから盤面内に延びる (3ピース分の高さ)
  const nextAreaTop = 0;
  const nextAreaBottom = board.top + 90;
  const slotHeight = Math.floor((nextAreaBottom - nextAreaTop) / 3);

  const results = [];
  for (let i = 0; i < 3; i++) {
    const slotTop = nextAreaTop + slotHeight * i;
    const slotBottom = slotTop + slotHeight;
    const piece = detectPieceInArea(data, width, height, slotTop, slotBottom, nextAreaLeft, nextAreaRight);
    if (piece) results.push(piece);
  }

  // 最低1つは返す
  if (results.length === 0) results.push({ type: 1, r: TYPE_RADII[1] });
  return results;
}

/**
 * 指定領域内の最大色ブロブからピースタイプを推定 (HOLD/NEXT共通)
 * ピースが無い場合は null を返す
 */
function detectPieceInArea(data, width, height, areaTop, areaBottom, areaLeft, areaRight) {
  let maxBlobSize = 0;
  let bestColor = null;

  const gridStep = 4;
  const visited = new Set();

  for (let y = areaTop; y < areaBottom; y += gridStep) {
    for (let x = areaLeft; x < areaRight; x += gridStep) {
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
        { left: areaLeft, right: areaRight, top: areaTop, bottom: areaBottom });

      if (blob && blob.pixelCount > maxBlobSize) {
        maxBlobSize = blob.pixelCount;
        bestColor = blob.avgColor;
      }
    }
  }

  if (!bestColor) return null;

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

  // ドロップ可能なピースはtype 1-5程度
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

/**
 * おじゃまゲージを検出する
 * ボード左壁の外側にある縦ゲージ（アメリカ国旗から降ってくるおじゃま予告）のレベルを検出
 * ゲージが下まで到達するとおじゃまブロックが降ってくる
 *
 * 検出方法: 左壁の外側〜壁境界の細い領域をスキャンし、
 * 通常の壁色(灰色)やミニボード背景(黄/オレンジ)でない
 * 着色ピクセル(赤/青/白のアメリカ国旗カラー等)の縦方向の広がりを測定
 */
function detectOjamaGauge(data, width, height, calibration) {
  const { board, walls } = calibration;
  if (!walls) return { level: 0 };

  // ゲージ領域: ボード左壁の外側〜壁境界にかけての細い縦領域
  const scanLeft = Math.max(0, walls.leftOuter - 15);
  const scanRight = walls.leftOuter + 3;
  const scanTop = board.top + 10;
  const scanBottom = board.bottom - 5;
  const totalHeight = scanBottom - scanTop;

  if (totalHeight <= 0 || scanLeft >= scanRight) return { level: 0 };

  // セクションごとにスキャン (10px単位)
  const sectionSize = 8;
  const rowStep = 2;
  let filledSections = 0;
  let totalSections = 0;
  let lowestFilledY = scanTop;

  for (let sy = scanTop; sy < scanBottom; sy += sectionSize) {
    totalSections++;
    let coloredCount = 0;
    let sampleCount = 0;
    const syEnd = Math.min(sy + sectionSize, scanBottom);

    for (let y = sy; y < syEnd; y += rowStep) {
      for (let x = scanLeft; x < scanRight; x += 2) {
        const idx = (y * width + x) * 4;
        const r = data[idx], g = data[idx + 1], b = data[idx + 2];
        const brightness = (r + g + b) / 3;
        const max = Math.max(r, g, b);
        const min = Math.min(r, g, b);
        const sat = max > 0 ? (max - min) / max : 0;

        sampleCount++;

        // 除外: 暗い背景(brightness<55)
        if (brightness < 55) continue;
        // 除外: 通常の壁色 (灰色: brightness 140-250, sat<0.12)
        if (brightness >= 140 && brightness <= 250 && sat < 0.12) continue;
        // 除外: ミニボード背景の黄/オレンジ (r>160,g>100,b<90)
        if (r > 160 && g > 100 && b < 90) continue;

        // ゲージの色: アメリカ国旗カラー or 明るく彩度のある色
        const isRed = r > 150 && g < 100 && b < 100;
        const isBlue = r < 100 && g < 100 && b > 140;
        const isWhiteBright = brightness > 210 && sat < 0.2;
        const isSaturated = sat > 0.25 && brightness > 80;

        if (isRed || isBlue || isWhiteBright || isSaturated) {
          coloredCount++;
        }
      }
    }

    // セクションの25%以上が着色 → ゲージ充填あり
    if (sampleCount > 0 && coloredCount / sampleCount > 0.25) {
      filledSections++;
      if (sy + sectionSize > lowestFilledY) lowestFilledY = sy + sectionSize;
    }
  }

  if (filledSections === 0) return { level: 0 };

  // ノイズフィルタ: 全セクションの8%未満ならノイズ
  if (filledSections / totalSections < 0.08) return { level: 0 };

  // ゲージレベル: 上から下への充填度合い (0=空, 1=満タン→おじゃま発動)
  const level = Math.min(1.0, (lowestFilledY - scanTop) / totalHeight);

  return {
    level: Math.round(level * 100) / 100,
  };
}

/**
 * YOUR領域から現在の順位を検出する
 * 順位はYOUR下部に白/シアン文字で表示 (1-91)
 */
function detectRank(data, width, height, board) {
  // 順位表示エリア: YOUR下部の大きな数字 (y≈60-82 付近)
  const x1 = board.left - 9;
  const x2 = board.left + 36;
  const scanY1 = board.top + 40;
  const scanY2 = board.top + 68;

  if (x1 < 0 || scanY2 > height || x2 > width) return null;

  // 明るい行の範囲を自動検出
  let y1 = scanY2, y2 = scanY1;
  for (let y = scanY1; y < scanY2; y++) {
    for (let x = x1; x < x2; x++) {
      const idx = (y * width + x) * 4;
      if ((data[idx] + data[idx + 1] + data[idx + 2]) / 3 > 90) {
        if (y < y1) y1 = y;
        if (y + 1 > y2) y2 = y + 1;
      }
    }
  }
  if (y2 <= y1 || y2 - y1 < 4) return null;

  // 列ごとの明るいピクセル数
  const colBright = [];
  for (let x = x1; x < x2; x++) {
    let cnt = 0;
    for (let y = y1; y < y2; y++) {
      const idx = (y * width + x) * 4;
      const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
      if (br > 90) cnt++;
    }
    colBright.push(cnt);
  }

  // 明るいコンテンツの全体範囲を検出
  let firstBright = -1, lastBright = -1;
  for (let i = 0; i < colBright.length; i++) {
    if (colBright[i] >= 2) {
      if (firstBright < 0) firstBright = i;
      lastBright = i;
    }
  }
  if (firstBright < 0) return null;

  const totalWidth = lastBright - firstBright + 1;
  const absFirst = firstBright + x1;
  const absLast = lastBright + x1 + 1;

  let digitRanges;
  if (totalWidth <= 18) {
    // 1桁
    digitRanges = [{ start: absFirst, end: absLast }];
  } else {
    // 2桁: 中央付近で最も暗い列で分割
    const mid = firstBright + Math.floor(totalWidth * 0.3);
    const midEnd = firstBright + Math.floor(totalWidth * 0.7);
    let splitCol = mid, minBright = Infinity;
    for (let i = mid; i <= midEnd; i++) {
      if (colBright[i] < minBright) { minBright = colBright[i]; splitCol = i; }
    }
    digitRanges = [
      { start: absFirst, end: splitCol + x1 },
      { start: splitCol + x1 + 1, end: absLast },
    ];
  }

  // 各数字領域をパターンマッチ
  const digits = [];
  for (const r of digitRanges) {
    if (r.end - r.start < 2) continue;
    const d = recognizeDigit(data, width, r.start, r.end, y1, y2);
    if (d !== null) digits.push(d);
  }

  if (digits.length === 0) return null;

  let rank = 0;
  for (const d of digits) rank = rank * 10 + d;

  if (rank < 1 || rank > 91) return null;
  return rank;
}

/**
 * 数字領域を3x5グリッドに正規化してパターンマッチング
 */
function recognizeDigit(data, width, xStart, xEnd, yStart, yEnd) {
  const gw = xEnd - xStart;
  const gh = yEnd - yStart;
  const cellW = gw / 3;
  const cellH = gh / 5;

  // 3x5グリッドの各セルで明るいピクセルの割合を計算
  let pattern = 0;
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 3; col++) {
      const cx1 = Math.floor(xStart + col * cellW);
      const cx2 = Math.floor(xStart + (col + 1) * cellW);
      const cy1 = Math.floor(yStart + row * cellH);
      const cy2 = Math.floor(yStart + (row + 1) * cellH);

      let bright = 0, total = 0;
      for (let y = cy1; y < cy2; y++) {
        for (let x = cx1; x < cx2; x++) {
          const idx = (y * width + x) * 4;
          const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
          total++;
          if (br > 90) bright++;
        }
      }
      if (total > 0 && bright / total > 0.3) {
        pattern |= 1 << (14 - (row * 3 + col));
      }
    }
  }

  // パターンマッチング (3x5グリッド, MSB=左上)
  // ハミング距離が最小の数字を選択
  const TEMPLATES = {
    0: 0b111_101_101_101_111, // 31599
    1: 0b010_110_010_010_111, // 11415 — also try 0b110_010_010_010_111
    2: 0b111_001_111_100_111, // 29607
    3: 0b111_001_111_001_111, // 29415
    4: 0b101_101_111_001_001, // 22345
    5: 0b111_100_111_001_111, // 30887
    6: 0b111_100_111_101_111, // 30943
    7: 0b111_001_001_001_001, // 28873
    8: 0b111_101_111_101_111, // 31855
    9: 0b111_101_111_001_111, // 31799
  };

  // 1の代替パターン (フォントによるバリエーション)
  const ALT_TEMPLATES = {
    1: [0b110_010_010_010_111, 0b010_010_010_010_010, 0b001_001_001_001_001],
    2: [0b111_001_111_110_111, 0b111_101_011_110_111],
    7: [0b111_001_001_010_010, 0b111_001_010_010_100],
  };

  let bestDigit = -1;
  let bestDist = 16; // max possible Hamming distance = 15

  for (const [digit, tmpl] of Object.entries(TEMPLATES)) {
    const d = hammingDistance(pattern, tmpl);
    if (d < bestDist) { bestDist = d; bestDigit = parseInt(digit); }
  }

  // 代替パターンもチェック
  for (const [digit, tmpls] of Object.entries(ALT_TEMPLATES)) {
    for (const tmpl of tmpls) {
      const d = hammingDistance(pattern, tmpl);
      if (d < bestDist) { bestDist = d; bestDigit = parseInt(digit); }
    }
  }

  // 距離が大きすぎたら認識失敗
  if (bestDist > 4) return null;

  return bestDigit;
}

function hammingDistance(a, b) {
  let xor = a ^ b;
  let count = 0;
  while (xor) { count += xor & 1; xor >>= 1; }
  return count;
}

/**
 * ランキング画面を検出し、確定順位を返す
 * ランキング画面: 画面下半分が明るい(プレイヤーリスト)、上部に星と数字
 * @param {string} screenshotPath
 * @returns {number|null} 確定順位 (1-91) or null (ランキング画面でない場合)
 */
export async function detectRankingScreen(screenshotPath) {
  const image = sharp(screenshotPath);
  const metadata = await image.metadata();
  const { data } = await image.raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const { width, height } = metadata;

  // 1. 赤い星の数字を直接探す (最も信頼性が高い — 星があれば即確定)
  const starRank = readRankFromRedStar(data, width, height);
  if (starRank != null && starRank >= 1 && starRank <= 91) {
    return starRank;
  }

  // 2. フォールバック: 中央の明るさでランキング画面を推定
  let centerBright = 0, centerTotal = 0;
  const step = 6;
  for (let y = Math.floor(height * 0.3); y < Math.floor(height * 0.85); y += step) {
    for (let x = Math.floor(width * 0.35); x < Math.floor(width * 0.65); x += step) {
      const idx = (y * width + x) * 4;
      const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
      centerTotal++;
      if (br > 100) centerBright++;
    }
  }
  const centerBrightRatio = centerTotal > 0 ? centerBright / centerTotal : 0;

  if (centerBrightRatio < 0.50) return null;

  return -1; // ランキング画面だが星の数字読み取り失敗
}

/**
 * ランキング画面の赤い星の中の白い数字を読み取る
 * 星は画面上部(5-33%)の中央右寄り(55-72%)に表示される
 */
function readRankFromRedStar(data, width, height) {
  const digitY1 = Math.floor(height * 0.05);
  const digitY2 = Math.floor(height * 0.24);
  const digitX1 = Math.floor(width * 0.55);
  const digitX2 = Math.floor(width * 0.72);

  // 0. 検索エリアに赤い星(集中した赤ピクセル)が存在するか確認
  let redInArea = 0;
  for (let y = digitY1; y < digitY2; y += 3) {
    for (let x = digitX1; x < digitX2; x += 3) {
      const idx = (y * width + x) * 4;
      if (data[idx] > 180 && data[idx + 1] < 100 && data[idx + 2] < 80) redInArea++;
    }
  }
  if (redInArea < 1500) return null; // 星がなければスキップ (ranking≈2200, game-over≈500-1200)

  // 1. 白ピクセル(br>180)の列分布で桁候補セグメントを検出
  //    閾値を緩め(180)にして細いストロークも拾う
  const colWhite = new Array(digitX2 - digitX1).fill(0);
  for (let y = digitY1; y < digitY2; y++) {
    for (let x = digitX1; x < digitX2; x++) {
      const idx = (y * width + x) * 4;
      const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
      if (br > 180) colWhite[x - digitX1]++;
    }
  }

  const minColHits = 2;
  const rawSegs = [];
  let inSeg = false, segStart = 0;
  for (let i = 0; i <= colWhite.length; i++) {
    const active = i < colWhite.length && colWhite[i] >= minColHits;
    if (active && !inSeg) { segStart = i; inSeg = true; }
    else if (!active && inSeg) {
      const w = i - segStart;
      if (w >= 8) rawSegs.push({ start: segStart + digitX1, end: i + digitX1, w });
      inSeg = false;
    }
  }

  if (rawSegs.length === 0) return null;

  // 2. 各セグメントの行範囲を取得し、最大高さのセグメントを基準にする
  for (const seg of rawSegs) {
    let rowMin = digitY2, rowMax = digitY1;
    for (let y = digitY1; y < digitY2; y++) {
      for (let x = seg.start; x < seg.end; x++) {
        const idx = (y * width + x) * 4;
        const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
        if (br > 180) { if (y < rowMin) rowMin = y; if (y > rowMax) rowMax = y; }
      }
    }
    seg.rowMin = rowMin;
    seg.rowMax = rowMax;
    seg.h = rowMax - rowMin;
  }

  // 最も高いセグメント(主桁)を基準に高さフィルタ
  // 高さ20px未満は除外、残りの中から右端の1-2セグメントを桁として採用
  // (RANKINGテキスト断片は左側にあるため、右端が数字)
  const maxH = Math.max(...rawSegs.map(s => s.h));
  if (maxH < 20) return null;
  const candidates = rawSegs.filter(s => s.h >= 15).sort((a, b) => a.start - b.start);
  if (candidates.length === 0) return null;

  // 右端から最大2セグメントを取得 (近接チェック: ギャップ50px以内)
  let digitSegs;
  if (candidates.length === 1) {
    digitSegs = [candidates[0]];
  } else {
    const last = candidates[candidates.length - 1];
    const secondLast = candidates[candidates.length - 2];
    const gap = last.start - secondLast.end;
    if (gap >= 0 && gap <= 50) {
      digitSegs = [secondLast, last];
    } else {
      digitSegs = [last];
    }
  }

  // 3. 基準高さの行範囲を統一して各桁を認識
  const refRowMin = Math.min(...digitSegs.map(s => s.rowMin));
  const refRowMax = Math.max(...digitSegs.map(s => s.rowMax));

  const digits = [];
  for (const seg of digitSegs) {
    const digit = recognizeDigitWhite(data, width, seg.start, seg.end, refRowMin, refRowMax + 1);
    if (digit == null) return null;
    digits.push(digit);
  }

  if (digits.length === 0) return null;
  const rank = digits.length === 1 ? digits[0] : digits[0] * 10 + digits[1];
  return (rank >= 1 && rank <= 91) ? rank : null;
}

/**
 * 白文字の数字を認識 (ランキング画面用、大きいフォント)
 */
function recognizeDigitWhite(data, width, xStart, xEnd, yStart, yEnd) {
  const gw = xEnd - xStart;
  const gh = yEnd - yStart;
  const cellW = gw / 3;
  const cellH = gh / 5;

  let pattern = 0;
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 3; col++) {
      const cx1 = Math.floor(xStart + col * cellW);
      const cx2 = Math.floor(xStart + (col + 1) * cellW);
      const cy1 = Math.floor(yStart + row * cellH);
      const cy2 = Math.floor(yStart + (row + 1) * cellH);

      let bright = 0, total = 0;
      for (let y = cy1; y < cy2; y++) {
        for (let x = cx1; x < cx2; x++) {
          const idx = (y * width + x) * 4;
          const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
          total++;
          if (br > 200) bright++;
        }
      }
      if (total > 0 && bright / total > 0.2) {
        pattern |= 1 << (14 - (row * 3 + col));
      }
    }
  }

  // ランキング画面の数字は大きく描画されるため、テンプレートマッチの許容距離を広めに
  const TEMPLATES = {
    0: 0b111_101_101_101_111,
    1: 0b010_110_010_010_111,
    2: 0b111_001_111_100_111,
    3: 0b111_001_111_001_111,
    4: 0b101_101_111_001_001,
    5: 0b111_100_111_001_111,
    6: 0b111_100_111_101_111,
    7: 0b111_001_001_001_001,
    8: 0b111_101_111_101_111,
    9: 0b111_101_111_001_111,
  };

  const ALT = {
    1: [0b110_010_010_010_111, 0b010_010_010_010_010, 0b001_001_001_001_001,
        0b100_100_100_100_100, 0b010_010_010_010_111, 0b110_010_010_010_010,
        0b110_110_110_110_111],
    2: [0b111_101_001_011_111, 0b111_001_011_100_111, 0b111_001_111_110_111,
        0b111_101_011_110_111, 0b111_001_011_110_111],
    3: [0b111_001_011_001_111, 0b111_001_111_001_110],
    4: [0b101_101_111_001_011, 0b100_101_111_001_001],
    5: [0b111_100_111_001_110, 0b111_110_111_001_111],
    6: [0b111_100_111_101_110, 0b110_100_111_101_111],
    7: [0b111_001_001_010_010, 0b111_001_010_010_100, 0b111_001_001_001_011],
    8: [0b111_101_011_101_111, 0b111_101_111_101_110],
    9: [0b111_101_111_001_110, 0b111_101_111_011_111],
  };

  let bestDigit = -1, bestDist = 16;
  for (const [d, t] of Object.entries(TEMPLATES)) {
    const dist = hammingDistance(pattern, t);
    if (dist < bestDist) { bestDist = dist; bestDigit = parseInt(d); }
  }
  for (const [d, tmpls] of Object.entries(ALT)) {
    for (const t of tmpls) {
      const dist = hammingDistance(pattern, t);
      if (dist < bestDist) { bestDist = dist; bestDigit = parseInt(d); }
    }
  }

  if (bestDist > 5) return null;
  return bestDigit;
}

export { TYPE_RADII };

/**
 * calibration.mjs - ゲームキャンバスの座標キャリブレーション
 *
 * スクリーンショットからゲームボードの境界を検出し、
 * ゲーム座標 ⇔ ピクセル座標の変換を提供する。
 */

import sharp from 'sharp';
import { readFileSync, writeFileSync, existsSync } from 'fs';

const CALIBRATION_PATH = 'tmp/calibration.json';

// ゲーム座標定数
const GAME_X_MIN = -3.0;
const GAME_X_MAX = 3.0;
const BOARD_X_MIN = -3.5;
const BOARD_X_MAX = 3.5;
const GAME_Y_MIN = -5.0;  // floor
const GAME_Y_MAX = 3.32;  // deadline area top

function getBoardGameWidth() {
  return BOARD_X_MAX - BOARD_X_MIN;
}

function gameXToBoardPixel(gameX, board) {
  return Math.round(
    board.left + ((gameX - BOARD_X_MIN) / getBoardGameWidth()) * board.width
  );
}

function withBoardDerivedDropArea(calibration) {
  if (!calibration?.board) return calibration;
  return {
    ...calibration,
    dropArea: {
      pixelLeft: gameXToBoardPixel(GAME_X_MIN, calibration.board),
      pixelRight: gameXToBoardPixel(GAME_X_MAX, calibration.board),
    },
  };
}

function pixelBrightness(data, width, x, y) {
  const idx = (y * width + x) * 4;
  return (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
}

function computeColumnStats(data, width, height, x, scanTop, scanBottom, rowStep = 4) {
  let bright = 0;
  let dark = 0;
  let total = 0;
  let sum = 0;

  const clampedX = Math.max(0, Math.min(width - 1, x));
  const y1 = Math.max(0, scanTop);
  const y2 = Math.min(height, scanBottom);
  for (let y = y1; y < y2; y += rowStep) {
    const brightness = pixelBrightness(data, width, clampedX, y);
    sum += brightness;
    total++;
    if (brightness > 170) bright++;
    if (brightness < 110) dark++;
  }

  if (total === 0) {
    return { avgBrightness: 0, brightRatio: 0, darkRatio: 0 };
  }
  return {
    avgBrightness: sum / total,
    brightRatio: bright / total,
    darkRatio: dark / total,
  };
}

function computeRowStats(data, width, y, scanLeft, scanRight, colStep = 6) {
  let dark = 0;
  let total = 0;
  let sum = 0;

  const x1 = Math.max(0, scanLeft);
  const x2 = Math.min(width, scanRight);
  for (let x = x1; x < x2; x += colStep) {
    const brightness = pixelBrightness(data, width, x, y);
    sum += brightness;
    total++;
    if (brightness < 110) dark++;
  }

  if (total === 0) {
    return { avgBrightness: 0, darkRatio: 0 };
  }
  return {
    avgBrightness: sum / total,
    darkRatio: dark / total,
  };
}

function findPeakBrightColumn(data, width, height, startX, endX) {
  const scanTop = Math.floor(height * 0.20);
  const scanBottom = Math.floor(height * 0.88);
  let best = null;

  for (let x = startX; x <= endX; x++) {
    const stats = computeColumnStats(data, width, height, x, scanTop, scanBottom);
    const score = stats.brightRatio * 4 + Math.max(0, (stats.avgBrightness - 160) / 80);
    if (!best || score > best.score) {
      best = { x, score, ...stats };
    }
  }

  return best;
}

function findInnerWallEdge(data, width, height, wallX, direction) {
  const scanTop = Math.floor(height * 0.25);
  const scanBottom = Math.floor(height * 0.88);

  for (let offset = 4; offset <= 64; offset++) {
    const x = wallX + direction * offset;
    if (x <= 0 || x >= width - 1) break;
    const stats = computeColumnStats(data, width, height, x, scanTop, scanBottom);
    if (stats.darkRatio > 0.25 && stats.avgBrightness < 135) {
      return x;
    }
  }

  return Math.max(0, Math.min(width - 1, wallX + direction * 20));
}

function findBoardVerticalBounds(data, width, height, leftWallInner, rightWallInner) {
  const usableWidth = rightWallInner - leftWallInner;
  if (usableWidth < 120) return null;

  const horizontalMargin = Math.max(12, Math.floor(usableWidth * 0.08));
  const scanLeft = leftWallInner + horizontalMargin;
  const scanRight = rightWallInner - horizontalMargin;
  const startY = Math.floor(height * 0.15);
  const endY = Math.floor(height * 0.97);
  const rows = [];

  for (let y = startY; y <= endY; y += 2) {
    const stats = computeRowStats(data, width, y, scanLeft, scanRight);
    rows.push({
      y,
      ...stats,
      active: stats.darkRatio > 0.22 && stats.avgBrightness < 140,
    });
  }

  const isStableActive = (index) => {
    let activeCount = 0;
    let total = 0;
    for (let i = Math.max(0, index - 2); i <= Math.min(rows.length - 1, index + 2); i++) {
      total++;
      if (rows[i].active) activeCount++;
    }
    return total > 0 && activeCount >= Math.min(3, total);
  };

  let top = -1;
  let bottom = -1;
  for (let i = 0; i < rows.length; i++) {
    if (isStableActive(i)) {
      top = rows[i].y;
      break;
    }
  }
  for (let i = rows.length - 1; i >= 0; i--) {
    if (isStableActive(i)) {
      bottom = rows[i].y;
      break;
    }
  }

  if (top === -1 || bottom === -1 || bottom - top < 140) return null;
  return { top, bottom };
}

/**
 * スクリーンショットからゲームボード領域を検出する。
 * ゲームボードの壁（明るい縦線）を検出して正確な領域を特定。
 *
 * ゲーム画面構造:
 *   左側: 他プレイヤーのミニボード (黄色/オレンジ)
 *   中央: 自分のプレイエリア (暗い背景 + 壁)
 *   右側: 他プレイヤーのミニボード
 *
 * @param {string} screenshotPath - スクリーンショットのパス
 * @returns {object} キャリブレーションデータ
 */
export async function calibrate(screenshotPath) {
  const image = sharp(screenshotPath);
  const metadata = await image.metadata();
  const { width, height } = metadata;
  const { data } = await image.raw().ensureAlpha().toBuffer({ resolveWithObject: true });

  let leftWallOuter = -1, leftWallInner = -1;
  let rightWallInner = -1, rightWallOuter = -1;
  let boardTop = -1, boardBottom = -1;
  let confidence = 0.35;
  let method = 'fallback';

  const leftPeak = findPeakBrightColumn(
    data,
    width,
    height,
    Math.floor(width * 0.25),
    Math.floor(width * 0.45),
  );
  const rightPeak = findPeakBrightColumn(
    data,
    width,
    height,
    Math.floor(width * 0.55),
    Math.floor(width * 0.75),
  );

  if (
    leftPeak?.brightRatio > 0.30 &&
    rightPeak?.brightRatio > 0.30 &&
    rightPeak.x - leftPeak.x > Math.floor(width * 0.18)
  ) {
    leftWallOuter = leftPeak.x;
    rightWallOuter = rightPeak.x;
    leftWallInner = findInnerWallEdge(data, width, height, leftWallOuter, +1);
    rightWallInner = findInnerWallEdge(data, width, height, rightWallOuter, -1);
    const verticalBounds = findBoardVerticalBounds(data, width, height, leftWallInner, rightWallInner);
    if (verticalBounds && leftWallInner < rightWallInner) {
      boardTop = verticalBounds.top;
      boardBottom = verticalBounds.bottom;
      confidence = 0.82;
      method = 'profile';
    }
  }

  if (
    leftWallInner === -1 || rightWallInner === -1 ||
    boardTop === -1 || boardBottom === -1 ||
    rightWallInner - leftWallInner < Math.floor(width * 0.15)
  ) {
    console.log('[calibration] Profile detection failed, using fallback');
    leftWallInner = Math.floor(width * 0.35);
    rightWallInner = Math.floor(width * 0.65);
    leftWallOuter = Math.max(0, leftWallInner - 20);
    rightWallOuter = Math.min(width - 1, rightWallInner + 20);
    boardTop = Math.floor(height * 0.42);
    boardBottom = Math.floor(height * 0.96);
    confidence = 0.35;
    method = 'fallback';
  }

  // 壁の内側がプレイエリア
  const boardLeft = leftWallInner;
  const boardRight = rightWallInner;
  const boardWidth = boardRight - boardLeft;
  const boardHeight = boardBottom - boardTop;

  // 盤面解析の座標系と同じく、壁の内側をゲーム座標 7.0 単位 (-3.5 ~ +3.5) に対応させる。
  // ドロップ可能範囲はその内側の -3.0 ~ +3.0。
  const boardGameWidth = getBoardGameWidth();
  const pixelsPerUnit = boardWidth / boardGameWidth;

  const calibration = {
    screen: { width, height },
    board: {
      left: boardLeft,
      right: boardRight,
      top: boardTop,
      bottom: boardBottom,
      width: boardWidth,
      height: boardHeight,
    },
    walls: {
      leftOuter: leftWallOuter,
      leftInner: leftWallInner,
      rightInner: rightWallInner,
      rightOuter: rightWallOuter,
    },
    dropArea: {
      // -3.0 ~ +3.0 のピクセル範囲
      pixelLeft: gameXToBoardPixel(GAME_X_MIN, { left: boardLeft, width: boardWidth }),
      pixelRight: gameXToBoardPixel(GAME_X_MAX, { left: boardLeft, width: boardWidth }),
    },
    pixelsPerUnit,
    confidence,
    method,
    isFallback: method === 'fallback',
    timestamp: new Date().toISOString(),
  };

  // 保存
  writeFileSync(CALIBRATION_PATH, JSON.stringify(calibration, null, 2));
  console.log('[calibration] Saved:', CALIBRATION_PATH);
  console.log('[calibration] Board area:', `${boardWidth}x${boardHeight} at (${boardLeft},${boardTop})`);
  console.log('[calibration] Method:', `${method} (confidence=${confidence.toFixed(2)})`);

  return calibration;
}

/**
 * キャッシュされたキャリブレーションを読み込む
 */
export function loadCalibration() {
  if (existsSync(CALIBRATION_PATH)) {
    const calibration = JSON.parse(readFileSync(CALIBRATION_PATH, 'utf-8'));
    if (calibration?.isFallback) {
      console.log('[calibration] Ignoring cached fallback calibration; recalibration required');
      return null;
    }
    return withBoardDerivedDropArea(calibration);
  }
  return null;
}

/**
 * ゲーム座標 → ピクセル座標
 * @param {number} gameX - ゲームX座標 [-3.0, +3.0]
 * @param {number} gameY - ゲームY座標 [-5.0, +3.32]
 * @param {object} cal - キャリブレーションデータ
 * @returns {{ px: number, py: number }}
 */
export function gameToPixel(gameX, gameY, cal) {
  const { board } = cal;

  // ゲーム座標系: X [-3.5, +3.5] → ピクセル [board.left, board.right]
  const normalizedX = (gameX - BOARD_X_MIN) / (BOARD_X_MAX - BOARD_X_MIN); // 0..1
  const px = board.left + normalizedX * board.width;

  // ゲーム座標系: Y [-5.0, +3.32] → ピクセル [board.bottom, board.top] (Y反転)
  const totalGameHeight = GAME_Y_MAX - GAME_Y_MIN;
  const normalizedY = (gameY - GAME_Y_MIN) / totalGameHeight; // 0..1
  const py = board.bottom - normalizedY * board.height;

  return { px: Math.round(px), py: Math.round(py) };
}

/**
 * ピクセル座標 → ゲーム座標
 * @param {number} px - ピクセルX
 * @param {number} py - ピクセルY
 * @param {object} cal - キャリブレーションデータ
 * @returns {{ gameX: number, gameY: number }}
 */
export function pixelToGame(px, py, cal) {
  const { board } = cal;

  const normalizedX = (px - board.left) / board.width;
  const gameX = BOARD_X_MIN + normalizedX * (BOARD_X_MAX - BOARD_X_MIN);

  const normalizedY = (board.bottom - py) / board.height;
  const gameY = GAME_Y_MIN + normalizedY * (GAME_Y_MAX - GAME_Y_MIN);

  return { gameX, gameY };
}

/**
 * ゲームXドロップ座標 → ピクセルX (ドロップ操作用、簡易版)
 * @param {number} gameX - ドロップX座標 [-3.0, +3.0]
 * @param {object} cal - キャリブレーションデータ
 * @returns {number} ピクセルX座標
 */
export function dropXToPixel(gameX, cal) {
  const { dropArea } = cal;
  const dropWidth = dropArea.pixelRight - dropArea.pixelLeft;
  // -3.0 → pixelLeft, +3.0 → pixelRight
  const normalized = (gameX - GAME_X_MIN) / (GAME_X_MAX - GAME_X_MIN);
  return Math.round(dropArea.pixelLeft + normalized * dropWidth);
}

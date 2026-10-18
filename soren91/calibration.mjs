/**
 * calibration.mjs - ゲームキャンバスの座標キャリブレーション
 *
 * スクリーンショットからゲームボードの境界を検出し、
 * ゲーム座標 ⇔ ピクセル座標の変換を提供する。
 */

import sharp from 'sharp';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { CALIBRATION_COORDINATE_SCHEMA, isUsableCalibration } from './calibration_contract.mjs';

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

  for (let offset = 1; offset <= Math.ceil(width * 0.05); offset++) {
    const x = wallX + direction * offset;
    if (x <= 0 || x >= width - 1) break;
    const stats = computeColumnStats(data, width, height, x, scanTop, scanBottom);
    // Leaving a bright wall must work with either empty background or grey
    // garbage inside it. A minimum dark-pixel ratio shifts a nearly full board.
    if (stats.brightRatio < 0.25 && stats.avgBrightness < 170) {
      return direction > 0 ? x : x + 1;
    }
  }

  return null;
}

function findBoardVerticalBounds(data, width, height, leftWallInner, rightWallInner) {
  const usableWidth = rightWallInner - leftWallInner;
  if (usableWidth < Math.max(40, width * 0.15)) return null;

  const horizontalMargin = Math.max(2, Math.floor(usableWidth * 0.02));
  const scanLeft = Math.ceil(leftWallInner + horizontalMargin);
  const scanRight = Math.floor(rightWallInner - horizontalMargin);
  const colStep = Math.max(1, Math.round(width / 1280 * 2));
  const rows = [];
  for (let y = 0; y < height; y++) {
    let samples = 0, red = 0, divider = 0, bright = 0, white = 0;
    for (let x = scanLeft; x < scanRight; x += colStep) {
      const i = (y * width + x) * 4;
      const r = data[i], g = data[i + 1], b = data[i + 2];
      const brightness = (r + g + b) / 3;
      const max = Math.max(r, g, b);
      const saturation = max ? (max - Math.min(r, g, b)) / max : 0;
      samples++;
      // The darker red defeat overlay must not merge into the bright line.
      if (r > 210 && g < 80 && b < 80) red++;
      if (saturation < 0.18) {
        if (brightness > 160) divider++;
        if (brightness > 185) bright++;
        if (brightness > 210) white++;
      }
    }
    rows.push({ y, red: red / samples, divider: divider / samples,
      bright: bright / samples, white: white / samples });
  }

  const bands = (key, threshold) => {
    const result = [];
    for (const row of rows) {
      if (row[key] < threshold) continue;
      const previous = result.at(-1);
      if (previous && previous.bottom === row.y) {
        previous.bottom = row.y + 1;
      } else {
        result.push({ top: row.y, bottom: row.y + 1 });
      }
    }
    return result;
  };

  // Red loss overlays cover a region; the deadline is a thin horizontal line.
  const maxLineThickness = Math.max(3, Math.ceil(height / 720 * 8));
  const deadlines = bands('red', 0.7)
    .filter(band => band.bottom - band.top <= maxLineThickness);
  const floors = bands('bright', 0.75).filter(band =>
    band.bottom - band.top <= maxLineThickness
    && rows.slice(band.top, band.bottom).some(row => row.white >= 0.6));
  const dividers = bands('divider', 0.8)
    .filter(band => band.bottom - band.top <= maxLineThickness);
  const expectedHeight = usableWidth * (GAME_Y_MAX - GAME_Y_MIN) / getBoardGameWidth();
  const tolerance = Math.max(3, usableWidth * 0.03);
  const candidates = [];

  for (const deadline of deadlines) {
    const top = (deadline.top + deadline.bottom - 1) / 2;
    for (const floor of floors) {
      // The first wall pixel is the exclusive lower edge of the play area.
      const bottom = floor.top;
      const error = Math.abs((bottom - top) - expectedHeight);
      if (error > tolerance) continue;
      const headers = dividers.filter(band =>
        band.bottom < deadline.top - usableWidth * 0.08
        && band.bottom > deadline.top - usableWidth * 0.5);
      if (headers.length !== 1) continue;
      candidates.push({ top, bottom, arenaTop: headers[0].bottom });
    }
  }
  // Ambiguous or obscured anchors cannot become a new trusted calibration.
  return candidates.length === 1 ? candidates[0] : null;
}

/**
 * RGBA画像から、壁・deadline・床・HUD境界を検出する（I/Oなし）。
 *
 * ゲーム画面構造:
 *   左側: 他プレイヤーのミニボード (黄色/オレンジ)
 *   中央: 自分のプレイエリア (暗い背景 + 壁)
 *   右側: 他プレイヤーのミニボード
 *
 * @param {Uint8Array} data - RGBA画素
 * @param {number} width - 画像の幅
 * @param {number} height - 画像の高さ
 * @returns {object} キャリブレーションデータ
 */
export function detectCalibration(data, width, height) {
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0
      || !data || data.length !== width * height * 4) throw new TypeError('Expected a finite RGBA image');
  let leftWallOuter = -1, leftWallInner = -1;
  let rightWallInner = -1, rightWallOuter = -1;
  let boardTop = -1, boardBottom = -1;
  let arenaTop = -1;
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
    const verticalBounds = leftWallInner !== null && rightWallInner !== null
      ? findBoardVerticalBounds(data, width, height, leftWallInner, rightWallInner)
      : null;
    if (verticalBounds && leftWallInner < rightWallInner) {
      boardTop = verticalBounds.top;
      boardBottom = verticalBounds.bottom;
      arenaTop = verticalBounds.arenaTop;
      confidence = 0.82;
      method = 'profile';
    }
  }

  if (
    leftWallInner === -1 || rightWallInner === -1 || leftWallInner === null || rightWallInner === null ||
    boardTop === -1 || boardBottom === -1 ||
    rightWallInner - leftWallInner < Math.floor(width * 0.15)
  ) {
    leftWallInner = Math.floor(width * 0.35);
    rightWallInner = Math.floor(width * 0.65);
    leftWallOuter = Math.max(0, leftWallInner - 20);
    rightWallOuter = Math.min(width - 1, rightWallInner + 20);
    boardTop = Math.floor(height * 0.42);
    boardBottom = Math.floor(height * 0.96);
    arenaTop = Math.floor(height * 0.18);
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
    coordinateSchema: CALIBRATION_COORDINATE_SCHEMA,
    screen: { width, height },
    board: {
      left: boardLeft,
      right: boardRight,
      top: boardTop,
      bottom: boardBottom,
      width: boardWidth,
      height: boardHeight,
    },
    arena: {
      left: boardLeft,
      right: boardRight,
      top: arenaTop,
      bottom: boardBottom,
      width: boardWidth,
      height: boardBottom - arenaTop,
    },
    hud: { top: 0, bottom: arenaTop },
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

  return calibration;
}

/** Decode a screenshot and persist only the calibration computed from it. */
export async function calibrate(screenshotPath) {
  const { data, info } = await sharp(screenshotPath).toColourspace('srgb').raw().ensureAlpha()
    .toBuffer({ resolveWithObject: true });
  const calibration = detectCalibration(data, info.width, info.height);
  if (calibration.isFallback) console.log('[calibration] Anchor detection failed, using fallback');
  writeFileSync(CALIBRATION_PATH, JSON.stringify(calibration, null, 2));
  console.log('[calibration] Saved:', CALIBRATION_PATH);
  const { board, method, confidence } = calibration;
  console.log('[calibration] Board area:', `${board.width}x${board.height} at (${board.left},${board.top})`);
  console.log('[calibration] Method:', `${method} (confidence=${confidence.toFixed(2)})`);
  return calibration;
}

/**
 * キャッシュされたキャリブレーションを読み込む
 */
export function loadCalibration(calibrationPath = CALIBRATION_PATH) {
  if (!existsSync(calibrationPath)) return null;
  let calibration;
  try {
    calibration = JSON.parse(readFileSync(calibrationPath, 'utf-8'));
  } catch {
    return null;
  }
  if (!isUsableCalibration(calibration)) {
    console.log('[calibration] Ignoring unverified cached geometry; recalibration required');
    return null;
  }
  return withBoardDerivedDropArea(calibration);
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

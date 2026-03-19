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
const GAME_Y_MIN = -5.0;  // floor
const GAME_Y_MAX = 3.32;  // deadline area top

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

  // 壁検出: 複数のスキャンラインで明るい縦線（壁）を見つける
  // ゲームボードの壁は白/明灰色の縦線 (brightness ~150-250)
  // 壁の内側は暗いゲームボード (brightness ~50)
  let leftWallOuter = -1, leftWallInner = -1;
  let rightWallInner = -1, rightWallOuter = -1;

  // 複数のスキャンラインで壁を検出 (最頻値を使用)
  const scanYs = [0.4, 0.5, 0.55, 0.6, 0.7].map(r => Math.floor(height * r));

  for (const scanY of scanYs) {
    const brightnesses = [];
    for (let x = 0; x < width; x++) {
      const idx = (scanY * width + x) * 4;
      brightnesses.push((data[idx] + data[idx + 1] + data[idx + 2]) / 3);
    }

    // 左壁: 「暗→明(壁)→暗(ボード内、幅100px以上)」パターン
    for (let x = Math.floor(width * 0.25); x < Math.floor(width * 0.5); x++) {
      if (brightnesses[x] > 150 && x >= 3 && brightnesses[x - 3] < 60) {
        // 壁の終わりを探す
        for (let x2 = x + 1; x2 < x + 25; x2++) {
          if (brightnesses[x2] < 60) {
            // 壁の後に十分な暗い領域があるか確認 (ゲームボード)
            let darkStretch = 0;
            for (let x3 = x2; x3 < x2 + 200 && x3 < width; x3++) {
              if (brightnesses[x3] < 60) darkStretch++;
            }
            if (darkStretch > 150) { // 200px中150px以上暗い = ゲームボード
              leftWallOuter = x;
              leftWallInner = x2;
            }
            break;
          }
        }
        if (leftWallInner !== -1) break;
      }
    }

    // 右壁: 右から探して「明(壁)の左に暗い領域(ボード)」パターン
    // 壁の外側(右)はミニボードで明るいので、内側(左)が暗いことで判定
    for (let x = Math.floor(width * 0.75); x > Math.floor(width * 0.5); x--) {
      if (brightnesses[x] > 150 && x >= 3 && brightnesses[x - 3] < 60) {
        // 壁の右端(外側)を探す
        let wallEnd = x;
        for (let x2 = x + 1; x2 < x + 25; x2++) {
          if (brightnesses[x2] < 100) { wallEnd = x2 - 1; break; }
          wallEnd = x2;
        }
        // 壁の左側に十分な暗い領域があるか確認
        let darkStretch = 0;
        for (let x3 = x - 3; x3 > x - 203 && x3 >= 0; x3--) {
          if (brightnesses[x3] < 60) darkStretch++;
        }
        if (darkStretch > 150) {
          rightWallOuter = wallEnd;
          rightWallInner = x - 1;
          // 正確な内側: 壁の左端を探す
          for (let x2 = x - 1; x2 > x - 25; x2--) {
            if (brightnesses[x2] < 60) { rightWallInner = x2; break; }
          }
        }
        if (rightWallInner !== -1) break;
      }
    }

    if (leftWallInner !== -1 && rightWallInner !== -1) break;
  }

  // フォールバック: 壁が見つからない場合は画面中央を推定
  if (leftWallInner === -1 || rightWallInner === -1) {
    console.log('[calibration] Wall detection failed, using fallback');
    leftWallInner = Math.floor(width * 0.35);
    rightWallInner = Math.floor(width * 0.65);
    leftWallOuter = leftWallInner - 15;
    rightWallOuter = rightWallInner + 15;
  }

  // 上下の境界検出: 中央列で暗い領域の範囲
  const midX = Math.floor((leftWallInner + rightWallInner) / 2);
  let boardTop = 0, boardBottom = height - 1;

  for (let y = 0; y < height; y++) {
    const idx = (y * width + midX) * 4;
    const b = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
    if (b < 60) { boardTop = y; break; }
  }
  for (let y = height - 1; y >= 0; y--) {
    const idx = (y * width + midX) * 4;
    const b = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
    if (b < 60) { boardBottom = y; break; }
  }

  // 壁の内側がプレイエリア
  const boardLeft = leftWallInner;
  const boardRight = rightWallInner;
  const boardWidth = boardRight - boardLeft;
  const boardHeight = boardBottom - boardTop;

  // 壁間距離がゲーム座標 7.0 単位 (-3.5 ~ +3.5) に対応
  // ドロップ可能範囲は -3.0 ~ +3.0
  const wallWidth = rightWallOuter - leftWallOuter; // 壁外側間 = 7.0単位
  const pixelsPerUnit = wallWidth / 7.0;

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
      pixelLeft: leftWallOuter + Math.floor(pixelsPerUnit * 0.5),
      pixelRight: leftWallOuter + Math.floor(pixelsPerUnit * 6.5),
    },
    pixelsPerUnit,
    timestamp: new Date().toISOString(),
  };

  // 保存
  writeFileSync(CALIBRATION_PATH, JSON.stringify(calibration, null, 2));
  console.log('[calibration] Saved:', CALIBRATION_PATH);
  console.log('[calibration] Board area:', `${boardWidth}x${boardHeight} at (${boardLeft},${boardTop})`);

  return calibration;
}

/**
 * キャッシュされたキャリブレーションを読み込む
 */
export function loadCalibration() {
  if (existsSync(CALIBRATION_PATH)) {
    return JSON.parse(readFileSync(CALIBRATION_PATH, 'utf-8'));
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
  const normalizedX = (gameX - (-3.5)) / 7.0; // 0..1
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
  const gameX = -3.5 + normalizedX * 7.0;

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

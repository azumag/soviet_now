/**
 * screenshot_analyzer.mjs - スクリーンショット → 盤面状態抽出
 *
 * Sharp でスクリーンショットを処理し、ゲーム状態を推定する。
 * - ゲーム状態検出: MOVE / WAITING / DROP
 * - ピース検出: 色セグメンテーション + サイズ分類
 * - 次のピース検出
 * - スコア検出
 */

import sharp from 'sharp';
import { gateObservation, usableCalibration } from './observation_guard.mjs';
import { detectHudPieces, detectCurrentPiece, extractSpriteComponents,
  classifySpriteComponent } from './sprite_perception.mjs';

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

function blobAspectRatio(blob) {
  const shortEdge = Math.max(1, Math.min(blob.bboxWidth, blob.bboxHeight));
  const longEdge = Math.max(blob.bboxWidth, blob.bboxHeight);
  return longEdge / shortEdge;
}

function blobSampleArea(blob) {
  return Math.max(1, blob.pixelCount) * Math.max(1, blob.sampleStep) * Math.max(1, blob.sampleStep);
}

function blobFillRatio(blob) {
  const bboxArea = Math.max(
    1,
    Math.max(1, blob.bboxWidth + Math.max(1, blob.sampleStep))
      * Math.max(1, blob.bboxHeight + Math.max(1, blob.sampleStep)),
  );
  return Math.min(1.6, blobSampleArea(blob) / bboxArea);
}

function blobEffectiveRadius(blob) {
  const sampledArea = blobSampleArea(blob);
  const areaRadius = Math.sqrt(sampledArea / Math.PI);
  // Include the sampled pixel footprint; max-min alone systematically shrinks
  // small pieces by almost one grid step.
  const step = Math.max(1, blob.sampleStep);
  const bboxRadius = Math.sqrt(Math.max(step, blob.bboxWidth + step) * Math.max(step, blob.bboxHeight + step)) / 2;
  const fillRatio = blobFillRatio(blob);
  const aspect = blobAspectRatio(blob);

  // 実スクショでは国旗模様の断片で面積が小さく出やすい。
  // fill が低い/横長なブロブほど bbox を重めに見てサイズ縮みを補正する。
  let bboxWeight = 0.56;
  if (fillRatio < 0.62) bboxWeight += 0.08;
  if (fillRatio < 0.48) bboxWeight += 0.07;
  if (aspect > 1.8) bboxWeight += 0.05;
  bboxWeight = Math.min(0.78, bboxWeight);

  return bboxRadius * bboxWeight + areaRadius * (1 - bboxWeight);
}

function blobPixelRadius(blob) {
  return blobEffectiveRadius(blob);
}

function blobBounds(blob) {
  const halfWidth = Math.max(1, blob.bboxWidth) / 2;
  const halfHeight = Math.max(1, blob.bboxHeight) / 2;
  return {
    left: Number.isFinite(blob.minX) ? blob.minX : blob.centerX - halfWidth,
    right: Number.isFinite(blob.maxX) ? blob.maxX : blob.centerX + halfWidth,
    top: Number.isFinite(blob.minY) ? blob.minY : blob.centerY - halfHeight,
    bottom: Number.isFinite(blob.maxY) ? blob.maxY : blob.centerY + halfHeight,
  };
}

function blobBoundsGapPx(a, b) {
  const boundsA = blobBounds(a);
  const boundsB = blobBounds(b);
  const dx = Math.max(0, Math.max(boundsA.left, boundsB.left) - Math.min(boundsA.right, boundsB.right));
  const dy = Math.max(0, Math.max(boundsA.top, boundsB.top) - Math.min(boundsA.bottom, boundsB.bottom));
  return Math.hypot(dx, dy);
}

function mergeBlobPair(a, b) {
  const areaA = blobSampleArea(a);
  const areaB = blobSampleArea(b);
  const totalArea = Math.max(1, areaA + areaB);
  const boundsA = blobBounds(a);
  const boundsB = blobBounds(b);
  const minX = Math.min(boundsA.left, boundsB.left);
  const maxX = Math.max(boundsA.right, boundsB.right);
  const minY = Math.min(boundsA.top, boundsB.top);
  const maxY = Math.max(boundsA.bottom, boundsB.bottom);

  return {
    centerX: (a.centerX * areaA + b.centerX * areaB) / totalArea,
    centerY: (a.centerY * areaA + b.centerY * areaB) / totalArea,
    pixelCount: Math.max(1, a.pixelCount + b.pixelCount),
    bboxWidth: maxX - minX,
    bboxHeight: maxY - minY,
    sampleStep: Math.max(1, Math.min(a.sampleStep ?? 1, b.sampleStep ?? 1)),
    avgColor: {
      r: Math.round(((a.avgColor?.r ?? 0) * areaA + (b.avgColor?.r ?? 0) * areaB) / totalArea),
      g: Math.round(((a.avgColor?.g ?? 0) * areaA + (b.avgColor?.g ?? 0) * areaB) / totalArea),
      b: Math.round(((a.avgColor?.b ?? 0) * areaA + (b.avgColor?.b ?? 0) * areaB) / totalArea),
    },
    minX,
    maxX,
    minY,
    maxY,
    sampledPixels: [...(a.sampledPixels || []), ...(b.sampledPixels || [])],
  };
}

function mergeNearbyBlobs(blobs, maxMergedRadiusPx = 96) {
  const sorted = blobs
    .filter(blob => blob && blob.pixelCount > 0 && blobAspectRatio(blob) <= 3.1)
    .sort((a, b) => blobPixelRadius(b) - blobPixelRadius(a));
  const clusters = [];

  for (const blob of sorted) {
    const blobRadiusPx = blobPixelRadius(blob);
    let bestIndex = -1;
    let bestScore = Infinity;

    for (let i = 0; i < clusters.length; i++) {
      const cluster = clusters[i];
      const clusterRadiusPx = blobPixelRadius(cluster);
      const centerDistancePx = Math.hypot(blob.centerX - cluster.centerX, blob.centerY - cluster.centerY);
      const gapPx = blobBoundsGapPx(blob, cluster);
      const distanceThresholdPx = Math.max(blobRadiusPx, clusterRadiusPx) * 1.05 + 8;
      if (centerDistancePx > distanceThresholdPx) continue;
      if (gapPx > Math.max(blob.sampleStep, cluster.sampleStep) * 1.5) continue;
      const bothRound = blobAspectRatio(blob) < 1.3 && blobAspectRatio(cluster) < 1.3
        && blobFillRatio(blob) > 0.64 && blobFillRatio(cluster) > 0.64;
      // Two already-complete adjacent discs are not flag fragments of one disc.
      if (bothRound && centerDistancePx > (blobRadiusPx + clusterRadiusPx) * 0.55) continue;

      const merged = mergeBlobPair(cluster, blob);
      if (blobAspectRatio(merged) > 1.8) continue;
      if (blobPixelRadius(merged) > maxMergedRadiusPx) continue;

      const score = centerDistancePx + gapPx * 1.5;
      if (score < bestScore) {
        bestScore = score;
        bestIndex = i;
      }
    }

    if (bestIndex === -1) {
      clusters.push(blob);
    } else {
      clusters[bestIndex] = mergeBlobPair(clusters[bestIndex], blob);
    }
  }

  return clusters;
}

function classifyRadius(gameRadius, maxType = 15) {
  let bestType = 1;
  let bestDiff = Infinity;

  for (const [type, radius] of Object.entries(TYPE_RADII)) {
    const numericType = parseInt(type, 10);
    if (numericType > maxType) continue;
    const diff = Math.abs(gameRadius - radius);
    if (diff < bestDiff) {
      bestDiff = diff;
      bestType = numericType;
    }
  }

  return { type: bestType, diff: bestDiff };
}

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
  const { data, info } = await image.toColourspace('srgb').raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const { width, height } = metadata;
  if (info.channels !== 4) throw new Error('Expected RGBA screenshot');

  // Calibrate BEFORE the first drop, including an empty first board. Merely
  // suppressing provisional drops would deadlock the old pieces>=N bootstrap.
  const sceneState = detectGameState(data, width, height, calibration?.arena || calibration?.board);
  if (sceneState === 'MOVE' && !usableCalibration(calibration, width, height)) {
    const { calibrate } = await import('./calibration.mjs');
    const candidate = await calibrate(screenshotPath);
    if (usableCalibration(candidate, width, height)) {
      Object.assign(calibration, candidate, { provisional: false });
    }
  }
  const calibrated = usableCalibration(calibration, width, height);
  const board = calibration?.board;
  // Keep real inter-round transitions intact. Bad geometry must not end a game.
  if (!calibrated) {
    return gateObservation({ state: sceneState === 'MOVE' ? 'DROP' : sceneState,
      pieces: [], next: null, nextPieces: [null, null, null], hold: null,
      garbage: { ratio: 0, height: -5, gauge: 0, columns: [] }, rank: null,
      confidence: 0, perception: { reason: 'uncalibrated' }, }, calibration);
  }
  const state = sceneState;

  // The controllable piece is in the arena above the deadline, independently
  // of the three horizontal future icons in the HUD. Only its identified
  // pixels are removed from physical occupancy; high stack pieces remain.
  const currentObservation = state === 'MOVE'
    ? detectCurrentPiece(data, width, height, calibration) : null;
  const current = canonicalPreview(currentObservation?.piece);
  if (current && Number.isFinite(currentObservation?.component?.centerY)) {
    current.y = -5 + (board.bottom - currentObservation.component.centerY) / board.height * 8.32;
  }
  const excludedPixels = current && currentObservation?.component?.pixels
    ? new Set(currentObservation.component.pixels) : null;

  // 2. 物理盤面のピース検出
  const pieces = state === 'MOVE' || state === 'DROP'
    ? detectPieces(data, width, height, calibration, excludedPixels)
    : [];

  const hudObservation = state === 'MOVE'
    ? detectHudPieces(data, width, height, calibration) : null;
  const future = (hudObservation?.nextPieces || []).map(canonicalPreview);
  const nextPieces = state === 'MOVE' ? [current, future[0] || null, future[1] || null] : [];
  const next = current;

  // 4. おじゃまブロック量を測定 (灰色領域の割合)
  const garbage = state === 'MOVE'
    ? measureGarbage(data, width, height, calibration, pieces)
    : { ratio: 0, height: 0 };

  // 4b. おじゃまゲージ検出 (左壁の予告ゲージ)
  if (state === 'MOVE') {
    const gauge = detectOjamaGauge(data, width, height, calibration);
    garbage.gauge = gauge.level;
  } else {
    garbage.gauge = 0;
  }

  // 5. HOLD ピース検出
  const hold = canonicalPreview(hudObservation?.hold);

  // 6. 順位検出 — リアルタイムOCRは精度不足のため無効化
  // ランキング画面からの検出 (detectRankingScreen) に委ねる
  const rank = null;

  const previewConfidenceValues = [
    ...(nextPieces || []).map(piece => piece?.confidence).filter(Number.isFinite),
    hold?.confidence,
  ].filter(Number.isFinite);
  const previewConfidence = previewConfidenceValues.length > 0
    ? previewConfidenceValues.reduce((sum, value) => sum + value, 0) / previewConfidenceValues.length
    : 0.4;
  const baseConfidence = pieces.length > 0 ? 0.58 : 0.35;
  const calibrationConfidence = Number.isFinite(calibration?.confidence) ? calibration.confidence : 0.4;
  const confidence = Math.max(0.2, Math.min(0.95, (baseConfidence + calibrationConfidence + previewConfidence) / 3));

  return gateObservation({
    state,
    rank,  // 現在の順位 (1-91) or null
    pieces,
    next,             // Visible controllable current, never a HUD future icon.
    nextPieces,       // [current, NEXT-left, NEXT-middle], preserving holes.
    hold,             // { type, r } or null
    holdObservedEmpty: hudObservation?.holdObservedEmpty === true,
    garbage,          // { ratio: 0-1, height: ゲーム座標でのおじゃまの高さ, gauge: 0-1 おじゃまゲージレベル }
    confidence: Math.round(confidence * 100) / 100,
  }, calibration);
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
 */
function detectGameState(data, width, height, board) {
  // calibration 前でも MATCHING / Starting 画面は全画面ベースで弾く。
  {
    const overlayCenterX = width * 0.5;
    const overlayCenterY = height * 0.52;
    const overlayOuterRx = width * 0.11;
    const overlayOuterRy = height * 0.13;
    const overlayInnerRx = width * 0.05;
    const overlayInnerRy = height * 0.06;
    const buttonLeft = Math.floor(width * 0.38);
    const buttonRight = Math.ceil(width * 0.62);
    const buttonTop = Math.floor(height * 0.66);
    const buttonBottom = Math.ceil(height * 0.80);
    let overlayRingGray = 0;
    let overlayRingSamples = 0;
    let overlayCoreDark = 0;
    let overlayCoreSamples = 0;
    let overlayButtonWarm = 0;
    let overlayButtonSamples = 0;

    for (let y = 0; y < height; y += 8) {
      for (let x = 0; x < width; x += 8) {
        const idx = (y * width + x) * 4;
        const r = data[idx], g = data[idx + 1], b = data[idx + 2];
        const brightness = (r + g + b) / 3;
        const saturation = Math.max(r, g, b) > 0 ? (Math.max(r, g, b) - Math.min(r, g, b)) / Math.max(r, g, b) : 0;

        const outerDx = (x - overlayCenterX) / Math.max(1, overlayOuterRx);
        const outerDy = (y - overlayCenterY) / Math.max(1, overlayOuterRy);
        const inOverlayOuter = outerDx * outerDx + outerDy * outerDy <= 1;
        const innerDx = (x - overlayCenterX) / Math.max(1, overlayInnerRx);
        const innerDy = (y - overlayCenterY) / Math.max(1, overlayInnerRy);
        const inOverlayInner = innerDx * innerDx + innerDy * innerDy <= 1;
        if (inOverlayOuter && !inOverlayInner) {
          overlayRingSamples++;
          if (brightness > 85 && brightness < 190 && saturation < 0.18) overlayRingGray++;
        }
        if (inOverlayInner) {
          overlayCoreSamples++;
          if (brightness < 110) overlayCoreDark++;
        }
        if (x >= buttonLeft && x < buttonRight && y >= buttonTop && y < buttonBottom) {
          overlayButtonSamples++;
          if (r > 170 && g > 60 && g < 220 && b < 170 && brightness > 110) overlayButtonWarm++;
        }
      }
    }

    const overlayRingGrayRatio = overlayRingSamples > 0 ? overlayRingGray / overlayRingSamples : 0;
    const overlayCoreDarkRatio = overlayCoreSamples > 0 ? overlayCoreDark / overlayCoreSamples : 0;
    const overlayButtonWarmRatio = overlayButtonSamples > 0 ? overlayButtonWarm / overlayButtonSamples : 0;

    if (
      overlayRingGrayRatio > 0.30 &&
      overlayCoreDarkRatio > 0.70 &&
      overlayButtonWarmRatio > 0.20
    ) {
      return 'WAITING';
    }
  }

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  if (board && Number.isFinite(board.left) && Number.isFinite(board.right) && Number.isFinite(board.top) && Number.isFinite(board.bottom)) {
    const boardLeft = clamp(Math.floor(board.left), 0, Math.max(0, width - 1));
    const boardRight = clamp(Math.ceil(board.right), boardLeft + 1, width);
    const boardTop = clamp(Math.floor(board.top), 0, Math.max(0, height - 1));
    const boardBottom = clamp(Math.ceil(board.bottom), boardTop + 1, height);
    const boardWidth = Math.max(1, boardRight - boardLeft);
    const boardHeight = Math.max(1, boardBottom - boardTop);

    let boardDark = 0;
    let boardWarmDark = 0;
    let buttonWarm = 0;
    let spinnerRingGray = 0;
    let spinnerRingSamples = 0;
    let spinnerCoreDark = 0;
    let spinnerCoreSamples = 0;
    let boardSamples = 0;
    let buttonSamples = 0;

    const boardStep = 6;
    const buttonLeft = boardLeft + Math.floor(boardWidth * 0.18);
    const buttonRight = boardLeft + Math.floor(boardWidth * 0.82);
    const buttonTop = boardTop + Math.floor(boardHeight * 0.58);
    const buttonBottom = boardTop + Math.floor(boardHeight * 0.88);
    const spinnerCenterX = boardLeft + boardWidth * 0.5;
    const spinnerCenterY = boardTop + boardHeight * 0.47;
    const spinnerOuterRx = boardWidth * 0.19;
    const spinnerOuterRy = boardHeight * 0.17;
    const spinnerInnerRx = boardWidth * 0.10;
    const spinnerInnerRy = boardHeight * 0.09;

    for (let y = boardTop; y < boardBottom; y += boardStep) {
      for (let x = boardLeft; x < boardRight; x += boardStep) {
        const idx = (y * width + x) * 4;
        const r = data[idx], g = data[idx + 1], b = data[idx + 2];
        const brightness = (r + g + b) / 3;
        const saturation = Math.max(r, g, b) > 0 ? (Math.max(r, g, b) - Math.min(r, g, b)) / Math.max(r, g, b) : 0;
        boardSamples++;
        if (brightness < 120) boardDark++;
        // The live play area is neutral-dark (grey/blue). A dark but *warm*
        // (brown/maroon) interior is the result/ranking panel instead.
        if (brightness < 130 && r > g + 15 && g >= b) boardWarmDark++;

        const outerDx = (x - spinnerCenterX) / Math.max(1, spinnerOuterRx);
        const outerDy = (y - spinnerCenterY) / Math.max(1, spinnerOuterRy);
        const inSpinnerOuter = outerDx * outerDx + outerDy * outerDy <= 1;
        const innerDx = (x - spinnerCenterX) / Math.max(1, spinnerInnerRx);
        const innerDy = (y - spinnerCenterY) / Math.max(1, spinnerInnerRy);
        const inSpinnerInner = innerDx * innerDx + innerDy * innerDy <= 1;
        if (inSpinnerOuter && !inSpinnerInner) {
          spinnerRingSamples++;
          if (brightness > 85 && brightness < 190 && saturation < 0.18) spinnerRingGray++;
        }
        if (inSpinnerInner) {
          spinnerCoreSamples++;
          if (brightness < 110) spinnerCoreDark++;
        }

        if (x >= buttonLeft && x < buttonRight && y >= buttonTop && y < buttonBottom) {
          buttonSamples++;
          if (r > 170 && g > 70 && g < 210 && b < 160 && brightness > 120) buttonWarm++;
        }
      }
    }

    const boardDarkRatio = boardSamples > 0 ? boardDark / boardSamples : 0;
    const boardWarmDarkRatio = boardSamples > 0 ? boardWarmDark / boardSamples : 0;
    const buttonWarmRatio = buttonSamples > 0 ? buttonWarm / buttonSamples : 0;
    const spinnerRingGrayRatio = spinnerRingSamples > 0 ? spinnerRingGray / spinnerRingSamples : 0;
    const spinnerCoreDarkRatio = spinnerCoreSamples > 0 ? spinnerCoreDark / spinnerCoreSamples : 0;

    // 結果/ランキング画面 ("RANKING"/"WAITING FOR THE NEXT GAME") は中央が濃茶の
    // 一覧パネルで、実際の盤面 (中立な暗色) より暖色の暗部が支配的になる。これを
    // 盤面と誤認すると board-moving のまま次ゲーム処理へ進めず、コーナー序盤で
    // キャプチャ/入力エラーが連続して bot が停止する。実測 0.01-0.02 (盤面) に対し
    // 0.68 (ランキング) なので、誤検出しない側に余裕を持たせた閾値にする。
    if (boardWarmDarkRatio > 0.5) {
      return 'WAITING';
    }

    if (
      boardDarkRatio > 0.70 &&
      buttonWarmRatio > 0.18 &&
      spinnerRingGrayRatio > 0.35 &&
      spinnerCoreDarkRatio > 0.72
    ) {
      return 'WAITING';
    }
  }

  // 画面中央列 (自分のプレイエリア) のみ分析
  // ゲームボード: 中央30-70%が暗い背景
  const centerLeft = Math.floor(width * 0.35);
  const centerRight = Math.floor(width * 0.65);
  const topArea = Math.floor(height * 0.1);  // UI上部を除外

  let centerDark = 0;   // やや暗いピクセル (ゲームボード背景を広めに拾う)
  let centerVeryDark = 0;
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

      if (brightness < 105) centerDark++;
      if (brightness < 65) centerVeryDark++;
      if (r > 180 && g < 80 && b < 80) centerRed++;
      if (r > 180 && g > 120 && b < 80) centerYellow++;
    }
  }

  if (centerSamples === 0) return 'WAITING';

  const darkRatio = centerDark / centerSamples;
  const veryDarkRatio = centerVeryDark / centerSamples;
  const redRatio = centerRed / centerSamples;
  const yellowRatio = centerYellow / centerSamples;

  // ゲームボード判定: 盤面は暗い領域が広く、しかも深い暗部も少し混じる。
  if (darkRatio > 0.18 || (darkRatio > 0.10 && veryDarkRatio > 0.03)) {
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
 * 色領域を検出し、国旗の断片のみを結合してサイズ分類する。
 * 低信頼度の形状は障害物として残し、戦略側では合体を確定しない。
 */
function detectPieces(data, width, height, calibration, excludedPixels = null) {
  const { board } = calibration;
  const arena = calibration.arena || board;
  const rawBlobs = [];

  // ボード領域内をグリッドスキャンし、色付きブロブを検出
  const gridStep = Math.max(1, Math.round(width / 1280 * 4));
  const visited = new Set();

  for (let y = Math.ceil(arena.top / gridStep) * gridStep; y < Math.min(height, arena.bottom); y += gridStep) {
    for (let x = Math.ceil(arena.left); x < Math.min(width, arena.right); x += gridStep) {
      if (excludedPixels?.has(y * width + x)) continue;
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
      const blob = floodFillEstimate(data, width, height, x, y, r, g, b, gridStep, visited, arena, excludedPixels);
      if (blob && blob.pixelCount > 5) { // 最小ブロブサイズ (ゲームの小ピースは小さい)
        rawBlobs.push(blob);
      }
    }
  }

  const records = [];
  const maxMergedRadiusPx = Math.max(40, (board.width / 7.0) * 1.65);
  const mergedBlobs = mergeNearbyBlobs(rawBlobs, maxMergedRadiusPx);
  for (const blob of mergedBlobs) {
    const piece = classifyBlob(blob, calibration);
    if (piece) records.push({ piece, blob });
  }

  // A white/red flag can be split into thin strips that the colour-blob path
  // rejects entirely. A connected full-colour silhouette can recover it. Keep
  // the fragment path for touching/rotated pieces when the template is unknown.
  const ppu = board.width / 7;
  const components = extractSpriteComponents(data, width, height, arena, {
    background: [50, 50, 50], excludePixels: excludedPixels || undefined,
    minPixels: Math.max(12, Math.round(ppu * ppu * 0.035)),
    pixelFilter: (r, g, b, a) => {
      const brightness = (r + g + b) / 3;
      const maximum = Math.max(r, g, b);
      const saturation = maximum > 0 ? (maximum - Math.min(r, g, b)) / maximum : 0;
      return a >= 200 && (saturation > 0.15 || brightness > 215 || brightness < 28);
    },
  });
  for (const component of components) {
    const bounds = component.bounds;
    const blob = { centerX: component.centerX, centerY: component.centerY,
      pixelCount: component.pixelCount, sampleStep: 1,
      bboxWidth: bounds.width - 1, bboxHeight: bounds.height - 1,
      minX: bounds.left, maxX: bounds.right - 1, minY: bounds.top, maxY: bounds.bottom - 1 };
    const shape = classifyBlob(blob, calibration);
    if (!shape) continue;
    const pixels = new Set(component.pixels);
    const overlap = records.map(record => ({ record,
      count: (record.blob.sampledPixels || []).filter(pixel => pixels.has(pixel)).length,
    })).filter(match => match.count > 0);
    // A merged colour record can span multiple disconnected flag fragments.
    // Its box need not fit any one silhouette. A partial pixel overlap must
    // not add that silhouette on top of the existing physical obstacle.
    const represented = overlap.filter(match =>
      match.count >= match.record.blob.sampledPixels.length * 0.85).map(match => match.record);
    if (overlap.some(match => !represented.includes(match.record))) continue;
    const label = classifySpriteComponent(data, width, height, component);
    const radius = TYPE_RADII[label?.type];
    const known = radius && label.confidence >= 0.65 && !component.touchesBoundary
      && Math.abs(shape.measuredRadius - radius) <= radius * 0.45;
    if (known) {
      for (const record of represented) records.splice(records.indexOf(record), 1);
      records.push({ blob, piece: { ...shape, type: label.type, r: radius,
        confidence: label.confidence, recognitionSource: 'flag-template' } });
    } else if (overlap.length === 0 && !records.some(record => {
      const b = blobBounds(record.blob);
      return b.left < bounds.right && b.right >= bounds.left
        && b.top < bounds.bottom && b.bottom >= bounds.top;
    })) {
      records.push({ blob, piece: { ...shape, confidence: Math.min(shape.confidence, 0.45) } });
    }
  }
  return records.map(record => record.piece);
}

/**
 * 簡易フラッドフィル: 同色領域のサイズと中心を推定
 */
function floodFillEstimate(data, width, height, startX, startY, targetR, targetG, targetB, gridStep, visited, board, excludedPixels = null) {
  const colorThreshold = 50; // RGB差の許容値
  const queue = [{ x: startX, y: startY }];
  let totalX = 0, totalY = 0, count = 0;
  let sumR = 0, sumG = 0, sumB = 0;
  let minX = startX, maxX = startX, minY = startY, maxY = startY;
  const sampledPixels = [];

  // Index-based queue avoids quadratic Array.shift work on large solid blobs.
  for (let head = 0; head < queue.length; head++) {
    const { x, y } = queue[head];
    const key = `${Math.floor(x / gridStep)}_${Math.floor(y / gridStep)}`;
    if (visited.has(key)) continue;
    if (x < 0 || x >= width || y < 0 || y >= height
        || x < board.left || x >= board.right || y < board.top || y >= board.bottom) continue;
    if (excludedPixels?.has(y * width + x)) continue;

    const idx = (y * width + x) * 4;
    const r = data[idx], g = data[idx + 1], b = data[idx + 2];
    const diff = Math.abs(r - targetR) + Math.abs(g - targetG) + Math.abs(b - targetB);
    if (diff > colorThreshold) continue;

    visited.add(key);
    sampledPixels.push(y * width + x);
    totalX += x;
    totalY += y;
    count++;
    sumR += r;
    sumG += g;
    sumB += b;
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
    sampleStep: gridStep,
    minX,
    maxX,
    minY,
    maxY,
    sampledPixels,
    avgColor: {
      r: Math.round(sumR / count),
      g: Math.round(sumG / count),
      b: Math.round(sumB / count),
    },
  };
}

/**
 * ブロブのサイズからピースタイプを推定
 */
function classifyBlob(blob, calibration) {
  const { board } = calibration;
  const aspectRatio = blobAspectRatio(blob);
  if (aspectRatio > 3.0) return null;

  // ブロブの実効半径（bboxと面積を併用）
  const blobRadius = blobEffectiveRadius(blob);

  // ピクセル→ゲーム座標の変換スケール
  const pixelsPerGameUnit = board.width / 7.0;

  // ゲーム座標での半径
  const gameRadius = blobRadius / pixelsPerGameUnit;
  if (gameRadius < 0.12 || gameRadius > 1.8) return null;

  const sizeGuess = classifyRadius(gameRadius, 15);
  // TYPE_COLORS are uncalibrated priors, not verified flag templates. A hue
  // guess must not overrule an unambiguous measured radius.
  const bestType = sizeGuess.type;
  if (sizeGuess.diff > 0.22) return null;

  // ゲーム座標での中心位置
  const normalizedX = (blob.centerX - board.left) / board.width;
  const gameX = -3.5 + normalizedX * 7.0;

  const normalizedY = (board.bottom - blob.centerY) / board.height;
  const gameY = -5.0 + normalizedY * 8.32; // total range: -5.0 to 3.32

  // Do not erase real pieces at hardcoded historical "ghost" coordinates.
  // Thin UI fragments are excluded by shape/top margin instead.
  const gx = Math.round(gameX * 100) / 100;
  const gy = Math.round(gameY * 100) / 100;
  const fragmented = aspectRatio > 1.8 || blobFillRatio(blob) < 0.45;
  const confidence = Math.max(0.3, Math.min(fragmented ? 0.45 : 0.9, 0.85 - sizeGuess.diff * 2));

  return {
    type: bestType,
    x: gx,
    y: gy,
    r: TYPE_RADII[bestType],
    confidence,
    measuredRadius: gameRadius,
  };
}

/** Project only checked label fields; pixel masks stay local to recognition. */
function canonicalPreview(piece) {
  if (!piece || !Number.isInteger(piece.type) || !TYPE_RADII[piece.type]
      || piece.fallback || !(piece.confidence >= 0.58)) return null;
  return { type: piece.type, r: TYPE_RADII[piece.type],
    confidence: piece.confidence, fallback: false, recognitionSource: 'flag-template' };
}

/** The HOLD icon has its own HUD cell, independent of the world deadline. */
function detectHoldPiece(data, width, height, calibration) {
  return canonicalPreview(detectHudPieces(data, width, height, calibration).hold);
}

/** Three horizontal future icons; the controllable current is separate. */
function detectNextPieces(data, width, height, calibration) {
  return detectHudPieces(data, width, height, calibration).nextPieces.map(canonicalPreview);
}

/**
 * おじゃまブロック(灰色)の量を測定
 * ボード内の灰色(低彩度, 中輝度)ピクセルの割合と、最高到達Y座標を返す
 */
function measureGarbage(data, width, height, calibration, pieces = []) {
  const { board } = calibration;
  const arena = calibration.arena || board;
  const step = Math.max(1, Math.round(width / 1280 * 6));
  const columns = [];
  let garbageCount = 0;
  let totalCount = 0;
  const bins = 28;
  for (let col = 0; col < bins; col++) {
    const left = board.left + board.width * col / bins;
    const right = board.left + board.width * (col + 1) / bins;
    let previousGray = false;
    let top = null;
    for (let y = Math.ceil(arena.top + step * 2); y < Math.min(height, arena.bottom - step); y += step) {
      let gray = 0, samples = 0;
      for (let x = Math.ceil(Math.max(board.left + step, left)); x < Math.min(width, board.right - step, right); x += step) {
        const idx = (y * width + x) * 4;
        const r = data[idx], g = data[idx + 1], b = data[idx + 2];
        const brightness = (r + g + b) / 3;
        const max = Math.max(r, g, b);
        const sat = max > 0 ? (max - Math.min(r, g, b)) / max : 0;
        totalCount++; samples++;
        if (brightness <= 100 || brightness >= 200 || sat >= 0.1) continue;
        const gx = -3.5 + (x - board.left) / board.width * 7;
        const gy = -5 + (board.bottom - y) / board.height * 8.32;
        if (pieces.some(p => p.confidence >= 0.6 && Math.hypot(gx - p.x, gy - p.y) < p.r * 0.9)) continue;
        garbageCount++; gray++;
      }
      const isGray = samples > 0 && gray / samples >= 0.4;
      // Require two consecutive sampled rows; UI hairlines and isolated noise
      // cannot turn a whole column into a high garbage wall.
      if (top === null && previousGray && isGray) top = -5 + (board.bottom - (y - step)) / board.height * 8.32;
      previousGray = isGray;
    }
    if (top !== null) columns.push({ left: -3.5 + col / bins * 7, right: -3.5 + (col + 1) / bins * 7, top });
  }
  return { ratio: totalCount ? garbageCount / totalCount : 0,
    height: Math.max(-5, ...columns.map(c => c.top)), pixelCount: garbageCount, columns };
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
  const arena = calibration.arena || board;
  if (!walls) return { level: 0 };

  // ゲージ領域: ボード左壁の外側〜壁境界にかけての細い縦領域
  const scanLeft = Math.max(0, Math.ceil(walls.leftOuter - 15 * height / 720));
  const scanRight = Math.min(width, Math.ceil(walls.leftOuter + 3 * height / 720));
  const scanTop = Math.max(0, Math.ceil(arena.top + 10 * height / 720));
  const scanBottom = Math.min(height, Math.floor(arena.bottom - 5 * height / 720));
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

  // 2. 1位演出: 中央に巨大な金星+白い「1」が出る画面は赤い小星がない
  const largeOverlayRank = readRankFromLargeVictoryOverlay(data, width, height);
  if (largeOverlayRank != null) {
    return largeOverlayRank;
  }

  // 3. フォールバック: 中央の明るさでランキング画面を推定
  let centerBright = 0, centerTotal = 0;
  const centerBrightnessValues = [];
  const step = 6;
  for (let y = Math.floor(height * 0.3); y < Math.floor(height * 0.85); y += step) {
    for (let x = Math.floor(width * 0.35); x < Math.floor(width * 0.65); x += step) {
      const idx = (y * width + x) * 4;
      const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
      centerBrightnessValues.push(br);
      centerTotal++;
      if (br > 100) centerBright++;
    }
  }
  const centerBrightRatio = centerTotal > 0 ? centerBright / centerTotal : 0;

  if (centerBrightRatio < 0.50) return null;
  if (isLowDetailBrightFade(centerBrightnessValues)) return null;

  return -1; // ランキング画面だが星の数字読み取り失敗
}

export async function detectConnectionErrorScreen(screenshotPath) {
  const image = sharp(screenshotPath);
  const metadata = await image.metadata();
  const { data } = await image.raw().ensureAlpha().toBuffer({ resolveWithObject: true });
  const { width, height } = metadata;

  const panelX1 = Math.floor(width * 0.32);
  const panelX2 = Math.ceil(width * 0.68);
  const panelY1 = Math.floor(height * 0.30);
  const panelY2 = Math.ceil(height * 0.60);
  const buttonX1 = Math.floor(width * 0.34);
  const buttonX2 = Math.ceil(width * 0.66);
  const buttonY1 = Math.floor(height * 0.47);
  const buttonY2 = Math.ceil(height * 0.56);

  let darkSamples = 0;
  let darkPixels = 0;
  let panelSamples = 0;
  let panelPixels = 0;
  let buttonSamples = 0;
  let buttonPixels = 0;

  for (let y = 0; y < height; y += 8) {
    for (let x = 0; x < width; x += 8) {
      const idx = (y * width + x) * 4;
      const r = data[idx], g = data[idx + 1], b = data[idx + 2];
      const brightness = (r + g + b) / 3;

      if (x < panelX1 || x >= panelX2 || y < panelY1 || y >= panelY2) {
        darkSamples++;
        if (brightness < 18) darkPixels++;
      } else {
        panelSamples++;
        if (g >= 65 && g <= 120 && r <= 35 && b >= 60 && b <= 110) panelPixels++;
      }

      if (x >= buttonX1 && x < buttonX2 && y >= buttonY1 && y < buttonY2) {
        buttonSamples++;
        if (r >= 70 && r <= 110 && g >= 180 && g <= 235 && b >= 130 && b <= 190) buttonPixels++;
      }
    }
  }

  const darkRatio = darkSamples > 0 ? darkPixels / darkSamples : 0;
  const panelRatio = panelSamples > 0 ? panelPixels / panelSamples : 0;
  const buttonRatio = buttonSamples > 0 ? buttonPixels / buttonSamples : 0;

  return darkRatio > 0.78 && panelRatio > 0.35 && buttonRatio > 0.45;
}

function quantile(sortedValues, ratio) {
  if (!sortedValues.length) return 0;
  const index = Math.min(sortedValues.length - 1, Math.max(0, Math.floor(sortedValues.length * ratio)));
  return sortedValues[index];
}

function isLowDetailBrightFade(values) {
  if (!values.length) return false;
  const sorted = [...values].sort((a, b) => a - b);
  const q05 = quantile(sorted, 0.05);
  const q95 = quantile(sorted, 0.95);
  return q05 > 235 && q95 - q05 < 12;
}

function readRankFromLargeVictoryOverlay(data, width, height) {
  const x1 = Math.floor(width * 0.34);
  const x2 = Math.floor(width * 0.66);
  const y1 = Math.floor(height * 0.25);
  const y2 = Math.floor(height * 0.82);

  const values = [];
  for (let y = y1; y < y2; y += 2) {
    for (let x = x1; x < x2; x += 2) {
      const idx = (y * width + x) * 4;
      values.push((data[idx] + data[idx + 1] + data[idx + 2]) / 3);
    }
  }

  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const q05 = quantile(sorted, 0.05);
  const q25 = quantile(sorted, 0.25);
  const q95 = quantile(sorted, 0.95);
  const contrast = q95 - q05;
  if (q05 < 90 || contrast < 18) return null;

  const darkThreshold = q25 + 3;
  let darkCount = 0;
  for (let y = y1; y < y2; y += 2) {
    for (let x = x1; x < x2; x += 2) {
      const idx = (y * width + x) * 4;
      const br = (data[idx] + data[idx + 1] + data[idx + 2]) / 3;
      if (br <= darkThreshold) darkCount++;
    }
  }

  const darkRatio = darkCount / values.length;
  if (darkRatio < 0.18 || darkRatio > 0.62) return null;

  // 現在の1位演出は中央巨大星に大きな「1」だけを重ねる。
  return 1;
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

  // 1. 白ピクセル(RGB全チャンネル>180)の列分布で桁候補セグメントを検出
  const colWhite = new Array(digitX2 - digitX1).fill(0);
  for (let y = digitY1; y < digitY2; y++) {
    for (let x = digitX1; x < digitX2; x++) {
      const idx = (y * width + x) * 4;
      if (data[idx] > 180 && data[idx + 1] > 180 && data[idx + 2] > 180) colWhite[x - digitX1]++;
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
        if (data[idx] > 180 && data[idx + 1] > 180 && data[idx + 2] > 180) {
          if (y < rowMin) rowMin = y; if (y > rowMax) rowMax = y;
        }
      }
    }
    seg.rowMin = rowMin;
    seg.rowMax = rowMax;
    seg.h = rowMax - rowMin;
  }

  // 最も高いセグメント(主桁)を基準に高さ・縦位置フィルタ
  const maxH = Math.max(...rawSegs.map(s => s.h));
  if (maxH < 20) return null;
  const tallest = rawSegs.find(s => s.h === maxH);
  // 主桁と縦方向で重なり、かつ高さが主桁の60%以上のセグメントのみ候補
  const candidates = rawSegs.filter(s =>
    s.h >= maxH * 0.6 &&
    s.rowMin < tallest.rowMax && s.rowMax > tallest.rowMin
  ).sort((a, b) => a.start - b.start);
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
          const r = data[idx], g = data[idx + 1], b = data[idx + 2];
          total++;
          // 白色判定: RGB全チャンネルが高い(赤い星背景の除外)
          if (r > 180 && g > 180 && b > 180) bright++;
        }
      }
      if (total > 0 && bright / total > 0.38) {
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
        0b110_110_110_110_111,
        0b111_111_011_011_111, 0b111_011_011_011_111, 0b011_011_011_011_111],
    2: [0b111_101_001_011_111, 0b111_001_011_100_111, 0b111_001_111_110_111,
        0b111_101_011_110_111, 0b111_001_011_110_111],
    3: [0b111_001_011_001_111, 0b111_001_111_001_110],
    4: [0b101_101_111_001_011, 0b100_101_111_001_001,
        0b011_111_111_111_111, 0b111_111_111_011_011],
    5: [0b111_100_111_001_110, 0b111_110_111_001_111],
    6: [0b111_100_111_101_110, 0b110_100_111_101_111,
        0b111_100_110_101_111, 0b111_100_110_101_110],
    7: [0b111_001_001_010_010, 0b111_001_010_010_100, 0b111_001_001_001_011,
        0b111_001_010_010_011, 0b111_001_011_010_010],
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

// Pure image primitives are exported for deterministic synthetic/replay tests.
export { TYPE_RADII, detectPieces, detectNextPieces, detectHoldPiece, measureGarbage, classifyBlob, mergeNearbyBlobs };

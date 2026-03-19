#!/usr/bin/env node
/**
 * main.mjs - 同志AI エントリポイント
 *
 * Playwright でブラウザを起動し、unityroom のゲームを自動プレイする。
 * スクリーンショットベースの盤面解析 + 戦略モジュールでドロップ位置を決定。
 */

import 'dotenv/config';
import { chromium } from 'playwright';
import { writeFileSync, appendFileSync, mkdirSync, existsSync, renameSync, readdirSync } from 'fs';
import { join } from 'path';
// calibration.mjs, screenshot_analyzer.mjs は動的ロード (ホットリロード対応)
async function loadModule(name) {
  const url = new URL(name, `file://${process.cwd()}/`).href;
  return await import(url + '?t=' + Date.now());
}
// strategy.mjs は毎ターン動的にロード (AI改善で更新されるため)
async function loadStrategy() {
  const url = new URL('./strategy.mjs', `file://${process.cwd()}/`).href;
  return await import(url + '?t=' + Date.now());
}

// --- 定数 ---
const GAME_URL = 'https://unityroom.com/games/sorengame91';
const PLAYER_NAME = 'DoCiAI:US';
const SCREENSHOT_DIR = 'tmp/screenshots';
const HISTORY_DIR = 'game_history';
const DROP_COOLDOWN_MS = 1200; // ドロップ間の最低待機時間 (ゲーム側クールダウン≈1秒)
const POLL_INTERVAL_MS = 200;  // 状態チェック間隔
const MOVE_TIMEOUT_MS = 30000; // MOVE待ちタイムアウト
const IMPROVEMENT_INTERVAL_GAMES = 12; // AI改善を走らせるゲーム間隔

// ディレクトリ確保
[SCREENSHOT_DIR, HISTORY_DIR, 'tmp/summaries', 'strategy_versions'].forEach(dir => {
  mkdirSync(dir, { recursive: true });
});

// --- ゲーム番号管理 ---
function getNextGameNumber() {
  const files = existsSync(HISTORY_DIR) ? readdirSync(HISTORY_DIR) : [];
  const nums = files
    .filter(f => f.startsWith('game_') && f.endsWith('.jsonl'))
    .map(f => parseInt(f.match(/game_(\d+)/)?.[1] || '0', 10));
  return nums.length > 0 ? Math.max(...nums) + 1 : 1;
}

// --- メイン ---
async function main() {
  console.log('[main] 同志AI 起動...');

  // Step 1: headless でトップページを開き、ゲームURLを取得
  console.log('[main] Fetching game URL (headless)...');
  const gameUrl = await fetchGameUrl();
  console.log('[main] Game URL:', gameUrl);

  // Step 2: 非headless でゲームURLだけを開く (広告なし)
  const browser = await chromium.launch({
    headless: false,
    args: ['--window-size=1280,720'],
  });
  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
  });

  try {
    // ゲームURLに直接遷移 + HTML intercept で unityInstance 取得
    const gamePage = await context.newPage();
    await gamePage.route('**/*play.unityroom.com/**', async route => {
      if (route.request().resourceType() === 'document') {
        const response = await route.fetch();
        let body = await response.text();
        body = body.replace(
          '.then((unityInstance) => {',
          '.then((unityInstance) => { window.__unityInstance = unityInstance;'
        );
        await route.fulfill({ response, body });
        console.log('[main] HTML intercepted, unityInstance hook injected');
      } else {
        await route.continue();
      }
    });
    await gamePage.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });

    // Unity canvas ロード待機
    console.log('[main] Waiting for Unity canvas...');
    await gamePage.waitForSelector('canvas', { timeout: 60000 });
    console.log('[main] Canvas found, waiting for Unity to fully load...');
    // Unityロード完了をポーリングで待つ (ローディングバーが非表示になるまで)
    for (let i = 0; i < 60; i++) {
      await gamePage.waitForTimeout(1000);
      const loaded = await gamePage.evaluate(() => {
        const bar = document.getElementById('unity-loading-bar');
        return !bar || bar.style.display === 'none';
      });
      if (loaded) {
        console.log(`[main] Unity loaded after ${i + 1}s`);
        break;
      }
    }
    await gamePage.waitForTimeout(3000); // 追加バッファ

    // タイトル画面: 名前入力 + PLAY
    await handleTitleScreen(gamePage);

    // ゲームボード表示を待ってからキャリブレーション
    // 最初は仮キャリブレーション (全画面) で待機→ボード検出後に再キャリブレーション
    const calMod = await loadModule('./calibration.mjs');
    let calibration = calMod.loadCalibration();
    if (!calibration) {
      // 仮キャリブレーション: 全画面をボードとして扱う
      calibration = {
        screen: { width: 1280, height: 720 },
        board: { left: 0, right: 1279, top: 0, bottom: 719, width: 1279, height: 719 },
        dropArea: { pixelLeft: 91, pixelRight: 1188 },
        timestamp: new Date().toISOString(),
      };
      console.log('[main] Using provisional calibration (full screen)');
    }

    // ゲームループ開始
    let gameNumber = getNextGameNumber();
    console.log(`[main] Starting game #${gameNumber}`);

    await gameLoop(gamePage, calibration, gameNumber);

  } catch (err) {
    console.error('[main] Fatal error:', err.message);
  } finally {
    await browser.close();
    console.log('[main] Browser closed.');
  }
}

/**
 * タイトル画面で名前を入力してPLAYを押す
 * Unity canvas内のUI要素なので、ピクセル座標でクリック+キー入力
 */
async function handleTitleScreen(page) {
  console.log('[main] Handling title screen...');

  const canvas = await page.$('canvas');
  if (!canvas) throw new Error('Canvas not found on title screen');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Canvas bounding box not available');

  // スクリーンショットで状態確認
  await page.screenshot({ path: join(SCREENSHOT_DIR, 'title.png') });

  // 名前入力欄をクリック (canvas座標 x=630, y=560 付近)
  const nameFieldX = box.x + 630;
  const nameFieldY = box.y + 560;

  console.log(`[main] Clicking name field at (${nameFieldX.toFixed(0)}, ${nameFieldY.toFixed(0)})`);
  await page.mouse.click(nameFieldX, nameFieldY);
  await sleep(500);

  // 既存テキストを全選択して削除
  await page.keyboard.press('Control+a');
  await sleep(200);
  await page.keyboard.press('Delete');
  await sleep(200);

  // ASCII名を1文字ずつキー入力 (Unity WebGLはkeyboard.pressのみ対応)
  console.log(`[main] Typing player name: ${PLAYER_NAME}`);
  for (const ch of PLAYER_NAME) {
    if (ch === '_') {
      await page.keyboard.press('Shift+Minus');
    } else if (ch === ':') {
      await page.keyboard.press('Shift+Semicolon');
    } else if (/[a-zA-Z0-9]/.test(ch)) {
      await page.keyboard.press(ch);
    } else {
      // その他の記号はdispatchEventで送信
      await page.evaluate((c) => {
        const canvas = document.querySelector('canvas');
        canvas.dispatchEvent(new KeyboardEvent('keypress', { key: c, charCode: c.charCodeAt(0), bubbles: true }));
      }, ch);
    }
    await sleep(60);
  }
  await sleep(500);

  // スクリーンショットで入力確認
  await page.screenshot({ path: join(SCREENSHOT_DIR, 'title_named.png') });

  // PLAYボタンをクリック (入力欄の下)
  const playButtonX = box.x + 630;
  const playButtonY = box.y + 645;

  console.log(`[main] Clicking PLAY button at (${playButtonX.toFixed(0)}, ${playButtonY.toFixed(0)})`);
  await page.mouse.click(playButtonX, playButtonY);
  await sleep(2000);

  console.log('[main] Title screen done, game should be starting...');
}

/**
 * headless ブラウザでトップページを開き、ゲームURLを取得して閉じる
 * トップページの広告を表示せずに済む
 */
async function fetchGameUrl() {
  const headless = await chromium.launch({ headless: true });
  try {
    const ctx = await headless.newContext();
    const page = await ctx.newPage();
    await page.goto(GAME_URL, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForTimeout(2000);

    const iframeSrc = await page.$eval(
      'iframe[src*="play.unityroom.com"]',
      el => el.src
    ).catch(() => null);

    const tabHref = await page.$eval(
      'a[href*="play.unityroom.com"]',
      el => el.href
    ).catch(() => null);

    const gameUrl = iframeSrc || tabHref;
    if (!gameUrl) throw new Error('Game URL not found on page');
    return gameUrl;
  } finally {
    await headless.close();
  }
}

/**
 * メインゲームループ
 */
async function gameLoop(page, calibration, gameNumber) {
  const historyFile = join(HISTORY_DIR, 'latest.jsonl');
  let turn = 0;
  let lastDropTime = 0;
  let consecutiveErrors = 0;
  let waitingLogged = false;
  let waitingCount = 0;
  let calibrated = false;
  let moveCount = 0;
  let roundEnded = false;
  let holdUsedThisTurn = false;

  console.log('[game] Game loop started');

  while (true) {
    try {
      // ドロップクールダウン
      const elapsed = Date.now() - lastDropTime;
      if (elapsed < DROP_COOLDOWN_MS) {
        await sleep(DROP_COOLDOWN_MS - elapsed);
      }

      // スクリーンショット取得
      const screenshotPath = join(SCREENSHOT_DIR, `turn_${String(turn).padStart(4, '0')}.png`);
      await page.screenshot({ path: screenshotPath });

      // 盤面解析
      const { analyzeScreenshot } = await loadModule('./screenshot_analyzer.mjs');
      const boardState = await analyzeScreenshot(screenshotPath, calibration);
      console.log(`[game] Turn ${turn}: state=${boardState.state}, pieces=${boardState.pieces.length}, score=${boardState.score}, conf=${boardState.confidence.toFixed(2)}`);

      // ゲームオーバー処理
      if (boardState.state === 'GAMEOVER') {
        console.log(`[game] GAMEOVER at turn ${turn}, score=${boardState.score}`);
        await handleGameOver(page, gameNumber, turn, boardState, historyFile);
        return;
      }

      // 待機画面 (ランキング/接続中/タイトル画面)
      if (boardState.state === 'WAITING') {
        waitingCount++;

        // ラウンド終了判定: ゲーム中 (turn>5) に連続3回以上WAITINGが続いたらラウンド終了
        if (turn > 5 && waitingCount >= 3 && !roundEnded) {
          console.log(`[game] Round ended at turn ${turn}`);
          roundEnded = true;
          // 履歴保存 + AI改善 (非同期 — ゲームループをブロックしない)
          handleGameOver(page, gameNumber, turn, boardState, historyFile)
            .catch(e => console.error('[game] Post-game error:', e.message));
          // 次ラウンド用にリセット
          gameNumber++;
          turn = 0;
          calibrated = false;
          moveCount = 0;
        }

        if (!waitingLogged) {
          console.log('[game] Waiting for next round...');
          waitingLogged = true;
        }
        // 長時間WAITINGが続く場合 → タイトル画面に戻った可能性
        if (waitingCount > 20 && waitingCount % 20 === 0) {
          console.log('[game] Long wait detected, attempting re-entry...');
          try {
            await handleTitleScreen(page);
          } catch (e) {
            console.log('[game] Re-entry attempt failed:', e.message);
          }
        }
        await sleep(1000); // WAITING中は1秒間隔でチェック
        continue;
      }
      waitingLogged = false;
      waitingCount = 0;
      roundEnded = false;

      // MOVE状態が安定してからキャリブレーション (初回のみ)
      if (!calibrated) {
        moveCount++;
        if (moveCount >= 3) { // 3回連続MOVEで安定と判断
          console.log('[game] Game board stable, running calibration...');
          const calScreenshot = join(SCREENSHOT_DIR, 'calibration.png');
          await page.screenshot({ path: calScreenshot });
          const { calibrate } = await loadModule('./calibration.mjs');
          calibration = await calibrate(calScreenshot);
          calibrated = true;
        }
      }

      // MOVE状態でない場合は待機
      if (boardState.state !== 'MOVE') {
        await sleep(POLL_INTERVAL_MS);
        continue;
      }

      // 戦略決定 (canHoldを付与)
      boardState.canHold = !holdUsedThisTurn;
      const { decide } = await loadStrategy();
      const decision = decide(boardState);
      console.log(`[game] Decision: x=${decision.x.toFixed(2)}, reason=${decision.reason}${decision.hold ? ' [HOLD]' : ''}`);

      // HOLD操作: 右クリックでswap/save → ドロップせず再解析
      if (decision.hold && !holdUsedThisTurn) {
        await executeHold(page, calibration);
        holdUsedThisTurn = true;
        lastDropTime = Date.now();
        continue; // ピースが変わるので再解析
      }

      // マウスドロップ実行
      await executeDrop(page, decision.x, calibration);
      holdUsedThisTurn = false; // ドロップ後にhold権をリセット
      lastDropTime = Date.now();

      // ターン記録
      const record = {
        turn,
        timestamp: new Date().toISOString(),
        state: boardState,
        decision,
      };
      appendFileSync(historyFile, JSON.stringify(record) + '\n');

      turn++;
      consecutiveErrors = 0;

    } catch (err) {
      consecutiveErrors++;
      console.error(`[game] Error (${consecutiveErrors}):`, err.message);

      if (consecutiveErrors > 10) {
        console.error('[game] Too many consecutive errors, stopping');
        return;
      }

      await sleep(1000);
    }
  }
}

/**
 * HOLD操作を実行 (右クリック)
 * 現在のカーソルピースをHOLD領域に保持、既にHOLDがあれば入れ替え
 */
async function executeHold(page, calibration) {
  const { board } = calibration;
  const canvas = await page.$('canvas');
  if (!canvas) throw new Error('Canvas not found');

  // ボード中央で右クリック
  const clickX = board.left + Math.floor(board.width / 2);
  const clickY = board.top + Math.floor(board.height * 0.3);
  await page.mouse.click(clickX, clickY, { button: 'right' });
  await sleep(300);
}

/**
 * ドロップ操作を実行
 * ゲームX座標をピクセル座標に変換し、キャンバス上でクリック
 */
async function executeDrop(page, gameX, calibration) {
  const { dropXToPixel } = await loadModule('./calibration.mjs');
  const pixelX = dropXToPixel(gameX, calibration);

  // ドロップ: まずマウスをX位置に移動し、ボード中央付近でクリック
  const { board } = calibration;
  const pixelY = board.top + Math.floor(board.height * 0.3); // ボード上部30%あたり

  // canvas要素を取得
  const canvas = await page.$('canvas');
  if (!canvas) {
    throw new Error('Canvas not found');
  }

  const box = await canvas.boundingBox();
  if (!box) {
    throw new Error('Canvas bounding box not available');
  }

  const clickX = pixelX;
  const clickY = pixelY;

  // マウスをX位置に移動 (ゲームがマウス位置でドロップ先を決定)
  await page.mouse.move(clickX, clickY);
  await sleep(200);
  // クリックでドロップ実行
  await page.mouse.click(clickX, clickY);
}

/**
 * ラウンド終了処理: 履歴保存 + AI改善ループ
 * ラウンド制なのでリトライ不要（自動で次ラウンドが始まる）
 */
async function handleGameOver(page, gameNumber, turns, finalState, historyFile) {
  // 履歴ファイルをリネーム保存
  const archivePath = join(HISTORY_DIR, `game_${String(gameNumber).padStart(4, '0')}.jsonl`);
  if (existsSync(historyFile)) {
    try {
      renameSync(historyFile, archivePath);
      console.log(`[game] History saved: ${archivePath}`);
    } catch (e) {
      console.log(`[game] History save failed: ${e.message}`);
    }
  }

  // ゲームサマリー保存
  const summary = {
    gameNumber,
    turns,
    score: finalState.score,
    piecesAtEnd: finalState.pieces.length,
    timestamp: new Date().toISOString(),
  };
  const summaryPath = join('tmp/summaries', `game_${String(gameNumber).padStart(4, '0')}.json`);
  writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  console.log(`[game] Summary: turns=${turns}, score=${finalState.score}`);

  if (gameNumber % IMPROVEMENT_INTERVAL_GAMES !== 0) {
    console.log(`[game] Skipping improvement for game #${gameNumber} (runs every ${IMPROVEMENT_INTERVAL_GAMES} games)`);
    return;
  }

  // AI改善ループ起動
  try {
    const impUrl = new URL('./improve.mjs', `file://${process.cwd()}/`).href;
    const { runImprovement } = await import(impUrl + '?t=' + Date.now());
    await runImprovement(gameNumber, archivePath, summaryPath);
  } catch (err) {
    console.error('[game] Improvement loop error:', err.message);
  }
}

/**
 * sleep ユーティリティ
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// 実行
main().catch(console.error);

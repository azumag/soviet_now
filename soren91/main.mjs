#!/usr/bin/env node
/**
 * main.mjs - 同志AI エントリポイント
 *
 * Playwright でブラウザを起動し、unityroom のゲームを自動プレイする。
 * スクリーンショットベースの盤面解析 + 戦略モジュールでドロップ位置を決定。
 */

import 'dotenv/config';
import { parse as parseDotenv } from 'dotenv';
import { chromium } from 'playwright';
import { writeFileSync, appendFileSync, mkdirSync, existsSync, renameSync, readdirSync, readFileSync, unlinkSync, copyFileSync, rmdirSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { execSync } from 'child_process';
import {
  computeStrategyHashFromFile,
  recordCompletedGame,
  snapshotCurrentStrategyForGame,
} from './lineage.mjs';
// calibration.mjs, screenshot_analyzer.mjs は動的ロード (ホットリロード対応)
async function loadModule(name) {
  const url = new URL(name, `file://${process.cwd()}/`).href;
  return await import(url + '?t=' + Date.now());
}
// strategy.mjs は毎ターン動的にロード (AI改善で更新されるため)
async function loadStrategy(strategyPath = './strategy.mjs') {
  const url = new URL(strategyPath, `file://${process.cwd()}/`).href;
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
const DEFAULT_IMPROVEMENT_INTERVAL_GAMES = 12;
const DEFAULT_AUDIO_GAIN_MULTIPLIER = 0.70;
const DEFAULT_SHARED_CDP_PORT = 9222;
const ENV_PATH = '.env';
const RUNTIME_CONFIG_PATH = 'runtime_config.json';

// ディレクトリ確保
[SCREENSHOT_DIR, HISTORY_DIR, 'tmp/summaries', 'strategy_versions', 'tmp/strategy_snapshots', 'tmp/state', 'tmp/game_screenshots'].forEach(dir => {
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

function parsePositiveInt(value) {
  const num = Number.parseInt(String(value), 10);
  return Number.isInteger(num) && num > 0 ? num : null;
}

function loadImprovementSchedule() {
  // 環境変数オーバーライド (最優先 — soren91_control.sh メリケンモード等で使用)
  const envInterval = parsePositiveInt(process.env.IMPROVEMENT_INTERVAL_GAMES);
  if (envInterval) return { interval: envInterval, source: 'process.env' };

  if (existsSync(RUNTIME_CONFIG_PATH)) {
    try {
      const config = JSON.parse(readFileSync(RUNTIME_CONFIG_PATH, 'utf-8'));
      const interval = parsePositiveInt(config.improvementIntervalGames);
      if (interval) return { interval, source: RUNTIME_CONFIG_PATH };
      console.log(`[config] Ignoring invalid improvementIntervalGames in ${RUNTIME_CONFIG_PATH}`);
    } catch (err) {
      console.log(`[config] Failed to parse ${RUNTIME_CONFIG_PATH}: ${err.message}`);
    }
  }

  if (existsSync(ENV_PATH)) {
    try {
      const env = parseDotenv(readFileSync(ENV_PATH, 'utf-8'));
      const interval = parsePositiveInt(env.IMPROVEMENT_INTERVAL_GAMES);
      if (interval) return { interval, source: ENV_PATH };
      if (typeof env.IMPROVEMENT_INTERVAL_GAMES !== 'undefined') {
        console.log(`[config] Ignoring invalid IMPROVEMENT_INTERVAL_GAMES in ${ENV_PATH}`);
      }
    } catch (err) {
      console.log(`[config] Failed to parse ${ENV_PATH}: ${err.message}`);
    }
  }

  return { interval: DEFAULT_IMPROVEMENT_INTERVAL_GAMES, source: 'default' };
}

function loadAudioGainMultiplier() {
  const raw = process.env.SOREN91_AUDIO_GAIN_MULTIPLIER;
  if (raw == null || raw === '') {
    return DEFAULT_AUDIO_GAIN_MULTIPLIER;
  }

  const value = Number.parseFloat(String(raw));
  if (Number.isFinite(value) && value >= 0) {
    return value;
  }

  console.log(`[config] Ignoring invalid SOREN91_AUDIO_GAIN_MULTIPLIER=${raw}`);
  return DEFAULT_AUDIO_GAIN_MULTIPLIER;
}

async function installAudioGainLimiter(page, multiplier) {
  if (!Number.isFinite(multiplier) || multiplier < 0) {
    return;
  }

  await page.addInitScript(({ multiplierValue }) => {
    const gainMultiplier = Number(multiplierValue);
    if (!Number.isFinite(gainMultiplier) || gainMultiplier < 0) {
      return;
    }

    const AudioNodeCtor = globalThis.AudioNode;
    if (AudioNodeCtor?.prototype && typeof AudioNodeCtor.prototype.connect === 'function') {
      const originalConnect = AudioNodeCtor.prototype.connect;

      const ensureMasterGain = (ctx) => {
        if (!ctx.__soren91MasterGain) {
          const masterGain = ctx.createGain();
          masterGain.gain.value = gainMultiplier;
          originalConnect.call(masterGain, ctx.destination);
          Object.defineProperty(ctx, '__soren91MasterGain', {
            value: masterGain,
            configurable: true,
          });
        } else if (ctx.__soren91MasterGain?.gain) {
          ctx.__soren91MasterGain.gain.value = gainMultiplier;
        }
        return ctx.__soren91MasterGain;
      };

      if (!globalThis.__soren91AudioGainPatched) {
        AudioNodeCtor.prototype.connect = function patchedConnect(destination, ...args) {
          try {
            if (destination && this?.context && destination === this.context.destination) {
              const masterGain = ensureMasterGain(this.context);
              return originalConnect.call(this, masterGain, ...args);
            }
          } catch (_) {
            // Fall through to the native connect path.
          }
          return originalConnect.call(this, destination, ...args);
        };

        Object.defineProperty(globalThis, '__soren91AudioGainPatched', {
          value: true,
          configurable: true,
        });
      }
    }

    const applyMediaVolume = (elem) => {
      try {
        if (elem && typeof elem.volume === 'number' && elem.volume > gainMultiplier) {
          elem.volume = gainMultiplier;
        }
      } catch (_) {
        // Ignore media volume failures and keep gameplay alive.
      }
    };

    const sweepMedia = () => {
      try {
        document.querySelectorAll('audio,video').forEach(applyMediaVolume);
      } catch (_) {
        // Ignore missing DOM during early boot.
      }
    };

    if (typeof HTMLMediaElement === 'function' && !globalThis.__soren91MediaPlayPatched) {
      const originalPlay = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function patchedPlay(...args) {
        applyMediaVolume(this);
        return originalPlay.apply(this, args);
      };
      Object.defineProperty(globalThis, '__soren91MediaPlayPatched', {
        value: true,
        configurable: true,
      });
    }

    const installObserver = () => {
      if (globalThis.__soren91MediaObserverInstalled || !document.documentElement) {
        return;
      }
      const observer = new MutationObserver(() => sweepMedia());
      observer.observe(document.documentElement, { childList: true, subtree: true });
      Object.defineProperty(globalThis, '__soren91MediaObserverInstalled', {
        value: true,
        configurable: true,
      });
    };

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        sweepMedia();
        installObserver();
      }, { once: true });
    } else {
      sweepMedia();
      installObserver();
    }

    globalThis.addEventListener('load', () => sweepMedia(), { once: true });
    globalThis.__soren91AudioGainMultiplier = gainMultiplier;
  }, { multiplierValue: multiplier });
}

/**
 * 共有ブラウザ接続を試行 (CDP経由)
 * SOREN91_SHARED_BROWSER=1 の場合のみ有効
 * 失敗時は null を返し、呼び出し元で単独起動にフォールバック
 */
async function connectToSharedBrowser() {
  if (process.env.SOREN91_SHARED_BROWSER !== '1') return null;

  const __dirname = dirname(fileURLToPath(import.meta.url));
  const cdpEndpointFile = join(__dirname, '..', 'tmp', 'cdp_endpoint.json');
  const cdpPort = Number.parseInt(process.env.SOREN_CDP_PORT || '', 10) || DEFAULT_SHARED_CDP_PORT;
  const fallbackUrl = `http://127.0.0.1:${cdpPort}`;
  const candidateUrls = [];
  let endpointPidAlive = true;

  if (existsSync(cdpEndpointFile)) {
    try {
      const endpoint = JSON.parse(readFileSync(cdpEndpointFile, 'utf-8'));
      if (endpoint?.url) candidateUrls.push(endpoint.url);
      if (endpoint?.pid) {
        try {
          execSync(`kill -0 ${endpoint.pid}`, { stdio: 'ignore' });
        } catch {
          endpointPidAlive = false;
          console.log(`[main] CDP endpoint writer PID ${endpoint.pid} is not alive; trying browser port directly`);
        }
      }
    } catch (e) {
      console.log(`[main] Failed to parse CDP endpoint file: ${e.message}`);
    }
  } else {
    console.log('[main] CDP endpoint file not found; trying shared browser port directly');
  }

  if (!candidateUrls.includes(fallbackUrl)) {
    candidateUrls.push(fallbackUrl);
  }

  for (const url of candidateUrls) {
    try {
      console.log(`[main] Connecting to shared browser at ${url}...`);
      const browser = await chromium.connectOverCDP(url);
      console.log('[main] Connected to shared browser via CDP');
      return browser;
    } catch (e) {
      console.log(`[main] CDP connection failed at ${url}: ${e.message}`);
      if (url !== fallbackUrl && !endpointPidAlive) {
        try { unlinkSync(cdpEndpointFile); } catch {}
      }
    }
  }

  console.log('[main] Shared browser unavailable, falling back to standalone browser');
  return null;
}

function chooseSharedBrowserContext(browser) {
  const contexts = browser.contexts();
  if (contexts.length === 0) return null;

  const withLocalPage = contexts.find(ctx =>
    ctx.pages().some(page => {
      const url = page.url();
      return url.startsWith('http://localhost:') || url.startsWith('http://127.0.0.1:');
    })
  );
  if (withLocalPage) return withLocalPage;

  const withAnyPage = contexts.find(ctx => ctx.pages().length > 0);
  return withAnyPage || contexts[0];
}

function chooseSharedBrowserAnchorPage(context) {
  const pages = context.pages();
  if (pages.length === 0) return null;

  const localPage = pages.find(page => {
    const url = page.url();
    return url.startsWith('http://localhost:') || url.startsWith('http://127.0.0.1:');
  });
  return localPage || pages[0];
}

async function openSharedBrowserTab(context) {
  const anchorPage = chooseSharedBrowserAnchorPage(context);
  if (!anchorPage) {
    return await context.newPage();
  }

  const popupPromise = context.waitForEvent('page');
  await anchorPage.evaluate(() => {
    window.open('about:blank', '_blank');
  });
  const popupPage = await popupPromise;
  return popupPage;
}

// --- メイン ---
async function main() {
  console.log('[main] 同志AI 起動...');
  const audioGainMultiplier = loadAudioGainMultiplier();
  console.log(`[main] soren91 audio gain multiplier=${audioGainMultiplier}`);

  // Step 1: headless でトップページを開き、ゲームURLを取得
  console.log('[main] Fetching game URL (headless)...');
  const gameUrl = await fetchGameUrl();
  console.log('[main] Game URL:', gameUrl);

  // Step 2: 非headless でゲームURLだけを開く (広告なし)
  // 共有ブラウザ接続を試行、失敗なら従来の単独起動
  const sharedBrowser = await connectToSharedBrowser();
  const isSharedMode = sharedBrowser != null;
  const browser = sharedBrowser || await chromium.launch({
    headless: false,
    args: ['--window-size=1280,720'],
  });
  let context = null;
  let ownsContext = false;
  if (isSharedMode) {
    context = chooseSharedBrowserContext(browser);
    if (context) {
      console.log('[main] Running in shared browser mode (existing context/new tab)');
    } else {
      console.log('[main] Shared browser has no reusable context; creating isolated context');
      context = await browser.newContext({
        viewport: { width: 1280, height: 720 },
        locale: 'ja-JP',
        timezoneId: 'Asia/Tokyo',
      });
      ownsContext = true;
    }
  } else {
    context = await browser.newContext({
      viewport: { width: 1280, height: 720 },
      locale: 'ja-JP',
      timezoneId: 'Asia/Tokyo',
    });
    ownsContext = true;
  }
  if (isSharedMode) {
    console.log(`[main] Shared browser contexts=${browser.contexts().length}`);
  }

  let gamePage = null;
  try {
    // ゲームURLに直接遷移 + HTML intercept で unityInstance 取得
    gamePage = (isSharedMode && !ownsContext)
      ? await openSharedBrowserTab(context)
      : await context.newPage();
    if (isSharedMode) {
      try {
        await gamePage.setViewportSize({ width: 1280, height: 720 });
      } catch {}
    }
    await installAudioGainLimiter(gamePage, audioGainMultiplier);
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
    if (isSharedMode) {
      await gamePage.bringToFront();
    }

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
    try { unlinkSync('tmp/in_game'); } catch {}
    if (isSharedMode) {
      // 共有モード: 既存 context を再利用した場合は context 全体を閉じない。
      // browser.close() on a CDP-connected browser only disconnects
      // (does NOT kill the external Chromium)
      if (!ownsContext && gamePage && !gamePage.isClosed()) {
        try {
          await gamePage.close();
        } catch {}
      }
      if (ownsContext) {
        await context.close();
      }
      // soviet_local のページを前面に戻す
      try {
        const contexts = browser.contexts();
        for (const ctx of contexts) {
          const pages = ctx.pages();
          if (pages.length > 0) {
            await pages[0].bringToFront();
            break;
          }
        }
      } catch {}
      await browser.close();
      console.log('[main] Shared browser CDP disconnected.');
    } else {
      await browser.close();
      console.log('[main] Browser closed.');
    }
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
    const ctx = await headless.newContext({ locale: 'ja-JP', timezoneId: 'Asia/Tokyo' });
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
  let currentStrategySnapshot = snapshotCurrentStrategyForGame(gameNumber);
  let turn = 0;
  let lastDropTime = 0;
  let consecutiveErrors = 0;
  let waitingLogged = false;
  let waitingCount = 0;
  let calibrated = false;
  let moveCount = 0;
  let roundEnded = false;
  let holdUsedThisTurn = false;
  let lastKnownRank = null;
  let rankingDetected = false;
  let pendingGameOver = null;


  console.log('[game] Game loop started');
  try { writeFileSync('tmp/in_game', String(gameNumber)); } catch {}
  console.log(`[game] Round strategy fixed: game=#${gameNumber}, hash=${currentStrategySnapshot.strategyHash}`);

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
      // ランク追跡
      if (boardState.rank != null) lastKnownRank = boardState.rank;
      console.log(`[game] Turn ${turn}: state=${boardState.state}, pieces=${boardState.pieces.length}, rank=${boardState.rank ?? lastKnownRank ?? '?'}, conf=${boardState.confidence.toFixed(2)}`);

      // ゲームオーバー処理 — ランキング画面を待ってから終了
      if (boardState.state === 'GAMEOVER') {
        console.log(`[game] GAMEOVER at turn ${turn}, waiting for ranking screen...`);
        // ランキング画面が表示されるまで最大15秒待つ
        for (let rk = 0; rk < 12; rk++) {
          await sleep(1200);
          const rkScreenshot = join(SCREENSHOT_DIR, `ranking_wait_${rk}.png`);
          try {
            await page.screenshot({ path: rkScreenshot });
            const { detectRankingScreen } = await loadModule('./screenshot_analyzer.mjs');
            const rkResult = await detectRankingScreen(rkScreenshot);
            if (rkResult != null) {
              const rkPath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
              const { copyFileSync } = await import('fs');
              try { copyFileSync(rkScreenshot, rkPath); } catch {}
              if (rkResult > 0) lastKnownRank = rkResult;
              console.log(`[game] RANKING screen detected after GAMEOVER! rank=${rkResult > 0 ? rkResult : 'pending OCR'}`);
              break;
            }
          } catch {}
        }
        if (!boardState.rank && lastKnownRank) boardState.rank = lastKnownRank;
        console.log(`[game] Final rank=${boardState.rank ?? '?'}`);
        await handleGameOver(page, gameNumber, turn, boardState, historyFile, currentStrategySnapshot);
        try { unlinkSync('tmp/in_game'); } catch {}
        return;
      }

      // 待機画面 (ランキング/接続中/タイトル画面)
      if (boardState.state === 'WAITING') {
        waitingCount++;

        // WAITING中: 毎フレームでランキング画面を検出し、スクショを上書き保存
        // (最初のフレームは遷移中の場合があるため、最後に検出したフレームが最も正確)
        if (!roundEnded) {
          try {
            const { detectRankingScreen } = await loadModule('./screenshot_analyzer.mjs');
            const rankResult = await detectRankingScreen(screenshotPath);
            if (rankResult != null) {
              // ランキング画面スクショを上書き保存（後のフレームほど完全なランキング表示）
              const rkPath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
              try { copyFileSync(screenshotPath, rkPath); } catch {}
              // rankResult > 0 なら正確な値で確定、-1 は星なし(late pathで再試行)
              if (rankResult > 0) {
                lastKnownRank = rankResult;
                if (!rankingDetected) {
                  console.log(`[game] RANKING screen detected! rank=${rankResult}`);
                }
                rankingDetected = true;
              } else if (!rankingDetected) {
                console.log(`[game] RANKING screen detected (star not yet visible)`);
              }
            }
          } catch (e) {
            if (!e.message?.includes('is not a function')) {
              console.log(`[game] Ranking detection error: ${e.message}`);
            }
          }
        }

        // ラウンド終了判定: ゲーム中 (turn>5) に連続6回以上WAITINGが続いたらラウンド終了
        // (ランキング画面が完全に表示されるまで待つため、3→6に増加)
        if (turn > 5 && waitingCount >= 6 && !roundEnded) {
          console.log(`[game] Round ended at turn ${turn}, final rank=${lastKnownRank ?? '?'}`);
          roundEnded = true;
          // 最終順位をboardStateに付与
          if (lastKnownRank) boardState.rank = lastKnownRank;
          // 履歴保存 + AI改善 (非同期 — ゲームループをブロックしない)
          const finishedSnapshot = currentStrategySnapshot;
          pendingGameOver = handleGameOver(page, gameNumber, turn, boardState, historyFile, finishedSnapshot)
            .catch(e => console.error('[game] Post-game error:', e.message))
            .finally(() => { pendingGameOver = null; });
          // 次ラウンド用にリセット
          gameNumber++;
          currentStrategySnapshot = snapshotCurrentStrategyForGame(gameNumber);
          console.log(`[game] Next round strategy fixed: game=#${gameNumber}, hash=${currentStrategySnapshot.strategyHash}`);
          turn = 0;
          calibrated = false;
          moveCount = 0;
          lastKnownRank = null;
          rankingDetected = false;


          // Stop file チェック (外部からの graceful stop 要求)
          if (existsSync('tmp/stop')) {
            console.log(`[game] Stop requested, waiting for pending game data save...`);
            if (pendingGameOver) await pendingGameOver;
            console.log('[game] Exiting gracefully');
            return;
          }
        }

        // ラウンド終了後もランキング画面を検出し続ける (星は遅れて表示される)
        // waitingCount 7-16 の間 (roundEnd後 ~1-10秒) だけ検出を継続
        if (roundEnded && !rankingDetected && waitingCount >= 7 && waitingCount <= 16) {
          try {
            const { detectRankingScreen } = await loadModule('./screenshot_analyzer.mjs');
            const lateRankResult = await detectRankingScreen(screenshotPath);
            if (lateRankResult != null && lateRankResult > 0) {
              rankingDetected = true;
              const prevGameNum = gameNumber - 1;
              // ランキングスクリーンショット保存
              const rkPath = join('tmp/summaries', `ranking_${String(prevGameNum).padStart(4, '0')}.png`);
              try { copyFileSync(screenshotPath, rkPath); } catch {}
              // ゲームサマリーにランクを追記 (星検出はOCRより信頼性が高いため上書き可)
              const summaryPath = join('tmp/summaries', `game_${String(prevGameNum).padStart(4, '0')}.json`);
              if (existsSync(summaryPath)) {
                try {
                  const summary = JSON.parse(readFileSync(summaryPath, 'utf-8'));
                  const prevRank = summary.rank;
                  summary.rank = lateRankResult;
                  writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
                  console.log(`[game] Late ranking detection: game #${prevGameNum} rank=${lateRankResult}${prevRank != null ? ` (was ${prevRank})` : ''}`);
                } catch {}
              }
            }
          } catch {}
        }

        // Stop file チェック (ラウンド間での安全な停止)
        if (existsSync('tmp/stop') && turn <= 1) {
          if (pendingGameOver) await pendingGameOver;
          console.log('[game] Stop requested between rounds, exiting');
          return;
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
      const { decide } = await loadStrategy(`./${currentStrategySnapshot.snapshotPath}`);
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
      try { writeFileSync('tmp/in_game', String(gameNumber)); } catch {}
      consecutiveErrors = 0;

      // 試合中コメント: 20ターンごとに生成 (非同期、ゲームをブロックしない)
      if (turn > 0 && turn % 20 === 0) {
        (async () => {
          try {
            const { generateMidgameComment } = await loadModule('./comment.mjs');
            await generateMidgameComment(gameNumber, turn, boardState);
          } catch (err) {
            console.log(`[game] Midgame comment error: ${err.message}`);
          }
        })();
      }

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
async function handleGameOver(page, gameNumber, turns, finalState, historyFile, strategySnapshot) {
  // ゲーム別スクリーンショットをアーカイブ (3枚: 序盤/中盤/終盤)
  // 同期処理 — 次ラウンドのスクリーンショット上書き前に完了する
  const gameScreenshotDir = join('tmp/game_screenshots', `game_${String(gameNumber).padStart(4, '0')}`);
  try {
    mkdirSync(gameScreenshotDir, { recursive: true });
    const ssFiles = readdirSync(SCREENSHOT_DIR)
      .filter(f => f.startsWith('turn_') && f.endsWith('.png')).sort();
    if (ssFiles.length > 0) {
      const earlyIdx = Math.min(2, ssFiles.length - 1);
      const midIdx = Math.floor(ssFiles.length / 2);
      const lateIdx = ssFiles.length - 1;
      for (const idx of [...new Set([earlyIdx, midIdx, lateIdx])]) {
        copyFileSync(
          join(SCREENSHOT_DIR, ssFiles[idx]),
          join(gameScreenshotDir, ssFiles[idx])
        );
      }
      console.log(`[game] Archived ${[...new Set([earlyIdx, midIdx, lateIdx])].length} screenshots to ${gameScreenshotDir}`);
    }
  } catch (e) {
    console.log(`[game] Screenshot archive failed: ${e.message}`);
  }

  // 古いゲームスクリーンショット削除 (最新24ゲーム分のみ保持)
  try {
    const gameSSDirs = readdirSync('tmp/game_screenshots').filter(d => d.startsWith('game_')).sort();
    for (const d of gameSSDirs.slice(0, Math.max(0, gameSSDirs.length - 24))) {
      const dirPath = join('tmp/game_screenshots', d);
      readdirSync(dirPath).forEach(f => unlinkSync(join(dirPath, f)));
      rmdirSync(dirPath);
    }
  } catch {}

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

  const strategyHash = strategySnapshot?.strategyHash
    || computeStrategyHashFromFile(strategySnapshot?.snapshotPath || 'strategy.mjs');

  const rankingImagePath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
  let resultScreenOcr = null;
  if (existsSync(rankingImagePath)) {
    try {
      const { analyzeResultScreen } = await loadModule('./result_screen_ocr.mjs');
      resultScreenOcr = await analyzeResultScreen(rankingImagePath);
    } catch (err) {
      console.log(`[game] Result OCR failed: ${err.message}`);
    }
  }

  const detectedRank = (finalState.rank && finalState.rank > 0)
    ? finalState.rank
    : (resultScreenOcr?.rank && resultScreenOcr.rank > 0 ? resultScreenOcr.rank : null);

  // ゲームサマリー保存
  const summary = {
    gameNumber,
    turns,
    rank: detectedRank,
    piecesAtEnd: finalState.pieces.length,
    strategyHash: strategyHash || null,
    timestamp: new Date().toISOString(),
  };
  if (resultScreenOcr && (resultScreenOcr.rank != null || (resultScreenOcr.lines || []).length > 0)) {
    summary.resultScreenOcr = {
      imagePath: resultScreenOcr.imagePath || null,
      rank: resultScreenOcr.rank ?? null,
      lines: (resultScreenOcr.lines || []).slice(0, 8),
    };
  }
  const summaryPath = join('tmp/summaries', `game_${String(gameNumber).padStart(4, '0')}.json`);
  writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  console.log(`[game] Summary: turns=${turns}, rank=${summary.rank}, hash=${strategyHash}`);

  // ランキング画面コメント生成 (非同期、ゲームループをブロックしない)
  if (existsSync(rankingImagePath)) {
    (async () => {
      try {
        const { generateRankingComment } = await loadModule('./comment.mjs');
        await generateRankingComment(rankingImagePath, gameNumber, detectedRank);
      } catch (err) {
        console.log(`[game] Ranking comment error: ${err.message}`);
      }
    })();
  }

  try {
    const lineageResult = recordCompletedGame({
      strategySnapshotPath: strategySnapshot?.snapshotPath || '',
      strategyHash,
      summary,
      archivePath,
      summaryPath,
    });
    console.log(`[lineage] ${lineageResult.status}: hash=${lineageResult.strategyHash || 'n/a'}`);
  } catch (err) {
    console.log(`[lineage] update failed: ${err.message}`);
  }

  // 外部制御モード: 内蔵改善をスキップ (親プロセスが soren91_improve() で管理)
  if (process.env.SOREN91_EXTERNAL_IMPROVE === '1') {
    console.log(`[game] External improvement mode, skipping internal for game #${gameNumber}`);
    return;
  }

  const { interval: improvementIntervalGames, source: improvementIntervalSource } = loadImprovementSchedule();
  if (gameNumber % improvementIntervalGames !== 0) {
    console.log(`[game] Skipping improvement for game #${gameNumber} (runs every ${improvementIntervalGames} games via ${improvementIntervalSource})`);
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

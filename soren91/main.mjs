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
import { execSync, execFile } from 'child_process';
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

// --- シグナルハンドラ ---
['SIGINT', 'SIGTERM'].forEach(sig => {
  process.on(sig, () => {
    console.log(`[main] Received ${sig}, setting stop flag for graceful exit...`);
    try {
      writeFileSync('tmp/stop', '');
    } catch (e) {
      console.error(`[main] Failed to write tmp/stop upon ${sig}:`, e);
    }

    if (shutdownTimer) {
      console.log(`[main] Received ${sig} again, forcing immediate cleanup`);
      void cleanupRuntime(`signal:${sig}`).finally(() => process.exit(0));
      return;
    }

    shutdownTimer = setTimeout(() => {
      console.log(`[main] Graceful stop timed out after ${sig}, forcing cleanup`);
      void cleanupRuntime(`signal-timeout:${sig}`).finally(() => process.exit(0));
    }, 5000);
    shutdownTimer.unref?.();
  });
});

// --- 定数 ---
const GAME_URL = 'https://unityroom.com/games/sorengame91';
const PLAYER_NAME = 'DoCiAI:US';
const SCREENSHOT_DIR = 'tmp/screenshots';
const HISTORY_DIR = 'game_history';
const DROP_COOLDOWN_MS = 1200; // ドロップ間の最低待機時間 (ゲーム側クールダウン≈1秒)
const POLL_INTERVAL_MS = 200;  // 状態チェック間隔
const MOVE_TIMEOUT_MS = 30000; // MOVE待ちタイムアウト
const CALIBRATION_MIN_CONFIDENCE = 0.55;
const CALIBRATION_MIN_PIECES = 3;
const DEFAULT_IMPROVEMENT_INTERVAL_GAMES = 12;
const DEFAULT_AUDIO_GAIN_MULTIPLIER = 0.70;
const DEFAULT_SHARED_CDP_PORT = 9222;
const DEFAULT_CHROME_AUDIO_OUTPUT_LABEL = 'BlackHole 2ch';
const ENV_PATH = '.env';
const RUNTIME_CONFIG_PATH = 'runtime_config.json';
const SOREN91_MODE_FLAG_FILE = join(dirname(fileURLToPath(import.meta.url)), '..', 'tmp', '.soren91_mode_active');
const SOREN91_MAIN_PID_FILE = 'tmp/main.pid';
let activeBrowser = null;
let activeContext = null;
let activeGamePage = null;
let activeIsSharedMode = false;
let activeOwnsContext = false;
let shutdownTimer = null;
let cleanupPromise = null;
const rankingCommentQueuedGames = new Set();
const rankingCommentInFlightGames = new Set();

// ディレクトリ確保
[SCREENSHOT_DIR, HISTORY_DIR, 'tmp/summaries', 'strategy_versions', 'tmp/strategy_snapshots', 'tmp/state', 'tmp/game_screenshots'].forEach(dir => {
  mkdirSync(dir, { recursive: true });
});

function clearSoren91ModeFlag() {
  try {
    unlinkSync(SOREN91_MODE_FLAG_FILE);
  } catch {}
}

function isSoren91GameUrl(url) {
  return typeof url === 'string' && (
    url.includes('sorengame91') ||
    url.includes('play.unityroom.com')
  );
}

async function closeSharedSoren91Pages(browser, preferredPage = null) {
  const pagesToClose = [];
  const seen = new Set();

  if (preferredPage) {
    pagesToClose.push(preferredPage);
    seen.add(preferredPage);
  }

  for (const ctx of browser.contexts()) {
    for (const page of ctx.pages()) {
      if (seen.has(page)) continue;
      let url = '';
      try {
        url = page.url();
      } catch {}
      if (!isSoren91GameUrl(url)) continue;
      pagesToClose.push(page);
      seen.add(page);
    }
  }

  for (const page of pagesToClose) {
    try {
      if (!page.isClosed()) {
        await page.close({ runBeforeUnload: false });
      }
    } catch (err) {
      console.log(`[main] Failed to close soren91 shared tab: ${err.message}`);
    }
  }
}

async function cleanupRuntime(reason = 'normal') {
  if (cleanupPromise) {
    return cleanupPromise;
  }

  cleanupPromise = (async () => {
    if (shutdownTimer) {
      clearTimeout(shutdownTimer);
      shutdownTimer = null;
    }

    const browser = activeBrowser;
    const context = activeContext;
    const gamePage = activeGamePage;
    const isSharedMode = activeIsSharedMode;
    const ownsContext = activeOwnsContext;

    activeBrowser = null;
    activeContext = null;
    activeGamePage = null;
    activeIsSharedMode = false;
    activeOwnsContext = false;

    try { unlinkSync('tmp/in_game'); } catch {}
    try { unlinkSync(SOREN91_MAIN_PID_FILE); } catch {}
    clearSoren91ModeFlag();

    if (!browser) return;

    try {
      if (isSharedMode) {
        if (!ownsContext && gamePage && !gamePage.isClosed()) {
          try {
            await gamePage.close();
          } catch (err) {
            console.log(`[main] Preferred game tab close failed during ${reason}: ${err.message}`);
          }
        }
        await closeSharedSoren91Pages(browser, gamePage);
        if (ownsContext && context) {
          try {
            await context.close();
          } catch (err) {
            console.log(`[main] Shared context close failed during ${reason}: ${err.message}`);
          }
        }
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
        // soren91 is a GUEST on soviet_local's shared Chrome (connected via
        // connectOverCDP). browser.close() on a CDP-connected browser closes
        // the whole Chrome — which kills soviet_local's page, leaving the local
        // game muted (its resume page.evaluate throws) and the bridge wedged.
        // Do NOT close the shared browser; just drop our CDP connection.
        console.log(`[main] Shared browser left running for owner (${reason}); soren91 detached.`);
      } else {
        await browser.close();
        console.log(`[main] Browser closed (${reason}).`);
      }
    } catch (err) {
      console.log(`[main] Browser cleanup failed during ${reason}: ${err.message}`);
    }
  })();

  return cleanupPromise;
}

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

async function queueRankingCommentOnce(gameNumber, detectedRank, reason = 'post-game') {
  if (rankingCommentQueuedGames.has(gameNumber) || rankingCommentInFlightGames.has(gameNumber)) {
    console.log(`[game] Ranking comment already queued/in-flight for game #${gameNumber} (${reason})`);
    return false;
  }

  const rankingImagePath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
  if (detectedRank == null && !existsSync(rankingImagePath)) {
    console.log(`[game] Ranking comment deferred for game #${gameNumber}: no context yet (${reason})`);
    return false;
  }

  rankingCommentInFlightGames.add(gameNumber);
  try {
    const { generateRankingComment } = await loadModule('./comment.mjs');
    const imagePath = existsSync(rankingImagePath) ? rankingImagePath : null;
    const comment = await generateRankingComment(imagePath, gameNumber, detectedRank);
    if (comment) {
      rankingCommentQueuedGames.add(gameNumber);
      return true;
    }
  } catch (err) {
    console.log(`[game] Ranking comment error for game #${gameNumber} (${reason}): ${err.message}`);
  } finally {
    rankingCommentInFlightGames.delete(gameNumber);
  }
  return false;
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

function loadChromeAudioOutputLabel() {
  return process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || DEFAULT_CHROME_AUDIO_OUTPUT_LABEL;
}

async function grantSpeakerSelection(page, gameUrl) {
  try {
    const origin = new URL(gameUrl).origin;
    const cdpSession = await page.context().newCDPSession(page);
    await cdpSession.send('Browser.grantPermissions', {
      origin,
      permissions: ['speakerSelection', 'audioCapture'],
    });
    console.log(`[main] Granted speakerSelection for ${origin}`);
  } catch (err) {
    console.log(`[main] Failed to grant speakerSelection: ${err.message}`);
  }
}

async function installAudioOutputRouter(page, audioOutputLabel) {
  if (!audioOutputLabel) return;

  await page.addInitScript((label) => {
    globalThis.__soren91AudioOutputLabel = label;
    globalThis.__soren91AudioOutputDeviceId = '';
    globalThis.__soren91AudioOutputError = '';
    globalThis.__soren91AudioContexts = [];

    globalThis.__soren91RouteAudioOutput = async (nextLabel = globalThis.__soren91AudioOutputLabel) => {
      if (!nextLabel || !navigator.mediaDevices?.enumerateDevices) return false;
      const AudioContextCtor = globalThis.AudioContext || globalThis.webkitAudioContext;
      if (!AudioContextCtor?.prototype?.setSinkId) {
        globalThis.__soren91AudioOutputError = 'AudioContext.setSinkId is unavailable';
        return false;
      }

      const devices = await navigator.mediaDevices.enumerateDevices();
      const target = devices.find(device =>
        device.kind === 'audiooutput' &&
        device.label &&
        device.label.toLowerCase().includes(String(nextLabel).toLowerCase())
      );
      if (!target) {
        globalThis.__soren91AudioOutputError = `audio output not found: ${nextLabel}`;
        return false;
      }

      globalThis.__soren91AudioOutputDeviceId = target.deviceId;
      const currentContexts = globalThis.__soren91AudioContexts || [];
      const alreadyRouted = currentContexts.length > 0 && currentContexts.every(ctx =>
        !ctx || typeof ctx.sinkId === 'undefined' || ctx.sinkId === target.deviceId
      );
      const allRunning = currentContexts.every(ctx => !ctx || ctx.state === 'running');
      if (alreadyRouted && allRunning) return true;
      for (const ctx of globalThis.__soren91AudioContexts || []) {
        if (typeof ctx.setSinkId !== 'function') continue;
        try {
          if (ctx.state === 'suspended' && ctx.sinkId) {
            await ctx.setSinkId('');
            try { ctx.resume().catch(() => {}); } catch (_) {}
            await new Promise(r => setTimeout(r, 250));
          }
          await ctx.setSinkId(target.deviceId);
          if (ctx.state === 'suspended') {
            try { ctx.resume().catch(() => {}); } catch (_) {}
          }
        } catch (err) {
          globalThis.__soren91AudioOutputError = err && err.message ? err.message : String(err);
          return false;
        }
      }
      globalThis.__soren91AudioOutputError = '';
      return true;
    };

    const OriginalAudioContext = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (OriginalAudioContext && !globalThis.__soren91AudioOutputPatched) {
      const WrappedAudioContext = function(...args) {
        const ctx = new OriginalAudioContext(...args);
        globalThis.__soren91AudioContexts.push(ctx);
        globalThis.__soren91RouteAudioOutput().catch(err => {
          globalThis.__soren91AudioOutputError = err && err.message ? err.message : String(err);
        });
        return ctx;
      };
      WrappedAudioContext.prototype = OriginalAudioContext.prototype;
      globalThis.AudioContext = WrappedAudioContext;
      if (globalThis.webkitAudioContext) globalThis.webkitAudioContext = WrappedAudioContext;
      Object.defineProperty(globalThis, '__soren91AudioOutputPatched', {
        value: true,
        configurable: true,
      });
    }

    if (!globalThis.__soren91AudioOutputWatchdogInstalled) {
      globalThis.__soren91AudioOutputWatchdogInstalled = true;
      setInterval(() => {
        globalThis.__soren91RouteAudioOutput?.().catch(err => {
          globalThis.__soren91AudioOutputError = err && err.message ? err.message : String(err);
        });
      }, 5000);
    }
  }, audioOutputLabel);
}

async function reportAudioOutputRoute(page, audioOutputLabel) {
  if (!audioOutputLabel) return;
  try {
    const audioRoute = await page.evaluate(async (label) => {
      const routed = await globalThis.__soren91RouteAudioOutput?.(label);
      return {
        routed: Boolean(routed),
        label,
        deviceId: globalThis.__soren91AudioOutputDeviceId || '',
        error: globalThis.__soren91AudioOutputError || '',
        contexts: Array.isArray(globalThis.__soren91AudioContexts) ? globalThis.__soren91AudioContexts.length : 0,
      };
    }, audioOutputLabel);
    console.log('[main] Chrome audio route:', JSON.stringify(audioRoute));
  } catch (err) {
    console.log(`[main] Failed to route Chrome audio: ${err.message}`);
  }
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

async function openSharedBrowserTab(context, anchorPage = null) {
  if (anchorPage && !anchorPage.isClosed()) {
    try {
      const popupPromise = context.waitForEvent('page', { timeout: 5000 });
      await anchorPage.evaluate(() => {
        window.open('about:blank', '_blank');
      });
      const page = await popupPromise;
      await page.waitForLoadState('domcontentloaded', { timeout: 5000 }).catch(() => {});
      return page;
    } catch (err) {
      console.log(`[main] Failed to open shared browser tab from anchor page: ${err.message}`);
    }
  }

  return await context.newPage();
}

async function gotoGamePageWithRecovery({ page, context, gameUrl, isSharedMode, ownsContext, audioGainMultiplier, audioOutputLabel, anchorPage = null }) {
  try {
    await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    return page;
  } catch (err) {
    if (!isSharedMode || ownsContext || !String(err?.message || '').includes('net::ERR_ABORTED')) {
      throw err;
    }

    console.log('[main] Shared tab navigation aborted; retrying with a fresh CDP tab');
    try {
      if (!page.isClosed()) await page.close({ runBeforeUnload: false });
    } catch (closeErr) {
      console.log(`[main] Failed to close aborted shared tab: ${closeErr.message}`);
    }

    const retryAnchorPage = anchorPage && !anchorPage.isClosed()
      ? anchorPage
      : chooseSharedBrowserAnchorPage(context);
    const retryPage = await openSharedBrowserTab(context, retryAnchorPage);
    activeGamePage = retryPage;
    try {
      await retryPage.setViewportSize({ width: 1280, height: 720 });
    } catch {}
    await installAudioGainLimiter(retryPage, audioGainMultiplier);
    await grantSpeakerSelection(retryPage, gameUrl);
    await installAudioOutputRouter(retryPage, audioOutputLabel);
    await retryPage.route('**/*play.unityroom.com/**', async route => {
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
    await retryPage.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
    return retryPage;
  }
}

// --- メイン ---
async function main() {
  console.log('[main] 同志AI 起動...');
  const audioGainMultiplier = loadAudioGainMultiplier();
  const audioOutputLabel = loadChromeAudioOutputLabel();
  console.log(`[main] soren91 audio gain multiplier=${audioGainMultiplier}`);
  console.log(`[main] soren91 Chrome audio output label=${audioOutputLabel}`);
  try {
    writeFileSync(SOREN91_MAIN_PID_FILE, String(process.pid));
  } catch (err) {
    console.log(`[main] Failed to write main pid file: ${err.message}`);
  }

  // soren91 モード開始フラグを立てる
  // say_enqueue.sh はこのフラグがある場合、古い soren91:ranking_comment の再生をスキップする
  try {
    writeFileSync(SOREN91_MODE_FLAG_FILE, String(Date.now()));
    console.log('[main] soren91 mode flag set');
  } catch (err) {
    console.log(`[main] Failed to set soren91 mode flag: ${err.message}`);
  }

  // Step 1: トップページHTMLからゲームURLを取得
  console.log('[main] Fetching game URL...');
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

  activeBrowser = browser;
  activeContext = context;
  activeIsSharedMode = isSharedMode;
  activeOwnsContext = ownsContext;

  let gamePage = null;
  try {
    if (isSharedMode && !ownsContext) {
      console.log('[main] Closing stale soren91 shared tabs before launch...');
      await closeSharedSoren91Pages(browser);
    }

    // ゲームURLに直接遷移 + HTML intercept で unityInstance 取得
    const anchorPage = (isSharedMode && !ownsContext)
      ? chooseSharedBrowserAnchorPage(context)
      : null;
    gamePage = (isSharedMode && !ownsContext)
      ? await openSharedBrowserTab(context, anchorPage)
      : await context.newPage();
    if (isSharedMode) {
      try {
        await gamePage.setViewportSize({ width: 1280, height: 720 });
      } catch {}
    }
    activeGamePage = gamePage;
    await installAudioGainLimiter(gamePage, audioGainMultiplier);
    await grantSpeakerSelection(gamePage, gameUrl);
    await installAudioOutputRouter(gamePage, audioOutputLabel);
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
    gamePage = await gotoGamePageWithRecovery({
      page: gamePage,
      context,
      gameUrl,
      isSharedMode,
      ownsContext,
      audioGainMultiplier,
      audioOutputLabel,
      anchorPage,
    });
    if (isSharedMode) {
      await gamePage.bringToFront();
    }

    // Unity canvas ロード待機
    console.log('[main] Waiting for Unity canvas...');
    await gamePage.waitForSelector('canvas', { timeout: 60000 });
    console.log('[main] Canvas found, waiting for Unity to fully load...');
    await reportAudioOutputRoute(gamePage, audioOutputLabel);
    // Unityロード完了をポーリングで待つ (ローディングバーが非表示になるまで)
    for (let i = 0; i < 60; i++) {
      await sleep(1000);
      const loaded = await gamePage.evaluate(() => {
        const bar = document.getElementById('unity-loading-bar');
        return !bar || bar.style.display === 'none';
      });
      if (loaded) {
        console.log(`[main] Unity loaded after ${i + 1}s`);
        break;
      }
    }
    await sleep(3000); // 追加バッファ

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
        provisional: true,
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
    await cleanupRuntime('main-finally');
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
  await clickCanvasPoint(page, nameFieldX, nameFieldY, 'name field');
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
  await clickCanvasPoint(page, playButtonX, playButtonY, 'PLAY button');
  await sleep(2000);

  console.log('[main] Title screen done, game should be starting...');
}

async function clickCanvasPoint(page, x, y, label = 'canvas point') {
  const timeoutMs = Number(process.env.SOREN91_CLICK_TIMEOUT_MS || 2500);
  try {
    await Promise.race([
      page.mouse.click(x, y),
      sleep(timeoutMs).then(() => {
        throw new Error(`page.mouse.click timeout after ${timeoutMs}ms`);
      }),
    ]);
    return;
  } catch (err) {
    console.log(`[main] Mouse click fallback for ${label}: ${err.message}`);
  }

  const session = await page.context().newCDPSession(page);
  try {
    await session.send('Input.dispatchMouseEvent', {
      type: 'mousePressed',
      x,
      y,
      button: 'left',
      clickCount: 1,
    });
    await sleep(80);
    await session.send('Input.dispatchMouseEvent', {
      type: 'mouseReleased',
      x,
      y,
      button: 'left',
      clickCount: 1,
    });
  } finally {
    await session.detach().catch(() => {});
  }
}

async function recoverFromConnectionError(page) {
  const canvas = await page.$('canvas');
  if (!canvas) throw new Error('Canvas not found on connection error screen');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Canvas bounding box not available');

  const okX = box.x + Math.floor(box.width * 0.5);
  const okY = box.y + Math.floor(box.height * 0.515);
  console.log(`[main] Clicking connection error OK at (${okX.toFixed(0)}, ${okY.toFixed(0)})`);
  await page.mouse.click(okX, okY);
  await sleep(1500);

  try {
    await handleTitleScreen(page);
  } catch (err) {
    console.log(`[main] Title re-entry after connection error failed: ${err.message}; reloading`);
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForSelector('canvas', { timeout: 60000 });
    await sleep(3000);
    await handleTitleScreen(page);
  }
}

/**
 * トップページHTMLからゲームURLを取得する。
 * URL抽出だけに headless Chromium を起動すると、macOS の Mach port 権限で落ちることがある。
 */
async function fetchGameUrl() {
  const response = await fetch(GAME_URL, {
    headers: {
      'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'accept-language': 'ja,en-US;q=0.9,en;q=0.8',
      'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36',
    },
  });
  if (!response.ok) {
    throw new Error(`Game URL page fetch failed: HTTP ${response.status}`);
  }

  const html = await response.text();
  const match = html.match(/(?:src|href)=["']([^"']*play\.unityroom\.com[^"']*)["']/i);
  if (!match?.[1]) throw new Error('Game URL not found on page');
  return new URL(match[1].replace(/&amp;/g, '&'), GAME_URL).href;
}

/**
 * メインゲームループ
 */
async function gameLoop(page, calibration, gameNumber) {
  let historyFile = join(HISTORY_DIR, `latest_${String(gameNumber).padStart(4, '0')}.jsonl`);
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
  let midgameCommentSent = false;


  console.log('[game] Game loop started');
  try { writeFileSync('tmp/in_game', String(gameNumber)); } catch {}
  console.log(`[game] Round strategy fixed: game=#${gameNumber}, hash=${currentStrategySnapshot.strategyHash}`);

  while (true) {
    try {
      if (existsSync('tmp/stop')) {
        if (pendingGameOver) await pendingGameOver;
        console.log('[game] Stop requested, exiting main loop');
        return;
      }

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

      // soren91 では GAMEOVER を独立終了イベントにせず、
      // WAITING/ランキング遷移として既存のラウンド終了ロジックに流す。
      if (boardState.state === 'GAMEOVER') {
        console.log(`[game] GAMEOVER detected at turn ${turn}, treating as WAITING transition`);
        boardState.state = 'WAITING';
      }

      // 待機画面 (ランキング/接続中/タイトル画面)
      if (boardState.state === 'WAITING') {
        waitingCount++;

        try {
          const { detectConnectionErrorScreen } = await loadModule('./screenshot_analyzer.mjs');
          if (await detectConnectionErrorScreen(screenshotPath)) {
            console.log(`[game] Connection error screen detected; abandoning game #${gameNumber} without ranking comment`);
            await recoverFromConnectionError(page);
            if (turn > 0 || existsSync(historyFile)) {
              try { unlinkSync(historyFile); } catch {}
              gameNumber++;
              historyFile = join(HISTORY_DIR, `latest_${String(gameNumber).padStart(4, '0')}.jsonl`);
              currentStrategySnapshot = snapshotCurrentStrategyForGame(gameNumber);
              console.log(`[game] Next round strategy fixed after reconnect: game=#${gameNumber}, hash=${currentStrategySnapshot.strategyHash}`);
            }
            turn = 0;
            calibrated = false;
            moveCount = 0;
            lastKnownRank = null;
            rankingDetected = false;
            roundEnded = false;
            waitingCount = 0;
            waitingLogged = false;
            holdUsedThisTurn = false;
            midgameCommentSent = false;
            await sleep(1000);
            continue;
          }
        } catch (e) {
          console.log(`[game] Connection error detection failed: ${e.message}`);
        }

        // WAITING中: 毎フレームでランキング画面を検出し、スクショを上書き保存
        // (最初のフレームは遷移中の場合があるため、最後に検出したフレームが最も正確)
        if (!roundEnded) {
          try {
            const { detectRankingScreen } = await loadModule('./screenshot_analyzer.mjs');
            const rankResult = await detectRankingScreen(screenshotPath);
            if (rankResult != null) {
              const rkPath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
              // rankResult > 0 なら正確な値で確定、-1 は星なし(late pathで再試行)
              if (rankResult > 0) {
                // 確定順位つきのフレームは最も価値が高いので保存する
                try { copyFileSync(screenshotPath, rkPath); } catch {}
                lastKnownRank = rankResult;
                if (!rankingDetected) {
                  console.log(`[game] RANKING screen detected! rank=${rankResult}`);
                }
                rankingDetected = true;
              } else if (!rankingDetected) {
                // 不完全なランキング候補は最初の1枚だけ残す。
                // 後続の白フェード/遷移フレームで有用な画像を上書きしない。
                if (!existsSync(rkPath)) {
                  try { copyFileSync(screenshotPath, rkPath); } catch {}
                }
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
          // historyFileを新ゲーム番号で作成 (handleGameOverと競合しないよう)
          const nextHistoryFile = join(HISTORY_DIR, `latest_${String(gameNumber + 1).padStart(4, '0')}.jsonl`);
          gameNumber++;
          historyFile = nextHistoryFile;
          currentStrategySnapshot = snapshotCurrentStrategyForGame(gameNumber);
          console.log(`[game] Next round strategy fixed: game=#${gameNumber}, hash=${currentStrategySnapshot.strategyHash}`);
          turn = 0;
          calibrated = false;
          moveCount = 0;
          lastKnownRank = null;
          rankingDetected = false;
          midgameCommentSent = false;


          // 定時ラジオチェック (親プロジェクトの時刻ベースコーナーをメリケンAIペルソナで実行)
          try {
            const prevGameNum = gameNumber - 1;
            const radioProc = execFile('/bin/bash', [join(dirname(fileURLToPath(import.meta.url)), 'radio_bridge.sh'), String(prevGameNum), '0'], {
              cwd: dirname(fileURLToPath(import.meta.url)),
            }, (err) => {
              if (err && err.killed) console.log('[radio] bridge killed');
              else if (err) console.log(`[radio] bridge error: ${err.message}`);
            });
            radioProc.unref(); // Node.js の終了を妨げない
          } catch (e) {
            console.log(`[radio] bridge launch error: ${e.message}`);
          }

          // Stop file チェック (外部からの graceful stop 要求)
          if (existsSync('tmp/stop')) {
            console.log(`[game] Stop requested, waiting for pending game data save...`);
            if (pendingGameOver) await pendingGameOver;
            console.log('[game] Exiting gracefully');
            return;
          }
        }

        // ラウンド終了後もランキング画面を検出し続ける。
        // 結果表示がMATCHING画面の後にかなり遅れて出ることがあるため、次ゲーム開始まで広めに見る。
        if (roundEnded && !rankingDetected && waitingCount >= 7 && waitingCount <= 180) {
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
              void queueRankingCommentOnce(prevGameNum, lateRankResult, 'late-ranking-detection');
            }
          } catch {}
        }

        // Stop file チェック (ラウンド間での安全な停止)
        if (existsSync('tmp/stop')) {
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
        const shouldAcceptForCalibration =
          (boardState.pieces?.length ?? 0) >= CALIBRATION_MIN_PIECES &&
          (
            (boardState.confidence ?? 0) >= CALIBRATION_MIN_CONFIDENCE ||
            calibration?.provisional === true
          );
        if (shouldAcceptForCalibration) {
          moveCount++;
        } else {
          moveCount = 0;
        }
        if (moveCount >= 3) { // 十分な信頼度と盤面密度で3回連続MOVEなら安定と判断
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

      if (existsSync('tmp/stop')) {
        if (pendingGameOver) await pendingGameOver;
        console.log('[game] Stop requested after move, exiting');
        return;
      }

      // 試合中コメント: 1試合1回、20ターン到達後に生成 (非同期、ゲームをブロックしない)
      // pieces < 3 はマッチング画面の誤検出の可能性が高いためスキップ
      if (!midgameCommentSent && turn >= 20 && boardState.pieces.length >= 3) {
        midgameCommentSent = true;
        (async () => {
          try {
            const { generateMidgameComment } = await loadModule('./comment.mjs');
            await generateMidgameComment(gameNumber, turn, boardState, screenshotPath);
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

  // ドロップ: まずマウスをX位置に移動し、ボード上部寄りでクリック
  const { board } = calibration;
  const pixelY = board.top + Math.floor(board.height * 0.18);

  // canvas要素を取得
  const canvas = await page.$('canvas');
  if (!canvas) {
    throw new Error('Canvas not found');
  }

  const box = await canvas.boundingBox();
  if (!box) {
    throw new Error('Canvas bounding box not available');
  }

  const clickX = Math.max(box.x + 4, Math.min(box.x + box.width - 4, pixelX));
  const clickY = Math.max(box.y + 4, Math.min(box.y + box.height - 4, pixelY));
  if (process.env.SOREN91_DEBUG_DROP === '1') {
    console.log(`[game] Drop click: gameX=${gameX.toFixed(2)} pixel=(${clickX.toFixed(0)},${clickY.toFixed(0)}) cal=${calibration.method || 'provisional'}${calibration.provisional ? ':provisional' : ''}`);
  }

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

  // 履歴ファイルをゲーム番号付きから確定版へリネーム保存
  const archivePath = join(HISTORY_DIR, `game_${String(gameNumber).padStart(4, '0')}.jsonl`);
  if (existsSync(historyFile)) {
    try {
      renameSync(historyFile, archivePath);
      console.log(`[game] History saved: ${archivePath}`);
    } catch (e) {
      console.log(`[game] History save failed: ${e.message}`);
    }
  }
  
  // 古い latest_*.jsonl ファイルを削除（ゲーム破損時のリカバリ用）
  try {
    const latestFiles = readdirSync(HISTORY_DIR)
      .filter(f => f.startsWith('latest_') && f.endsWith('.jsonl'))
      .sort();
    for (const f of latestFiles) {
      unlinkSync(join(HISTORY_DIR, f));
    }
  } catch {}

  const strategyHash = strategySnapshot?.strategyHash
    || computeStrategyHashFromFile(strategySnapshot?.snapshotPath || 'strategy.mjs');

  const {
    rankingImagePath,
    resultScreenOcr,
    detectedRank,
  } = await waitForRankingCommentContext(gameNumber, finalState.rank);

  // ゲームサマリー保存
  const summary = {
    gameNumber,
    turns,
    rank: detectedRank,
    piecesAtEnd: finalState.pieces.length,
    strategyHash: strategyHash || null,
    timestamp: new Date().toISOString(),
  };
  if (
    resultScreenOcr
    && (
      resultScreenOcr.rank != null
      || (resultScreenOcr.lines || []).length > 0
      || (resultScreenOcr.playerNames || []).length > 0
    )
  ) {
    summary.resultScreenOcr = {
      imagePath: resultScreenOcr.imagePath || null,
      rank: resultScreenOcr.rank ?? null,
      lines: (resultScreenOcr.lines || []).slice(0, 8),
      playerNames: (resultScreenOcr.playerNames || []).slice(0, 8),
    };
  }
  const summaryPath = join('tmp/summaries', `game_${String(gameNumber).padStart(4, '0')}.json`);
  writeFileSync(summaryPath, JSON.stringify(summary, null, 2));
  console.log(`[game] Summary: turns=${turns}, rank=${summary.rank}, hash=${strategyHash}`);

  // ランキング画面コメント生成 (非同期、ゲームループをブロックしない)
  // 順位またはランキング画面由来の情報がない場合は、接続エラー等の誤検出なので喋らない
  const hasRankingCommentContext = detectedRank != null
    || existsSync(rankingImagePath)
    || Boolean(resultScreenOcr && (
      resultScreenOcr.rank != null
      || (resultScreenOcr.lines || []).length > 0
      || (resultScreenOcr.playerNames || []).length > 0
    ));
  (async () => {
    try {
      if (!hasRankingCommentContext) {
        console.log(`[game] Skipping ranking comment for game #${gameNumber}: ranking context unavailable`);
        return;
      }
      await queueRankingCommentOnce(gameNumber, detectedRank, 'post-game');
    } catch (err) {
      console.log(`[game] Ranking comment error: ${err.message}`);
    }
  })();

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

async function waitForRankingCommentContext(gameNumber, initialRank) {
  const rankingImagePath = join('tmp/summaries', `ranking_${String(gameNumber).padStart(4, '0')}.png`);
  let detectedRank = (initialRank && initialRank > 0) ? initialRank : null;
  let resultScreenOcr = null;

  if (!detectedRank && !existsSync(rankingImagePath)) {
    const timeoutMs = 35000;
    const pollMs = 500;
    const deadline = Date.now() + timeoutMs;
    console.log(`[game] Waiting for ranking context for game #${gameNumber} (up to ${timeoutMs}ms)`);
    while (Date.now() < deadline) {
      if (existsSync(rankingImagePath)) break;
      await sleep(pollMs);
    }
  }

  if (existsSync(rankingImagePath)) {
    try {
      const { analyzeResultScreen } = await loadModule('./result_screen_ocr.mjs');
      resultScreenOcr = await analyzeResultScreen(rankingImagePath);
      if (!detectedRank && resultScreenOcr?.rank && resultScreenOcr.rank > 0) {
        detectedRank = resultScreenOcr.rank;
      }
    } catch (err) {
      console.log(`[game] Result OCR failed: ${err.message}`);
    }
  }

  if (!existsSync(rankingImagePath) && detectedRank == null) {
    console.log(`[game] Ranking context unavailable for game #${gameNumber}`);
  }

  return {
    rankingImagePath,
    resultScreenOcr,
    detectedRank,
  };
}

/**
 * sleep ユーティリティ
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// 実行
main().catch(console.error);

import { chromium } from 'playwright';
import fs from 'fs';
import http from 'http';
import path from 'path';
import crypto from 'crypto';
import { fileURLToPath } from 'url';
import { execFile } from 'child_process';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

function loadDotEnv() {
  const envPath = path.join(__dirname, '.env');
  if (!fs.existsSync(envPath)) return;
  for (const line of fs.readFileSync(envPath, 'utf-8').split(/\n/)) {
    const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
    if (!match) continue;
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    process.env[match[1]] = value;
  }
}

loadDotEnv();

function bridgeLogExit(reason) {
  const line = `[${new Date().toISOString()}] [BRIDGE-EXIT] ${reason}`;
  try { console.error(line); } catch {}
  try { fs.appendFileSync('tmp/soviet_local.exit.log', `${line}\n`); } catch {}
}

process.on('beforeExit', (code) => bridgeLogExit(`beforeExit code=${code}`));
process.on('exit', (code) => bridgeLogExit(`exit code=${code}`));
for (const signal of ['SIGTERM', 'SIGHUP']) {
  process.on(signal, () => {
    bridgeLogExit(`signal ${signal}`);
    process.exit(signal === 'SIGTERM' ? 143 : 129);
  });
}

const BUILD_DIR = 'sorengame/build';
const COMMAND_FILE = 'commands.txt';
const GAME_STATE_PATH = 'game_state.json';
const MUTE_FLAG_FILE = 'tmp/mute_local_bgm';
const AUDIO_HEALTH_FILE = 'tmp/state/local_audio_health.json';
const SERVE_PORT = parseInt(process.env.SOREN_SERVE_PORT || '8080', 10);
const CDP_PORT = parseInt(process.env.SOREN_CDP_PORT || '9222', 10);
const CDP_ENDPOINT_FILE = path.join(__dirname, 'tmp', 'cdp_endpoint.json');
const USER_DATA_DIR = process.env.SOREN_LOCAL_USER_DATA_DIR || path.join(__dirname, 'tmp', 'soviet_local_chromium_profile');
const CHROME_HEADLESS = ['1', 'true', 'yes', 'on'].includes(String(process.env.SOREN_CHROME_HEADLESS || '').toLowerCase());
// Unity WebGL can crash Chrome when AudioContext.setSinkId() is applied to its
// context on some macOS audio graphs. Keep per-context routing opt-in; OBS
// application-audio capture is safer for the live game.
const CHROME_AUDIO_OUTPUT_LABEL = process.env.SOREN_CHROME_AUDIO_OUTPUT_LABEL || '';
// 起動時に音量スライダーを必要な範囲だけ下げる。
// AudioManager: 実音量 = defaultVolume * 0.125 * value (slider int 0..10, 既定3)。
// SE は既定 1.5 (=既定3の半分)。BGM は通常本線では触らず、並列評価などが env で指定する。
const SE_VOLUME_RAW = process.env.SOREN_SE_VOLUME ?? '1.5';
const SE_VOLUME = (SE_VOLUME_RAW === 'off' || SE_VOLUME_RAW === '') ? null : Number(SE_VOLUME_RAW);
const BGM_VOLUME_RAW = process.env.SOREN_BGM_VOLUME ?? 'off';
const BGM_VOLUME = (BGM_VOLUME_RAW === 'off' || BGM_VOLUME_RAW === '') ? null : Number(BGM_VOLUME_RAW);
const UNITY_VOLUME_REAPPLY_MS = parseInt(process.env.SOREN_UNITY_VOLUME_REAPPLY_MS || '5000', 10);
const UNITY_AUDIO_WATCHDOG_MS = parseInt(process.env.SOREN_UNITY_AUDIO_WATCHDOG_MS || '10000', 10);
const UNITY_AUDIO_RECOVER_COOLDOWN_MS = parseInt(process.env.SOREN_UNITY_AUDIO_RECOVER_COOLDOWN_MS || '30000', 10);
const OBS_GAME_SOURCE_NAME = process.env.SOREN_OBS_GAME_SOURCE_NAME || 'sorengame';

function writeJsonAtomic(filePath, data) {
  const tmpPath = `${filePath}.tmp`;
  fs.writeFileSync(tmpPath, JSON.stringify(data));
  fs.renameSync(tmpPath, filePath);
}

function seedChromeTranslatePreferences(userDataDir) {
  const defaultDir = path.join(userDataDir, 'Default');
  const prefPath = path.join(defaultDir, 'Preferences');
  let prefs = {};
  try {
    if (fs.existsSync(prefPath)) {
      prefs = JSON.parse(fs.readFileSync(prefPath, 'utf-8'));
    }
  } catch {
    prefs = {};
  }

  prefs.profile = { ...(prefs.profile || {}), exit_type: 'Normal', exited_clean: true };
  prefs.translate = { ...(prefs.translate || {}), enabled: false };
  prefs.translate_blocked_languages = ['en', 'ja'];
  prefs.translate_site_blacklist = Array.from(new Set([
    ...(
      Array.isArray(prefs.translate_site_blacklist)
        ? prefs.translate_site_blacklist
        : []
    ),
    'localhost',
    '127.0.0.1',
  ]));
  prefs.intl = { ...(prefs.intl || {}), accept_languages: 'ja-JP,ja,en-US,en' };

  fs.mkdirSync(defaultDir, { recursive: true });
  writeJsonAtomic(prefPath, prefs);
}

function chromeAppPathFromExecutable(executablePath) {
  if (process.env.SOREN_CHROME_APP_PATH) return process.env.SOREN_CHROME_APP_PATH;
  const marker = '.app/Contents/MacOS/';
  const idx = executablePath.indexOf(marker);
  if (idx === -1) return '';
  return executablePath.slice(0, idx + '.app'.length);
}

async function waitForCdpBrowser(port, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      return await chromium.connectOverCDP(`http://127.0.0.1:${port}`);
    } catch (err) {
      lastError = err;
      await new Promise(resolve => setTimeout(resolve, 250));
    }
  }
  throw lastError || new Error(`CDP did not become ready on port ${port}`);
}

async function launchPersistentContextWithoutFocus(userDataDir, args) {
  if (process.platform !== 'darwin' || process.env.SOREN_CHROME_NO_FOCUS_LAUNCH === '0') {
    return null;
  }
  if (process.env.SOREN_CHROME_ATTACH_ONLY === '1') {
    const browser = await waitForCdpBrowser(CDP_PORT);
    const context = browser.contexts()[0];
    if (!context) {
      await browser.close().catch(() => {});
      throw new Error('attached Chromium has no default context');
    }
    console.log('[NO-FOCUS] attached to prelaunched Chromium via CDP');
    return { browser, context };
  }
  if (process.env.SOREN_CHROME_FORCE_PLAYWRIGHT_LAUNCH === '1') {
    return null;
  }

  const appPath = chromeAppPathFromExecutable(process.env.SOREN_CHROME_EXECUTABLE_PATH || chromium.executablePath());
  if (!appPath) return null;

  fs.mkdirSync(userDataDir, { recursive: true });
  const openArgs = [
    '-g',
    '-n',
    appPath,
    '--args',
    `--user-data-dir=${userDataDir}`,
    ...args,
  ];

  await new Promise((resolve, reject) => {
    const savedEnv = {
      XDG_CONFIG_HOME: process.env.XDG_CONFIG_HOME,
      XDG_CACHE_HOME: process.env.XDG_CACHE_HOME,
      PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH,
    };
    delete process.env.XDG_CONFIG_HOME;
    delete process.env.XDG_CACHE_HOME;
    delete process.env.PLAYWRIGHT_BROWSERS_PATH;
    execFile('/usr/bin/open', openArgs, (err) => {
      for (const [key, value] of Object.entries(savedEnv)) {
        if (typeof value === 'undefined') delete process.env[key];
        else process.env[key] = value;
      }
      if (err) reject(err);
      else resolve();
    });
  });

  const browser = await waitForCdpBrowser(CDP_PORT);
  const context = browser.contexts()[0];
  if (!context) {
    await browser.close().catch(() => {});
    throw new Error('background-launched Chromium has no default context');
  }
  console.log('[NO-FOCUS] Chromium launched in background via macOS open -g');
  return { browser, context };
}

function browserLaunchEnv(userDataDir) {
  const env = { ...process.env };
  const homeDir = env.SOREN_CHROME_HOME || env.HOME || '';
  const configHome = env.XDG_CONFIG_HOME || (homeDir ? path.join(homeDir, '.config') : '');
  const cacheHome = env.XDG_CACHE_HOME || (homeDir ? path.join(homeDir, '.cache') : '');
  const tmpDir = env.TMPDIR || path.join(userDataDir, 'tmp');
  for (const dir of [homeDir, configHome, cacheHome, tmpDir]) {
    if (dir) fs.mkdirSync(dir, { recursive: true });
  }
  if (homeDir) env.HOME = homeDir;
  if (configHome) env.XDG_CONFIG_HOME = configHome;
  if (cacheHome) env.XDG_CACHE_HOME = cacheHome;
  env.TMPDIR = tmpDir;
  return env;
}

function sha256Base64(text) {
  return crypto.createHash('sha256').update(text).digest('base64');
}

async function connectObs() {
  const port = process.env.OBS_WEBSOCKET_PORT;
  const password = process.env.OBS_WEBSOCKET_PASSWORD;
  if (!port || !password || typeof WebSocket !== 'function') return null;

  const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
  const url = `ws://${host}:${Number(port)}`;
  const ws = new WebSocket(url);
  let hello = null;
  let ready = false;
  let requestSeq = 0;
  const pending = new Map();

  ws.addEventListener('message', (event) => {
    const payload = JSON.parse(String(event.data));
    if (payload.op === 0) {
      hello = payload.d || {};
      return;
    }
    if (payload.op === 2) {
      ready = true;
      return;
    }
    if (payload.op === 7) {
      const data = payload.d || {};
      const pendingRequest = pending.get(data.requestId);
      if (!pendingRequest) return;
      pending.delete(data.requestId);
      const status = data.requestStatus || {};
      if (status.result) {
        pendingRequest.resolve(data.responseData || {});
      } else {
        pendingRequest.reject(new Error(`${data.requestType} failed: ${status.comment || status.code || 'unknown'}`));
      }
    }
  });

  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`Timed out connecting to OBS at ${url}`)), 3000);
    ws.addEventListener('open', () => {
      clearTimeout(timer);
      resolve();
    });
    ws.addEventListener('error', (event) => {
      clearTimeout(timer);
      reject(new Error(event?.error?.message || `Failed to connect to OBS at ${url}`));
    });
  });

  const helloDeadline = Date.now() + 3000;
  while (!hello) {
    if (Date.now() > helloDeadline) throw new Error('Timed out waiting for OBS hello');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  const identify = { op: 1, d: { rpcVersion: 1, eventSubscriptions: 0 } };
  const auth = hello.authentication;
  if (auth && auth.challenge && auth.salt) {
    const secret = sha256Base64(password + auth.salt);
    identify.d.authentication = sha256Base64(secret + auth.challenge);
  }
  ws.send(JSON.stringify(identify));

  const readyDeadline = Date.now() + 3000;
  while (!ready) {
    if (Date.now() > readyDeadline) throw new Error('Timed out waiting for OBS identify');
    await new Promise(resolve => setTimeout(resolve, 25));
  }

  return {
    request(requestType, requestData = {}) {
      const requestId = `soren-${++requestSeq}`;
      ws.send(JSON.stringify({ op: 6, d: { requestType, requestId, requestData } }));
      return new Promise((resolve, reject) => pending.set(requestId, { resolve, reject }));
    },
    close() {
      try { ws.close(); } catch {}
    },
  };
}

async function updateObsGameSource() {
  const obs = await connectObs();
  if (!obs) return;

  try {
    const response = await obs.request('GetInputPropertiesListPropertyItems', {
      inputName: OBS_GAME_SOURCE_NAME,
      propertyName: 'window',
    });
    const windows = Array.isArray(response.propertyItems) ? response.propertyItems : [];
    const chromeWindowPattern = /\[Google Chrome(?: for Testing)?\]/;
    const target = windows.find(item =>
      chromeWindowPattern.test(item.itemName || '') && /Unity WebGL Player \| soren-game/.test(item.itemName || '')
    ) || windows.find(item => chromeWindowPattern.test(item.itemName || ''));

    if (!target) {
      console.warn(`OBS game source target window not found for ${OBS_GAME_SOURCE_NAME}`);
      return;
    }

    await obs.request('SetInputSettings', {
      inputName: OBS_GAME_SOURCE_NAME,
      inputSettings: {
        type: 1,
        application: 'com.google.chrome.for.testing',
        window: target.itemValue,
        show_cursor: false,
      },
      overlay: true,
    });
    console.log(`OBS game source ${OBS_GAME_SOURCE_NAME} -> ${target.itemName} (${target.itemValue})`);
  } finally {
    obs.close();
  }
}

// MIME types for Unity WebGL build
const MIME_TYPES = {
  '.html': 'text/html',
  '.js': 'application/javascript',
  '.css': 'text/css',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.data': 'application/octet-stream',
  '.wasm': 'application/wasm',
  '.gz': null, // handled specially
};

async function applyUnityVolume(page, label, objectName, methodName, volume, attempts = 10) {
  if (volume == null || !Number.isFinite(volume)) return { configured: false, applied: false };

  let applied = false;
  for (let i = 0; i < attempts; i++) {
    try {
      const ok = await page.evaluate(({ objectName: targetObject, methodName: targetMethod, value }) => {
        if (!window.unityInstance || typeof window.unityInstance.SendMessage !== 'function') return false;
        window.unityInstance.SendMessage(targetObject, targetMethod, value);
        return true;
      }, { objectName, methodName, value: volume });
      if (ok) { applied = true; break; }
    } catch {}
    await page.waitForTimeout(1000);
  }
  console.log(applied
    ? `${label} volume set to ${volume}`
    : `WARNING: failed to set ${label} volume (unityInstance/AudioManager unavailable)`);
  return { configured: true, applied };
}

async function installUnityVolumeReapply(page) {
  const bgmVolume = (BGM_VOLUME != null && Number.isFinite(BGM_VOLUME)) ? BGM_VOLUME : null;
  const seVolume = (SE_VOLUME != null && Number.isFinite(SE_VOLUME)) ? SE_VOLUME : null;
  if (bgmVolume == null && seVolume == null) return;

  await page.evaluate(({ bgmVolume: nextBgm, seVolume: nextSe, intervalMs }) => {
    const apply = () => {
      try {
        if (!window.unityInstance || typeof window.unityInstance.SendMessage !== 'function') return;
        if (nextBgm != null) {
          window.unityInstance.SendMessage('Audio Manager', 'SetBGMVolume', nextBgm);
        }
        if (nextSe != null) {
          window.unityInstance.SendMessage('Audio Manager', 'SetSEVolume', nextSe);
        }
      } catch (_) {
        // Unity may not be ready during scene loads; the next tick will retry.
      }
    };
    apply();
    if (Number.isFinite(intervalMs) && intervalMs > 0 && !window.__sorenUnityVolumeReapplyTimer) {
      window.__sorenUnityVolumeReapplyTimer = setInterval(apply, intervalMs);
    }
  }, { bgmVolume, seVolume, intervalMs: UNITY_VOLUME_REAPPLY_MS });
}

// Custom static file server that handles .gz files with correct Content-Encoding
function startServer() {
  return new Promise((resolve) => {
    const server = http.createServer((req, res) => {
      let filePath = path.join(BUILD_DIR, req.url === '/' ? 'index.html' : req.url);
      filePath = decodeURIComponent(filePath);

      if (!fs.existsSync(filePath)) {
        res.writeHead(404);
        res.end('Not found');
        return;
      }

      const ext = path.extname(filePath);

      const noCache = {
        'Cache-Control': 'no-store, no-cache, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
      };

      if (ext === '.gz') {
        // Serve .gz files with Content-Encoding: gzip and correct Content-Type
        const innerExt = path.extname(filePath.slice(0, -3)); // e.g. .js from .js.gz
        const contentType = MIME_TYPES[innerExt] || 'application/octet-stream';
        res.writeHead(200, {
          'Content-Type': contentType,
          'Content-Encoding': 'gzip',
          ...noCache,
        });
      } else {
        const contentType = MIME_TYPES[ext] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': contentType, ...noCache });
      }

      fs.createReadStream(filePath).pipe(res);
    });

    server.listen(SERVE_PORT, () => {
      resolve(server);
    });
  });
}

// Read commands from commands.txt (same format as soviet_game.mjs)
function readCommands() {
  try {
    if (!fs.existsSync(COMMAND_FILE)) return [];
    const content = fs.readFileSync(COMMAND_FILE, 'utf-8').trim();
    if (!content) return [];

    const lines = content.split('\n').filter(l => l.trim());
    const commands = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.toLowerCase() === 'retry') {
        commands.push({ action: 'retry' });
      } else if (trimmed.startsWith('[')) {
        try {
          commands.push(...JSON.parse(trimmed));
        } catch (e) {
          console.log('Failed to parse JSON:', trimmed);
        }
      } else if (trimmed.toLowerCase() === 'mute') {
        commands.push({ action: 'mute' });
      } else if (trimmed.toLowerCase() === 'unmute') {
        commands.push({ action: 'unmute' });
      } else {
        // x,y format (canvas coords) — convert to game X coord
        const parts = trimmed.split(',').map(s => parseInt(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          // Convert canvas X (410-830 range) to game X (-3 to +3 range)
          const boardL = 410, boardR = 830;
          const gameX = ((parts[0] - boardL) / (boardR - boardL)) * 6 - 3;
          const clampedX = Math.max(-3.0, Math.min(3.0, gameX));
          commands.push({ action: 'drop', x: clampedX });
        }
      }
    }
    return commands;
  } catch (e) {
    console.error('Error reading commands:', e);
    return [];
  }
}

function clearCommands() {
  try { fs.writeFileSync(COMMAND_FILE, ''); } catch (e) {}
}

// Get game state from JS Bridge
async function getGameState(page) {
  try {
    const state = await page.evaluate(() => window.__sorenGameState);
    return state || null;
  } catch (e) {
    console.error('Error getting game state:', e.message);
    return null;
  }
}

// Write game state to JSON file for AI loop
function writeGameState(state) {
  if (!state) return;
  fs.writeFileSync(GAME_STATE_PATH, JSON.stringify(state, null, 2));
}

function writeAudioHealth(health) {
  try {
    fs.mkdirSync(path.dirname(AUDIO_HEALTH_FILE), { recursive: true });
    fs.writeFileSync(AUDIO_HEALTH_FILE, JSON.stringify({
      updatedAt: new Date().toISOString(),
      ...health,
    }, null, 2));
  } catch {}
}

async function inspectUnityAudio(page) {
  try {
    return await page.evaluate(() => {
      const unity = (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext)
        ? Module.WebAudio.audioContext : null;
      const tracked = (window.__sorenAudioContexts || []).map((ctx) => ({
        state: ctx ? ctx.state : null,
        sinkId: (ctx && typeof ctx.sinkId !== 'undefined') ? String(ctx.sinkId) : 'n/a',
      }));
      return {
        unityPresent: Boolean(unity),
        unityState: unity ? unity.state : null,
        tracked,
        muted: Boolean(window.__sorenMuted),
        routeError: window.__sorenAudioOutputError || '',
        visibility: document.visibilityState,
        hidden: document.hidden,
      };
    });
  } catch (e) {
    return { error: (e && e.message) || String(e) };
  }
}

function unityAudioNeedsRecovery(health) {
  if (!health || health.error || health.muted) return false;
  const states = [];
  if (health.unityState) states.push(health.unityState);
  for (const item of health.tracked || []) {
    if (item && item.state) states.push(item.state);
  }
  return states.some((state) => state === 'suspended' || state === 'interrupted');
}

async function recoverUnityAudio(page, audioDiagLog, reason) {
  try {
    await page.mouse.click(640, 360);
  } catch (e) {
    audioDiagLog(`[AUDIO-WATCHDOG-CLICK-ERROR] ${(e && e.message) || String(e)}`);
  }
  try {
    const result = await page.evaluate(async () => {
      window.__sorenMuted = false;
      const list = [...(window.__sorenAudioContexts || [])];
      const unity = (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext)
        ? Module.WebAudio.audioContext : null;
      if (unity && !list.includes(unity)) list.push(unity);
      for (let attempt = 0; attempt < 5; attempt++) {
        for (const ctx of list) {
          try { ctx.resume(); } catch {}
        }
        await new Promise(r => setTimeout(r, 500));
      }
      return {
        unityState: unity ? unity.state : null,
        tracked: list.map((ctx) => ({ state: ctx ? ctx.state : null })),
      };
    });
    audioDiagLog(`[AUDIO-WATCHDOG-RECOVER] reason=${reason} ${JSON.stringify(result)}`);
    return result;
  } catch (e) {
    audioDiagLog(`[AUDIO-WATCHDOG-RECOVER-ERROR] reason=${reason} ${(e && e.message) || String(e)}`);
    return null;
  }
}

// Check if state has changed (compare relevant fields)
function stateChanged(prev, curr) {
  if (!prev || !curr) return true;
  return prev.state !== curr.state ||
         prev.score !== curr.score ||
         JSON.stringify(prev.pieces) !== JSON.stringify(curr.pieces);
}

// Execute a command via JS Bridge
async function executeCommand(page, command) {
  if (command.action === 'retry') {
    console.log('Executing: RETRY');
    await page.evaluate(() => { window.__sorenCommand = 'RETRY'; });
    await page.waitForTimeout(2000);
    // Re-inject best score after scene reload
    try {
      const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
      if (bestScore > 0) {
        await page.evaluate((s) => { window.__sorenCommand = 'SET_RECORD:' + s; }, bestScore);
        console.log(`Re-injected best score record: ${bestScore}`);
        await page.waitForTimeout(500);
      }
    } catch (e) { /* ignore */ }
  } else if (command.action === 'cmd') {
    console.log(`Executing: ${command.value}`);
    await page.evaluate((v) => { window.__sorenCommand = v; }, command.value);
    await page.waitForTimeout(1000);
  } else if (command.action === 'drop') {
    console.log(`Executing: DROP at x=${command.x.toFixed(3)}`);
    await page.evaluate((x) => { window.__sorenCommand = 'DROP:' + x; }, command.x);
    await page.waitForTimeout(500);
  } else if (command.action === 'mute') {
    console.log('Executing: MUTE');
    await page.evaluate(() => {
      if (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext) {
        try { Module.WebAudio.audioContext.suspend(); } catch {}
      }
    });
  } else if (command.action === 'unmute') {
    console.log('Executing: UNMUTE');
    await page.evaluate(() => {
      if (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext) {
        try { Module.WebAudio.audioContext.resume(); } catch {}
      }
    });
  }

  // Update state after command
  const state = await getGameState(page);
  writeGameState(state);
  return state;
}

async function runLocalController() {
  // Check build directory exists
  if (!fs.existsSync(BUILD_DIR)) {
    console.error(`Build directory not found: ${BUILD_DIR}`);
    console.error('Please build the Unity WebGL project first (File → Build Settings → Build)');
    process.exit(1);
  }

  // Start local server
  console.log(`Starting local server for ${BUILD_DIR} on port ${SERVE_PORT}...`);
  let server;
  try {
    server = await startServer();
    console.log(`Server started on port ${SERVE_PORT}`);
  } catch (e) {
    console.error('Failed to start server:', e.message);
    process.exit(1);
  }

  // Cleanup on exit
  function removeCdpEndpoint() {
    try { fs.unlinkSync(CDP_ENDPOINT_FILE); } catch {}
  }
  process.on('SIGINT', () => {
    console.log('\nShutting down...');
    removeCdpEndpoint();
    server.close();
    process.exit(0);
  });
  process.on('exit', removeCdpEndpoint);

  let browser;
  let context;
  let closeBrowserAfterContext = false;
  async function closeBrowser() {
    if (context) {
      try {
        await context.close();
      } catch (err) {
        if (!closeBrowserAfterContext) throw err;
      }
      if (!closeBrowserAfterContext) return;
    }
    if (browser) {
      await browser.close();
    }
  }

  try {
    fs.mkdirSync(path.dirname(USER_DATA_DIR), { recursive: true });
    seedChromeTranslatePreferences(USER_DATA_DIR);
    const launchArgs = [
      '--window-size=1300,800',
      `--remote-debugging-port=${CDP_PORT}`,
      // 復旧時の kill -9 でプロファイルが unclean になり「正しく終了しませんでした」
      // 復元バブルが配信画面隅に出続けるのを抑止
      '--hide-crash-restore-bubble',
      '--disable-session-crashed-bubble',
      '--disable-crash-reporter',
      '--disable-crashpad',
      `--crash-dumps-dir=${path.join(USER_DATA_DIR, 'Crashpad')}`,
      '--no-first-run',
      '--no-default-browser-check',
      '--password-store=basic',
      '--use-mock-keychain',
      // Chrome の翻訳バー(英語→日本語 このページを翻訳しますか)を配信画面に出さない。
      // Playwright 既定の --disable-features に既に Translate が含まれるため、
      // ここで別の --disable-features を渡すと後勝ちで Playwright の hardening を
      // 上書きしてしまう。別スイッチの --disable-translate のみ追加し、確実な
      // 抑止は profile Preferences (_br_clean_profile_exit) 側で行う。
      '--disable-translate',
      // 自動操作ブラウザはユーザー操作が無く、autoplay ポリシーで AudioContext が
      // suspended のまま resume できず無音化する。bridge 再起動毎の無音を防ぐ。
      '--autoplay-policy=no-user-gesture-required',
    ];
    const backgroundLaunch = CHROME_HEADLESS ? null : await launchPersistentContextWithoutFocus(USER_DATA_DIR, launchArgs).catch(err => {
      console.error(`[NO-FOCUS] background launch failed, falling back to Playwright launch: ${err.message}`);
      return null;
    });
    if (backgroundLaunch) {
      browser = backgroundLaunch.browser;
      context = backgroundLaunch.context;
      closeBrowserAfterContext = true;
    } else {
      const playwrightLaunchArgs = launchArgs.filter(arg => !arg.startsWith('--remote-debugging-port='));
      context = await chromium.launchPersistentContext(USER_DATA_DIR, {
        executablePath: process.env.SOREN_CHROME_EXECUTABLE_PATH || undefined,
        headless: CHROME_HEADLESS,
        viewport: { width: 1280, height: 720 },
        deviceScaleFactor: 1,
        env: browserLaunchEnv(USER_DATA_DIR),
        args: playwrightLaunchArgs,
      });
      browser = context.browser();
    }
  } catch (e) {
    console.error(`Failed to launch browser: ${e.message}`);
    removeCdpEndpoint();
    server.close();
    process.exit(1);
  }

  // Write CDP endpoint file for soren91 shared browser mode
  try {
    fs.writeFileSync(CDP_ENDPOINT_FILE, JSON.stringify({
      url: `http://localhost:${CDP_PORT}`,
      port: CDP_PORT,
      pid: process.pid,
      userDataDir: USER_DATA_DIR,
      startedAt: new Date().toISOString(),
    }));
    console.log(`CDP endpoint written: ${CDP_ENDPOINT_FILE} (port=${CDP_PORT})`);
  } catch (e) {
    console.warn(`Failed to write CDP endpoint file: ${e.message}`);
  }

  const page = context.pages()[0] || await context.newPage();

  // --- ブラウザ死亡時の確実・即時クリーンexit (ハング/ゾンビ根絶) ---
  // 復旧は soren_loop の inline _ensure_bridge_alive に委ねる2層設計。
  // ここはハングさせず port を解放して即終了することに専念する。
  let _brExiting = false;
  function fatalExit(reason, code = 1) {
    if (_brExiting) return;
    _brExiting = true;
    // cleanup がハングしても必ず終了する強制タイマを最初に張る (port解放保証)
    const _forced = setTimeout(() => process.exit(code), 3000);
    if (_forced.unref) _forced.unref();
    try { console.error('[BRIDGE-FATAL] ' + reason); } catch {}
    (async () => {
      const withTimeout = (p) =>
        Promise.race([Promise.resolve(p).catch(() => {}), new Promise((r) => setTimeout(r, 1500))]);
      try { removeCdpEndpoint(); } catch {}
      try { await withTimeout(context && context.close && context.close()); } catch {}
      try { await withTimeout(browser && browser.close && browser.close()); } catch {}
      try { server && server.close && server.close(); } catch {}
      process.exit(code);
    })();
  }
  try {
    context.on('close', () => fatalExit('context closed'));
    const _b = (context.browser && context.browser()) || browser;
    if (_b && _b.on) _b.on('disconnected', () => fatalExit('browser disconnected'));
    page.on('crash', () => fatalExit('page crashed'));
  } catch (e) { console.warn(`Failed to attach death handlers: ${e && e.message}`); }
  // 分類による分岐はしない (Protocol error 等の誤判定回避)。常に fatalExit。
  process.on('unhandledRejection', (e) => {
    try { console.error((e && e.stack) || e); } catch {}
    fatalExit('unhandledRejection: ' + ((e && e.message) || e));
  });
  process.on('uncaughtException', (e) => {
    try { console.error((e && e.stack) || e); } catch {}
    fatalExit('uncaughtException: ' + ((e && e.message) || e));
  });

  console.log('=== Soren Local Game Controller ===');

  const gameOrigin = `http://localhost:${SERVE_PORT}`;
  let speakerPermissionSession = null;
  const grantAudioPermissions = async () => {
    try {
      if (!speakerPermissionSession) {
        speakerPermissionSession = await context.newCDPSession(page);
      }
      await speakerPermissionSession.send('Browser.grantPermissions', {
        origin: gameOrigin,
        permissions: ['speakerSelection', 'audioCapture'],
      });
      return true;
    } catch (e) {
      console.warn(`Failed to grant speakerSelection: ${e.message}`);
      speakerPermissionSession = null;
      return false;
    }
  };
  try {
    if (await grantAudioPermissions()) console.log(`Granted speakerSelection for ${gameOrigin}`);
  } catch {}

  // Hook AudioContext to track all instances for mute/unmute control and route
  // browser game audio to BlackHole without changing the macOS default output.
  await page.addInitScript((audioOutputLabel) => {
    window.__sorenAudioContexts = [];
    window.__sorenMuted = false;
    window.__sorenAudioOutputLabel = audioOutputLabel;
    window.__sorenAudioOutputDeviceId = '';
    window.__sorenAudioOutputError = '';
    // #90 安定化: ライブ setSinkId(稼働中コンテキストの出力デバイス切替) は macOS
    // CoreAudio + Unity WASM グラフでクラッシュ要因 → 廃止。代わりに deviceId を
    // 事前解決し、AudioContext を生成時に {sinkId} で目的デバイスに固定する
    // (生成時バインド=デバイス切替イベント無し=クラッシュしない)。
    // システム既定出力デバイスは変更しない (ハウリング回避・恒久制約)。
    window.__sorenSinkId = '';
    // label から audiooutput deviceId を解決し __sorenSinkId に保存 (setSinkId は呼ばない)
    window.__sorenResolveSink = async (label = window.__sorenAudioOutputLabel) => {
      if (!label || !navigator.mediaDevices?.enumerateDevices) return false;
      try {
        const devices = await navigator.mediaDevices.enumerateDevices();
        const target = devices.find(d =>
          d.kind === 'audiooutput' && d.label &&
          d.label.toLowerCase().includes(String(label).toLowerCase()));
        if (target && target.deviceId) {
          window.__sorenSinkId = target.deviceId;
          window.__sorenAudioOutputDeviceId = target.deviceId;
          window.__sorenAudioOutputError = '';
          return true;
        }
        window.__sorenAudioOutputError = `audio output not found: ${label}`;
        return false;
      } catch (e) {
        window.__sorenAudioOutputError = e && e.message ? e.message : String(e);
        return false;
      }
    };
    // 後方互換: 旧名 __sorenRouteAudioOutput は deviceId 再解決のみ (setSinkId しない)
    window.__sorenRouteAudioOutput = (label) => window.__sorenResolveSink(label);
    // 事前解決を即開始 (Unity WebGL は wasm/asset DL で数秒かかるため、通常
    // この解決が Unity の AudioContext 生成より先に完了する)
    (async () => {
      for (let i = 0; i < 40 && !window.__sorenSinkId; i++) {
        if (await window.__sorenResolveSink()) break;
        await new Promise(r => setTimeout(r, 500));
      }
    })();

    const OrigAudioContext = window.AudioContext || window.webkitAudioContext;
    if (OrigAudioContext) {
      const Wrapped = function(...args) {
        // 呼び出し側が sinkId 未指定 かつ deviceId 解決済なら生成時に注入。
        // ライブ setSinkId は一切しない (#90 crash 回避)。未解決(レース)なら
        // 通常生成 (既定デバイス) し setSinkId しない=その1起動は OBS に乗らない
        // が crash 回避優先・稀。次 bridge 起動で解決済になり復旧。
        let ctx;
        try {
          const sid = window.__sorenSinkId;
          const opt0 = (args[0] && typeof args[0] === 'object') ? args[0] : null;
          if (sid && (!opt0 || !('sinkId' in opt0))) {
            const merged = Object.assign({}, opt0 || {}, { sinkId: sid });
            ctx = new OrigAudioContext(merged);
          } else {
            ctx = new OrigAudioContext(...args);
          }
        } catch (e) {
          // {sinkId} オプション非対応/無効デバイス時は素の生成にフォールバック
          try { ctx = new OrigAudioContext(...args); }
          catch (e2) { ctx = new OrigAudioContext(); }
          window.__sorenAudioOutputError = e && e.message ? e.message : String(e);
        }
        window.__sorenAudioContexts.push(ctx);
        if (window.__sorenMuted) {
          try { ctx.suspend(); } catch {}
        }
        return ctx;
      };
      Wrapped.prototype = OrigAudioContext.prototype;
      window.AudioContext = Wrapped;
      if (window.webkitAudioContext) window.webkitAudioContext = Wrapped;
    }

    // Fix1 (#90): 稼働中 AudioContext 自己治癒ウォッチドッグ。
    // BlackHole/WebAudio device error で稼働中に suspended 化し自動 resume
    // されない慢性障害を、bridge 再起動なしでページ内で回復する。
    // - 5s 毎。resume() は fire-and-forget (await すると無限ハングし得る)
    // - 毎周期 Module.WebAudio.audioContext も対象に含める (構築時 wrap 分以外)
    // - 再入防止フラグ。route 再適用は時間予算付き (watchdog 自体を wedge しない)
    // - 意図的 mute (__sorenMuted) 中は resume しない (radio/TTS 優先仕様維持)
    window.__sorenAudioHealBusy = false;
    window.__sorenAudioLastRoute = 0;
    setInterval(() => {
      if (window.__sorenAudioHealBusy || window.__sorenMuted) return;
      window.__sorenAudioHealBusy = true;
      try {
        const list = [...(window.__sorenAudioContexts || [])];
        try {
          const um = (typeof Module !== 'undefined' && Module.WebAudio
            && Module.WebAudio.audioContext) ? Module.WebAudio.audioContext : null;
          if (um && list.indexOf(um) === -1) list.push(um);
        } catch {}
        let anySuspended = false;
        for (const ctx of list) {
          try {
            if (ctx && ctx.state === 'suspended') {
              anySuspended = true;
              ctx.resume().catch(() => {}); // fire-and-forget
            }
          } catch {}
        }
        // 持続 suspend or route error 時、最大 30s に1回だけ再ルート
        const now = Date.now();
        if ((anySuspended || window.__sorenAudioOutputError)
            && now - (window.__sorenAudioLastRoute || 0) > 30000) {
          window.__sorenAudioLastRoute = now;
          try {
            window.__sorenRouteAudioOutput?.().catch(() => {});
          } catch {}
        }
      } finally {
        window.__sorenAudioHealBusy = false;
      }
    }, 5000);
  }, CHROME_AUDIO_OUTPUT_LABEL);

  console.log(`Navigating to http://localhost:${SERVE_PORT}...`);

  await page.goto(gameOrigin, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // Wait for Unity canvas to initialize
  let canvasReady = false;
  for (let i = 0; i < 60; i++) {
    canvasReady = await page.evaluate(() => {
      const canvas = document.getElementById('unity-canvas') || document.querySelector('canvas');
      return canvas && canvas.width > 300;
    });
    if (canvasReady) break;
    console.log(`Waiting for Unity canvas init... (${i + 1}/60)`);
    await page.waitForTimeout(1000);
  }

  if (!canvasReady) {
    console.error('Unity canvas failed to initialize!');
    await closeBrowser();
    server.close();
    return;
  }

  console.log('Unity canvas ready');

  // AudioManager の音量スライダー onValueChanged と同一実体を SendMessage で操作する。
  // AudioManager は Awake で初期化されるため初回はリトライし、その後も
  // 並列評価窓の BGM が画面遷移などで戻らないよう短い間隔で再適用する。
  await applyUnityVolume(page, 'BGM', 'Audio Manager', 'SetBGMVolume', BGM_VOLUME);
  await applyUnityVolume(page, 'SE', 'Audio Manager', 'SetSEVolume', SE_VOLUME);
  await installUnityVolumeReapply(page);

  try {
    await updateObsGameSource();
  } catch (e) {
    console.warn(`Failed to update OBS game source: ${e.message}`);
  }

  try {
    const audioRoute = await page.evaluate(async (label) => {
      if (!label) return { routed: false, label, deviceId: '', error: 'per-context audio routing disabled', contexts: 0 };
      const routed = await window.__sorenRouteAudioOutput?.(label);
      return {
        routed: Boolean(routed),
        label,
        deviceId: window.__sorenAudioOutputDeviceId || '',
        error: window.__sorenAudioOutputError || '',
        contexts: Array.isArray(window.__sorenAudioContexts) ? window.__sorenAudioContexts.length : 0,
      };
    }, CHROME_AUDIO_OUTPUT_LABEL);
    console.log('Chrome audio route:', JSON.stringify(audioRoute));
  } catch (e) {
    console.warn(`Failed to route Chrome audio: ${e.message}`);
  }

  // Force canvas to fill viewport exactly — hide footer, reset margins, override container positioning
  const canvasInfo = await page.evaluate(() => {
    // Hide footer
    const footer = document.getElementById('unity-footer');
    if (footer) footer.style.display = 'none';
    // Hide loading bar
    const loadingBar = document.getElementById('unity-loading-bar');
    if (loadingBar) loadingBar.style.display = 'none';
    // Reset body
    document.body.style.margin = '0';
    document.body.style.padding = '0';
    document.body.style.overflow = 'hidden';
    // Override container — remove centering transform, pin to top-left
    const container = document.getElementById('unity-container');
    if (container) {
      container.style.position = 'absolute';
      container.style.left = '0';
      container.style.top = '0';
      container.style.transform = 'none';
    }
    // Ensure canvas fills exactly
    const canvas = document.getElementById('unity-canvas');
    if (canvas) {
      canvas.style.width = '1280px';
      canvas.style.height = '720px';
      canvas.style.display = 'block';
    }
    return {
      canvasWidth: canvas?.width,
      canvasHeight: canvas?.height,
      cssWidth: canvas?.style.width,
      cssHeight: canvas?.style.height,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
    };
  });
  console.log('Canvas layout:', JSON.stringify(canvasInfo));

  // Capture browser console for debugging
  page.on('console', msg => {
    if (msg.type() === 'error' || msg.type() === 'warning' || msg.text().includes('FIXUI')) {
      console.log(`[BROWSER ${msg.type().toUpperCase()}] ${msg.text()}`);
    }
  });

  // Wait for JS Bridge to be active
  let bridgeReady = false;
  for (let i = 0; i < 30; i++) {
    const state = await getGameState(page);
    if (state && state.state) {
      bridgeReady = true;
      console.log(`JS Bridge active, game state: ${state.state}`);
      break;
    }
    console.log(`Waiting for JS Bridge... (${i + 1}/30)`);
    await page.waitForTimeout(1000);
  }

  if (!bridgeReady) {
    console.error('JS Bridge not responding. Is SorenBridge component attached in the scene?');
    await closeBrowser();
    server.close();
    return;
  }

  // Inject best score record from best_score.txt
  try {
    const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
    if (bestScore > 0) {
      await page.evaluate((s) => { window.__sorenCommand = 'SET_RECORD:' + s; }, bestScore);
      console.log(`Injected best score record: ${bestScore}`);
      await page.waitForTimeout(500);
    }
  } catch (e) {
    console.log('No best_score.txt found, skipping record injection');
  }

  // Click to start the game
  await page.mouse.click(640, 360);
  await page.waitForTimeout(2000);

  // Initial state
  const initialState = await getGameState(page);
  writeGameState(initialState);
  console.log('Initial game state saved');
  console.log(`Watching for commands in: ${COMMAND_FILE}`);

  // Main loop: poll commands and game state
  let processedCount = 0;
  let lastState = null;
  const STATE_CHECK_INTERVAL = 3;
  const NULL_STATE_WARN_THRESHOLD = 10;
  const NULL_STATE_RELOAD_THRESHOLD = 30;

  let checkCount = 0;
  let nullStateCount = 0;
  let isMuted = false;
  let lastAudioRouteHealAt = 0;
  let lastAudioWatchdogAt = 0;
  let lastUnityAudioRecoverAt = 0;
  while (true) {
    // Check mute flag file (independent of commands.txt to avoid race condition)
    const shouldMute = fs.existsSync(MUTE_FLAG_FILE);
    const audioDiagLog = (msg) => {
      const line = `[${new Date().toISOString()}] ${msg}`;
      console.log(line);
      try { fs.appendFileSync('tmp/audio_diag.log', line + '\n'); } catch (e) {}
    };
    if (shouldMute && !isMuted) {
      audioDiagLog('MUTE flag detected, muting audio');
      await page.evaluate(() => {
        window.__sorenMuted = true;
        // Suspend all known AudioContexts
        if (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext) {
          try { Module.WebAudio.audioContext.suspend(); } catch {}
        }
        // Also suspend any contexts we've tracked
        (window.__sorenAudioContexts || []).forEach(ctx => {
          try { ctx.suspend(); } catch {}
        });
      });
      isMuted = true;
    } else if (!shouldMute && isMuted) {
      audioDiagLog('MUTE flag removed, resuming audio');
      // Diagnosis (from [AUDIO-UNMUTE] logs): the tracked AudioContext stays
      // "suspended" after resume() even though the tab is visible. The local
      // game is driven via the window.__sorenCommand JS bridge, so after the
      // initial startup click NO trusted input event ever reaches the page —
      // Unity WebGL resumes its AudioContext only from a real focus/input
      // event, and Chrome gates resume() the same way. Deliver one real
      // trusted gesture (Space keypress, ignored by this mouse-only game)
      // before resuming so Unity's handler fires and resume() is honored.
      try {
        await page.keyboard.press('Space');
      } catch (e) {
        console.warn(`unmute activation keypress failed: ${e.message}`);
      }
      // resume() fired WITHOUT await (an awaited resume() can hang in Chrome
      // and wedge this loop) and retried a few times after the gesture. The
      // whole evaluate is bounded by a timeout and wrapped so we learn whether
      // it throws, times out, or what context state results.
      try {
        const evalPromise = page.evaluate(async () => {
          const snap = () => {
            const unity = (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext)
              ? Module.WebAudio.audioContext : null;
            return {
              unityPresent: Boolean(unity),
              unityState: unity ? unity.state : null,
              tracked: (window.__sorenAudioContexts || []).map(c => ({
                state: c.state,
                sinkId: (typeof c.sinkId !== 'undefined') ? String(c.sinkId) : 'n/a',
              })),
              routeError: window.__sorenAudioOutputError || '',
              routedDeviceId: window.__sorenAudioOutputDeviceId || '',
              visibility: document.visibilityState,
              hidden: document.hidden,
            };
          };
          const before = snap();
          window.__sorenMuted = false;
          const ctxs = () => {
            const list = [...(window.__sorenAudioContexts || [])];
            const unity = (typeof Module !== 'undefined' && Module.WebAudio && Module.WebAudio.audioContext)
              ? Module.WebAudio.audioContext : null;
            if (unity && !list.includes(unity)) list.push(unity);
            return list;
          };
          // Retry resume() a few times: the trusted gesture grants activation
          // but Unity/Chrome may need a beat before honoring it.
          for (let attempt = 0; attempt < 5; attempt++) {
            const all = ctxs();
            if (all.length && all.every(c => c.state === 'running')) break;
            for (const c of all) { try { c.resume(); } catch (e) {} }
            await new Promise(r => setTimeout(r, 500));
          }
          return { before, after: snap() };
        });
        const timeoutMarker = Symbol('timeout');
        const result = await Promise.race([
          evalPromise.catch(e => ({ __err: (e && e.message) || String(e) })),
          new Promise(r => setTimeout(() => r(timeoutMarker), 8000)),
        ]);
        if (result === timeoutMarker) {
          audioDiagLog('[AUDIO-UNMUTE-TIMEOUT] page.evaluate did not return within 8s (resume likely hung / page detached)');
        } else if (result && result.__err) {
          audioDiagLog(`[AUDIO-UNMUTE-ERROR] ${result.__err}`);
        } else {
          audioDiagLog(`[AUDIO-UNMUTE] ${JSON.stringify(result)}`);
        }
      } catch (e) {
        audioDiagLog(`[AUDIO-UNMUTE-ERROR] outer: ${(e && e.message) || String(e)}`);
      }
      // Always clear muted state so the loop never gets stuck re-entering this
      // branch (a stuck branch would also block game resumption).
      isMuted = false;
    }

    if (!shouldMute && !isMuted && Date.now() - lastAudioRouteHealAt > 10000) {
      lastAudioRouteHealAt = Date.now();
      await grantAudioPermissions();
      try {
        await page.evaluate((label) => {
          window.__sorenRouteAudioOutput?.(label).catch(() => {});
        }, CHROME_AUDIO_OUTPUT_LABEL);
      } catch (e) {
        audioDiagLog(`[AUDIO-ROUTE-HEAL-ERROR] ${(e && e.message) || String(e)}`);
      }
    }

    if (!shouldMute && !isMuted && Date.now() - lastAudioWatchdogAt > UNITY_AUDIO_WATCHDOG_MS) {
      lastAudioWatchdogAt = Date.now();
      const health = await inspectUnityAudio(page);
      writeAudioHealth({
        ...health,
        lastRecoverAt: lastUnityAudioRecoverAt ? new Date(lastUnityAudioRecoverAt).toISOString() : null,
      });
      if (unityAudioNeedsRecovery(health) && Date.now() - lastUnityAudioRecoverAt > UNITY_AUDIO_RECOVER_COOLDOWN_MS) {
        lastUnityAudioRecoverAt = Date.now();
        const recovered = await recoverUnityAudio(page, audioDiagLog, health.unityState || 'tracked_suspended');
        writeAudioHealth({
          before: health,
          after: recovered,
          lastRecoverAt: new Date(lastUnityAudioRecoverAt).toISOString(),
        });
      }
    }

    // Muted = meriken mode active, skip all page interactions to avoid stealing tab focus
    if (isMuted) {
      await page.waitForTimeout(1000);
      continue;
    }

    const commands = readCommands();

    if (commands.length > processedCount) {
      for (let i = processedCount; i < commands.length; i++) {
        const state = await executeCommand(page, commands[i]);
        lastState = state;
        if (state) nullStateCount = 0;
        processedCount++;

        if (i === commands.length - 1) {
          clearCommands();
          processedCount = 0;
        }
      }
    } else {
      checkCount++;
      if (checkCount >= STATE_CHECK_INTERVAL) {
        checkCount = 0;
        const state = await getGameState(page);

        if (!state) {
          nullStateCount++;
          if (nullStateCount === NULL_STATE_WARN_THRESHOLD) {
            console.warn(`[BRIDGE] game state null ${nullStateCount} times in a row — JS Bridge may be broken`);
          }
          if (nullStateCount >= NULL_STATE_RELOAD_THRESHOLD) {
            console.warn(`[BRIDGE] game state null ${nullStateCount} times — reloading page to recover`);
            try {
              await page.reload({ waitUntil: 'domcontentloaded', timeout: 30000 });
              // Wait for Unity canvas + Bridge to re-init
              for (let i = 0; i < 60; i++) {
                const s = await getGameState(page);
                if (s && s.state) {
                  console.log(`[BRIDGE] Recovered after reload, state: ${s.state}`);
                  // Re-inject best score
                  try {
                    const bestScore = parseInt(fs.readFileSync('best_score.txt', 'utf-8').trim(), 10);
                    if (bestScore > 0) {
                      await page.evaluate((sc) => { window.__sorenCommand = 'SET_RECORD:' + sc; }, bestScore);
                      console.log(`[BRIDGE] Re-injected best score: ${bestScore}`);
                      await page.waitForTimeout(500);
                    }
                  } catch (e2) { /* ignore */ }
                  // Click to start game
                  await page.mouse.click(640, 360);
                  await page.waitForTimeout(2000);
                  lastState = s;
                  writeGameState(s);
                  break;
                }
                await page.waitForTimeout(1000);
              }
            } catch (e) {
              console.error(`[BRIDGE] Reload failed: ${e.message}`);
            }
            nullStateCount = 0;
          }
        } else {
          if (nullStateCount >= NULL_STATE_WARN_THRESHOLD) {
            console.log(`[BRIDGE] game state recovered after ${nullStateCount} null reads`);
          }
          nullStateCount = 0;

          if (stateChanged(lastState, state)) {
            writeGameState(state);
            console.log(`State: ${state.state}, score=${state.score}, pieces=${state.pieces?.length || 0}`);
          }
          lastState = state;
        }
      }
    }

    await page.waitForTimeout(200);
  }
}

runLocalController().catch((e) => {
  try { console.error('[BRIDGE-FATAL] runLocalController rejected: ' + ((e && (e.stack || e.message)) || e)); } catch {}
  const _f = setTimeout(() => process.exit(1), 3000);
  if (_f.unref) _f.unref();
  process.exit(1);
});

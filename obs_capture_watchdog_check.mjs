// OBS game-capture self-heal: keeps the shared `sorengame` window-capture pointed
// at the LIVE Chrome game window and recovers when it goes stale or freezes.
//
// Two independent recovery signals:
//   1. BINDING VALIDITY (deterministic, fast): read the source's currently bound
//      window id and compare it to the live window that matches the expected
//      title for the CURRENT display mode (china vs meriken, from
//      tmp/state/soren_display_mode). If the bound window is gone/stale/wrong, the
//      capture is showing a dead window's last frame (the classic symptom after a
//      Chrome crash/restart) -> rebind to the live window immediately. This needs
//      neither an 8s pixel sample nor game_state to be advancing, so it heals the
//      crash/restart case fast and in either display mode.
//   2. PIXEL FREEZE (fallback): if the binding is correct but the capture stream
//      stalled, 3 screenshots over ~8s are identical while game_state advances.
//      macOS keeps its established bounce. Linux XComposite reports the signal
//      but leaves the source untouched unless same-value recovery is explicitly
//      enabled. XSHM is screenshot-only and is never rebound or bounced.
//
// Safety: if NO live window matches the expected title (e.g. Chrome mid-relaunch),
// the source is left UNTOUCHED — we never blank a working/last-good capture.
//
// Exit codes: 0 = ok / no action, 10 = was frozen and bounced,
//             11 = binding was stale and rebound, 2 = error/skip.
//
// Reuses soviet_local.mjs's OBS WS v5 handshake. Env:
//   OBS_WEBSOCKET_PORT / OBS_WEBSOCKET_PASSWORD / OBS_WEBSOCKET_HOST
//   OBS_WEBSOCKET_TIMEOUT_MS    (connect/identify/request timeout; default 3000)
//   SOREN_OBS_GAME_SOURCE_NAME (default sorengame)
//   SOREN_DISPLAY_STATE_FILE   (default tmp/state/soren_display_mode)
//   SOREN_CHINA_WINDOW_REGEX   (default "Unity WebGL Player \\| soren-game")
//   SOREN_MERIKEN_WINDOW_REGEX (default "91人対戦|ソ連ゲーム91")
//   SOREN_CHROME_APP_ID        (default com.google.chrome.for.testing)
//   SOREN_OBS_PLATFORM         (test/compat override; default process.platform)
//   OBS_WINDOW_CAPTURE_INPUT_KIND / OBS_WINDOW_CAPTURE_FAMILY
//   OBS_WINDOW_CAPTURE_WINDOW_PROPERTY
//   OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED=1 (Linux-only risky opt-in)
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

function loadDotEnv(path = '.env') {
  try {
    const text = fs.readFileSync(path, 'utf8');
    for (const line of text.split(/\n/)) {
      const match = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (!match || Object.prototype.hasOwnProperty.call(process.env, match[1])) continue;
      let value = match[2].trim();
      if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
        value = value.slice(1, -1);
      }
      process.env[match[1]] = value;
    }
  } catch {}
}

loadDotEnv();

const NAME = process.env.SOREN_OBS_GAME_SOURCE_NAME || 'sorengame';
const MODE_FILE = process.env.SOREN_DISPLAY_STATE_FILE || 'tmp/state/soren_display_mode';
const CHINA_RE = new RegExp(process.env.SOREN_CHINA_WINDOW_REGEX || 'Unity WebGL Player \\| soren-game');
const MERIKEN_RE = new RegExp(process.env.SOREN_MERIKEN_WINDOW_REGEX || '91人対戦|ソ連ゲーム91');
const APP_ID = process.env.SOREN_CHROME_APP_ID || 'com.google.chrome.for.testing';
const CAPTURE_PLATFORM = String(process.env.SOREN_OBS_PLATFORM || process.platform).toLowerCase();
const INPUT_KIND = process.env.OBS_WINDOW_CAPTURE_INPUT_KIND
  || (CAPTURE_PLATFORM === 'linux' ? 'xcomposite_input' : 'screen_capture');
const CAPTURE_FAMILY_OVERRIDE = String(process.env.OBS_WINDOW_CAPTURE_FAMILY || '').toLowerCase();
const INFERRED_CAPTURE_FAMILY =
  /^xshm_input(?:_v\d+)?$/.test(INPUT_KIND)
    ? 'xshm'
    : /^xcomposite_input(?:_v\d+)?$/.test(INPUT_KIND)
      ? 'xcomposite'
      : /^screen_capture(?:_v\d+)?$/.test(INPUT_KIND)
        ? 'screen_capture'
        : CAPTURE_PLATFORM === 'linux'
          ? 'xcomposite'
          : 'screen_capture';
const CAPTURE_FAMILY = ['xcomposite', 'xshm', 'screen_capture'].includes(CAPTURE_FAMILY_OVERRIDE)
  ? CAPTURE_FAMILY_OVERRIDE
  : INFERRED_CAPTURE_FAMILY;
const IS_XCOMPOSITE = CAPTURE_FAMILY === 'xcomposite';
const IS_XSHM = CAPTURE_FAMILY === 'xshm';
const WINDOW_PROPERTY = process.env.OBS_WINDOW_CAPTURE_WINDOW_PROPERTY
  || (IS_XCOMPOSITE ? 'capture_window' : 'window');
const XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED =
  process.env.OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED === '1';
const DEFAULT_OBS_LOG_DIR = CAPTURE_PLATFORM === 'linux'
  ? path.join(os.homedir(), '.config', 'obs-studio', 'logs')
  : path.join(os.homedir(), 'Library', 'Application Support', 'obs-studio', 'logs');
const REQUEST_TIMEOUT_RAW = Number(process.env.OBS_WEBSOCKET_TIMEOUT_MS || 3000);
const REQUEST_TIMEOUT_MS = Number.isFinite(REQUEST_TIMEOUT_RAW) && REQUEST_TIMEOUT_RAW > 0
  ? REQUEST_TIMEOUT_RAW
  : 3000;

const gameStateSig = () => { try { const s = fs.statSync('game_state.json'); return `${s.mtimeMs}:${s.size}`; } catch { return ''; } };
const sha = (s) => crypto.createHash('sha256').update(s).digest('base64');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Cross-process mutex so this watchdog's SetInputSettings (bounce/rebind of the
// mac-capture `sorengame` source) never races a param-parallel candidate update,
// the main soviet_local bridge, or soren91 — concurrent obs_source_update on
// mac-capture double-frees and crashes OBS. Loaded dynamically + best-effort so a
// missing helper degrades to "no lock" instead of crashing the watchdog.
let _obsLock = null;
async function obsLock() {
  if (_obsLock) return _obsLock;
  try {
    _obsLock = await import('./lib/obs_source_lock.mjs');
  } catch {
    _obsLock = { acquireObsSourceLock: async () => false, releaseObsSourceLock: async () => {} };
  }
  return _obsLock;
}

const currentMode = () => { try { return fs.readFileSync(MODE_FILE, 'utf8').trim() === 'meriken' ? 'meriken' : 'china'; } catch { return 'china'; } };
const expectedRe = () => (currentMode() === 'meriken' ? MERIKEN_RE : CHINA_RE);

function latestObsLog() {
  const dir = process.env.OBS_LOG_DIR || DEFAULT_OBS_LOG_DIR;
  try {
    return fs.readdirSync(dir)
      .filter((name) => name.endsWith('.txt'))
      .map((name) => {
        const fullPath = path.join(dir, name);
        const stat = fs.statSync(fullPath);
        return { fullPath, mtimeMs: stat.mtimeMs, size: stat.size };
      })
      .sort((a, b) => b.mtimeMs - a.mtimeMs)[0] || null;
  } catch {
    return null;
  }
}

function obsSafeModePromptHint() {
  const log = latestObsLog();
  if (!log) return null;
  let text = '';
  try {
    const fd = fs.openSync(log.fullPath, 'r');
    const length = Math.min(log.size, 8192);
    const buffer = Buffer.alloc(length);
    fs.readSync(fd, buffer, 0, length, 0);
    fs.closeSync(fd);
    text = buffer.toString('utf8');
  } catch {
    return null;
  }
  if (!/\[Safe Mode\]\s+Unclean shutdown detected!/.test(text)) return null;
  return log;
}

export async function connectObs({
  WebSocketCtor = globalThis.WebSocket,
  requestTimeoutMs = REQUEST_TIMEOUT_MS,
} = {}) {
  const port = process.env.OBS_WEBSOCKET_PORT;
  const password = process.env.OBS_WEBSOCKET_PASSWORD;
  if (!port || !password || typeof WebSocketCtor !== 'function') return null;
  const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
  const ws = new WebSocketCtor(`ws://${host}:${Number(port)}`);
  let hello = null, ready = false, seq = 0;
  let socketFailure = null;
  const pending = new Map();
  const rejectPending = (error) => {
    for (const request of pending.values()) {
      clearTimeout(request.timer);
      request.reject(error);
    }
    pending.clear();
  };
  const abortSocket = (error) => {
    if (!socketFailure) socketFailure = error;
    rejectPending(socketFailure);
    try { ws.close(); } catch {}
  };
  ws.addEventListener('message', (e) => {
    let d;
    try {
      d = JSON.parse(String(e.data));
    } catch (error) {
      abortSocket(error);
      return;
    }
    if (d.op === 0) hello = d.d || {};
    else if (d.op === 2) ready = true;
    else if (d.op === 7) {
      const x = d.d || {}, pr = pending.get(x.requestId);
      if (pr) {
        pending.delete(x.requestId);
        clearTimeout(pr.timer);
        x.requestStatus?.result
          ? pr.resolve(x.responseData || {})
          : pr.reject(new Error(x.requestStatus?.comment || x.requestStatus?.code));
      }
    }
  });
  ws.addEventListener('close', () => {
    if (!socketFailure) socketFailure = new Error('OBS websocket closed');
    rejectPending(socketFailure);
  });
  ws.addEventListener('error', () => {
    if (!socketFailure) socketFailure = new Error('OBS websocket error');
    rejectPending(socketFailure);
  });

  try {
    await new Promise((resolve, reject) => {
      let settled = false;
      const finish = (callback, value) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        callback(value);
      };
      const timer = setTimeout(
        () => finish(reject, new Error('connect timeout')),
        requestTimeoutMs,
      );
      ws.addEventListener('open', () => finish(resolve));
      ws.addEventListener('error', () => finish(reject, socketFailure || new Error('connect error')));
      ws.addEventListener('close', () => finish(reject, socketFailure || new Error('connect closed')));
    });

    let deadline = Date.now() + requestTimeoutMs;
    while (!hello) {
      if (socketFailure) throw socketFailure;
      if (Date.now() >= deadline) throw new Error('hello timeout');
      await sleep(25);
    }
    const id = { op: 1, d: { rpcVersion: 1, eventSubscriptions: 0 } };
    if (hello.authentication?.challenge) {
      id.d.authentication = sha(sha(password + hello.authentication.salt) + hello.authentication.challenge);
    }
    ws.send(JSON.stringify(id));
    deadline = Date.now() + requestTimeoutMs;
    while (!ready) {
      if (socketFailure) throw socketFailure;
      if (Date.now() >= deadline) throw new Error('identify timeout');
      await sleep(25);
    }
  } catch (error) {
    abortSocket(error);
    throw error;
  }

  return {
    req: (requestType, requestData = {}) => {
      if (socketFailure) return Promise.reject(socketFailure);
      const requestId = `wd-${++seq}`;
      return new Promise((resolve, reject) => {
        const timer = setTimeout(() => {
          pending.delete(requestId);
          reject(new Error(`${requestType} timed out after ${requestTimeoutMs}ms`));
        }, requestTimeoutMs);
        pending.set(requestId, { resolve, reject, timer });
        try {
          ws.send(JSON.stringify({
            op: 6,
            d: { requestType, requestId, requestData },
          }));
        } catch (error) {
          pending.delete(requestId);
          clearTimeout(timer);
          reject(error);
        }
      });
    },
    close: () => abortSocket(new Error('OBS connection closed')),
  };
}

async function shot(obs) {
  const r = await obs.req('GetSourceScreenshot', { sourceName: NAME, imageFormat: 'jpg', imageWidth: 320, imageHeight: 180, imageCompressionQuality: 50 });
  return sha(String(r.imageData || ''));
}

// The live window (itemName/itemValue) whose title matches the current mode.
async function liveWindow(obs) {
  const wins = await obs.req('GetInputPropertiesListPropertyItems', {
    inputName: NAME,
    propertyName: WINDOW_PROPERTY,
  });
  const re = expectedRe();
  return (wins.propertyItems || []).find((w) => re.test(w.itemName || '')) || null;
}

// The platform window identifier currently bound to the source (null if unset).
async function boundWindow(obs) {
  const r = await obs.req('GetInputSettings', { inputName: NAME });
  const s = r.inputSettings || {};
  const value = s[WINDOW_PROPERTY];
  return (value === undefined || value === null) ? null : value;
}

export function captureRecoveryPlan(value, reason = 'stale') {
  // xshm_input captures the selected display, not a window. Chrome relaunches do
  // not invalidate it, and repeating SetInputSettings would only reinitialize a
  // healthy screen source. Keep both stale and frozen recovery mutation-free.
  if (IS_XSHM) return [];

  if (IS_XCOMPOSITE) {
    if (reason === 'frozen' && !XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED) return [];
    return [{
      requestType: 'SetInputSettings',
      requestData: {
        inputName: NAME,
        // Keep OBS's encoded `XID\r\ntitle\r\nclass` itemValue byte-for-byte.
        inputSettings: { [WINDOW_PROPERTY]: String(value) },
        // Merge the new capture_window into the XComposite source. Replacing all
        // settings would reset crop/cursor/border choices that belong to the VM.
        overlay: true,
      },
    }];
  }

  return [
    {
      requestType: 'SetInputSettings',
      requestData: {
        inputName: NAME,
        inputSettings: { application: APP_ID, type: 2, show_cursor: false, capture_audio: false },
        overlay: false,
      },
    },
    {
      requestType: 'SetInputSettings',
      requestData: {
        inputName: NAME,
        inputSettings: {
          application: APP_ID,
          type: 1,
          show_cursor: false,
          show_empty_names: false,
          capture_audio: false,
          ...(value == null ? {} : { [WINDOW_PROPERTY]: value }),
        },
        overlay: false,
      },
    },
  ];
}

// Apply one recovery plan through the OBS-WebSocket-shaped client. `lockOverride`
// and `sleepFn` are dependency-injection seams for behavior tests; production
// keeps the established macOS lock and exact 2.5 second SCStream teardown wait.
export async function recoverCapture(
  obs,
  value,
  reason = 'stale',
  { lockOverride, sleepFn = sleep } = {},
) {
  const requests = captureRecoveryPlan(value, reason);
  if (requests.length === 0) {
    return {
      action: 'no_action',
      reason: IS_XSHM
        ? 'xshm_screen_capture_never_rebound'
        : 'xcomposite_same_value_bounce_disabled',
      requestCount: 0,
    };
  }

  if (IS_XCOMPOSITE) {
    const request = requests[0];
    await obs.req(request.requestType, request.requestData);
    return { action: reason === 'stale' ? 'rebound' : 'bounced', requestCount: 1 };
  }

  const lock = lockOverride || await obsLock();
  const held = await lock.acquireObsSourceLock();
  try {
    await obs.req(requests[0].requestType, requests[0].requestData);
    // Wait long enough for mac-capture to fully teardown the old SCStream before the
    // second SetInputSettings arrives. 800ms was too short, causing a double-free in
    // mac-capture when OBS thread-pool workers raced on the same source object.
    await sleepFn(2500);
    await obs.req(requests[1].requestType, requests[1].requestData);
  } finally {
    await lock.releaseObsSourceLock(held);
  }
  return { action: reason === 'stale' ? 'rebound' : 'bounced', requestCount: 2 };
}

// Run one watchdog cycle through an OBS-WebSocket-shaped client. XSHM has no
// window binding to validate, so its only OBS requests are the three screenshots.
// Dependency injection keeps the exact production behavior testable without
// sleeping or connecting to a real OBS instance.
export async function checkCapture(
  obs,
  {
    sleepFn = sleep,
    gameStateSigFn = gameStateSig,
    modeFn = currentMode,
    logger = console,
    screenshotDelayMs = 4000,
  } = {},
) {
  const mode = modeFn();
  let live = null;
  let bound = null;

  if (!IS_XSHM) {
    // ---- 1. Binding-validity (deterministic window-capture families only) ----
    try { live = await liveWindow(obs); } catch (e) { /* fall through */ }
    try { bound = await boundWindow(obs); } catch (e) { /* fall through */ }

    if (!live) {
      // No window matches the expected title right now (Chrome relaunching, or title
      // not yet registered). Never blank a last-good capture — leave it and retry.
      logger.log(`no live window for mode=${mode} (bound=${bound}) -> leaving capture untouched`);
      return { exitCode: 0, action: 'no_live_window', captureFamily: CAPTURE_FAMILY };
    }

    if (String(bound) !== String(live.itemValue)) {
      logger.log(`STALE BINDING (${mode}): bound=${bound} live=${live.itemValue} [${live.itemName}] -> rebinding`);
      await recoverCapture(obs, live.itemValue, 'stale');
      logger.log('REBOUND');
      return { exitCode: 11, action: 'rebound', captureFamily: CAPTURE_FAMILY };
    }
  }

  // ---- 2. Pixel-freeze signal ----
  const gs0 = gameStateSigFn();
  const a = await shot(obs); await sleepFn(screenshotDelayMs);
  const b = await shot(obs); await sleepFn(screenshotDelayMs);
  const c = await shot(obs);
  const gs1 = gameStateSigFn();
  const framesIdentical = (a === b && b === c);
  const gameAdvanced = (gs0 !== '' && gs1 !== '' && gs0 !== gs1);

  if (framesIdentical && gameAdvanced) {
    if (IS_XSHM) {
      // XSHM captures a whole display and remains bound across Chrome restarts.
      // A static screenshot is only a warning signal; never re-send its settings.
      logger.warn(
        `FROZEN SIGNAL (${mode}): identical XSHM screen frames over 8s while game_state advanced; `
        + 'screen capture is never rebound or bounced -> no action',
      );
      return {
        exitCode: 0,
        action: 'warned_no_mutation',
        captureFamily: CAPTURE_FAMILY,
        framesIdentical,
        gameAdvanced,
      };
    }

    if (IS_XCOMPOSITE && !XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED) {
      // OBS 30.0.2's xcompcap_update registers the selected window again without
      // first unregistering the existing watcher. Repeated same-value updates can
      // therefore duplicate watcher-registry entries. Xvfb can also return black
      // GetSourceScreenshot frames while the underlying X11 window is healthy.
      // Treat this as a warning signal, not permission to mutate the binding.
      logger.warn(
        `FROZEN SIGNAL (${mode}): identical OBS frames over 8s while game_state advanced; `
        + 'XComposite same-value bounce is disabled -> no action '
        + '(set OBS_XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED=1 only after VM validation)',
      );
      return {
        exitCode: 0,
        action: 'warned_no_mutation',
        captureFamily: CAPTURE_FAMILY,
        framesIdentical,
        gameAdvanced,
      };
    }

    logger.log(`FROZEN (${mode}): identical OBS frames over 8s while game_state advanced -> bouncing`);
    await recoverCapture(obs, live.itemValue, 'frozen');
    logger.log('BOUNCED');
    return {
      exitCode: 10,
      action: 'bounced',
      captureFamily: CAPTURE_FAMILY,
      framesIdentical,
      gameAdvanced,
    };
  }

  if (framesIdentical) {
    logger.log(`inconclusive (${mode}): frames identical but game_state did not advance (game idle) -> no action`);
    return {
      exitCode: 0,
      action: 'inconclusive',
      captureFamily: CAPTURE_FAMILY,
      framesIdentical,
      gameAdvanced,
    };
  }

  if (IS_XSHM) {
    logger.log(`OK (${mode}): XSHM screen capture live (frames differ)`);
  } else {
    logger.log(`OK (${mode}): capture live (frames differ), binding valid (window=${bound})`);
  }
  return {
    exitCode: 0,
    action: 'ok',
    captureFamily: CAPTURE_FAMILY,
    framesIdentical,
    gameAdvanced,
  };
}

async function runWatchdog() {
  if (process.argv.includes('--print-config') || process.env.OBS_CAPTURE_WATCHDOG_CONFIG_ONLY === '1') {
    console.log(JSON.stringify({
      capturePlatform: CAPTURE_PLATFORM,
      inputKind: INPUT_KIND,
      captureFamily: CAPTURE_FAMILY,
      isXComposite: IS_XCOMPOSITE,
      isXshm: IS_XSHM,
      bindingValidationEnabled: !IS_XSHM,
      captureCheckAction: IS_XSHM
        ? 'screenshot_only_no_mutation'
        : 'binding_then_screenshot',
      windowProperty: WINDOW_PROPERTY,
      obsLogDir: process.env.OBS_LOG_DIR || DEFAULT_OBS_LOG_DIR,
      requestTimeoutMs: REQUEST_TIMEOUT_MS,
      xcompositeSameValueBounceEnabled: XCOMPOSITE_SAME_VALUE_BOUNCE_ENABLED,
      staleRecoveryPlan: captureRecoveryPlan('test-window', 'stale'),
      frozenRecoveryPlan: captureRecoveryPlan('test-window', 'frozen'),
    }));
    return 0;
  }

  let obs = null;
  try {
    obs = await connectObs();
  } catch (err) {
    const safeModeLog = obsSafeModePromptHint();
    if (safeModeLog) {
      console.error(`OBS unavailable: Safe Mode prompt is blocking websocket startup (latest_log=${safeModeLog.fullPath}). Choose normal startup in OBS, then watchdog can recheck capture.`);
    } else {
      console.error('OBS unavailable:', err?.message || String(err));
    }
    return 2;
  }
  if (!obs) { console.log('OBS not configured'); return 2; }
  try {
    const result = await checkCapture(obs);
    return result.exitCode;
  } catch (err) {
    console.error('watchdog check error:', err?.message || String(err));
    return 2;
  } finally { if (obs) obs.close(); }
}

const invokedAsScript = process.argv[1]
  && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (invokedAsScript) process.exitCode = await runWatchdog();

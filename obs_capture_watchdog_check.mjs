// OBS game-capture self-heal: keeps the shared `sorengame` window-capture pointed
// at the LIVE Chrome game window and recovers when it goes stale or freezes.
//
// Two independent recovery signals:
//   1. BINDING VALIDITY (deterministic, fast): read the source's currently bound
//      macOS window id and compare it to the live window that matches the expected
//      title for the CURRENT display mode (china vs meriken, from
//      tmp/state/soren_display_mode). If the bound window is gone/stale/wrong, the
//      capture is showing a dead window's last frame (the classic symptom after a
//      Chrome crash/restart) -> rebind to the live window immediately. This needs
//      neither an 8s pixel sample nor game_state to be advancing, so it heals the
//      crash/restart case fast and in either display mode.
//   2. PIXEL FREEZE (fallback): if the binding is correct but the SCStream itself
//      stalled, 3 screenshots over ~8s are identical while game_state advances ->
//      bounce the capture (window->application->window) to recreate the SCStream.
//
// Safety: if NO live window matches the expected title (e.g. Chrome mid-relaunch),
// the source is left UNTOUCHED — we never blank a working/last-good capture.
//
// Exit codes: 0 = ok / no action, 10 = was frozen and bounced,
//             11 = binding was stale and rebound, 2 = error/skip.
//
// Reuses soviet_local.mjs's OBS WS v5 handshake. Env:
//   OBS_WEBSOCKET_PORT / OBS_WEBSOCKET_PASSWORD / OBS_WEBSOCKET_HOST
//   SOREN_OBS_GAME_SOURCE_NAME (default sorengame)
//   SOREN_DISPLAY_STATE_FILE   (default tmp/state/soren_display_mode)
//   SOREN_CHINA_WINDOW_REGEX   (default "Unity WebGL Player \\| soren-game")
//   SOREN_MERIKEN_WINDOW_REGEX (default "91人対戦|ソ連ゲーム91")
//   SOREN_CHROME_APP_ID        (default com.google.chrome.for.testing)
import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

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
  const dir = process.env.OBS_LOG_DIR || path.join(os.homedir(), 'Library', 'Application Support', 'obs-studio', 'logs');
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

async function connectObs() {
  const port = process.env.OBS_WEBSOCKET_PORT;
  const password = process.env.OBS_WEBSOCKET_PASSWORD;
  if (!port || !password || typeof WebSocket !== 'function') return null;
  const host = process.env.OBS_WEBSOCKET_HOST || '127.0.0.1';
  const ws = new WebSocket(`ws://${host}:${Number(port)}`);
  let hello = null, ready = false, seq = 0;
  const pending = new Map();
  ws.addEventListener('message', (e) => {
    const d = JSON.parse(String(e.data));
    if (d.op === 0) hello = d.d || {};
    else if (d.op === 2) ready = true;
    else if (d.op === 7) {
      const x = d.d || {}, pr = pending.get(x.requestId);
      if (pr) { pending.delete(x.requestId); x.requestStatus?.result ? pr.resolve(x.responseData || {}) : pr.reject(new Error(x.requestStatus?.comment || x.requestStatus?.code)); }
    }
  });
  await new Promise((res, rej) => { const t = setTimeout(() => rej(new Error('connect timeout')), 3000); ws.addEventListener('open', () => { clearTimeout(t); res(); }); ws.addEventListener('error', () => rej(new Error('connect error'))); });
  let dl = Date.now() + 3000; while (!hello) { if (Date.now() > dl) throw new Error('hello timeout'); await sleep(25); }
  const id = { op: 1, d: { rpcVersion: 1, eventSubscriptions: 0 } };
  if (hello.authentication?.challenge) id.d.authentication = sha(sha(password + hello.authentication.salt) + hello.authentication.challenge);
  ws.send(JSON.stringify(id));
  dl = Date.now() + 3000; while (!ready) { if (Date.now() > dl) throw new Error('identify timeout'); await sleep(25); }
  return {
    req: (t, d = {}) => { const i = `wd-${++seq}`; ws.send(JSON.stringify({ op: 6, d: { requestType: t, requestId: i, requestData: d } })); return new Promise((rs, rj) => pending.set(i, { resolve: rs, reject: rj })); },
    close: () => { try { ws.close(); } catch {} },
  };
}

async function shot(obs) {
  const r = await obs.req('GetSourceScreenshot', { sourceName: NAME, imageFormat: 'jpg', imageWidth: 320, imageHeight: 180, imageCompressionQuality: 50 });
  return sha(String(r.imageData || ''));
}

// The live window (itemName/itemValue) whose title matches the current mode.
async function liveWindow(obs) {
  const wins = await obs.req('GetInputPropertiesListPropertyItems', { inputName: NAME, propertyName: 'window' });
  const re = expectedRe();
  return (wins.propertyItems || []).find((w) => re.test(w.itemName || '')) || null;
}

// The macOS window id currently bound to the source (null if unset).
async function boundWindow(obs) {
  const r = await obs.req('GetInputSettings', { inputName: NAME });
  const s = r.inputSettings || {};
  return (s.window === undefined || s.window === null) ? null : s.window;
}

// Recreate the SCStream and point it at `value`. window->application->window forces
// macOS to drop the old (possibly dead) capture and grab the target window fresh.
async function bounce(obs, value) {
  const lock = await obsLock();
  const held = await lock.acquireObsSourceLock();
  try {
    await obs.req('SetInputSettings', { inputName: NAME, inputSettings: { application: APP_ID, type: 2, show_cursor: false, capture_audio: false }, overlay: false });
    // Wait long enough for mac-capture to fully teardown the old SCStream before the
    // second SetInputSettings arrives. 800ms was too short, causing a double-free in
    // mac-capture when OBS thread-pool workers raced on the same source object.
    await sleep(2500);
    const settings = { application: APP_ID, type: 1, show_cursor: false, show_empty_names: false, capture_audio: false };
    if (value != null) settings.window = value;
    await obs.req('SetInputSettings', { inputName: NAME, inputSettings: settings, overlay: false });
  } finally {
    await lock.releaseObsSourceLock(held);
  }
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
  process.exit(2);
}
if (!obs) { console.log('OBS not configured'); process.exit(2); }
try {
  const mode = currentMode();

  // ---- 1. Binding-validity (deterministic) ----
  let live = null, bound = null;
  try { live = await liveWindow(obs); } catch (e) { /* fall through */ }
  try { bound = await boundWindow(obs); } catch (e) { /* fall through */ }

  if (!live) {
    // No window matches the expected title right now (Chrome relaunching, or title
    // not yet registered). Never blank a last-good capture — leave it and retry next cycle.
    console.log(`no live window for mode=${mode} (bound=${bound}) -> leaving capture untouched`);
    process.exit(0);
  }

  if (String(bound) !== String(live.itemValue)) {
    console.log(`STALE BINDING (${mode}): bound=${bound} live=${live.itemValue} [${live.itemName}] -> rebinding`);
    await bounce(obs, live.itemValue);
    console.log('REBOUND');
    process.exit(11);
  }

  // ---- 2. Pixel-freeze fallback (binding correct, stream stalled) ----
  const gs0 = gameStateSig();
  const a = await shot(obs); await sleep(4000);
  const b = await shot(obs); await sleep(4000);
  const c = await shot(obs);
  const gs1 = gameStateSig();
  const framesIdentical = (a === b && b === c);
  const gameAdvanced = (gs0 !== '' && gs1 !== '' && gs0 !== gs1);
  if (framesIdentical && gameAdvanced) {
    console.log(`FROZEN (${mode}): identical OBS frames over 8s while game_state advanced -> bouncing`);
    await bounce(obs, live.itemValue);
    console.log('BOUNCED');
    process.exit(10);
  }
  if (framesIdentical && !gameAdvanced) {
    console.log(`inconclusive (${mode}): frames identical but game_state did not advance (game idle) -> no action`);
    process.exit(0);
  }
  console.log(`OK (${mode}): capture live (frames differ), binding valid (window=${bound})`);
  process.exit(0);
} catch (err) {
  console.error('watchdog check error:', err?.message || String(err));
  process.exit(2);
} finally { if (obs) obs.close(); }

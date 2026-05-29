// OBS game-capture freeze detector + self-heal.
// Takes 3 screenshots of the `sorengame` source over ~8s. If ALL are identical
// (output frozen) it re-initializes the macOS screen_capture by bouncing the
// capture mode (window -> application -> window), which recreates the SCStream.
// Exit codes: 0 = ok / no action, 10 = was frozen and bounced, 2 = error/skip.
//
// Reuses soviet_local.mjs's OBS WS v5 handshake. Env: OBS_WEBSOCKET_PORT/PASSWORD/HOST.
import crypto from 'node:crypto';
import fs from 'node:fs';

const NAME = process.env.SOREN_OBS_GAME_SOURCE_NAME || 'sorengame';
const gameStateSig = () => { try { const s = fs.statSync('game_state.json'); return `${s.mtimeMs}:${s.size}`; } catch { return ''; } };
const sha = (s) => crypto.createHash('sha256').update(s).digest('base64');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

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

async function liveWindow(obs) {
  const wins = await obs.req('GetInputPropertiesListPropertyItems', { inputName: NAME, propertyName: 'window' });
  const t = (wins.propertyItems || []).find((w) => /Unity WebGL Player \| soren-game/.test(w.itemName || ''));
  return t ? t.itemValue : null;
}

async function bounce(obs) {
  const win = await liveWindow(obs);
  await obs.req('SetInputSettings', { inputName: NAME, inputSettings: { application: 'com.google.chrome.for.testing', type: 2, show_cursor: false, capture_audio: false }, overlay: false });
  await sleep(800);
  const settings = { application: 'com.google.chrome.for.testing', type: 1, show_cursor: false, capture_audio: false };
  if (win != null) settings.window = win;
  await obs.req('SetInputSettings', { inputName: NAME, inputSettings: settings, overlay: false });
}

const obs = await connectObs();
if (!obs) { console.log('OBS not configured'); process.exit(2); }
try {
  const gs0 = gameStateSig();
  const a = await shot(obs); await sleep(4000);
  const b = await shot(obs); await sleep(4000);
  const c = await shot(obs);
  const gs1 = gameStateSig();
  const framesIdentical = (a === b && b === c);
  const gameAdvanced = (gs0 !== '' && gs1 !== '' && gs0 !== gs1);
  if (framesIdentical && gameAdvanced) {
    // OBS output stuck while the game itself advanced => capture is frozen.
    console.log('FROZEN: identical OBS frames over 8s while game_state advanced -> bouncing');
    await bounce(obs);
    console.log('BOUNCED');
    process.exit(10);
  }
  if (framesIdentical && !gameAdvanced) {
    console.log('inconclusive: frames identical but game_state did not advance (game idle) -> no action');
    process.exit(0);
  }
  console.log('OK: capture is live (frames differ)');
  process.exit(0);
} catch (err) {
  console.error('watchdog check error:', err?.message || String(err));
  process.exit(2);
} finally { obs.close(); }

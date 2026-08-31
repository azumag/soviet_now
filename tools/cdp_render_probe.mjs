// Read-only CDP probe (Issue docich#23): measures rAF cadence, Unity fps (__sorenRenderStats),
// canvas backing vs CSS size and overlay iframes on a live page of the bridge Chrome.
// NOTE: once browser_frame_limiter is installed, window.requestAnimationFrame is the
// throttled one and the native function is not reachable (Window.prototype has no rAF),
// so nativeRafHz here reports the limiter cadence on production pages.
// usage: node cdp_probe.mjs [port] [urlSubstring] [seconds]
const port = process.argv[2] || '9322';
const match = process.argv[3] || 'localhost:8080';
const secs = Number(process.argv[4] || 3);
let WS = globalThis.WebSocket;
if (!WS) { WS = (await import('ws')).default; }
const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
const t = targets.find(x => (x.url || '').includes(match));
if (!t) { console.log(JSON.stringify({ error: 'no target', targets: targets.map(x => [x.type, x.url]) })); process.exit(1); }
const ws = new WS(t.webSocketDebuggerUrl);
let id = 0; const pending = new Map();
const send = (method, params = {}) => new Promise((res, rej) => { const i = ++id; pending.set(i, { res, rej }); ws.send(JSON.stringify({ id: i, method, params })); });
ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.rej(new Error(JSON.stringify(m.error))) : p.res(m.result); } };
await new Promise((r) => { ws.onopen = r; });
const expr = `(async () => {
  const S = ${secs * 1000};
  const nativeRaf = Window.prototype.requestAnimationFrame ? Window.prototype.requestAnimationFrame.bind(window) : null;
  const patched = Object.prototype.hasOwnProperty.call(window, 'requestAnimationFrame');
  let n = 0, w = 0; const t0 = performance.now(); const deltas = [];
  let last = t0;
  await new Promise((r) => {
    const f = (ts) => { n++; deltas.push(ts - last); last = ts; if (performance.now() - t0 < S) (nativeRaf || requestAnimationFrame)(f); else r(); };
    (nativeRaf || requestAnimationFrame)(f);
  });
  const el = (performance.now() - t0) / 1000;
  deltas.shift(); deltas.sort((a, b) => a - b);
  const pct = (p) => deltas.length ? Number(deltas[Math.floor((deltas.length - 1) * p)].toFixed(1)) : null;
  const cs = [...document.querySelectorAll('canvas')].map((c) => ({ id: c.id, w: c.width, h: c.height, cw: c.clientWidth, ch: c.clientHeight, vis: getComputedStyle(c).visibility, disp: getComputedStyle(c).display }));
  const anims = (document.getAnimations ? document.getAnimations().length : -1);
  const frames = [...document.querySelectorAll('iframe')].map((f) => ({ id: f.id, src: (f.src || '').slice(0, 80), srcdoc: !!f.srcdoc, w: f.clientWidth, h: f.clientHeight }));
  return JSON.stringify({ url: location.href, patchedRaf: patched, nativeRafHz: Number((n / el).toFixed(1)), deltaMs: { p10: pct(0.1), p50: pct(0.5), p90: pct(0.9), max: pct(1) }, renderStats: window.__sorenRenderStats || null, renderPaused: !!window.__sorenRenderPaused, vis: document.visibilityState, hasFocus: document.hasFocus(), dpr: devicePixelRatio, inner: [innerWidth, innerHeight], canvases: cs, animations: anims, iframes: frames, unity: !!(window.unityInstance || window.__unityInstance || window.gameInstance) });
})()`;
try {
  const r = await send('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
  console.log(r.result.value);
} catch (e) { console.log(JSON.stringify({ error: e.message })); }
ws.close();

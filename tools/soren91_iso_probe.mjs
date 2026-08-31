// Isolated Soren91 rendering-cost probe (Issue docich#23, 2026-08-27).
// Runs a SEPARATE Chrome for Testing on a SEPARATE Xvfb display (default :98) with its
// own profile and its own pulse null sink, so the live stream on :99 is never touched.
// Loads the unityroom Soren91 game, injects config.devicePixelRatio (the only way to
// really shrink Unity's draw buffer: Unity resets canvas.width/height to CSS size x DPR
// every frame when matchWebGLToCanvasSize is left at its default), caps rAF with the
// production browser_frame_limiter, joins a match, and measures native rAF cadence,
// Unity fps, drawing-buffer size, per-process CPU (incl. the live main-game Chrome and
// ffmpeg for context), pulse playback presence and the live stream's ffmpeg stats.
// Options: gl=swiftshader|angle-gl|angle-vulkan  dpr=0.5  fps=30  secs=60  mode=app|kiosk
//          nolimit=1  aa=1|0  pause_main=1 (pause the LIVE main Unity via CDP :9322; use
//          only at a game boundary, see handoff 2026-08-27)  tag=name  app_url=...
// Results (2026-08-27, VM A1 4vCPU): ~110-120 ms CPU per frame in the GPU process at
// 480x270, single-thread bound -> 6-9 fps even with the main game paused. Resolution
// (240x135: 15 Hz) and llvmpipe (angle-gl: 6 Hz) do not change the verdict.
//
// usage (cwd=/home/ubuntu/soren):
//   cd /home/ubuntu/soren && node tools/soren91_iso_probe.mjs gl=swiftshader dpr=0.5 fps=30 secs=60 mode=app nolimit=1 tag=r1
import { chromium } from 'playwright';
import fs from 'node:fs';
import { spawn, execSync } from 'node:child_process';
import { installAnimationFrameLimit } from '../browser_frame_limiter.mjs';
// NOTE: run from the soviet_now checkout root (needs ./node_modules/playwright).

const args = Object.fromEntries(process.argv.slice(2).map((a) => { const i = a.indexOf('='); return [a.slice(0, i), a.slice(i + 1)]; }));
const DISPLAY = args.display || ':98';
const DPR = Number(args.dpr || 0.5);
const FPS = Number(args.fps || 30);
const SECS = Number(args.secs || 60);
const MODE = args.mode || 'app';          // app | kiosk
const NOLIMIT = (args.nolimit ?? '1') === '1';
const TAG = args.tag || `dpr${DPR}_fps${FPS}_${MODE}`;
const PLAY = (args.play ?? '1') === '1';
const SOREN_DIR = process.env.SOREN_DIR || '/home/ubuntu/soren';
const PROFILE = `${SOREN_DIR}/tmp/iso_probe_profile`;
const CDP_PORT = Number(args.port || 9333);
const EXE = process.env.SOREN91_ISO_CHROME || '/home/ubuntu/.cache/ms-playwright/chromium-1208/chrome-linux/chrome';
const PULSE_SINK = 'iso_probe_sink';
const OUT = `${SOREN_DIR}/tmp/iso_probe_${TAG}`;
const GAME_W = 960, GAME_H = 540, GAME_TOP = 90;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const log = (m) => console.log(`[${new Date().toISOString().slice(11, 19)}] ${m}`);
const sh = (cmd) => { try { return execSync(cmd, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return ''; } };


// --- optional: pause/unpause the LIVE main game's Unity rendering (bridge Chrome on :9322) ---
async function mainRender(paused) {
  const targets = await (await fetch('http://127.0.0.1:9322/json')).json();
  const t = targets.find((x) => x.type === 'page' && /localhost:8080/.test(x.url || ''));
  if (!t) { log('main page target not found'); return null; }
  const ws = new WebSocket(t.webSocketDebuggerUrl);
  await new Promise((r) => { ws.onopen = r; });
  const res = await new Promise((resolve) => {
    ws.onmessage = (ev) => { const m = JSON.parse(ev.data); if (m.id === 1) resolve(m.result?.result?.value); };
    ws.send(JSON.stringify({ id: 1, method: 'Runtime.evaluate', params: { expression: `(() => { window.__sorenRenderPaused = ${paused}; return JSON.stringify({ paused: window.__sorenRenderPaused, stats: window.__sorenRenderStats }); })()`, returnByValue: true } }));
  });
  ws.close();
  log(`main render paused=${paused}: ${res}`);
  return res;
}
const PAUSE_MAIN = (args.pause_main ?? '0') === '1';

// --- Xvfb ---
const dispNum = DISPLAY.replace(':', '');
if (!fs.existsSync(`/tmp/.X11-unix/X${dispNum}`)) {
  log(`starting Xvfb ${DISPLAY}`);
  const xv = spawn('Xvfb', [DISPLAY, '-screen', '0', '1280x720x24', '-nolisten', 'tcp'], { detached: true, stdio: 'ignore' });
  xv.unref();
  await sleep(1500);
}
// --- pulse sink (audio must NOT reach the live sink) ---
if (!sh(`pactl list short sinks`).includes(PULSE_SINK)) {
  sh(`pactl load-module module-null-sink sink_name=${PULSE_SINK} sink_properties=device.description=iso_probe`);
  log(`pulse null sink ${PULSE_SINK} loaded`);
}

// --- game url ---
const html = await (await fetch('https://unityroom.com/games/sorengame91', { headers: { 'user-agent': 'Mozilla/5.0', 'accept-language': 'ja' } })).text();
const m = html.match(/(?:src|href)=["']([^"']*play\.unityroom\.com[^"']*)["']/i);
if (!m) throw new Error('game url not found');
const gameUrl = new URL(m[1].replace(/&amp;/g, '&'), 'https://unityroom.com/').href;
log(`game url ok (${gameUrl.slice(0, 40)}...)`);

// --- spawn chrome ---
fs.rmSync(PROFILE, { recursive: true, force: true });
const chromeArgs = [
  '--no-sandbox', '--disable-dev-shm-usage',
  ...((args.gl || 'swiftshader') === 'swiftshader' ? ['--enable-unsafe-swiftshader'] : []),
  ...(args.gl === 'angle-gl' ? ['--use-gl=angle', '--use-angle=gl', '--ignore-gpu-blocklist'] : []),
  ...(args.gl === 'angle-vulkan' ? ['--use-gl=angle', '--use-angle=vulkan', '--ignore-gpu-blocklist', '--enable-features=Vulkan'] : []),
  ...(args.gl === 'egl' ? ['--use-gl=egl', '--ignore-gpu-blocklist'] : []),
  '--no-first-run', '--no-default-browser-check', '--disable-infobars', '--test-type',
  '--hide-crash-restore-bubble', '--disable-session-crashed-bubble', '--disable-crash-reporter', '--disable-crashpad',
  '--password-store=basic', '--use-mock-keychain', '--disable-translate',
  '--autoplay-policy=no-user-gesture-required', '--disable-background-timer-throttling',
  '--disable-backgrounding-occluded-windows', '--disable-renderer-backgrounding',
  '--disable-features=Translate,MediaRouter,DialMediaRouteProvider,GlobalMediaControls,HttpsUpgrades,LensOverlay,OptimizationHints',
  `--user-data-dir=${PROFILE}`, `--remote-debugging-port=${CDP_PORT}`,
];
if (NOLIMIT) chromeArgs.push('--disable-frame-rate-limit', '--disable-gpu-vsync');
if (MODE === 'kiosk') chromeArgs.push('--kiosk', '--window-size=1280,720', 'about:blank');
else chromeArgs.push(`--window-size=${GAME_W},${GAME_H}`, `--window-position=0,${GAME_TOP}`, `--app=${args.app_url || 'http://127.0.0.1:18080/'}`);
const chrome = spawn(EXE, chromeArgs, { detached: true, stdio: 'ignore', env: { ...process.env, DISPLAY, PULSE_SINK, HOME: process.env.HOME } });
chrome.unref();
log(`chrome spawned pid=${chrome.pid} mode=${MODE} nolimit=${NOLIMIT}`);

let browser = null;
for (let i = 0; i < 40 && !browser; i++) {
  try { browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`, { timeout: 2000 }); } catch { await sleep(250); }
}
if (!browser) throw new Error('cdp connect failed');
const context = browser.contexts()[0];
let page = context.pages()[0] || await context.newPage();
log(`attached; pages=${context.pages().length} url=${page.url()}`);

// --- window shaping (app mode): strip WM decorations, pin geometry ---
const winId = () => sh(`DISPLAY=${DISPLAY} xdotool search --sync --onlyvisible --pid ${chrome.pid} 2>/dev/null | head -1`) || sh(`DISPLAY=${DISPLAY} xdotool search --onlyvisible --class chromium | head -1`);
if (MODE === 'app') {
  await sleep(500);
  const id = winId();
  if (id) {
    sh(`DISPLAY=${DISPLAY} xprop -id ${id} -f _MOTIF_WM_HINTS 32c -set _MOTIF_WM_HINTS "0x2, 0x0, 0x0, 0x0, 0x0"`);
    sh(`DISPLAY=${DISPLAY} wmctrl -i -r ${id} -e 0,0,${GAME_TOP},${GAME_W},${GAME_H}`);
    sh(`DISPLAY=${DISPLAY} wmctrl -i -r ${id} -b add,above`);
    log(`window ${id} undecorated+pinned: ${sh(`DISPLAY=${DISPLAY} xwininfo -id ${id} | grep -E "geometry|Absolute"`).replace(/\s+/g, ' ')}`);
  } else log('window id not found');
}

// --- page instrumentation ---
await page.addInitScript(() => { window.__isoNativeRaf = window.requestAnimationFrame.bind(window); });
await page.addInitScript(installAnimationFrameLimit, { renderFps: FPS });
await page.route('**/*play.unityroom.com/**', async (route) => {
  if (route.request().resourceType() !== 'document') return route.continue();
  const response = await route.fetch();
  let body = await response.text();
  const aaAttr = (args.aa ?? '1') === '0' ? 'webglContextAttributes: { antialias: false, powerPreference: "low-power" }, ' : '';
  body = body.replace('companyName: "empty",', `companyName: "empty", devicePixelRatio: ${DPR}, ${aaAttr}`);
  body = body.replace('.then((unityInstance) => {', '.then((unityInstance) => { window.__unityInstance = unityInstance;');
  await route.fulfill({ response, body });
});
page.on('dialog', async (d) => { log(`DIALOG: ${d.type()} ${d.message().slice(0, 80)}`); try { await d.dismiss(); } catch {} });
await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 45000 });
await page.waitForSelector('canvas', { timeout: 60000 });
for (let i = 0; i < 90; i++) {
  await sleep(1000);
  const loaded = await page.evaluate(() => { const bar = document.getElementById('unity-loading-bar'); return !bar || bar.style.display === 'none'; });
  if (loaded) { log(`unity loaded after ${i + 1}s`); break; }
}
await sleep(2000);
// stage css: canvas fills the 960x540 game area (kiosk: at (0,90); app: whole window)
await page.evaluate(({ mode, w, h, top }) => {
  document.body.style.margin = '0'; document.body.style.overflow = 'hidden'; document.body.style.background = '#000';
  document.documentElement.style.background = '#000';
  for (const s of ['#unity-footer', '#unity-loading-bar']) { const e = document.querySelector(s); if (e) e.style.display = 'none'; }
  const c = document.querySelector('#unity-container'); const cv = document.querySelector('#unity-canvas');
  const fix = () => {
    Object.assign(c.style, { position: 'fixed', left: '0px', top: mode === 'kiosk' ? `${top}px` : '0px', width: `${w}px`, height: `${h}px`, overflow: 'hidden', transform: 'none' });
    Object.assign(cv.style, { width: `${w}px`, height: `${h}px`, display: 'block' });
  };
  fix(); window.addEventListener('resize', () => setTimeout(fix, 0));
}, { mode: MODE, w: GAME_W, h: GAME_H, top: GAME_TOP });
sh(`ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x720 -i ${DISPLAY}.0+0,0 -frames:v 1 -y ${OUT}_title.png`);

if (PLAY) {
  const box = await (await page.$('canvas')).boundingBox();
  const px = (fx, fy) => [box.x + box.width * fx, box.y + box.height * fy];
  let [x, y] = px(630 / 1280, 560 / 720);
  await page.mouse.click(x, y); await sleep(500);
  await page.keyboard.press('Control+a'); await page.keyboard.press('Delete');
  await page.keyboard.type('DoCiAI:US', { delay: 40 }); await sleep(300);
  [x, y] = px(630 / 1280, 645 / 720);
  await page.mouse.click(x, y);
  log('title screen: name typed, PLAY clicked');
  await sleep(8000);
}

// --- measurement ---
if (PAUSE_MAIN) await mainRender(true);
const PIDS = () => sh(`pgrep -f ${PROFILE}`).split('\n').filter(Boolean).map(Number);
const cmdOf = (pid) => { try { return fs.readFileSync(`/proc/${pid}/cmdline`, 'utf8').split('\0'); } catch { return []; } };
const typeOf = (pid) => { const a = sh(`ps -o args= -p ${pid}`); const t = a.match(/--type=([a-z-]+)/); if (t) return t[1]; if (/utility-sub-type=([A-Za-z.]+)/.test(a)) return 'utility'; return 'browser'; };
const cpuTicks = (pid) => { try { const f = fs.readFileSync(`/proc/${pid}/stat`, 'utf8'); const p = f.slice(f.lastIndexOf(')') + 2).split(' '); return Number(p[11]) + Number(p[12]); } catch { return null; } };
const CLK = 100;
const LIVE = { main_gpu: Number(sh(`pgrep -f "type=gpu-process.*soviet_local_chromium_profile" | head -1`)), main_renderer_top: Number(sh(`ps -eo pid,pcpu,args --sort=-pcpu | grep "type=renderer.*soviet_local_chromium_profile" | head -1 | awk '{print $1}'`)), ffmpeg: Number(sh(`pgrep -f "ffmpeg -hide_banner.*x11grab" | head -1`)) };
const sample = () => { const s = {}; for (const pid of PIDS()) s[`s91_${typeOf(pid)}_${pid}`] = cpuTicks(pid); for (const [k, pid] of Object.entries(LIVE)) if (pid) s[`live_${k}_${pid}`] = cpuTicks(pid); return s; };
const probeExpr = `(async () => { const S = 20000; const nativeRaf = window.__isoNativeRaf || window.requestAnimationFrame.bind(window); let n = 0; const t0 = performance.now(); const deltas = []; let last = t0;
  await new Promise((r) => { const f = (ts) => { n++; deltas.push(ts - last); last = ts; if (performance.now() - t0 < S) nativeRaf(f); else r(); }; nativeRaf(f); });
  const el = (performance.now() - t0) / 1000; deltas.shift(); deltas.sort((a, b) => a - b); const pct = (p) => deltas.length ? Number(deltas[Math.floor((deltas.length - 1) * p)].toFixed(1)) : null;
  const cv = document.querySelector('#unity-canvas'); const gl = cv.getContext('webgl2') || cv.getContext('webgl'); const ca = gl ? gl.getContextAttributes() : null;
  return JSON.stringify({ nativeRafHz: Number((n / el).toFixed(1)), deltaMs: { p50: pct(0.5), p90: pct(0.9), p99: pct(0.99), max: pct(1) }, renderStats: window.__sorenRenderStats || null, canvas: { w: cv.width, h: cv.height, cw: cv.clientWidth, ch: cv.clientHeight }, drawingBuffer: gl ? [gl.drawingBufferWidth, gl.drawingBufferHeight] : null, ctxAttrs: ca ? { antialias: ca.antialias, alpha: ca.alpha, depth: ca.depth, stencil: ca.stencil, pdb: ca.preserveDrawingBuffer } : null, webgl2: !!(gl && gl.constructor && gl.constructor.name === 'WebGL2RenderingContext'), dpr: devicePixelRatio, moduleDpr: (window.__unityInstance && window.__unityInstance.Module) ? window.__unityInstance.Module.devicePixelRatio : null, inner: [innerWidth, innerHeight], vis: document.visibilityState, renderer: gl ? (gl.getExtension('WEBGL_debug_renderer_info') ? gl.getParameter(gl.getExtension('WEBGL_debug_renderer_info').UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER)) : null }); })()`;
const WIN = 20;
const sAll0 = sample(); const tAll0 = Date.now();
await sleep(Math.max(0, (SECS - WIN - 5) * 1000));
const sinkInputs = sh(`pactl list sink-inputs | grep -E "Sink Input|application.name|Corked|Mute|Sink:" | tr -s " " | paste -sd" "`);
sh(`ffmpeg -hide_banner -loglevel error -f x11grab -video_size 1280x720 -i ${DISPLAY}.0+0,0 -frames:v 1 -y ${OUT}_game.png`);
const s0 = sample(); const t0 = Date.now();
const cdp = await context.newCDPSession(page);
const probePromise = cdp.send('Runtime.evaluate', { expression: probeExpr, awaitPromise: true, returnByValue: true });
await sleep(WIN * 1000 + 500);
const s1 = sample(); const dt = (Date.now() - t0) / 1000;
const cpu = {}; for (const k of Object.keys(s1)) if (s0[k] != null && s1[k] != null) cpu[k] = Number(((s1[k] - s0[k]) / CLK / dt * 100).toFixed(1));
const dtAll = (Date.now() - tAll0) / 1000; const cpuAll = {}; for (const k of Object.keys(s1)) if (sAll0[k] != null && s1[k] != null) cpuAll[k] = Number(((s1[k] - sAll0[k]) / CLK / dtAll * 100).toFixed(1));
const probe = await probePromise.then((r) => r.exceptionDetails ? { error: 'exception', details: r.exceptionDetails.exception?.description || r.exceptionDetails.text, raw: r.result } : JSON.parse(r.result.value)).catch((e) => ({ error: e.message }));
const stream = sh(`cd ${SOREN_DIR} && ./direct_stream.sh status`);
let streamStat = null; try { const j = JSON.parse(stream); streamStat = { fps: j.fps, speed: j.speed, drop: j.drop_frames, dup: j.dup_frames }; } catch {}
const mainStatsDuring = PAUSE_MAIN ? await mainRender(true) : null;
if (PAUSE_MAIN) await mainRender(false);
const result = { tag: TAG, pauseMain: PAUSE_MAIN, mainStatsDuring, gl: args.gl || 'swiftshader', dpr: DPR, fps: FPS, mode: MODE, nolimit: NOLIMIT, aa: args.aa ?? '1', secs: dt, secsAll: dtAll, probe, cpu, cpuAll, sinkInputs, liveStream: streamStat, loadavg: sh('cat /proc/loadavg') };
fs.writeFileSync(`${OUT}.json`, JSON.stringify(result, null, 2));
console.log(JSON.stringify(result, null, 1));

// --- teardown ---
if (PAUSE_MAIN) await mainRender(false).catch(() => {});
try { await cdp.detach(); } catch {}
try { await browser.close(); } catch {}
await sleep(1000);
try { process.kill(-chrome.pid, 'SIGKILL'); } catch {}
for (const pid of PIDS()) { try { process.kill(pid, 'SIGKILL'); } catch {} }
log('done');
process.exit(0);

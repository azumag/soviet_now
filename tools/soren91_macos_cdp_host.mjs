#!/usr/bin/env node
// Soren91 macOS remote-CDP host (Issue #303 Phase 1).
//
// Holds a private CGVirtualDisplay, spawns Google Chrome offscreen with
// --remote-debugging-port on 127.0.0.1, and exposes that port to OCI over a
// TCP proxy bound ONLY to the Mac's Tailscale IPv4. The proxy also accepts
// only the specific OCI Tailscale peer derived from the reviewed SRT target.
//
// The OCI bot navigates the remote Chrome itself (connectOverCDP). This host
// polls the LOCAL CDP /json/list and, once a game page (play.unityroom.com)
// appears, starts the window-targeted capture helper + Chrome-scoped audio
// tap + ffmpeg SRT caller pipeline (same components as
// soren91_macos_session.mjs). Window title is ALWAYS read from the live
// /json/list entry — never assumed.
//
// Usage:
//   SOREN91_LOCAL_SRT_URL='srt://100.71.107.106:9000?mode=caller' \
//     node tools/soren91_macos_cdp_host.mjs --execute
//
// Safety: spawns and owns ONLY its own Chrome/proxy/capture/ffmpeg/tap/
// holder processes; never touches the operator's everyday Chrome. SIGTERM
// stops everything. Capture helper + audio tap binaries must be built
// (tools/macos/bin/). ffmpeg needs libsrt (e.g. ffmpeg-full); pass
// SOREN91_LOCAL_FFMPEG_BIN explicitly when the default ffmpeg lacks SRT.
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import {
  attachPipeGuards,
  buildCaptureArgs,
  isTailscaleIpv4Hostname,
  parseCaptureHelperStatus,
  startCaptureHelper,
  startVirtualDisplay,
  stopVirtualDisplay,
} from './soren91_macos_session.mjs';
import {
  buildAudioFfmpegInputArgs,
  buildFfmpegStdio,
  resolveTapPids,
  startAudioTap,
  stopAudioTap,
} from './soren91_macos_audio.mjs';
import {
  computePhysicalOverlap,
  parseVDisplayList,
  placementFor,
} from './soren91_offscreen_verify.mjs';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const here = path.dirname(fileURLToPath(import.meta.url));

function envFlag(env, name, defaultValue) {
  const raw = env?.[name];
  if (raw == null || raw === '') return defaultValue;
  return !/^(0|false|no|off)$/i.test(String(raw).trim());
}

export function defaults(env = process.env) {
  return {
    execute: false,
    cdpPort: Number(env.SOREN91_CDP_PORT || 9322),
    proxyPort: Number(env.SOREN91_CDP_PROXY_PORT || 19093),
    bindIp: env.SOREN91_CDP_BIND_IP || detectTailscaleIp(),
    width: Number(env.SOREN91_LOCAL_WIDTH || 960),
    height: Number(env.SOREN91_LOCAL_HEIGHT || 540),
    videoMbps: Number(env.SOREN91_LOCAL_VIDEO_MBPS || 2),
    srtUrl: env.SOREN91_LOCAL_SRT_URL || '',
    sessionSec: Number(env.SOREN91_CDP_HOST_SESSION_SEC || 1500),
    pollMs: Number(env.SOREN91_CDP_HOST_POLL_MS || 2000),
    audioTap: envFlag(env, 'SOREN91_LOCAL_AUDIO_TAP', true),
    captureHelperBin: env.SOREN91_LOCAL_CAPTURE_HELPER_BIN
      || path.join(here, 'macos', 'bin', 'soren91_window_capture'),
    audioTapBin: env.SOREN91_LOCAL_AUDIO_TAP_BIN
      || path.join(here, 'macos', 'bin', 'soren91_audio_tap'),
    virtualDisplayBin: env.SOREN91_LOCAL_VIRTUAL_DISPLAY_BIN
      || path.join(here, 'macos', 'bin', 'soren91_virtual_display'),
    chromeBin: env.SOREN91_CDP_CHROME_BIN
      || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    ffmpegBin: env.SOREN91_LOCAL_FFMPEG_BIN || 'ffmpeg',
    resultPath: env.SOREN91_CDP_HOST_RESULT_PATH
      || path.join(os.tmpdir(), 'soren91-macos-cdp-host-result.json'),
  };
}

export function detectTailscaleIp() {
  const found = [];
  for (const addrs of Object.values(os.networkInterfaces() || {})) {
    for (const addr of addrs || []) {
      if (addr?.family === 'IPv4' && !addr.internal && isTailscaleIpv4Hostname(addr.address)) {
        found.push(addr.address);
      }
    }
  }
  return found[0] || '';
}

export function validateOptions(options, platform = process.platform) {
  if (!Number.isInteger(options.cdpPort) || options.cdpPort < 1024 || options.cdpPort > 65535) {
    throw new Error('cdpPort must be 1024..65535');
  }
  if (!Number.isInteger(options.proxyPort) || options.proxyPort < 1024 || options.proxyPort > 65535) {
    throw new Error('proxyPort must be 1024..65535');
  }
  if (options.proxyPort === options.cdpPort) throw new Error('proxyPort must differ from cdpPort');
  // Fail-closed access control: the CDP proxy may ONLY bind a Tailscale IPv4.
  // The listener additionally rejects every source except the reviewed OCI
  // Tailscale peer derived from the SRT target.
  if (!isTailscaleIpv4Hostname(options.bindIp)) {
    throw new Error(
      `bindIp must be a Tailscale IPv4 in 100.64.0.0/10 (got ${JSON.stringify(options.bindIp)}); `
      + 'the CDP proxy must never bind 0.0.0.0 or a LAN address',
    );
  }
  if (options.width !== 960 || options.height !== 540) {
    throw new Error('macOS cdp-host output must be 960x540 (game-canvas crop; 1280x720 full-page is not used)');
  }
  if (!(options.videoMbps > 0 && options.videoMbps <= 8)) throw new Error('videoMbps must be >0 and <=8');
  if (!options.srtUrl && options.execute) throw new Error('--execute requires SOREN91_LOCAL_SRT_URL');
  if (options.execute && platform !== 'darwin') throw new Error('--execute is macOS-only');
  if (options.srtUrl) {
    let target;
    try { target = new URL(options.srtUrl); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
    if (target.protocol !== 'srt:') throw new Error('srtUrl must start with srt://');
    if (!target.port) throw new Error('srtUrl must include an explicit destination port');
    if (target.username || target.password) throw new Error('srtUrl must not contain userinfo credentials');
    for (const key of target.searchParams.keys()) {
      if (key.toLowerCase() === 'passphrase') throw new Error('srtUrl must not contain passphrase credentials');
    }
    const modes = target.searchParams.getAll('mode');
    if (modes.length !== 1 || modes[0].toLowerCase() !== 'caller') {
      throw new Error('srtUrl must explicitly use mode=caller for the OCI listener');
    }
    if (!isTailscaleIpv4Hostname(target.hostname)) {
      throw new Error('srtUrl host must be a Tailscale IPv4 address in 100.64.0.0/10');
    }
  }
  return options;
}

export function parseArgs(argv, env = process.env) {
  const options = defaults(env);
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--execute') { options.execute = true; continue; }
    if (arg === '--cdp-port') { options.cdpPort = Number(argv[++i]); continue; }
    if (arg === '--proxy-port') { options.proxyPort = Number(argv[++i]); continue; }
    if (arg === '--bind-ip') { options.bindIp = argv[++i]; continue; }
    if (arg === '--srt-url') { options.srtUrl = argv[++i]; continue; }
    if (arg === '--width') { options.width = Number(argv[++i]); continue; }
    if (arg === '--height') { options.height = Number(argv[++i]); continue; }
    throw new Error(`unknown argument: ${arg}`);
  }
  return options;
}

async function fetchJsonList(cdpPort) {
  const res = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
  if (!res.ok) throw new Error(`/json/list http=${res.status}`);
  return res.json();
}

export function isExactGameTargetUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return false;
    // Real game pages live on numeric subdomains (e.g.
    // https://74337.play.unityroom.com/...), so the bare host and any
    // single/multi-level subdomain of play.unityroom.com are accepted.
    // The leading-dot check rejects lookalikes such as
    // notplay.unityroom.com or play.unityroom.com.evil.com.
    const host = url.hostname.toLowerCase();
    return host === 'play.unityroom.com' || host.endsWith('.play.unityroom.com');
  } catch {
    return false;
  }
}

export function findGameTarget(targets) {
  const list = Array.isArray(targets) ? targets : [];
  return list.find((t) => t?.type === 'page'
    && typeof t?.url === 'string' && isExactGameTargetUrl(t.url)) || null;
}

export function normalizeRemoteAddress(value) {
  const address = String(value || '').trim();
  const mapped = address.match(/^::ffff:(\d+\.\d+\.\d+\.\d+)$/i);
  return mapped ? mapped[1] : address;
}

// Conventional exit codes for signal-initiated shutdown so the OCI stop
// path (and process supervisors) can distinguish a requested stop from a
// crash. The cdp-host MUST exit on SIGTERM/SIGINT: merely cleaning up
// children and returning to the wait loop leaves this process (and the
// agent's running=true) behind, wedging the next run with a stale 409.
export function signalExitCode(signal) {
  return signal === 'SIGINT' ? 130 : 143;
}

export function isAllowedCdpPeer(remoteAddress, allowedPeerIp) {
  const peer = normalizeRemoteAddress(remoteAddress);
  const allowed = normalizeRemoteAddress(allowedPeerIp);
  return isTailscaleIpv4Hostname(allowed) && peer === allowed;
}

// Game-canvas geometry from the page (CSS px, content-origin relative):
// { x, y, width, height, iw, ih, dpr } where (x,y,w,h) is
// `document.querySelector('#unity-canvas') || document.querySelector('canvas')`
// getBoundingClientRect(), (iw,ih) is window.innerWidth/innerHeight and dpr
// is window.devicePixelRatio. Fail-closed: anything missing, non-finite, or
// outside the measured content area throws — callers must never fall back to
// streaming the full page (with margins) at 1280x720.
export function parseCanvasGeometry(value) {
  const g = value && typeof value === 'object' ? value : null;
  for (const key of ['x', 'y', 'width', 'height', 'iw', 'ih', 'dpr']) {
    if (!g || !Number.isFinite(g[key])) {
      throw new Error(`game canvas geometry lacks finite ${key} (fail-closed): ${JSON.stringify(value)?.slice(0, 200)}`);
    }
  }
  if (!(g.width >= 64 && g.height >= 64)) {
    throw new Error(`game canvas too small (fail-closed): ${g.width}x${g.height}`);
  }
  if (!(g.iw > 0 && g.ih > 0)) {
    throw new Error(`game canvas content size invalid (fail-closed): ${g.iw}x${g.ih}`);
  }
  if (!(g.dpr > 0 && g.dpr <= 4)) {
    throw new Error(`game canvas devicePixelRatio out of range (fail-closed): ${g.dpr}`);
  }
  // The rect must sit inside the measured content area (2px tolerance for
  // subpixel rounding); a canvas larger than the viewport means the page
  // is zoomed or laid out unexpectedly — fail closed, never stream that.
  const overflow = Math.max(0, -g.x, -g.y, g.x + g.width - g.iw, g.y + g.height - g.ih);
  if (overflow > 2) {
    throw new Error(`game canvas rect outside content area (fail-closed): ${JSON.stringify(g)}`);
  }
  return { x: g.x, y: g.y, width: g.width, height: g.height, iw: g.iw, ih: g.ih, dpr: g.dpr };
}

// Crop rectangle in capture-frame pixels. ScreenCaptureKit captures the
// window at its outer size, so the frame origin is the window origin: the
// content origin inside the frame is the existing chrome offset
// (outer - inner), plus the canvas offset inside the content, all scaled by
// devicePixelRatio. Even origin/size enforced (chroma-subsampled yuv420p).
// Fail-closed: a rect outside the frame throws instead of misframing.
export function resolveCanvasCropFrame({ outerWidth, outerHeight, chromeLeft, chromeTop, canvas }) {
  for (const [key, val] of [['outerWidth', outerWidth], ['outerHeight', outerHeight], ['chromeLeft', chromeLeft], ['chromeTop', chromeTop]]) {
    if (!Number.isFinite(val)) throw new Error(`capture ${key} is required (fail-closed)`);
  }
  const geom = parseCanvasGeometry(canvas);
  const evenDown = (n) => Math.max(0, Math.floor(n / 2) * 2);
  const x = evenDown((chromeLeft + geom.x) * geom.dpr);
  const y = evenDown((chromeTop + geom.y) * geom.dpr);
  let w = evenDown(geom.width * geom.dpr);
  let h = evenDown(geom.height * geom.dpr);
  if (!(w >= 64 && h >= 64)) {
    throw new Error(`game canvas crop too small after even-rounding (fail-closed): ${w}x${h}`);
  }
  // Clamp at most subpixel tolerance (2 CSS px, carried from
  // parseCanvasGeometry); anything larger is a genuine unit/rect mismatch
  // and fails closed instead of silently streaming a wrong region.
  const tol = 2 * geom.dpr;
  const overW = x + w - outerWidth;
  const overH = y + h - outerHeight;
  if (x < 0 || y < 0 || overW > tol || overH > tol) {
    throw new Error(
      `game canvas crop outside capture frame (fail-closed): crop=${x},${y} ${w}x${h} frame=${outerWidth}x${outerHeight}`,
    );
  }
  if (overW > 0) w = evenDown(outerWidth - x);
  if (overH > 0) h = evenDown(outerHeight - y);
  if (!(x >= 0 && y >= 0 && w >= 64 && h >= 64 && x + w <= outerWidth && y + h <= outerHeight)) {
    throw new Error(
      `game canvas crop outside capture frame (fail-closed): crop=${x},${y} ${w}x${h} frame=${outerWidth}x${outerHeight}`,
    );
  }
  return { x, y, w, h };
}

// Canvas crop -> 960x540 output filter: crop the game canvas, scale to fit
// (aspect preserved) and pad to exactly outW x outH. Output resolution is
// fixed 960x540; a freeform full-page scale is never built here.
export function buildCanvasVideoFilter(crop, { outWidth = 960, outHeight = 540 } = {}) {
  if (!crop || !Number.isInteger(crop.x) || !Number.isInteger(crop.y)
    || !Number.isInteger(crop.w) || !Number.isInteger(crop.h)) {
    throw new Error('integer canvas crop {x,y,w,h} is required (fail-closed)');
  }
  if (outWidth !== 960 || outHeight !== 540) {
    throw new Error('canvas-crop output must be 960x540 (fail-closed)');
  }
  return `crop=${crop.w}:${crop.h}:${crop.x}:${crop.y}`
    + `,scale=${outWidth}:${outHeight}:force_original_aspect_ratio=decrease`
    + `,pad=${outWidth}:${outHeight}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1`;
}

// ffmpeg args for the canvas-cropped pipeline. Mirrors
// soren91_macos_session.mjs buildFfmpegArgs (same rawvideo input contract,
// same audio-tap wiring) but the video filter is the canvas crop/scale/pad
// above instead of the full-content chrome-band crop. The session builder is
// intentionally untouched.
export function buildCanvasFfmpegArgs(options, capture, crop) {
  if (!capture || !Number.isFinite(capture.outerWidth) || !Number.isFinite(capture.outerHeight)) {
    throw new Error('capture outerWidth/outerHeight from the renderer result is required');
  }
  const bitrate = `${options.videoMbps}M`;
  if (options.audioTap && options.audioDevice) {
    throw new Error('audioTap and audioDevice are mutually exclusive (fail-closed: ambiguous audio source)');
  }
  const args = [
    '-hide_banner', '-loglevel', 'warning', '-nostdin',
    '-f', 'rawvideo', '-pixel_format', 'bgra',
    '-video_size', `${capture.outerWidth}x${capture.outerHeight}`,
    '-framerate', '30',
    '-i', 'pipe:0',
  ];
  if (options.audioDevice) args.push('-f', 'avfoundation', '-i', `none:${options.audioDevice}`);
  if (options.audioTap) args.push(...buildAudioFfmpegInputArgs());
  args.push(
    '-vf', buildCanvasVideoFilter(crop, { outWidth: options.width, outHeight: options.height }),
    '-c:v', 'h264_videotoolbox', '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', `${options.videoMbps * 2}M`,
    '-g', '60', '-pix_fmt', 'yuv420p',
  );
  if (options.audioDevice || options.audioTap) args.push('-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '128k');
  else args.push('-an');
  args.push('-f', 'mpegts', options.srtUrl);
  return args;
}

export function buildChromeArgs(options, placement, profileDir) {
  return [
    `--remote-debugging-port=${options.cdpPort}`,
    '--remote-allow-origins=*',
    `--user-data-dir=${profileDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-features=Translate',
    `--window-position=${placement.left},${placement.top}`,
    `--window-size=${options.width},${options.height}`,
    '--autoplay-policy=no-user-gesture-required',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    ...(options.audioTap ? [] : ['--mute-audio']),
    'about:blank',
  ];
}

// Raw TCP forward BIND_IP:proxyPort -> 127.0.0.1:cdpPort. Byte-pipe (not
// HTTP-aware) so both /json/* polling and WebSocket upgrades pass through.
// Binding to Tailscale alone is not authentication: only the exact OCI peer
// is accepted, so another tailnet node cannot attach to the unauthenticated
// DevTools endpoint even if ACLs are broader than expected.
export function startCdpProxy({ bindIp, proxyPort, cdpPort, allowedPeerIp }) {
  if (!isTailscaleIpv4Hostname(allowedPeerIp)) {
    throw new Error('allowedPeerIp must be a Tailscale IPv4 address');
  }
  const server = net.createServer((client) => {
    if (!isAllowedCdpPeer(client.remoteAddress, allowedPeerIp)) {
      client.destroy();
      return;
    }
    const upstream = net.connect({ host: '127.0.0.1', port: cdpPort }, () => {
      client.pipe(upstream).pipe(client);
    });
    const destroy = () => { try { client.destroy(); } catch {} try { upstream.destroy(); } catch {} };
    client.on('error', destroy);
    upstream.on('error', destroy);
  });
  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(proxyPort, bindIp, () => resolve(server));
  });
}

function stopServer(server) {
  return new Promise((resolve) => {
    if (!server) return resolve();
    try { server.close(() => resolve()); } catch { resolve(); }
    setTimeout(resolve, 2000).unref?.();
  });
}

function terminate(child) {
  if (!child || child.exitCode != null || child.killed) return;
  try { child.kill('SIGTERM'); } catch {}
}

async function waitExit(child, timeoutMs = 10_000) {
  if (!child || child.exitCode != null) return;
  await new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

export async function main(argv = process.argv.slice(2), { platform = process.platform } = {}) {
  const options = validateOptions(parseArgs(argv), platform);
  const allowedPeerIp = options.srtUrl ? new URL(options.srtUrl).hostname : '';
  const plan = {
    backend: 'macos-cdp-host', tier: -1, execute: options.execute,
    cdp: `127.0.0.1:${options.cdpPort}`,
    proxy: `${options.bindIp}:${options.proxyPort} (tailscale peer-locked)`,
    output: [options.width, options.height, 30],
    transport: options.srtUrl ? 'srt-over-tailscale' : 'not-configured',
    display: 'offscreen-virtual',
  };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  const fs = await import('node:fs');
  let vdisplay = null;
  let chrome = null;
  let proxy = null;
  let browser = null;
  let capture = null;
  let audiotap = null;
  let ffmpeg = null;
  const cleanup = () => {
    terminate(ffmpeg); terminate(capture); terminate(audiotap); terminate(chrome);
    try { ffmpeg?.stdin?.destroy?.(); } catch {}
    try { capture?.stdout?.destroy?.(); } catch {}
    try { audiotap?.stdout?.destroy?.(); } catch {}
    if (vdisplay) { try { vdisplay.kill('SIGTERM'); } catch {} }
    if (proxy) { try { proxy.close(); } catch {} }
  };
  const shutdown = (signal) => {
    cleanup();
    process.exit(signalExitCode(signal));
  };
  const onSigint = () => shutdown('SIGINT');
  const onSigterm = () => shutdown('SIGTERM');
  process.once('SIGINT', onSigint);
  process.once('SIGTERM', onSigterm);
  const startedAt = Date.now();
  try {
    const held = await startVirtualDisplay(options.virtualDisplayBin, { timeoutMs: 30_000 });
    vdisplay = held.child;
    console.log(`SOREN91_CDP_HOST_VDISPLAY_READY=${JSON.stringify(held.status)}`);
    const { placementFor: place } = { placementFor };
    const placement = place(held.status.bounds);
    const profileDir = path.join(os.tmpdir(), `soren91-cdp-host-${Date.now()}`);
    fs.mkdirSync(profileDir, { recursive: true });
    // `--remote-allow-origins=*` is safe only behind the source-locked Tailscale
    // proxy above; Chrome itself remains bound to 127.0.0.1.
    chrome = spawn(options.chromeBin, buildChromeArgs(options, placement, profileDir), {
      stdio: ['ignore', 'ignore', 'inherit'],
    });
    console.log(`SOREN91_CDP_HOST_CHROME_PID=${chrome.pid}`);
    // Wait for the local DevTools HTTP endpoint.
    let ready = false;
    for (let i = 0; i < 60 && !ready; i += 1) {
      if (chrome.exitCode != null) throw new Error(`chrome exited early (code=${chrome.exitCode})`);
      try {
        const res = await fetch(`http://127.0.0.1:${options.cdpPort}/json/version`);
        ready = res.ok;
      } catch {}
      if (!ready) await sleep(500);
    }
    if (!ready) throw new Error('chrome DevTools endpoint did not come up');
    console.log(`SOREN91_CDP_HOST_CDP_READY=http://127.0.0.1:${options.cdpPort}`);
    proxy = await startCdpProxy({
      bindIp: options.bindIp,
      proxyPort: options.proxyPort,
      cdpPort: options.cdpPort,
      allowedPeerIp,
    });
    console.log(`SOREN91_CDP_HOST_PROXY_READY=${options.bindIp}:${options.proxyPort}`);

    // Wait for the OCI bot to navigate to the exact game origin.
    const deadline = startedAt + options.sessionSec * 1000;
    let game = null;
    while (Date.now() < deadline) {
      if (chrome.exitCode != null) throw new Error(`chrome exited while waiting (code=${chrome.exitCode})`);
      let targets = [];
      try { targets = await fetchJsonList(options.cdpPort); } catch (e) {
        console.error(`[cdp-host] /json/list poll failed: ${e?.message || e}`);
      }
      const pageCount = targets.filter((t) => t?.type === 'page').length;
      console.error(`[cdp-host] page_count=${pageCount}`);
      game = findGameTarget(targets);
      if (game) break;
      await sleep(options.pollMs);
    }
    if (!game) throw new Error('no exact https://play.unityroom.com target appeared before session deadline');
    console.log('SOREN91_CDP_HOST_GAME_FOUND=1');

    // Local calibration via CDP (same approach as the renderer): size the
    // real window so content == width x height, read the exact title.
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${options.cdpPort}`);
    const context = browser.contexts()[0];
    if (!context) throw new Error('remote chrome has no browser context');
    const page = context.pages().find((p) => isExactGameTargetUrl(p.url() || ''));
    if (!page) throw new Error('remote chrome has no exact play.unityroom.com page');
    const cdp = await context.newCDPSession(page);
    const { windowId } = await cdp.send('Browser.getWindowForTarget');
    let bounds = await cdp.send('Browser.getWindowBounds', { windowId });
    let inner = await page.evaluate(() => ({ iw: window.innerWidth, ih: window.innerHeight }));
    const chromeW = bounds.bounds.width - inner.iw;
    const chromeH = bounds.bounds.height - inner.ih;
    await cdp.send('Browser.setWindowBounds', {
      windowId,
      bounds: {
        left: placement.left, top: placement.top,
        width: options.width + chromeW, height: options.height + chromeH,
      },
    });
    await sleep(300);
    bounds = await cdp.send('Browser.getWindowBounds', { windowId });
    inner = await page.evaluate(() => ({ iw: window.innerWidth, ih: window.innerHeight }));
    console.error(`[cdp-host] content after calibration: ${JSON.stringify(inner)} (want ${options.width}x${options.height})`);
    const windowTitle = await page.title();
    if (!windowTitle) throw new Error('page title is empty; cannot build a fail-closed window match');
    console.log(`SOREN91_CDP_HOST_WINDOW_TITLE=${JSON.stringify(windowTitle)}`);
    // Fail-closed offscreen proof (measured rect vs physical displays).
    const windowRect = {
      x: bounds.bounds.left, y: bounds.bounds.top,
      width: bounds.bounds.width, height: bounds.bounds.height,
    };
    const listResult = spawnSync(options.virtualDisplayBin, ['--list'], { encoding: 'utf8', timeout: 20_000 });
    if (listResult.error) throw listResult.error;
    const line = String(listResult.stderr || '').trim().split('\n').pop()
      || String(listResult.stdout || '').trim().split('\n').pop();
    const listed = parseVDisplayList(line);
    const { overlap, area, displayIds } = computePhysicalOverlap(windowRect, listed, held.status.displayID);
    if (overlap) {
      throw new Error(`offscreen violation: ${JSON.stringify(windowRect)} intersects ${JSON.stringify(displayIds)} by ${area}px`);
    }
    console.log(`SOREN91_CDP_HOST_OFFSCREEN_OK=${JSON.stringify(windowRect)}`);
    const captureInfo = {
      bundleId: 'com.google.Chrome',
      windowTitle,
      outerWidth: bounds.bounds.width,
      outerHeight: bounds.bounds.height,
      chromeTop: bounds.bounds.height - inner.ih,
      chromeLeft: bounds.bounds.width - inner.iw,
    };
    // Game-canvas crop (Issue #303 feedback): streaming the full page with
    // margins at 1280x720 made the game tiny. Crop ONLY the Unity canvas
    // and scale it to 960x540. Fail-closed: no canvas / bad rect aborts the
    // run instead of silently streaming the page with margins.
    let canvasGeom = null;
    for (let attempt = 0; attempt < 30; attempt += 1) {
      try {
        canvasGeom = await page.evaluate(() => {
          const el = document.querySelector('#unity-canvas') || document.querySelector('canvas');
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return {
            x: r.x, y: r.y, width: r.width, height: r.height,
            iw: window.innerWidth, ih: window.innerHeight,
            dpr: window.devicePixelRatio || 1,
          };
        });
      } catch { canvasGeom = null; }
      if (canvasGeom && canvasGeom.width > 0 && canvasGeom.height > 0) break;
      await sleep(2000);
    }
    if (!canvasGeom) {
      throw new Error('game canvas not found (fail-closed: refusing to stream the full page with margins)');
    }
    const canvasCrop = resolveCanvasCropFrame({
      outerWidth: captureInfo.outerWidth,
      outerHeight: captureInfo.outerHeight,
      chromeLeft: captureInfo.chromeLeft,
      chromeTop: captureInfo.chromeTop,
      canvas: canvasGeom,
    });
    console.log(`SOREN91_CDP_HOST_CANVAS_CROP=${JSON.stringify(canvasCrop)}`);
    // Pre-warm Chrome audio so the tap poll finds a tappable process.
    try {
      await page.evaluate(() => {
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (!Ctx) return;
          const ctx = new Ctx();
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          gain.gain.value = 0;
          osc.connect(gain); gain.connect(ctx.destination);
          osc.start();
          try { osc.stop(ctx.currentTime + 0.5); } catch {}
        } catch {}
      });
    } catch {}

    const captureArgs = buildCaptureArgs({ captureHelperBin: options.captureHelperBin }, captureInfo);
    const started = await startCaptureHelper(options.captureHelperBin, captureArgs, 30_000);
    capture = started.child;
    console.log(`SOREN91_CDP_HOST_CAPTURE_READY=${JSON.stringify(started.status)}`);

    if (options.audioTap) {
      let tapPids = null;
      let lastError = null;
      for (let attempt = 0; attempt < 10; attempt += 1) {
        const ps = spawnSync('ps', ['-ax', '-o', 'pid,ppid,command'], { encoding: 'utf8' });
        if (ps.error) lastError = ps.error;
        else {
          try { tapPids = resolveTapPids(ps.stdout || '', chrome.pid); break; }
          catch (e) { lastError = e; }
        }
        await sleep(1000);
      }
      if (!tapPids) throw new Error(`audio tap PID resolution failed (fail-closed): ${lastError?.message || lastError}`);
      const tapStarted = await startAudioTap(options.audioTapBin, tapPids, { timeoutMs: 30_000 });
      audiotap = tapStarted.child;
      console.log(`SOREN91_CDP_HOST_AUDIO_TAP_READY=${JSON.stringify(tapStarted.status)}`);
    }

    const ffmpegOpts = {
      width: options.width, height: options.height, videoMbps: options.videoMbps,
      audioTap: options.audioTap, audioDevice: '', srtUrl: options.srtUrl,
    };
    const ffmpegStdio = buildFfmpegStdio(options.audioTap);
    ffmpegStdio[2] = 'pipe';
    ffmpeg = spawn(options.ffmpegBin, buildCanvasFfmpegArgs(ffmpegOpts, captureInfo, canvasCrop), { stdio: ffmpegStdio });
    ffmpeg.stderr?.on('data', (chunk) => { try { process.stderr.write(chunk); } catch {} });
    // Pipe guards (Issue #303): when the OCI listener goes away, ffmpeg
    // exits and in-flight frame/PCM writes fail with EPIPE. Without guards
    // the unhandled 'error' event crashes this host (observed 2026-09-13:
    // listener pkill -> ffmpeg SRT I/O error -> EPIPE throw -> orphaned
    // Chrome/proxy/capture/tap/holder). Guarded errors are benign: the host
    // stays up until its deadline/SIGTERM and still cleans up.
    attachPipeGuards({ capture, audiotap, ffmpeg });
    ffmpeg.on('exit', (code, signal) => {
      console.error(`[cdp-host] ffmpeg exited code=${code} signal=${signal} (listener may have closed; host continues until deadline/SIGTERM)`);
    });
    capture.stdout.pipe(ffmpeg.stdin);
    if (audiotap) {
      audiotap.stdout.pipe(ffmpeg.stdio[3]);
      try { audiotap.stdout.resume?.(); } catch {}
    }
    console.log(`SOREN91_CDP_HOST_STREAMING=${options.srtUrl.replace(/\/\/.*@/, '//***@')}`);
    fs.writeFileSync(options.resultPath, JSON.stringify({
      ok: true, proxy: `${options.bindIp}:${options.proxyPort}`,
      windowTitle, capture: captureInfo, canvasCrop, srt: 'caller-started',
    }, null, 2));
    await sleep(Math.max(0, deadline - Date.now()));
    console.log('SOREN91_CDP_HOST_END=deadline');
    return { ok: true };
  } finally {
    cleanup();
    process.removeListener('SIGINT', onSigint);
    process.removeListener('SIGTERM', onSigterm);
    if (audiotap) await stopAudioTap(audiotap).catch(() => {});
    if (vdisplay) await stopVirtualDisplay(vdisplay).catch(() => {});
    if (browser) await browser.close().catch(() => {});
    if (ffmpeg) await waitExit(ffmpeg, 5000);
    if (capture) await waitExit(capture, 5000);
    if (chrome) await waitExit(chrome, 5000);
    if (proxy) await stopServer(proxy);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

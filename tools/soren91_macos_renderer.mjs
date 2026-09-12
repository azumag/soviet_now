#!/usr/bin/env node
// Soren91 macOS local renderer (Tier -1 counterpart to soren91_windows_renderer.mjs).
//
// Launches the EXISTING Google Chrome (Playwright `channel: 'chrome'`, no managed
// Chromium download) in --app mode, joins the live Soren91 (sorengame91) match,
// and measures native rAF fps + WebGL2 / hardware-renderer facts for
// `measureSec`. Emits a JSON result to SOREN91_LOCAL_RESULT_PATH identifying
// this exact window (bundle id + exact page title) for a ScreenCaptureKit
// window-targeted capture — see tools/macos/soren91_window_capture.swift and
// Issue #303's window-overlap finding for why this is NOT a screen-position
// crop: capturing by window identity means the stream only ever contains
// this window's content, regardless of what else is on screen or in front
// of it. `chromeTop`/`chromeLeft` (measured via CDP, not assumed) locate the
// browser-chrome band inside that window's own capture so the downstream
// ffmpeg crop can strip it without depending on screen geometry. Then idles
// for `renderSec` so the external capture can run against the live window
// until SIGINT/SIGTERM.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import {
  computePhysicalOverlap,
  parseVDisplayBounds,
  parseVDisplayList,
  placementFor,
} from './soren91_offscreen_verify.mjs';

const here = path.dirname(fileURLToPath(import.meta.url));
const defaultVirtualDisplayBin = path.join(here, 'macos', 'bin', 'soren91_virtual_display');

const width = Number(process.env.SOREN91_LOCAL_WIDTH || 960);
const height = Number(process.env.SOREN91_LOCAL_HEIGHT || 540);
const minFps = Number(process.env.SOREN91_LOCAL_MIN_FPS || 30);
const measureSec = Number(process.env.SOREN91_LOCAL_MEASURE_SEC || 60);
const renderSec = Number(process.env.SOREN91_LOCAL_RENDER_SEC || 1980);
const windowTop = Number(process.env.SOREN91_LOCAL_WINDOW_TOP || 30); // below the macOS menu bar
const playerName = process.env.SOREN91_LOCAL_PLAYER_NAME || 'DoCiAI:MC';
const resultPath = process.env.SOREN91_LOCAL_RESULT_PATH;
// Offscreen (Issue #303): the session holds a private CGVirtualDisplay and
// passes its MEASURED bounds here. The window is parked inside them and,
// after calibration, proven (from measured bounds, never assumed) to touch
// no physical display — see verifyOffscreenPlacement. Absent = legacy
// on-screen behavior (only with an explicit onscreen opt-in at the session).
const vdisplayRaw = process.env.SOREN91_LOCAL_VDISPLAY_BOUNDS || '';
const virtualDisplayBin = process.env.SOREN91_LOCAL_VIRTUAL_DISPLAY_BIN || defaultVirtualDisplayBin;
// Silent by default (Issue #303 audio): without SOREN91_LOCAL_AUDIO_TAP the
// session sends no audio, so mute Chrome at the source. With the tap enabled
// the game audio is captured (and physically muted) by the process tap
// instead — the session omits this env var in that case.
const muteAudioRaw = process.env.SOREN91_LOCAL_MUTE_AUDIO || '';
const muteAudio = muteAudioRaw !== '' && !/^(0|false|no|off)$/i.test(muteAudioRaw.trim());
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

if (!resultPath) throw new Error('SOREN91_LOCAL_RESULT_PATH is required');
if (process.platform !== 'darwin') throw new Error('soren91_macos_renderer must run on macOS');

function command(commandName, args, timeout = 20_000) {
  const result = spawnSync(commandName, args, { encoding: 'utf8', timeout });
  return {
    ok: result.status === 0,
    stdout: String(result.stdout || '').trim(),
    stderr: String(result.stderr || '').trim(),
  };
}

function emit(result) {
  fs.writeFileSync(resultPath, `${JSON.stringify(result, null, 2)}\n`);
  console.log(`SOREN91_LOCAL_READY=${JSON.stringify(result)}`);
}

function waitForStop(ms) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms);
    const stop = () => { clearTimeout(timer); resolve(); };
    process.once('SIGINT', stop);
    process.once('SIGTERM', stop);
  });
}

// Real (non-emulated) window content bounds. `newContext({ viewport: {...} })`
// would apply CDP device-metrics emulation, which makes window.innerWidth/
// innerHeight report virtualized values that do NOT match the physical
// on-screen window — so we deliberately use `viewport: null` and resize the
// real window via CDP Browser.setWindowBounds until the real content area
// matches width x height. The output here is WINDOW-RELATIVE (outer size +
// chrome-band offset inside that window), not a screen-position crop rect —
// it stays valid regardless of where the window sits on screen or what is in
// front of it, because the downstream capture selects this window by
// identity (bundle id + exact title), not by screen coordinates.
async function calibrateWindowBounds(context, page, { width: w, height: h, left = 0, top }) {
  const cdp = await context.newCDPSession(page);
  const { windowId } = await cdp.send('Browser.getWindowForTarget');
  let bounds = await cdp.send('Browser.getWindowBounds', { windowId });
  let inner = await page.evaluate(() => ({ iw: window.innerWidth, ih: window.innerHeight }));
  const chromeW = bounds.bounds.width - inner.iw;
  const chromeH = bounds.bounds.height - inner.ih;
  await cdp.send('Browser.setWindowBounds', {
    windowId,
    bounds: { left, top, width: w + chromeW, height: h + chromeH },
  });
  await page.waitForTimeout(300);
  bounds = await cdp.send('Browser.getWindowBounds', { windowId });
  inner = await page.evaluate(() => ({ iw: window.innerWidth, ih: window.innerHeight }));
  if (inner.iw !== w || inner.ih !== h) {
    throw new Error(`window content size mismatch after calibration: ${JSON.stringify(inner)}`);
  }
  return {
    outerWidth: bounds.bounds.width,
    outerHeight: bounds.bounds.height,
    chromeTop: bounds.bounds.height - inner.ih,
    chromeLeft: bounds.bounds.width - inner.iw,
    // Measured screen position (never the requested one): feeds the
    // physical-overlap proof below.
    windowLeft: bounds.bounds.left,
    windowTop: bounds.bounds.top,
  };
}

// Fail-closed offscreen proof (Issue #303): with the window's MEASURED rect
// and the helper's --list output (physical displays = all online displays
// except our virtual displayID), even 1px of intersection fails the run.
// Captures no pixels, reads no window titles — bounds arithmetic only.
function verifyOffscreenPlacement(windowBounds, vdisplay) {
  const windowRect = {
    x: windowBounds.windowLeft,
    y: windowBounds.windowTop,
    width: windowBounds.outerWidth,
    height: windowBounds.outerHeight,
  };
  let listed;
  try {
    const listResult = spawnSync(virtualDisplayBin, ['--list'], { encoding: 'utf8', timeout: 20_000 });
    if (listResult.error) throw listResult.error;
    const line = String(listResult.stderr || '').trim().split('\n').pop()
      || String(listResult.stdout || '').trim().split('\n').pop();
    listed = parseVDisplayList(line);
  } catch (error) {
    throw new Error(`offscreen verification unavailable (fail-closed): ${error?.message || error}`);
  }
  const { overlap, area, displayIds } = computePhysicalOverlap(windowRect, listed, vdisplay.displayID);
  if (overlap) {
    throw new Error(
      `offscreen violation: measured window ${JSON.stringify(windowRect)} intersects `
      + `physical display(s) ${JSON.stringify(displayIds)} by ${area}px (fail-closed)`,
    );
  }
  return {
    requested: true,
    displayID: vdisplay.displayID,
    bounds: vdisplay.bounds,
    windowBounds: windowRect,
    physicalOverlap: false,
  };
}

let browser;
try {
  // Offscreen placement comes from the holder's MEASURED virtual display
  // bounds (via the session). An unparsable value fails closed here rather
  // than guessing a position.
  const vdisplay = vdisplayRaw ? parseVDisplayBounds(vdisplayRaw) : null;
  const placement = vdisplay
    ? placementFor(vdisplay.bounds)
    : { left: 0, top: windowTop };
  const videotoolbox = command('ffmpeg', [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=size=960x540:rate=30',
    '-frames:v', '30', '-c:v', 'h264_videotoolbox', '-f', 'null', '-',
  ], 30_000);

  browser = await chromium.launch({
    channel: 'chrome',
    headless: false,
    args: [
      '--app=about:blank',
      `--window-position=${placement.left},${placement.top}`,
      `--window-size=${width},${height}`,
      '--ignore-gpu-blocklist',
      '--disable-gpu-vsync',
      '--disable-frame-rate-limit',
      '--autoplay-policy=no-user-gesture-required',
      '--disable-background-timer-throttling',
      '--disable-backgrounding-occluded-windows',
      '--disable-renderer-backgrounding',
      ...(muteAudio ? ['--mute-audio'] : []),
    ],
  });

  const context = browser.contexts()[0] || (await browser.newContext({ viewport: null }));
  let page = context.pages()[0];
  for (let i = 0; i < 50 && !page; i += 1) {
    await sleep(100);
    page = context.pages()[0];
  }
  page = page || (await context.newPage());
  await page.waitForTimeout(300);

  const windowBounds = await calibrateWindowBounds(context, page, {
    width, height, left: placement.left, top: placement.top,
  });
  // Offscreen proof BEFORE joining the match: the measured window rect must
  // not touch any physical display. Throws fail-closed on any overlap (or
  // when the proof cannot run) — the run never proceeds visibly.
  const offscreen = vdisplay
    ? verifyOffscreenPlacement(windowBounds, vdisplay)
    : { requested: false };
  await page.addInitScript(() => { window.__soren91NativeRaf = window.requestAnimationFrame.bind(window); });

  const landing = await fetch('https://unityroom.com/games/sorengame91', {
    headers: { 'user-agent': 'Mozilla/5.0', 'accept-language': 'ja' },
  }).then((response) => response.text());
  const match = landing.match(/(?:src|href)=["']([^"']*play\.unityroom\.com[^"']*)["']/i);
  if (!match) throw new Error('unityroom game URL not found');
  const gameUrl = new URL(match[1].replace(/&amp;/g, '&'), 'https://unityroom.com/').href;
  await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await page.waitForSelector('canvas', { timeout: 90_000 });
  await page.waitForFunction(() => {
    const bar = document.getElementById('unity-loading-bar');
    return !bar || bar.style.display === 'none';
  }, null, { timeout: 90_000 });

  const canvas = page.locator('canvas');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('canvas has no bounding box');
  await page.mouse.click(box.x + box.width * (630 / 1280), box.y + box.height * (560 / 720));
  await sleep(500);
  await page.keyboard.press('Control+a');
  await page.keyboard.type(playerName, { delay: 30 });
  await page.mouse.click(box.x + box.width * (630 / 1280), box.y + box.height * (645 / 720));
  await sleep(8_000);

  // Exact (not substring) window title, used by the ScreenCaptureKit helper to
  // identify THIS window and no other — see soren91_window_capture.swift's
  // fail-closed matching. Chrome sets the native window title from
  // document.title, so this must be read from the live page, not assumed.
  const windowTitle = await page.title();
  if (!windowTitle) throw new Error('page title is empty; cannot build a fail-closed window match for capture');

  const probe = await page.evaluate(async ({ seconds }) => {
    const raf = window.__soren91NativeRaf || window.requestAnimationFrame.bind(window);
    let frames = 0;
    const deltas = [];
    let previous = performance.now();
    const started = previous;
    await new Promise((resolve) => {
      const tick = (timestamp) => {
        frames += 1;
        deltas.push(timestamp - previous);
        previous = timestamp;
        if (performance.now() - started < seconds * 1000) raf(tick);
        else resolve();
      };
      raf(tick);
    });
    deltas.shift();
    deltas.sort((a, b) => a - b);
    const percentile = (value) => deltas.length
      ? Number(deltas[Math.floor((deltas.length - 1) * value)].toFixed(1))
      : null;
    const target = document.querySelector('#unity-canvas') || document.querySelector('canvas');
    const gl = target.getContext('webgl2');
    const debug = gl?.getExtension('WEBGL_debug_renderer_info');
    return {
      fps: Number((frames / ((performance.now() - started) / 1000)).toFixed(1)),
      deltaMs: { p50: percentile(0.5), p90: percentile(0.9), p99: percentile(0.99) },
      renderer: gl ? gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER) : null,
      vendor: gl ? gl.getParameter(debug ? debug.UNMASKED_VENDOR_WEBGL : gl.VENDOR) : null,
      webgl2: Boolean(gl),
      drawingBuffer: gl ? [gl.drawingBufferWidth, gl.drawingBufferHeight] : null,
      canvas: target ? [target.width, target.height, target.clientWidth, target.clientHeight] : null,
    };
  }, { seconds: measureSec });

  const renderer = String(probe.renderer || '');
  // macOS does not require an NVIDIA string; Apple/ANGLE/Metal hardware renderers
  // are acceptable, SwiftShader/llvmpipe/generic "software" renderers are not.
  const hardwareRenderer = /apple|angle|metal/i.test(renderer) && !/(swiftshader|llvmpipe|software)/i.test(renderer);
  const correctBuffer = probe.drawingBuffer?.[0] === width && probe.drawingBuffer?.[1] === height;
  const result = {
    pass: Boolean(videotoolbox.ok && hardwareRenderer && probe.webgl2 && correctBuffer && probe.fps >= minFps),
    criteria: { minFps, width, height, measureSec },
    videotoolbox: { pass: videotoolbox.ok, error: videotoolbox.ok ? '' : videotoolbox.stderr.slice(-500) },
    probe,
    // Consumed by soren91_macos_session.mjs to build the ScreenCaptureKit
    // helper's --bundle-id/--title/--width/--height args and the downstream
    // ffmpeg chrome-band crop. bundleId is fixed because this renderer always
    // launches the stable Google Chrome channel via Playwright `channel:'chrome'`.
    // `capture.offscreen` is the offscreen proof (Issue #303): when the
    // session parked this window on the virtual display, it carries the
    // measured window rect and the zero-physical-overlap verdict.
    // `requested:false` = legacy visible run (explicit opt-in only).
    capture: { bundleId: 'com.google.Chrome', windowTitle, ...windowBounds, offscreen },
    checks: { hardwareRenderer, webgl2: probe.webgl2, correctBuffer, fps: probe.fps >= minFps },
  };
  emit(result);
  if (!result.pass) process.exitCode = 1;
  else await waitForStop(renderSec * 1000);
} catch (error) {
  emit({ pass: false, reason: error?.message || String(error) });
  process.exitCode = 1;
} finally {
  await browser?.close().catch(() => {});
}

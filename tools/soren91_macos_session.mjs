#!/usr/bin/env node
// Soren91 macOS Tier -1 session (same start/status/stop contract as
// soren91_windows_session.mjs), targeting the macOS local renderer +
// ScreenCaptureKit window-targeted capture + VideoToolbox encode chain.
//
// Capture pipeline: tools/macos/soren91_window_capture.swift (built by
// soren91_window_capture_build.sh) captures ONE window, selected by exact
// bundle id + exact title (fail-closed: 0 or >1 matches refuses to run — see
// that file's header). This replaces the original avfoundation
// "whole-display capture + fixed-coordinate crop" design, which recorded
// whatever was on-screen at a hard-coded position — in a real incident
// during this Issue's investigation, that captured the operator's own
// browser window instead of the game (see Issue #303). Window-identity
// capture means the stream only ever contains this specific window's
// content, regardless of z-order, occlusion, or screen position. The
// helper's raw BGRA frames are piped into ffmpeg's stdin, which crops out
// the browser-chrome band (measured window-relative by the renderer, not
// assumed) and encodes with h264_videotoolbox before sending over SRT.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import readline from 'node:readline';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const here = path.dirname(fileURLToPath(import.meta.url));
const defaultRenderer = path.join(here, 'soren91_macos_renderer.mjs');
const defaultCaptureHelperBin = path.join(here, 'macos', 'bin', 'soren91_window_capture');
const defaultVirtualDisplayBin = path.join(here, 'macos', 'bin', 'soren91_virtual_display');

function envFlag(env, name, defaultValue) {
  const raw = env?.[name];
  if (raw == null || raw === '') return defaultValue;
  return !/^(0|false|no|off)$/i.test(String(raw).trim());
}

export function defaults(env = process.env) {
  return {
    execute: false,
    sessionSec: Number(env.SOREN91_LOCAL_SESSION_SEC || 1800),
    hardMaxSec: Number(env.SOREN91_LOCAL_HARD_MAX_SEC || 2400),
    bootTimeoutSec: Number(env.SOREN91_LOCAL_BOOT_TIMEOUT_SEC || 180),
    minFps: Number(env.SOREN91_LOCAL_MIN_FPS || 30),
    width: Number(env.SOREN91_LOCAL_WIDTH || 960),
    height: Number(env.SOREN91_LOCAL_HEIGHT || 540),
    videoMbps: Number(env.SOREN91_LOCAL_VIDEO_MBPS || 2),
    srtUrl: env.SOREN91_LOCAL_SRT_URL || '',
    audioDevice: env.SOREN91_LOCAL_AUDIO_DEVICE || '',
    renderer: env.SOREN91_LOCAL_RENDERER || defaultRenderer,
    captureHelperBin: env.SOREN91_LOCAL_CAPTURE_HELPER_BIN || defaultCaptureHelperBin,
    // Offscreen (Issue #303): hold a private CGVirtualDisplay and park the
    // Chrome window on it so nothing game-related ever shows on a physical
    // screen. ON by default; turning it off shows a visible window and is a
    // privacy-relevant choice, so it additionally requires allowOnscreen.
    offscreen: envFlag(env, 'SOREN91_LOCAL_OFFSCREEN', true),
    allowOnscreen: envFlag(env, 'SOREN91_LOCAL_ALLOW_ONSCREEN', false),
    virtualDisplayBin: env.SOREN91_LOCAL_VIRTUAL_DISPLAY_BIN || defaultVirtualDisplayBin,
    resultPath: env.SOREN91_LOCAL_RESULT_PATH || path.join(os.tmpdir(), 'soren91-macos-local-result.json'),
    ffmpegBin: env.SOREN91_LOCAL_FFMPEG_BIN || 'ffmpeg',
  };
}

export function parseArgs(argv, env = process.env) {
  const options = defaults(env);
  const takesValue = new Map([
    ['--session-sec', 'sessionSec'], ['--hard-max-sec', 'hardMaxSec'],
    ['--boot-timeout-sec', 'bootTimeoutSec'], ['--min-fps', 'minFps'],
    ['--width', 'width'], ['--height', 'height'], ['--video-mbps', 'videoMbps'],
    ['--srt-url', 'srtUrl'], ['--audio-device', 'audioDevice'],
    ['--renderer', 'renderer'], ['--capture-helper-bin', 'captureHelperBin'],
    ['--virtual-display-bin', 'virtualDisplayBin'],
    ['--result-path', 'resultPath'], ['--ffmpeg-bin', 'ffmpegBin'],
  ]);
  const strings = new Set([
    'srtUrl', 'audioDevice', 'renderer', 'captureHelperBin', 'virtualDisplayBin', 'resultPath', 'ffmpegBin',
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--execute') { options.execute = true; continue; }
    if (arg === '--offscreen') { options.offscreen = true; continue; }
    if (arg === '--no-offscreen') { options.offscreen = false; continue; }
    if (arg === '--allow-onscreen') { options.allowOnscreen = true; continue; }
    const key = takesValue.get(arg);
    if (!key) throw new Error(`unknown argument: ${arg}`);
    const value = argv[++index];
    if (value == null) throw new Error(`${arg} requires a value`);
    options[key] = strings.has(key) ? value : Number(value);
  }
  return options;
}

export function isTailscaleIpv4Hostname(hostname) {
  const octets = String(hostname || '').split('.');
  if (octets.length !== 4 || octets.some((value) => !/^\d{1,3}$/.test(value))) return false;
  const numbers = octets.map(Number);
  if (numbers.some((value) => value < 0 || value > 255)) return false;
  return numbers[0] === 100 && numbers[1] >= 64 && numbers[1] <= 127;
}

export function validateOptions(options, platform = process.platform) {
  if (!Number.isInteger(options.sessionSec) || options.sessionSec < 60 || options.sessionSec > 1800) {
    throw new Error('sessionSec must be 60..1800 (production target is 30 minutes)');
  }
  if (!Number.isInteger(options.hardMaxSec) || options.hardMaxSec < options.sessionSec || options.hardMaxSec > 2400) {
    throw new Error('hardMaxSec must be sessionSec..2400');
  }
  if (!Number.isInteger(options.bootTimeoutSec) || options.bootTimeoutSec < 30 || options.bootTimeoutSec > 300) {
    throw new Error('bootTimeoutSec must be 30..300');
  }
  if (options.minFps !== 30) throw new Error('local macOS renderer requires 30fps');
  if (options.width !== 960 || options.height !== 540) throw new Error('local macOS renderer output must be 960x540');
  if (!(options.videoMbps > 0 && options.videoMbps <= 8)) throw new Error('videoMbps must be >0 and <=8');
  if (!options.renderer) throw new Error('renderer path is required');
  if (!options.captureHelperBin) throw new Error('captureHelperBin path is required');
  if (options.offscreen && !options.virtualDisplayBin) {
    throw new Error('virtualDisplayBin path is required when offscreen is enabled');
  }
  if (options.srtUrl) {
    let target;
    try { target = new URL(options.srtUrl); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
    if (target.protocol !== 'srt:') throw new Error('srtUrl must start with srt://');
    if (target.username || target.password) {
      throw new Error('srtUrl userinfo is forbidden; credentials must not be carried in argv');
    }
    if (!target.port) throw new Error('srtUrl must include an explicit destination port');
    if ([...target.searchParams.keys()].some((key) => key.toLowerCase() === 'passphrase')) {
      throw new Error('SRT passphrase in argv is forbidden; use Tailscale transport without an SRT passphrase');
    }
    const modes = target.searchParams.getAll('mode');
    if (modes.length !== 1 || modes[0].toLowerCase() !== 'caller') {
      throw new Error('srtUrl must explicitly use mode=caller for the OCI listener');
    }
    if (!isTailscaleIpv4Hostname(target.hostname)) {
      throw new Error('srtUrl host must be a Tailscale IPv4 address in 100.64.0.0/10');
    }
  }
  if (options.execute && platform !== 'darwin') throw new Error('paid/live local renderer execution is macOS-only');
  if (options.execute && !options.srtUrl) throw new Error('--execute requires SOREN91_LOCAL_SRT_URL or --srt-url');
  return options;
}

// Offscreen display mode resolution (Issue #303, privacy-first /
// fail-closed): offscreen is the default. Running on-screen shows a visible
// game window, so it requires an explicit opt-in — either --allow-onscreen
// or SOREN91_LOCAL_ALLOW_ONSCREEN=1 — otherwise this throws instead of
// silently falling back to a visible window.
export function resolveDisplayMode(options) {
  if (options.offscreen) return 'offscreen';
  if (options.allowOnscreen) return 'onscreen';
  throw new Error(
    'onscreen execution shows a visible game window and requires an explicit opt-in: '
    + 'pass --allow-onscreen (or SOREN91_LOCAL_ALLOW_ONSCREEN=1), or re-enable the '
    + 'default offscreen virtual display with --offscreen',
  );
}

// Reads the virtual display holder's first stderr line as its
// readiness/failure signal (see soren91_virtual_display.swift). Fail-closed:
// any non-ok payload, non-JSON line, or payload without a numeric displayID
// and finite bounds throws — callers must never place the window based on
// ambiguous output.
export function parseVirtualDisplayStatus(line) {
  let payload;
  try { payload = JSON.parse(line); } catch { throw new Error(`virtual display helper emitted non-JSON status: ${line}`); }
  if (payload?.ok !== true) {
    throw new Error(`virtual display helper failed (fail-closed): ${payload?.error || JSON.stringify(payload)}`);
  }
  const bounds = payload?.bounds;
  if (!Number.isFinite(payload?.displayID)
    || !bounds || !Number.isFinite(bounds.x) || !Number.isFinite(bounds.y)
    || !Number.isFinite(bounds.width) || !Number.isFinite(bounds.height)
    || !(bounds.width > 0 && bounds.height > 0)) {
    throw new Error(`virtual display helper status lacks displayID/bounds (fail-closed): ${line}`);
  }
  return payload;
}

// Spawns the virtual display holder and waits for its first stderr line
// (readiness or failure). The holder survives until SIGTERM/SIGINT — only
// process exit releases its CGVirtualDisplay — so the caller owns stopping
// it via stopVirtualDisplay() (the session does this in its finally block
// and WAITS for the exit before returning, proving the display is gone).
export async function startVirtualDisplay(bin, { timeoutMs = 30_000, spawnImpl = spawn } = {}) {
  const child = spawnImpl(bin, [], { stdio: ['ignore', 'ignore', 'pipe'] });
  const rl = readline.createInterface({ input: child.stderr });
  const status = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('virtual display helper readiness timed out')), timeoutMs);
    let settled = false;
    rl.once('line', (line) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { resolve(parseVirtualDisplayStatus(line)); } catch (error) { reject(error); }
    });
    child.once('exit', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`virtual display helper exited before readiness (code=${code})`));
    });
    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
  });
  rl.on('line', (line) => console.error(`[vdisplay] ${line}`));
  return { child, status };
}

// Stops the holder and waits for its exit, proving the virtual display was
// released (a CGVirtualDisplay dies only with its holder process).
export async function stopVirtualDisplay(child, { timeoutMs = 10_000 } = {}) {
  if (!child || child.exitCode != null || child.killed) return { exited: true };
  try { child.kill('SIGTERM'); } catch { return { exited: false }; }
  const exited = await new Promise((resolve) => {
    if (child.exitCode != null) return resolve(true);
    const timer = setTimeout(() => resolve(false), timeoutMs);
    child.once('exit', () => { clearTimeout(timer); resolve(true); });
  });
  if (!exited) {
    try { child.kill('SIGKILL'); } catch {}
    await new Promise((resolve) => {
      if (child.exitCode != null) return resolve();
      child.once('exit', () => resolve());
    });
  }
  return { exited: true };
}

// `capture` comes from the renderer's result JSON (soren91_macos_renderer.mjs
// calibrateWindowBounds): the exact window to capture (bundleId + exact
// title) and its outer pixel size. Captured at the window's own native size
// (not pre-scaled to 960x540) so the chrome-band crop below stays pixel
// accurate.
export function buildCaptureArgs(options, capture) {
  if (!capture || !capture.bundleId || !capture.windowTitle) {
    throw new Error('capture target (bundleId, windowTitle) from the renderer result is required');
  }
  if (!Number.isFinite(capture.outerWidth) || !Number.isFinite(capture.outerHeight)) {
    throw new Error('capture outerWidth/outerHeight from the renderer result is required');
  }
  return [
    '--bundle-id', capture.bundleId,
    '--title', capture.windowTitle,
    '--width', String(capture.outerWidth),
    '--height', String(capture.outerHeight),
    '--fps', '30',
  ];
}

// Reads the capture helper's first stderr line as its readiness/failure
// signal (see soren91_window_capture.swift's `emitStatus`/`failClosed`).
// Fail-closed: any non-ok payload (or non-JSON line) throws — callers must
// never start piping frames on ambiguous or unparseable status.
export function parseCaptureHelperStatus(line) {
  let payload;
  try { payload = JSON.parse(line); } catch { throw new Error(`capture helper emitted non-JSON status: ${line}`); }
  if (payload?.ok !== true) {
    throw new Error(`capture helper failed (fail-closed): ${payload?.error || JSON.stringify(payload)}`);
  }
  return payload;
}

// `capture` supplies the window's own outer size (rawvideo input dimensions)
// and the window-relative chrome-band offset (chromeTop/chromeLeft) to crop
// away — both measured per-window by the renderer, never assumed from screen
// geometry. Because the upstream frames are already scoped to one window by
// identity (see buildCaptureArgs), a wrong offset here can at worst misframe
// that SAME window — it can never pull in a different window's content the
// way the old whole-display-capture design could.
export function buildFfmpegArgs(options, capture) {
  if (!capture || !Number.isFinite(capture.chromeTop) || !Number.isFinite(capture.chromeLeft)) {
    throw new Error('capture chrome offsets (chromeTop, chromeLeft) from the renderer result is required');
  }
  if (!Number.isFinite(capture.outerWidth) || !Number.isFinite(capture.outerHeight)) {
    throw new Error('capture outerWidth/outerHeight from the renderer result is required');
  }
  const bitrate = `${options.videoMbps}M`;
  const args = [
    '-hide_banner', '-loglevel', 'warning', '-nostdin',
    '-f', 'rawvideo', '-pixel_format', 'bgra',
    '-video_size', `${capture.outerWidth}x${capture.outerHeight}`,
    '-framerate', '30',
    '-i', 'pipe:0',
  ];
  if (options.audioDevice) args.push('-f', 'avfoundation', '-i', `none:${options.audioDevice}`);
  args.push(
    '-vf', `crop=${options.width}:${options.height}:${capture.chromeLeft}:${capture.chromeTop}`,
    '-c:v', 'h264_videotoolbox', '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', `${options.videoMbps * 2}M`,
    '-g', '60', '-pix_fmt', 'yuv420p',
  );
  if (options.audioDevice) args.push('-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '128k');
  else args.push('-an');
  args.push('-f', 'mpegts', options.srtUrl);
  return args;
}

export function buildRendererEnv(options, env = process.env, { vdisplay = null } = {}) {
  const rendererEnv = {
    ...env,
    SOREN91_LOCAL_RENDER_SEC: String(options.sessionSec + options.bootTimeoutSec),
    SOREN91_LOCAL_MIN_FPS: String(options.minFps),
    SOREN91_LOCAL_WIDTH: String(options.width),
    SOREN91_LOCAL_HEIGHT: String(options.height),
    SOREN91_LOCAL_RESULT_PATH: options.resultPath,
    SOREN91_LOCAL_VIRTUAL_DISPLAY_BIN: options.virtualDisplayBin,
  };
  // The holder's measured bounds (never assumed coordinates): the renderer
  // parks the Chrome window inside them and proves zero physical overlap.
  if (vdisplay) {
    rendererEnv.SOREN91_LOCAL_VDISPLAY_BOUNDS = JSON.stringify({
      displayID: vdisplay.displayID,
      bounds: vdisplay.bounds,
    });
  }
  return rendererEnv;
}

function run(bin, args, { timeout = 20_000 } = {}) {
  const result = spawnSync(bin, args, { encoding: 'utf8', timeout });
  if (result.error) throw result.error;
  const output = `${String(result.stdout || '')}\n${String(result.stderr || '')}`;
  if (result.status !== 0) {
    throw new Error(`${bin} check failed: ${output.trim()}`);
  }
  return output;
}

function waitForExit(child) {
  return new Promise((resolve) => child.once('exit', (code, signal) => resolve({ code, signal })));
}

async function waitForResult(resultPath, child, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (fs.existsSync(resultPath)) {
      try { return JSON.parse(fs.readFileSync(resultPath, 'utf8')); } catch {}
    }
    if (child.exitCode != null) throw new Error(`renderer exited before readiness (code=${child.exitCode})`);
    await sleep(500);
  }
  throw new Error('renderer readiness timed out');
}

// Spawns the capture helper and waits for its first stderr line (readiness
// or failure). Only after an ok:true status do we consider the pipe safe to
// wire into ffmpeg — see parseCaptureHelperStatus.
async function startCaptureHelper(bin, args, timeoutMs) {
  const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] });
  const rl = readline.createInterface({ input: child.stderr });
  const status = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('capture helper readiness timed out')), timeoutMs);
    let settled = false;
    rl.once('line', (line) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { resolve(parseCaptureHelperStatus(line)); } catch (error) { reject(error); }
    });
    child.once('exit', (code) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`capture helper exited before readiness (code=${code})`));
    });
    child.once('error', (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
  });
  // Keep forwarding any further diagnostic lines for visibility.
  rl.on('line', (line) => console.error(`[capture] ${line}`));
  return { child, status };
}

function terminateTree(child) {
  if (!child || child.exitCode != null || child.killed) return;
  try { child.kill('SIGTERM'); } catch {}
}

export async function main(argv = process.argv.slice(2), { platform = process.platform } = {}) {
  const options = validateOptions(parseArgs(argv), platform);
  const plan = {
    backend: 'local-macos', tier: -1, execute: options.execute,
    sessionSec: options.sessionSec, hardMaxSec: options.hardMaxSec,
    output: [options.width, options.height, 30], capture: 'screencapturekit-window',
    encoder: 'h264_videotoolbox', transport: options.srtUrl ? 'srt-over-tailscale' : 'not-configured',
    display: options.offscreen ? 'offscreen-virtual' : 'onscreen',
  };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  // Privacy-first mode selection: resolveDisplayMode throws unless onscreen
  // was explicitly opted into — an offscreen failure below therefore never
  // silently becomes a visible window.
  const displayMode = resolveDisplayMode(options);

  if (!fs.existsSync(options.captureHelperBin)) {
    throw new Error(`capture helper binary not found at ${options.captureHelperBin}; run tools/soren91_window_capture_build.sh first`);
  }
  const encoders = run(options.ffmpegBin, ['-hide_banner', '-encoders']);
  if (!/h264_videotoolbox/i.test(encoders)) throw new Error('ffmpeg does not expose h264_videotoolbox');
  const protocols = run(options.ffmpegBin, ['-hide_banner', '-protocols']);
  if (!/(^|\s)srt(\s|$)/im.test(protocols)) {
    throw new Error('ffmpeg does not expose the SRT protocol (this build may be missing libsrt; e.g. Homebrew ffmpeg-full)');
  }
  if (options.audioDevice) {
    const devices = run(options.ffmpegBin, ['-hide_banner', '-devices']);
    if (!/avfoundation/i.test(devices)) throw new Error('ffmpeg does not expose avfoundation (needed for --audio-device)');
  }

  fs.rmSync(options.resultPath, { force: true });
  const startedAt = Date.now();
  const hardDeadline = startedAt + options.hardMaxSec * 1000;
  let renderer;
  let capture;
  let ffmpeg;
  let vdisplay;
  let vdisplayStatus = null;
  const cleanup = () => {
    terminateTree(ffmpeg); terminateTree(capture); terminateTree(renderer);
    if (vdisplay) { try { vdisplay.kill('SIGTERM'); } catch {} }
  };
  process.once('SIGINT', cleanup);
  process.once('SIGTERM', cleanup);
  try {
    if (displayMode === 'offscreen') {
      try {
        if (!fs.existsSync(options.virtualDisplayBin)) {
          throw new Error(`virtual display helper binary not found at ${options.virtualDisplayBin}; run tools/soren91_virtual_display_build.sh first`);
        }
        const held = await startVirtualDisplay(options.virtualDisplayBin, {
          timeoutMs: options.bootTimeoutSec * 1000,
        });
        vdisplay = held.child;
        vdisplayStatus = held.status;
        console.log(`SOREN91_LOCAL_VDISPLAY_READY=${JSON.stringify(vdisplayStatus)}`);
      } catch (error) {
        // Fail-closed: never fall back to a visible window on our own. Only
        // an explicit --allow-onscreen / SOREN91_LOCAL_ALLOW_ONSCREEN=1
        // continues on-screen (resolveDisplayMode already recorded the
        // opt-in when offscreen was disabled; reaching here with offscreen
        // enabled means the holder failed, so re-check the opt-in).
        if (!options.allowOnscreen) {
          throw new Error(`offscreen virtual display unavailable (fail-closed): ${error?.message || error}`);
        }
        console.error(`[vdisplay] holder failed but onscreen was explicitly allowed; continuing visibly: ${error?.message || error}`);
      }
    }
    renderer = spawn(process.execPath, [options.renderer], {
      env: buildRendererEnv(options, process.env, { vdisplay: vdisplayStatus }),
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    const result = await waitForResult(options.resultPath, renderer, options.bootTimeoutSec * 1000);
    if (!result?.pass) throw new Error(`renderer probe failed: ${result?.reason || JSON.stringify(result)}`);

    const captureArgs = buildCaptureArgs(options, result.capture);
    const started = await startCaptureHelper(options.captureHelperBin, captureArgs, options.bootTimeoutSec * 1000);
    capture = started.child;
    console.log(`SOREN91_LOCAL_CAPTURE_READY=${JSON.stringify(started.status)}`);

    ffmpeg = spawn(options.ffmpegBin, buildFfmpegArgs(options, result.capture), {
      stdio: ['pipe', 'inherit', 'inherit'],
    });
    capture.stdout.pipe(ffmpeg.stdin);

    const streamDeadline = Math.min(Date.now() + options.sessionSec * 1000, hardDeadline);
    const remaining = Math.max(0, streamDeadline - Date.now());
    const outcome = await Promise.race([
      sleep(remaining).then(() => ({ kind: 'deadline' })),
      waitForExit(ffmpeg).then((value) => ({ kind: 'ffmpeg-exit', value })),
      waitForExit(capture).then((value) => ({ kind: 'capture-exit', value })),
      waitForExit(renderer).then((value) => ({ kind: 'renderer-exit', value })),
    ]);
    if (outcome.kind !== 'deadline') throw new Error(`${outcome.kind}: ${JSON.stringify(outcome.value)}`);
    return { ...plan, result, completed: true };
  } finally {
    cleanup();
    process.removeListener('SIGINT', cleanup);
    process.removeListener('SIGTERM', cleanup);
    // The virtual display dies only with its holder: wait for the exit to
    // prove the display is released before returning.
    if (vdisplay) await stopVirtualDisplay(vdisplay);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

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
import {
  buildAudioFfmpegInputArgs,
  buildFfmpegStdio,
  earlyAttachAudioTap,
  isBenignPipeError,
  resolveSessionEnd,
  resolveTapPids,
  startAudioTap,
  stopAudioTap,
} from './soren91_macos_audio.mjs';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const here = path.dirname(fileURLToPath(import.meta.url));
const defaultRenderer = path.join(here, 'soren91_macos_renderer.mjs');
const defaultCaptureHelperBin = path.join(here, 'macos', 'bin', 'soren91_window_capture');
const defaultVirtualDisplayBin = path.join(here, 'macos', 'bin', 'soren91_virtual_display');
const defaultAudioTapBin = path.join(here, 'macos', 'bin', 'soren91_audio_tap');

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
    // Chrome-scoped audio tap (Issue #303): ON by default. Disable with
    // SOREN91_LOCAL_AUDIO_TAP=0. When on, the session taps ONLY
    // the automation Chrome's descendant PIDs (see soren91_macos_audio.mjs)
    // and wires the helper's s16le PCM into ffmpeg's fd 3. When off, no
    // audio is sent and the renderer launches Chrome with --mute-audio.
    audioTap: envFlag(env, 'SOREN91_LOCAL_AUDIO_TAP', true),
    audioTapBin: env.SOREN91_LOCAL_AUDIO_TAP_BIN || defaultAudioTapBin,
    // Bound for consulting ffmpeg's exit when a producer child wins the
    // end-of-stream race (Issue #303: `audio-tap-exit {SIGPIPE}` and later
    // `capture-exit {code:null,signal:SIGPIPE}` both beat ffmpeg's close
    // racer when the listener closed first). 0 disables the wait (classify
    // the winner alone); see resolveSessionEnd.
    ffmpegExitWaitMs: (() => {
      const raw = Number(env.SOREN91_LOCAL_FFMPEG_EXIT_WAIT_MS);
      return Number.isFinite(raw) && raw >= 0 ? raw : 5000;
    })(),
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
  // Chrome process-tap audio: the helper's s16le 48kHz stereo PCM arrives on
  // fd 3 (see buildFfmpegStdio). Input index stays 1 here because the tap
  // and the legacy avfoundation device are mutually exclusive above.
  if (options.audioTap) args.push(...buildAudioFfmpegInputArgs());
  args.push(
    '-vf', `crop=${options.width}:${options.height}:${capture.chromeLeft}:${capture.chromeTop}`,
    '-c:v', 'h264_videotoolbox', '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', `${options.videoMbps * 2}M`,
    '-g', '60', '-pix_fmt', 'yuv420p',
  );
  if (options.audioDevice || options.audioTap) args.push('-map', '0:v', '-map', '1:a', '-c:a', 'aac', '-b:a', '128k');
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
  // Silent by default: without the audio tap nothing downstream consumes
  // Chrome audio, so mute it at the source. With the tap enabled the game
  // audio is captured instead (and physically muted by the tap itself).
  if (!options.audioTap) rendererEnv.SOREN91_LOCAL_MUTE_AUDIO = '1';
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

// ffmpeg's racer waits for 'close' (not 'exit'): 'close' fires after the
// stdio pipes are flushed, so the stderr ring buffer below is complete when
// we classify the exit. 'exit' can precede the last stderr chunk, which
// would hide the Broken pipe / muxer marker from classifyFfmpegExit.
function waitForCloseExit(child) {
  return new Promise((resolve) => child.once('close', (code, signal) => resolve({ code, signal })));
}

// Cancellable session-deadline racer (Issue #303): the old inline
// `sleep(remaining).then(...)` left its timer pending after an early
// throw/return, holding the event loop open until sessionSec elapsed —
// the process hung ~30 minutes with exitCode=1 already set. The session
// MUST call cancel() in its finally block so no timer survives teardown.
export function createSessionDeadline(ms) {
  let timer = null;
  const promise = new Promise((resolve) => {
    timer = setTimeout(() => {
      timer = null;
      resolve({ kind: 'deadline' });
    }, ms);
  });
  return {
    promise,
    cancel() {
      if (timer != null) {
        clearTimeout(timer);
        timer = null;
      }
    },
  };
}

// Bounded tail of ffmpeg stderr (Issue #303): the last bytes are kept for
// failure diagnostics while the full stream still goes to process.stderr.
export function createStderrTail(limit = 8192) {
  let tail = '';
  return {
    push(chunk) { tail = `${tail}${String(chunk ?? '')}`.slice(-limit); },
    text() { return tail; },
  };
}

// Attaches 'error' handlers to the frame/PCM feeding pipes and ffmpeg's
// input side (Issue #303): when the receiver (OCI listener) closes first,
// ffmpeg exits and in-flight writes fail with EPIPE /
// ERR_STREAM_DESTROYED / ERR_STREAM_WRITE_AFTER_END. Those are benign
// (debug log only, sinkClosed set for classifyFfmpegExit); any other pipe
// error is logged loudly and recorded. Returns the shared tracker.
export function attachPipeGuards({ capture, audiotap, ffmpeg } = {}) {
  const tracker = { sinkClosed: false, errors: [] };
  const guard = (label) => (error) => {
    if (isBenignPipeError(error)) {
      tracker.sinkClosed = true;
      console.error(`[ffmpeg-pipe] benign ${label} close (${error.code}); ignoring`);
      return;
    }
    tracker.errors.push({ label, message: error?.message || String(error) });
    console.error(`[ffmpeg-pipe] ${label} error: ${error?.stack || error?.message || error}`);
  };
  capture?.stdout?.on?.('error', guard('capture-stdout'));
  audiotap?.stdout?.on?.('error', guard('audiotap-stdout'));
  ffmpeg?.stdin?.on?.('error', guard('ffmpeg-stdin'));
  ffmpeg?.stdio?.[3]?.on?.('error', guard('ffmpeg-fd3'));
  ffmpeg?.stderr?.on?.('error', () => {});
  return tracker;
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
export async function startCaptureHelper(bin, args, timeoutMs) {
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
  let audiotap;
  let vdisplay;
  let vdisplayStatus = null;
  // Early audio-tap attach (Issue #303, Part B): polling starts right after
  // the renderer spawns so the tap mute is up before game audio begins.
  let earlyTapPromise = null;
  let earlyTapDetach = null;
  const earlyTapCancel = { value: false };
  const cleanup = () => {
    terminateTree(ffmpeg); terminateTree(capture); terminateTree(renderer); terminateTree(audiotap);
    try { ffmpeg?.stdin?.destroy?.(); } catch {}
    try { capture?.stdout?.destroy?.(); } catch {}
    try { audiotap?.stdout?.destroy?.(); } catch {}
    try { ffmpeg?.stdio?.[3]?.destroy?.(); } catch {}
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
    // Fail fast on a missing tap binary (before the background poll starts
    // retrying a spawn that can never succeed).
    if (options.audioTap && !fs.existsSync(options.audioTapBin)) {
      throw new Error(`audio tap helper binary not found at ${options.audioTapBin}; run tools/soren91_audio_tap_build.sh first`);
    }
    // Early-attach (Issue #303, Part B): poll for the automation Chrome's
    // AudioService from NOW — not after renderer readiness — so the tap
    // (and its CATapMutedWhenTapped physical mute) is up before the game
    // starts making sound. PCM is drain-read until ffmpeg takes over the
    // pipe; a null result falls back to the post-readiness retry below.
    // The never-reject wrapper avoids an unhandled rejection when an
    // earlier boot step throws before we await it; the finally block
    // cancels the poll so it cannot hold the event loop open.
    const bootDeadlineMs = Date.now() + options.bootTimeoutSec * 1000;
    earlyTapPromise = options.audioTap
      ? earlyAttachAudioTap({
        rendererPid: renderer.pid,
        audioTapBin: options.audioTapBin,
        deadlineMs: bootDeadlineMs,
        isCancelled: () => earlyTapCancel.value,
      }).then((value) => value, () => null)
      : Promise.resolve(null);
    const result = await waitForResult(options.resultPath, renderer, options.bootTimeoutSec * 1000);
    if (!result?.pass) throw new Error(`renderer probe failed: ${result?.reason || JSON.stringify(result)}`);

    const captureArgs = buildCaptureArgs(options, result.capture);
    const started = await startCaptureHelper(options.captureHelperBin, captureArgs, options.bootTimeoutSec * 1000);
    capture = started.child;
    console.log(`SOREN91_LOCAL_CAPTURE_READY=${JSON.stringify(started.status)}`);

    // Chrome-scoped audio tap (Issue #303): resolve the automation Chrome's
    // descendant PIDs from the renderer's PID via `ps`, then tap ONLY those.
    // Fail-closed: no resolvable PID aborts the session — the tap is never
    // widened to a bundle-ID scope (which would capture/mute everyday Chrome).
    if (options.audioTap) {
      const early = await earlyTapPromise;
      earlyTapPromise = null;
      if (early) {
        audiotap = early.child;
        earlyTapDetach = early.detachDrain;
        console.log(`SOREN91_LOCAL_AUDIO_TAP_READY=${JSON.stringify(early.status)}`);
        console.log('SOREN91_LOCAL_AUDIO_TAP_ATTACH=early');
      } else {
        let tapPids = null;
        let lastError = null;
        for (let attempt = 0; attempt < 10; attempt += 1) {
          const ps = spawnSync('ps', ['-ax', '-o', 'pid,ppid,command'], { encoding: 'utf8' });
          if (ps.error) {
            lastError = ps.error;
          } else {
            try {
              tapPids = resolveTapPids(ps.stdout || '', renderer.pid);
              break;
            } catch (error) { lastError = error; }
          }
          await sleep(1000);
        }
        if (!tapPids) throw new Error(`audio tap PID resolution failed (fail-closed): ${lastError?.message || lastError}`);
        const audioStarted = await startAudioTap(options.audioTapBin, tapPids, {
          timeoutMs: options.bootTimeoutSec * 1000,
        });
        audiotap = audioStarted.child;
        console.log(`SOREN91_LOCAL_AUDIO_TAP_READY=${JSON.stringify(audioStarted.status)}`);
        console.log('SOREN91_LOCAL_AUDIO_TAP_ATTACH=fallback');
      }
    }

    // ffmpeg stderr is piped (not inherited) so a bounded tail is kept
    // for failure diagnostics; every chunk is still forwarded to
    // process.stderr for live visibility.
    const ffmpegStdio = buildFfmpegStdio(options.audioTap);
    ffmpegStdio[2] = 'pipe';
    ffmpeg = spawn(options.ffmpegBin, buildFfmpegArgs(options, result.capture), {
      stdio: ffmpegStdio,
    });
    const ffmpegStderr = createStderrTail();
    ffmpeg.stderr?.on('data', (chunk) => {
      try { process.stderr.write(chunk); } catch {}
      ffmpegStderr.push(chunk);
    });
    const pipeTracker = attachPipeGuards({ capture, audiotap, ffmpeg });
    capture.stdout.pipe(ffmpeg.stdin);
    if (audiotap) {
      // Early-attach switch-over: stop drain-reading, then hand the live
      // PCM pipe to ffmpeg's fd 3. In-flight chunks may drop at the cut —
      // acceptable; listeners never stack and nothing throws.
      try { earlyTapDetach?.(); } catch {}
      earlyTapDetach = null;
      audiotap.stdout.pipe(ffmpeg.stdio[3]);
      try { audiotap.stdout.resume?.(); } catch {}
    }

    const streamDeadline = Math.min(Date.now() + options.sessionSec * 1000, hardDeadline);
    const remaining = Math.max(0, streamDeadline - Date.now());
    const deadline = createSessionDeadline(remaining);
    // Latest observed ffmpeg close state. When a producer child wins the
    // race (live: `audio-tap-exit {SIGPIPE}` and later `capture-exit
    // {code:null,signal:SIGPIPE}` both beat ffmpeg's close racer when the
    // OCI listener closed first), resolveSessionEnd polls this (bounded by
    // ffmpegExitWaitMs) and classifies FFMPEG's exit — not the winner alone.
    let ffmpegExit = null;
    const racers = [
      deadline.promise,
      waitForCloseExit(ffmpeg).then((value) => {
        ffmpegExit = value;
        return { kind: 'ffmpeg-exit', value };
      }),
      waitForExit(capture).then((value) => ({ kind: 'capture-exit', value })),
      waitForExit(renderer).then((value) => ({ kind: 'renderer-exit', value })),
    ];
    if (audiotap) racers.push(waitForExit(audiotap).then((value) => ({ kind: 'audio-tap-exit', value })));
    const outcome = await Promise.race(racers);
    try {
      const verdict = await resolveSessionEnd({
        kind: outcome.kind,
        value: outcome.value,
        getStderr: () => ffmpegStderr.text(),
        getSinkClosed: () => pipeTracker.sinkClosed,
        getFfmpegExit: () => ffmpegExit,
        ffmpegWaitMs: options.ffmpegExitWaitMs,
      });
      if (verdict === 'deadline') {
        console.log('SOREN91_LOCAL_SESSION_END=deadline');
        return { ...plan, result, completed: true, endReason: 'deadline' };
      }
      if (verdict === 'consumer-closed') {
        // The OCI listener closing first (e.g. `-t 120` expiring) kills
        // ffmpeg with an EPIPE-flavoured muxer error — or a producer 'exit'
        // wins the race first because the SIGPIPE-ignoring Swift helpers
        // stop (exit 0 on EPIPE) before ffmpeg's 'close' fires, or because
        // a pre-SIGPIPE-fix helper build dies with signal SIGPIPE. All are
        // a normal end of stream (exit 0), not a failure: resolveSessionEnd
        // consulted ffmpeg's exit rather than the winner alone.
        console.log('SOREN91_LOCAL_SESSION_END=consumer-closed');
        return { ...plan, result, completed: true, endReason: 'consumer-closed' };
      }
      const tail = ffmpegStderr.text().slice(-2000);
      const detail = ffmpegExit && outcome.kind !== 'ffmpeg-exit'
        ? `${outcome.kind}: ${JSON.stringify(outcome.value)} (ffmpeg-exit: ${JSON.stringify(ffmpegExit)})`
        : `${outcome.kind}: ${JSON.stringify(outcome.value)}`;
      throw new Error(`${detail}${tail ? `\nstderr tail: ${tail}` : ''}`);
    } finally {
      // Cancel the deadline timer FIRST: without this the pending
      // setTimeout keeps the event loop alive until sessionSec elapses
      // (~30 min hang after an early exit — Issue #303).
      deadline.cancel();
    }
  } finally {
    // Cancel the early-tap poll FIRST so it cannot hold the event loop
    // open (same hang class as the deadline timer — Issue #303), then
    // await it so a just-started helper is owned by the session (or
    // SIGTERMed by the cancel race inside earlyAttachAudioTap) rather
    // than leaked. Bounded by one poll interval / one tap-handshake cap.
    earlyTapCancel.value = true;
    if (earlyTapPromise) await earlyTapPromise.catch(() => null);
    earlyTapPromise = null;
    cleanup();
    process.removeListener('SIGINT', cleanup);
    process.removeListener('SIGTERM', cleanup);
    // The audio tap's teardown (IOProc stop -> aggregate destroy -> tap
    // destroy) releases the CATapMutedWhenTapped physical mute: wait for its
    // exit to prove the mute is released before returning.
    if (audiotap) await stopAudioTap(audiotap);
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

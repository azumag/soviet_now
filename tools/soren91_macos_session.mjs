#!/usr/bin/env node
// Soren91 macOS Tier -1 session (same start/status/stop contract as
// soren91_windows_session.mjs, targeting the macOS local renderer/capture/encode
// chain instead of Windows gdigrab + NVENC).
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const here = path.dirname(fileURLToPath(import.meta.url));
const defaultRenderer = path.join(here, 'soren91_macos_renderer.mjs');

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
    captureDeviceIndex: env.SOREN91_LOCAL_CAPTURE_DEVICE_INDEX || '1', // avfoundation "Capture screen 0"
    srtUrl: env.SOREN91_LOCAL_SRT_URL || '',
    audioDevice: env.SOREN91_LOCAL_AUDIO_DEVICE || '',
    renderer: env.SOREN91_LOCAL_RENDERER || defaultRenderer,
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
    ['--capture-device-index', 'captureDeviceIndex'],
    ['--srt-url', 'srtUrl'], ['--audio-device', 'audioDevice'],
    ['--renderer', 'renderer'], ['--result-path', 'resultPath'],
    ['--ffmpeg-bin', 'ffmpegBin'],
  ]);
  const strings = new Set([
    'captureDeviceIndex', 'srtUrl', 'audioDevice', 'renderer', 'resultPath', 'ffmpegBin',
  ]);
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === '--execute') { options.execute = true; continue; }
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
  if (!options.captureDeviceIndex) throw new Error('captureDeviceIndex must not be empty');
  if (!options.renderer) throw new Error('renderer path is required');
  if (options.srtUrl) {
    let target;
    try { target = new URL(options.srtUrl); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
    if (target.protocol !== 'srt:') throw new Error('srtUrl must start with srt://');
    if (/passphrase=/i.test(options.srtUrl)) {
      throw new Error('SRT passphrase in argv is forbidden; use Tailscale transport without an SRT passphrase');
    }
    if (!isTailscaleIpv4Hostname(target.hostname)) {
      throw new Error('srtUrl host must be a Tailscale IPv4 address in 100.64.0.0/10');
    }
  }
  if (options.execute && platform !== 'darwin') throw new Error('paid/live local renderer execution is macOS-only');
  if (options.execute && !options.srtUrl) throw new Error('--execute requires SOREN91_LOCAL_SRT_URL or --srt-url');
  return options;
}

// `crop` comes from the renderer's result JSON (real, CDP-measured window content
// bounds — see soren91_macos_renderer.mjs calibrateWindowBounds). Capturing the
// whole display and cropping to it, rather than sending the raw display feed,
// keeps the rest of the Mac's desktop out of the stream.
export function buildFfmpegArgs(options, crop) {
  if (!crop || !Number.isFinite(crop.cropX) || !Number.isFinite(crop.cropY)) {
    throw new Error('crop rect (cropX, cropY) from the renderer result is required');
  }
  const bitrate = `${options.videoMbps}M`;
  const inputSpec = options.audioDevice
    ? `${options.captureDeviceIndex}:${options.audioDevice}`
    : `${options.captureDeviceIndex}:none`;
  const args = [
    '-hide_banner', '-loglevel', 'warning', '-nostdin',
    '-f', 'avfoundation', '-framerate', '30', '-pixel_format', 'uyvy422', '-capture_cursor', '0',
    '-i', inputSpec,
    '-vf', `crop=${options.width}:${options.height}:${crop.cropX}:${crop.cropY}`,
    '-c:v', 'h264_videotoolbox', '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', `${options.videoMbps * 2}M`,
    '-g', '60', '-pix_fmt', 'yuv420p',
  ];
  if (options.audioDevice) args.push('-c:a', 'aac', '-b:a', '128k');
  else args.push('-an');
  args.push('-f', 'mpegts', options.srtUrl);
  return args;
}

export function buildRendererEnv(options, env = process.env) {
  return {
    ...env,
    SOREN91_LOCAL_RENDER_SEC: String(options.sessionSec + options.bootTimeoutSec),
    SOREN91_LOCAL_MIN_FPS: String(options.minFps),
    SOREN91_LOCAL_WIDTH: String(options.width),
    SOREN91_LOCAL_HEIGHT: String(options.height),
    SOREN91_LOCAL_RESULT_PATH: options.resultPath,
  };
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

function terminateTree(child) {
  if (!child || child.exitCode != null || child.killed) return;
  try { child.kill('SIGTERM'); } catch {}
}

export async function main(argv = process.argv.slice(2), { platform = process.platform } = {}) {
  const options = validateOptions(parseArgs(argv), platform);
  const plan = {
    backend: 'local-macos', tier: -1, execute: options.execute,
    sessionSec: options.sessionSec, hardMaxSec: options.hardMaxSec,
    output: [options.width, options.height, 30], capture: 'avfoundation-crop',
    encoder: 'h264_videotoolbox', transport: options.srtUrl ? 'srt-over-tailscale' : 'not-configured',
  };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  const encoders = run(options.ffmpegBin, ['-hide_banner', '-encoders']);
  if (!/h264_videotoolbox/i.test(encoders)) throw new Error('ffmpeg does not expose h264_videotoolbox');
  const devices = run(options.ffmpegBin, ['-hide_banner', '-devices']);
  if (!/avfoundation/i.test(devices)) throw new Error('ffmpeg does not expose avfoundation');
  const protocols = run(options.ffmpegBin, ['-hide_banner', '-protocols']);
  if (!/(^|\s)srt(\s|$)/im.test(protocols)) {
    throw new Error('ffmpeg does not expose the SRT protocol (this build may be missing libsrt; e.g. Homebrew ffmpeg-full)');
  }

  fs.rmSync(options.resultPath, { force: true });
  const startedAt = Date.now();
  const hardDeadline = startedAt + options.hardMaxSec * 1000;
  let renderer;
  let ffmpeg;
  const cleanup = () => { terminateTree(ffmpeg); terminateTree(renderer); };
  process.once('SIGINT', cleanup);
  process.once('SIGTERM', cleanup);
  try {
    renderer = spawn(process.execPath, [options.renderer], {
      env: buildRendererEnv(options),
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    const result = await waitForResult(options.resultPath, renderer, options.bootTimeoutSec * 1000);
    if (!result?.pass) throw new Error(`renderer probe failed: ${result?.reason || JSON.stringify(result)}`);

    ffmpeg = spawn(options.ffmpegBin, buildFfmpegArgs(options, result.crop), {
      stdio: ['ignore', 'inherit', 'inherit'],
    });
    const streamDeadline = Math.min(Date.now() + options.sessionSec * 1000, hardDeadline);
    const remaining = Math.max(0, streamDeadline - Date.now());
    const outcome = await Promise.race([
      sleep(remaining).then(() => ({ kind: 'deadline' })),
      waitForExit(ffmpeg).then((value) => ({ kind: 'ffmpeg-exit', value })),
      waitForExit(renderer).then((value) => ({ kind: 'renderer-exit', value })),
    ]);
    if (outcome.kind !== 'deadline') throw new Error(`${outcome.kind}: ${JSON.stringify(outcome.value)}`);
    return { ...plan, result, completed: true };
  } finally {
    cleanup();
    process.removeListener('SIGINT', cleanup);
    process.removeListener('SIGTERM', cleanup);
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().catch((error) => {
    console.error(error?.stack || error);
    process.exitCode = 1;
  });
}

#!/usr/bin/env node
// Soren91 Windows remote-CDP host (Windows counterpart of
// tools/soren91_macos_cdp_host.mjs; same agent contract).
//
// Spawns a DEDICATED-profile Google Chrome with --headless=new (no window is
// ever created on any display) and --remote-debugging-port on 127.0.0.1, and
// exposes that port to OCI over a TCP proxy bound ONLY to this machine's
// Tailscale IPv4 that accepts ONLY the OCI Tailscale peer derived from the
// reviewed SRT target. The OCI bot drives the page itself (connectOverCDP).
//
// Once the LOCAL /json/list shows a game page (play.unityroom.com), this host
// captures it WITHOUT screen coordinates:
//   video: CDP Page.startScreencast on that exact page target (frames come
//          from the page's own compositor, never from the desktop), the Unity
//          canvas is cropped per frame (sharp) and scaled/padded to 960x540,
//          paced to a constant 30fps into ffmpeg (h264_nvenc, libx264 fallback).
//   audio: tools/windows/bin/soren91_process_loopback.exe (ApplicationLoopback
//          restricted to the Chrome process tree this host spawned).
// ffmpeg sends mpegts over SRT (caller) to the reviewed OCI listener.
//
// Privacy guarantees enforced in code (and covered by
// tests/test_soren91_windows_cdp_host.mjs):
//   - Chrome args always contain --headless=new and a per-session
//     --user-data-dir; the operator's everyday Chrome profile is never used.
//   - The CDP port must be free before launch and the DevTools browser
//     process must be the Chrome this host spawned (SystemInfo.getProcessInfo).
//   - Frames are only accepted while the screencast page's URL is on the
//     reviewed game origin; leaving it fails closed.
//   - A window audit (no pixels) runs every few seconds: any visible
//     top-level window owned by our Chrome tree fails closed.
//   - ffmpeg never receives a screen-grab input (gdigrab/ddagrab/desktop).
//
// Stop: the agent closes this process's stdin (graceful request), then
// escalates to `taskkill /T /F`. `--reap-orphans` kills anything still
// running with our profile marker and deletes stale profile directories.
//
// Usage:
//   SOREN91_LOCAL_SRT_URL='srt://100.x.y.z:19192?mode=caller' \
//     node tools/soren91_windows_cdp_host.mjs --execute
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  canvasSamplesMatch,
  computeDriverDeadline,
  computeSessionDeadline,
  detectTailscaleIp,
  findGameTarget,
  isAllowedCdpPeer,
  isExactGameTargetUrl,
  parseCanvasGeometry,
  signalExitCode,
  startCdpProxy,
} from './soren91_macos_cdp_host.mjs';
import { isTailscaleIpv4Hostname } from './soren91_macos_session.mjs';
import { isBenignPipeError, resolveSessionEnd } from './soren91_macos_audio.mjs';

export { isAllowedCdpPeer, startCdpProxy };

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const here = path.dirname(fileURLToPath(import.meta.url));

export const PROFILE_DIR_PREFIX = 'soren91-win-cdp-host-';
export const FRESH_PROFILE_DIR_MS = 60_000;
// ffmpeg input backlog (in output frames) beyond which the encoder is not
// keeping up with real time; buffering further would only delay the stream.
export const MAX_FFMPEG_BACKLOG_FRAMES = 90;
export const OUTPUT_FPS = 30;
export const WINDOW_AUDIT_MS = 5000;
export const GEOMETRY_WATCH_MS = 500;
export const FFMPEG_EXIT_WAIT_MS = 5000;
// A frame whose crop cannot be resolved (content resized, canvas missing) is
// skipped and the previous output frame is repeated; this long without a
// usable frame fails closed.
export const MAX_UNUSABLE_FRAME_MS = 10_000;
// Forbidden ffmpeg inputs: anything that reads the desktop by coordinates.
const SCREEN_GRAB_FORMATS = ['gdigrab', 'ddagrab', 'dshow', 'lavfi'];

function envFlag(env, name, defaultValue) {
  const raw = env?.[name];
  if (raw == null || raw === '') return defaultValue;
  return !/^(0|false|no|off)$/i.test(String(raw).trim());
}

export function detectChromeBin(env = process.env, exists = fs.existsSync) {
  if (env.SOREN91_CDP_CHROME_BIN) return env.SOREN91_CDP_CHROME_BIN;
  const candidates = [env.ProgramFiles, env['ProgramFiles(x86)'], env.LOCALAPPDATA]
    .filter(Boolean)
    .map((root) => path.win32.join(root, 'Google', 'Chrome', 'Application', 'chrome.exe'));
  return candidates.find((candidate) => exists(candidate)) || candidates[0] || 'chrome.exe';
}

export function defaults(env = process.env) {
  return {
    execute: false,
    reapOrphans: false,
    cdpPort: Number(env.SOREN91_CDP_PORT || 9322),
    proxyPort: Number(env.SOREN91_CDP_PROXY_PORT || 19093),
    bindIp: env.SOREN91_CDP_BIND_IP || detectTailscaleIp(),
    width: Number(env.SOREN91_LOCAL_WIDTH || 960),
    height: Number(env.SOREN91_LOCAL_HEIGHT || 540),
    // Headless window (= page viewport) size. The OCI bot does not override
    // the viewport on a remote browser, so this is the page layout size.
    contentWidth: Number(env.SOREN91_CDP_HOST_CONTENT_WIDTH || 1280),
    contentHeight: Number(env.SOREN91_CDP_HOST_CONTENT_HEIGHT || 720),
    videoMbps: Number(env.SOREN91_LOCAL_VIDEO_MBPS || 2),
    videoEncoder: String(env.SOREN91_LOCAL_VIDEO_ENCODER || 'auto').trim().toLowerCase(),
    srtUrl: env.SOREN91_LOCAL_SRT_URL || '',
    sessionSec: Number(env.SOREN91_CDP_HOST_SESSION_SEC || 1500),
    driverWaitSec: Number(env.SOREN91_CDP_HOST_DRIVER_WAIT_SEC || 45),
    pollMs: Number(env.SOREN91_CDP_HOST_POLL_MS || 1000),
    audio: envFlag(env, 'SOREN91_LOCAL_AUDIO_LOOPBACK', true),
    audioGain: Number(env.SOREN91_LOCAL_AUDIO_GAIN || 1.0),
    audioFilter: String(env.SOREN91_LOCAL_AUDIO_FILTER || '').trim(),
    // Render endpoint the dedicated Chrome plays into instead of this PC's
    // speakers (an unused virtual cable), and the directory that Chrome lives
    // in. Windows remembers the routing per executable path.
    audioSink: String(env.SOREN91_LOCAL_AUDIO_SINK || '').trim(),
    audioSinkDir: String(env.SOREN91_LOCAL_AUDIO_SINK_DIR || '').trim(),
    loopbackBin: env.SOREN91_LOCAL_LOOPBACK_BIN
      || path.join(here, 'windows', 'bin', 'soren91_process_loopback.exe'),
    windowAuditBin: env.SOREN91_LOCAL_WINDOW_AUDIT_BIN
      || path.join(here, 'windows', 'bin', 'soren91_window_audit.exe'),
    chromeBin: detectChromeBin(env),
    ffmpegBin: env.SOREN91_LOCAL_FFMPEG_BIN || 'ffmpeg',
    tmpDir: env.SOREN91_CDP_HOST_TMP_DIR || os.tmpdir(),
    resultPath: env.SOREN91_CDP_HOST_RESULT_PATH
      || path.join(os.tmpdir(), 'soren91-windows-cdp-host-result.json'),
  };
}

export function validateSrtUrl(srtUrl) {
  let target;
  try { target = new URL(srtUrl); } catch { throw new Error('srtUrl must be a valid srt:// URL'); }
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
  return target;
}

export function validateOptions(options, platform = process.platform) {
  for (const key of ['cdpPort', 'proxyPort']) {
    if (!Number.isInteger(options[key]) || options[key] < 1024 || options[key] > 65535) {
      throw new Error(`${key} must be 1024..65535`);
    }
  }
  if (options.proxyPort === options.cdpPort) throw new Error('proxyPort must differ from cdpPort');
  if (!options.reapOrphans && !isTailscaleIpv4Hostname(options.bindIp)) {
    throw new Error(
      `bindIp must be a Tailscale IPv4 in 100.64.0.0/10 (got ${JSON.stringify(options.bindIp)}); `
      + 'the CDP proxy must never bind 0.0.0.0 or a LAN address',
    );
  }
  if (options.width !== 960 || options.height !== 540) {
    throw new Error('Windows cdp-host output must be 960x540');
  }
  for (const key of ['contentWidth', 'contentHeight']) {
    const value = options[key];
    if (!Number.isInteger(value) || value < 640 || value > 4096) {
      throw new Error(`${key} must be an integer 640..4096`);
    }
  }
  if (!(options.videoMbps > 0 && options.videoMbps <= 8)) throw new Error('videoMbps must be >0 and <=8');
  if (!['auto', 'h264_nvenc', 'libx264'].includes(options.videoEncoder)) {
    throw new Error('videoEncoder must be auto, h264_nvenc or libx264');
  }
  if (!Number.isInteger(options.driverWaitSec) || options.driverWaitSec < 5 || options.driverWaitSec > 120) {
    throw new Error('driverWaitSec must be an integer 5..120');
  }
  if (!Number.isInteger(options.sessionSec) || options.sessionSec < 30 || options.sessionSec > 7200) {
    throw new Error('sessionSec must be an integer 30..7200');
  }
  if (!Number.isFinite(options.audioGain) || options.audioGain < 0.1 || options.audioGain > 16) {
    throw new Error('audioGain must be a number 0.1..16');
  }
  if (options.execute && !options.srtUrl) throw new Error('--execute requires SOREN91_LOCAL_SRT_URL');
  if ((options.execute || options.reapOrphans) && platform !== 'win32') {
    throw new Error('--execute/--reap-orphans are Windows-only');
  }
  if (options.execute && options.audio) validateAudioSink(options);
  if (options.srtUrl) validateSrtUrl(options.srtUrl);
  return options;
}

const OPERATOR_CHROME_TAIL = '\\google\\chrome\\application\\chrome.exe';

// Process loopback captures after the session mute, so the game cannot be
// muted at the source; it is routed to an endpoint nobody listens to instead.
// That per-app routing persists by executable path, so it must only ever be
// set for a dedicated Chrome (Chrome for Testing), never the operator's.
export function validateAudioSink(options) {
  if (!options.audioSink || !options.audioSinkDir) {
    throw new Error(
      'audio needs SOREN91_LOCAL_AUDIO_SINK and SOREN91_LOCAL_AUDIO_SINK_DIR '
      + '(otherwise the game plays on this PC\'s speakers); set SOREN91_LOCAL_AUDIO_LOOPBACK=0 to stream without audio',
    );
  }
  const chrome = path.win32.resolve(options.chromeBin).toLowerCase();
  const dir = `${path.win32.resolve(options.audioSinkDir).toLowerCase().replace(/\\+$/, '')}\\`;
  if (chrome.endsWith(OPERATOR_CHROME_TAIL)) {
    throw new Error('audio sink routing needs a dedicated Chrome (SOREN91_CDP_CHROME_BIN), not the installed Google Chrome');
  }
  if (!chrome.startsWith(dir)) throw new Error('SOREN91_CDP_CHROME_BIN must live under SOREN91_LOCAL_AUDIO_SINK_DIR');
  return options;
}

export function buildLoopbackArgs(options, chromePid, parentPid) {
  const args = ['--pid', String(chromePid), '--expect-image', 'chrome.exe', '--parent-pid', String(parentPid)];
  if (options.audioSink) args.push('--sink-endpoint', options.audioSink, '--sink-allowed-dir', options.audioSinkDir);
  return args;
}

export function parseArgs(argv, env = process.env) {
  const options = defaults(env);
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--execute') { options.execute = true; continue; }
    if (arg === '--reap-orphans') { options.reapOrphans = true; continue; }
    if (arg === '--cdp-port') { options.cdpPort = Number(argv[++i]); continue; }
    if (arg === '--proxy-port') { options.proxyPort = Number(argv[++i]); continue; }
    if (arg === '--bind-ip') { options.bindIp = argv[++i]; continue; }
    if (arg === '--srt-url') { options.srtUrl = argv[++i]; continue; }
    throw new Error(`unknown argument: ${arg}`);
  }
  return options;
}

export function profileMarker(profileDir) {
  return `--user-data-dir=${profileDir}`;
}

export function buildChromeArgs(options, profileDir) {
  if (!profileDir || !path.win32.basename(profileDir).startsWith(PROFILE_DIR_PREFIX)) {
    throw new Error(`profileDir must be a dedicated ${PROFILE_DIR_PREFIX}* directory (fail-closed)`);
  }
  return [
    // Headless: Chrome never creates a window on any display, so nothing the
    // operator sees can be captured and nothing is shown on screen.
    '--headless=new',
    `--remote-debugging-port=${options.cdpPort}`,
    '--remote-debugging-address=127.0.0.1',
    // Safe only behind the peer-locked Tailscale proxy; Chrome itself stays
    // bound to 127.0.0.1.
    '--remote-allow-origins=*',
    profileMarker(profileDir),
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-features=Translate',
    '--use-angle=d3d11',
    `--window-size=${options.contentWidth},${options.contentHeight}`,
    '--autoplay-policy=no-user-gesture-required',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--hide-scrollbars',
    ...(options.audio ? [] : ['--mute-audio']),
    'about:blank',
  ];
}

// Encoder choice. `auto` uses NVENC only when ffmpeg lists it AND a 1-frame
// trial encode succeeded (a listed encoder can still lack a usable driver).
export function resolveVideoEncoder(requested, { encodersText = '', nvencTrialOk = false } = {}) {
  if (requested === 'libx264') return 'libx264';
  const listed = /\bh264_nvenc\b/.test(String(encodersText));
  if (requested === 'h264_nvenc') {
    if (!listed) throw new Error('ffmpeg does not expose h264_nvenc');
    return 'h264_nvenc';
  }
  return listed && nvencTrialOk ? 'h264_nvenc' : 'libx264';
}

export function buildFfmpegArgs(options, { encoder }) {
  if (options.width !== 960 || options.height !== 540) throw new Error('output must be 960x540 (fail-closed)');
  const bitrate = `${options.videoMbps}M`;
  const args = [
    '-hide_banner', '-loglevel', 'warning', '-nostdin',
    '-thread_queue_size', '64',
    '-f', 'rawvideo', '-pixel_format', 'rgb24',
    '-video_size', `${options.width}x${options.height}`,
    '-framerate', String(OUTPUT_FPS),
    '-i', 'pipe:0',
  ];
  if (options.audio) {
    args.push('-thread_queue_size', '256', '-f', 's16le', '-ar', '48000', '-ac', '2', '-channel_layout', 'stereo', '-i', 'pipe:3');
  }
  args.push('-map', '0:v');
  if (options.audio) args.push('-map', '1:a');
  if (encoder === 'h264_nvenc') {
    args.push('-c:v', 'h264_nvenc', '-preset', 'p4', '-tune', 'll', '-rc', 'cbr', '-bf', '0');
  } else if (encoder === 'libx264') {
    args.push('-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency');
  } else {
    throw new Error(`unsupported encoder ${JSON.stringify(encoder)}`);
  }
  args.push(
    '-b:v', bitrate, '-maxrate', bitrate, '-bufsize', `${options.videoMbps * 2}M`,
    '-g', '60', '-pix_fmt', 'yuv420p', '-r', String(OUTPUT_FPS),
  );
  if (options.audio) {
    const gain = Number(options.audioGain ?? 1);
    const filter = String(options.audioFilter || '').trim()
      || (Number.isFinite(gain) && gain !== 1 ? `volume=${gain}` : '');
    if (filter) args.push('-af', filter);
    args.push('-c:a', 'aac', '-b:a', '128k', '-ar', '48000');
  } else {
    args.push('-an');
  }
  args.push('-f', 'mpegts', options.srtUrl);
  assertNoScreenGrabInputs(args);
  return args;
}

// Fail-closed guard: the pipeline may only read pipes, never the desktop.
export function assertNoScreenGrabInputs(args) {
  for (let i = 0; i < args.length; i += 1) {
    if (args[i] === '-f' && SCREEN_GRAB_FORMATS.includes(String(args[i + 1]).toLowerCase())) {
      throw new Error(`screen-grab input ${args[i + 1]} is forbidden (fail-closed)`);
    }
    if (args[i] === '-i' && !/^pipe:\d+$/.test(String(args[i + 1]))) {
      throw new Error(`ffmpeg input ${JSON.stringify(args[i + 1])} is not a pipe (fail-closed)`);
    }
  }
  return args;
}

// JPEG dimensions from the first SOFn marker (baseline/progressive).
export function parseJpegSize(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 4 || buf[0] !== 0xff || buf[1] !== 0xd8) return null;
  let offset = 2;
  while (offset + 4 <= buf.length) {
    if (buf[offset] !== 0xff) return null;
    const marker = buf[offset + 1];
    if (marker === 0xff) { offset += 1; continue; }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) { offset += 2; continue; }
    const length = buf.readUInt16BE(offset + 2);
    const isSof = marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker);
    if (isSof) {
      if (offset + 9 > buf.length) return null;
      return { height: buf.readUInt16BE(offset + 5), width: buf.readUInt16BE(offset + 7) };
    }
    if (length < 2) return null;
    offset += 2 + length;
  }
  return null;
}

// Canvas rect (CSS px) -> integer extract rect in screencast frame pixels.
// Fail-closed: the frame must be a uniform scale of the measured content
// (within 2%), and the rect must lie inside the frame (2px tolerance).
export function resolveScreencastCrop({ frameWidth, frameHeight, canvas }) {
  if (!(Number.isInteger(frameWidth) && Number.isInteger(frameHeight) && frameWidth >= 64 && frameHeight >= 64)) {
    throw new Error(`screencast frame size invalid (fail-closed): ${frameWidth}x${frameHeight}`);
  }
  const geom = parseCanvasGeometry(canvas);
  const sx = frameWidth / geom.iw;
  const sy = frameHeight / geom.ih;
  if (Math.abs(sx - sy) / Math.max(sx, sy) > 0.02) {
    throw new Error(`screencast frame ${frameWidth}x${frameHeight} is not a uniform scale of content ${geom.iw}x${geom.ih} (fail-closed)`);
  }
  const left = Math.max(0, Math.round(geom.x * sx));
  const top = Math.max(0, Math.round(geom.y * sy));
  let width = Math.round(geom.width * sx);
  let height = Math.round(geom.height * sy);
  if (left + width > frameWidth) width = frameWidth - left;
  if (top + height > frameHeight) height = frameHeight - top;
  if (!(width >= 64 && height >= 64)) {
    throw new Error(`game canvas crop too small (fail-closed): ${width}x${height}`);
  }
  return { left, top, width, height };
}

// Constant-rate pacer: how many output frames are due at `nowMs` so that
// frame n is written at startedAt + n/fps. Late ticks catch up (bounded) so
// the video timeline (n/30) never drifts from wall clock / the audio.
export function framesDue(startedAtMs, nowMs, framesWritten, fps = OUTPUT_FPS, maxBurst = fps * 2) {
  const due = Math.floor(((nowMs - startedAtMs) * fps) / 1000) + 1 - framesWritten;
  return Math.max(0, Math.min(maxBurst, due));
}

// Owned-browser proof: the DevTools endpoint on our port must belong to the
// Chrome process this host spawned (never an unrelated/operator Chrome).
export function assertOwnedBrowserPid(processInfo, expectedPid) {
  const list = Array.isArray(processInfo) ? processInfo : [];
  const browser = list.find((entry) => entry?.type === 'browser');
  if (!browser || Number(browser.id) !== Number(expectedPid)) {
    throw new Error(
      `DevTools browser process ${browser?.id ?? 'unknown'} is not the Chrome this host spawned (${expectedPid}) (fail-closed)`,
    );
  }
  return true;
}

// Visible-window audit result -> violation list (empty = OK).
export function windowAuditViolations(audit) {
  if (!audit || typeof audit !== 'object' || !Array.isArray(audit.visible)) {
    throw new Error('window audit result is malformed (fail-closed)');
  }
  return audit.visible;
}

// Selects processes that belong to a cdp-host session: their command line
// must carry our dedicated --user-data-dir marker. The operator's Chrome
// never carries it, so it can never be selected.
export function selectOwnedProcesses(processes, { tmpDir = os.tmpdir(), profileDir = null } = {}) {
  const list = Array.isArray(processes) ? processes : [];
  const needle = profileDir
    ? profileMarker(profileDir).toLowerCase()
    : profileMarker(path.win32.join(tmpDir, PROFILE_DIR_PREFIX)).toLowerCase();
  return list.filter((entry) => {
    const commandLine = String(entry?.commandLine || '').toLowerCase().replace(/"/g, '');
    return Number.isInteger(entry?.pid) && entry.pid > 0 && commandLine.includes(needle);
  });
}

export function listProcessesWindows() {
  const result = spawnSync('powershell.exe', [
    '-NoProfile', '-NonInteractive', '-Command',
    'Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress',
  ], { encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 64 * 1024 * 1024 });
  if (result.error) throw result.error;
  const parsed = JSON.parse(String(result.stdout || '[]') || '[]');
  return (Array.isArray(parsed) ? parsed : [parsed]).map((entry) => ({
    pid: Number(entry?.ProcessId), commandLine: entry?.CommandLine || '',
  }));
}

export function killProcessTreeWindows(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return;
  spawnSync('taskkill', ['/PID', String(pid), '/T', '/F'], { stdio: 'ignore', windowsHide: true });
}

export function reapOrphans({
  tmpDir = os.tmpdir(),
  profileDir = null,
  listImpl = listProcessesWindows,
  killImpl = killProcessTreeWindows,
  readdirImpl = (dir) => fs.readdirSync(dir),
  rmImpl = (dir) => fs.rmSync(dir, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }),
  now = () => Date.now(),
  graceMs = FRESH_PROFILE_DIR_MS,
} = {}) {
  let killed = [];
  try {
    killed = selectOwnedProcesses(listImpl(), { tmpDir, profileDir }).map((entry) => entry.pid);
    for (const pid of killed) killImpl(pid);
  } catch (error) {
    console.error(`[win-cdp-host] orphan process sweep failed: ${error?.message || error}`);
  }
  const removed = [];
  if (profileDir) {
    try { rmImpl(profileDir); removed.push(profileDir); } catch {}
  } else {
    let entries = [];
    try { entries = readdirImpl(tmpDir); } catch {}
    for (const name of entries) {
      if (!name.startsWith(PROFILE_DIR_PREFIX)) continue;
      const createdAt = Number(name.slice(PROFILE_DIR_PREFIX.length));
      // Every owned process was killed above, so nothing still uses these;
      // the grace only spares a directory a session is creating right now.
      if (Number.isFinite(createdAt) && now() - createdAt < graceMs) continue;
      try { rmImpl(path.win32.join(tmpDir, name)); removed.push(name); } catch {}
    }
  }
  return { killed, removed };
}

function portIsFree(port) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    const done = (free) => { try { socket.destroy(); } catch {} resolve(free); };
    socket.once('connect', () => done(false));
    socket.once('error', () => done(true));
    socket.setTimeout(1000, () => done(true));
  });
}

async function fetchJsonList(cdpPort) {
  const res = await fetch(`http://127.0.0.1:${cdpPort}/json/list`);
  if (!res.ok) throw new Error(`/json/list http=${res.status}`);
  return res.json();
}

function runWindowAudit(bin, rootPid) {
  const result = spawnSync(bin, ['--root-pid', String(rootPid)], {
    encoding: 'utf8', windowsHide: true, timeout: 15_000,
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`window audit failed: ${String(result.stderr || '').trim()}`);
  return JSON.parse(String(result.stdout || '').trim());
}

function probeNvenc(ffmpegBin) {
  const encoders = spawnSync(ffmpegBin, ['-hide_banner', '-encoders'], {
    encoding: 'utf8', windowsHide: true, timeout: 20_000,
  });
  const encodersText = `${encoders.stdout || ''}`;
  if (!/\bh264_nvenc\b/.test(encodersText)) return { encodersText, nvencTrialOk: false };
  const trial = spawnSync(ffmpegBin, [
    '-hide_banner', '-loglevel', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=256x144:r=30:d=0.2',
    '-c:v', 'h264_nvenc', '-f', 'null', '-',
  ], { encoding: 'utf8', windowsHide: true, timeout: 20_000 });
  return { encodersText, nvencTrialOk: trial.status === 0 };
}

function childExit(child, kind, event = 'exit') {
  if (child.exitCode != null || child.signalCode != null) {
    return Promise.resolve({ kind, value: { code: child.exitCode, signal: child.signalCode } });
  }
  return new Promise((resolve) => child.once(event, (code, signal) => resolve({ kind, value: { code, signal } })));
}

function waitExit(child, timeoutMs) {
  if (!child || child.exitCode != null || child.signalCode != null) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    child.once('exit', () => { clearTimeout(timer); resolve(); });
  });
}

function sampleCanvasScript() {
  const el = document.querySelector('#unity-canvas') || document.querySelector('canvas');
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return {
    x: r.x, y: r.y, width: r.width, height: r.height,
    iw: window.innerWidth, ih: window.innerHeight, dpr: window.devicePixelRatio || 1,
  };
}

export async function main(argv = process.argv.slice(2), { platform = process.platform } = {}) {
  const options = validateOptions(parseArgs(argv), platform);
  if (options.reapOrphans) {
    const reaped = reapOrphans({ tmpDir: options.tmpDir });
    console.log(`SOREN91_CDP_HOST_REAPED=${JSON.stringify({ killed: reaped.killed.length, removed: reaped.removed.length })}`);
    return reaped;
  }
  const allowedPeerIp = options.srtUrl ? new URL(options.srtUrl).hostname : '';
  const plan = {
    backend: 'windows-cdp-host', tier: -1, execute: options.execute,
    cdp: `127.0.0.1:${options.cdpPort}`,
    proxy: `${options.bindIp}:${options.proxyPort} (tailscale peer-locked)`,
    output: [options.width, options.height, OUTPUT_FPS],
    transport: options.srtUrl ? 'srt-over-tailscale' : 'not-configured',
    display: 'headless (no window)',
    capture: 'cdp-screencast (page target only)',
    audio: options.audio ? 'process-loopback (own chrome tree)' : 'none',
  };
  console.log(JSON.stringify(plan, null, 2));
  if (!options.execute) return plan;

  // Heavy deps are loaded only for a real session so contract tests and
  // --reap-orphans stay lightweight.
  const [{ chromium }, { default: sharp }] = await Promise.all([import('playwright'), import('sharp')]);

  let chrome = null;
  let proxy = null;
  let browser = null;
  let loopback = null;
  let ffmpeg = null;
  let profileDir = null;
  let pacer = null;
  let geometryWatch = null;
  let auditWatch = null;
  let failClosedReject = null;
  const failClosedPromise = new Promise((_, reject) => { failClosedReject = reject; });
  failClosedPromise.catch(() => {});
  let failed = false;
  const failClosed = (reason) => {
    if (failed) return;
    failed = true;
    console.error(`[win-cdp-host] FAIL-CLOSED ${reason}`);
    failClosedReject(new Error(`fail-closed: ${reason}`));
  };

  let cleanedUp = false;
  const cleanup = () => {
    if (cleanedUp) return;
    cleanedUp = true;
    if (pacer) { clearInterval(pacer); pacer = null; }
    if (geometryWatch) { clearInterval(geometryWatch); geometryWatch = null; }
    if (auditWatch) { clearInterval(auditWatch); auditWatch = null; }
    try { ffmpeg?.stdin?.destroy?.(); } catch {}
    try { loopback?.stdout?.destroy?.(); } catch {}
    for (const child of [ffmpeg, loopback, chrome]) {
      if (child && child.exitCode == null && child.signalCode == null) killProcessTreeWindows(child.pid);
    }
    if (proxy) { try { proxy.close(); } catch {} }
    if (profileDir) reapOrphans({ tmpDir: options.tmpDir, profileDir });
  };
  const shutdown = (signal) => {
    console.error(`[win-cdp-host] stop requested (${signal})`);
    cleanup();
    process.exit(signalExitCode(signal));
  };
  const onSigint = () => shutdown('SIGINT');
  const onSigterm = () => shutdown('SIGTERM');
  const onStdinEnd = () => shutdown('SIGTERM');
  process.once('SIGINT', onSigint);
  process.once('SIGTERM', onSigterm);
  process.once('SIGBREAK', onSigterm);
  // The agent requests a graceful stop by closing our stdin; an agent crash
  // closes it too, so this host never outlives its supervisor.
  if (!process.stdin.isTTY) {
    process.stdin.on('data', () => {});
    process.stdin.once('end', onStdinEnd);
    process.stdin.once('close', onStdinEnd);
  }

  const startedAt = Date.now();
  try {
    // Previous sessions killed with taskkill /F cannot clean up; sweep first.
    reapOrphans({ tmpDir: options.tmpDir });
    if (!(await portIsFree(options.cdpPort))) {
      throw new Error(`127.0.0.1:${options.cdpPort} is already in use; refusing to attach to a Chrome this host did not spawn`);
    }
    profileDir = path.win32.join(options.tmpDir, `${PROFILE_DIR_PREFIX}${Date.now()}`);
    fs.mkdirSync(profileDir, { recursive: true });
    chrome = spawn(options.chromeBin, buildChromeArgs(options, profileDir), {
      stdio: ['ignore', 'ignore', 'ignore'], windowsHide: true,
    });
    console.log(`SOREN91_CDP_HOST_CHROME_PID=${chrome.pid}`);
    let ready = false;
    for (let i = 0; i < 60 && !ready; i += 1) {
      if (chrome.exitCode != null) throw new Error(`chrome exited early (code=${chrome.exitCode})`);
      try { ready = (await fetch(`http://127.0.0.1:${options.cdpPort}/json/version`)).ok; } catch {}
      if (!ready) await sleep(500);
    }
    if (!ready) throw new Error('chrome DevTools endpoint did not come up');
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${options.cdpPort}`);
    const browserCdp = await browser.newBrowserCDPSession();
    const { processInfo } = await browserCdp.send('SystemInfo.getProcessInfo');
    assertOwnedBrowserPid(processInfo, chrome.pid);
    const initialAudit = runWindowAudit(options.windowAuditBin, chrome.pid);
    if (windowAuditViolations(initialAudit).length) {
      throw new Error(`our Chrome shows a visible window (fail-closed): ${JSON.stringify(initialAudit.visible)}`);
    }
    console.log(`SOREN91_CDP_HOST_CDP_READY=http://127.0.0.1:${options.cdpPort}`);
    proxy = await startCdpProxy({
      bindIp: options.bindIp, proxyPort: options.proxyPort, cdpPort: options.cdpPort, allowedPeerIp,
    });
    console.log(`SOREN91_CDP_HOST_PROXY_READY=${options.bindIp}:${options.proxyPort}`);

    // Keep auditing for the whole session: a visible window from our Chrome
    // tree is a privacy violation, never something to stream past.
    auditWatch = setInterval(() => {
      try {
        const audit = runWindowAudit(options.windowAuditBin, chrome.pid);
        const violations = windowAuditViolations(audit);
        if (violations.length) failClosed(`visible window from our Chrome tree: ${JSON.stringify(violations)}`);
      } catch (error) {
        if (chrome.exitCode == null) failClosed(`window audit unavailable: ${error?.message || error}`);
      }
    }, WINDOW_AUDIT_MS);
    auditWatch.unref?.();

    const sessionDeadline = computeSessionDeadline(startedAt, options.sessionSec);
    const driverDeadline = computeDriverDeadline(startedAt, Date.now(), options.sessionSec, options.driverWaitSec);
    console.log('SOREN91_CDP_HOST_DRIVER_WAITING=1');
    let gameTarget = null;
    while (Date.now() < driverDeadline && !failed) {
      if (chrome.exitCode != null) throw new Error(`chrome exited while waiting (code=${chrome.exitCode})`);
      let targets = [];
      try { targets = await fetchJsonList(options.cdpPort); } catch (error) {
        console.error(`[win-cdp-host] /json/list poll failed: ${error?.message || error}`);
      }
      gameTarget = findGameTarget(targets);
      if (gameTarget) break;
      await sleep(options.pollMs);
    }
    if (failed) await failClosedPromise;
    if (!gameTarget) throw new Error('cdp-host driver did not reach the reviewed game origin before driver wait deadline');
    console.log('SOREN91_CDP_HOST_GAME_FOUND=1');

    // Locate the exact page through our own local CDP connection.
    let page = null;
    for (let i = 0; i < 20 && !page; i += 1) {
      for (const context of browser.contexts()) {
        page = context.pages().find((candidate) => isExactGameTargetUrl(candidate.url() || '')) || page;
      }
      if (!page) await sleep(250);
    }
    if (!page) throw new Error('local CDP has no exact play.unityroom.com page');
    const cdp = await page.context().newCDPSession(page);
    page.once('close', () => failClosed('game page closed'));
    page.on('framenavigated', (frame) => {
      if (frame === page.mainFrame() && !isExactGameTargetUrl(frame.url())) {
        failClosed('game page navigated away from the reviewed game origin');
      }
    });

    // Canvas geometry: sampled continuously; a new geometry is adopted only
    // after it has been stable for GEOMETRY_STABLE_MS and validated.
    let activeCanvas = null;
    let candidate = null;
    let candidateSince = 0;
    const sampleCanvas = async () => {
      try { return await page.evaluate(sampleCanvasScript); } catch { return null; }
    };
    const adoptGeometry = (sample) => {
      try { parseCanvasGeometry(sample); } catch { return; }
      if (activeCanvas && canvasSamplesMatch(activeCanvas, sample)) return;
      activeCanvas = sample;
      console.log(`SOREN91_CDP_HOST_CANVAS=${JSON.stringify(sample)}`);
    };
    const geometryTick = async () => {
      const sample = await sampleCanvas();
      const now = Date.now();
      if (!sample) { candidate = null; return; }
      if (candidate && canvasSamplesMatch(candidate, sample)) {
        if (now - candidateSince >= 1000) adoptGeometry(sample);
      } else {
        candidate = sample;
        candidateSince = now;
      }
    };
    const geometryDeadline = Date.now() + 180_000;
    while (!activeCanvas) {
      if (failed) await failClosedPromise;
      if (Date.now() > geometryDeadline) throw new Error('game canvas geometry never stabilized (fail-closed)');
      await geometryTick();
      if (!activeCanvas) await sleep(250);
    }
    let geometryBusy = false;
    geometryWatch = setInterval(async () => {
      if (geometryBusy) return;
      geometryBusy = true;
      try { await geometryTick(); } finally { geometryBusy = false; }
    }, GEOMETRY_WATCH_MS);
    geometryWatch.unref?.();

    // Screencast: frames come from this page target's compositor only.
    let latestJpeg = null;
    let latestSeq = 0;
    let screencastFrames = 0;
    cdp.on('Page.screencastFrame', (event) => {
      cdp.send('Page.screencastFrameAck', { sessionId: event.sessionId }).catch(() => {});
      if (!isExactGameTargetUrl(page.url() || '')) {
        failClosed('screencast frame from a non-game URL');
        return;
      }
      latestJpeg = Buffer.from(event.data, 'base64');
      latestSeq += 1;
      screencastFrames += 1;
    });
    await cdp.send('Page.startScreencast', { format: 'jpeg', quality: 85, everyNthFrame: 1 });

    let outputFrame = null;
    let processedSeq = 0;
    let processing = false;
    let lastUsableAt = Date.now();
    let cropLogged = '';
    const processLatest = async () => {
      if (processing || !latestJpeg || latestSeq === processedSeq) return;
      processing = true;
      const seq = latestSeq;
      const jpeg = latestJpeg;
      try {
        const size = parseJpegSize(jpeg);
        if (!size) throw new Error('screencast frame is not a JPEG');
        const crop = resolveScreencastCrop({ frameWidth: size.width, frameHeight: size.height, canvas: activeCanvas });
        const key = JSON.stringify({ ...crop, frame: `${size.width}x${size.height}` });
        if (key !== cropLogged) { cropLogged = key; console.log(`SOREN91_CDP_HOST_CANVAS_CROP=${key}`); }
        outputFrame = await sharp(jpeg)
          .extract(crop)
          .resize(options.width, options.height, { fit: 'contain', background: { r: 0, g: 0, b: 0 } })
          .removeAlpha()
          .raw()
          .toBuffer();
        lastUsableAt = Date.now();
      } catch (error) {
        if (Date.now() - lastUsableAt > MAX_UNUSABLE_FRAME_MS) {
          failClosed(`no usable game frame for ${MAX_UNUSABLE_FRAME_MS}ms: ${error?.message || error}`);
        }
      } finally {
        processedSeq = seq;
        processing = false;
      }
    };
    const firstFrameDeadline = Date.now() + 30_000;
    while (!outputFrame) {
      if (failed) await failClosedPromise;
      if (Date.now() > firstFrameDeadline) throw new Error('no usable screencast frame within 30s (fail-closed)');
      await processLatest();
      if (!outputFrame) await sleep(50);
    }

    const { encodersText, nvencTrialOk } = options.videoEncoder === 'libx264'
      ? { encodersText: '', nvencTrialOk: false }
      : probeNvenc(options.ffmpegBin);
    const encoder = resolveVideoEncoder(options.videoEncoder, { encodersText, nvencTrialOk });
    const ffmpegArgs = buildFfmpegArgs(options, { encoder });
    ffmpeg = spawn(options.ffmpegBin, ffmpegArgs, {
      stdio: ['pipe', 'ignore', 'pipe', ...(options.audio ? ['pipe'] : [])], windowsHide: true,
    });
    console.log(`SOREN91_CDP_HOST_ENCODER=${encoder}`);
    let ffmpegStderr = '';
    ffmpeg.stderr.on('data', (chunk) => {
      try { process.stderr.write(chunk); } catch {}
      ffmpegStderr = (ffmpegStderr + String(chunk)).slice(-8192);
    });
    let sinkClosed = false;
    const guard = (stream) => stream?.on?.('error', (error) => {
      if (isBenignPipeError(error) || error?.code === 'EOF') { sinkClosed = true; return; }
      console.error(`[win-cdp-host] pipe error: ${error?.message || error}`);
    });
    guard(ffmpeg.stdin);
    let ffmpegExit = null;
    ffmpeg.on('close', (code, signal) => {
      ffmpegExit = { code, signal };
      console.error(`[win-cdp-host] ffmpeg closed code=${code} signal=${signal}`);
    });

    if (options.audio) {
      loopback = spawn(options.loopbackBin, buildLoopbackArgs(options, chrome.pid, process.pid), {
        stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true,
      });
      guard(loopback.stdout);
      guard(ffmpeg.stdio[3]);
      loopback.stdout.pipe(ffmpeg.stdio[3]);
      const loopbackReady = await new Promise((resolve) => {
        let text = '';
        const timer = setTimeout(() => resolve(null), 15_000);
        loopback.stderr.on('data', (chunk) => {
          text += String(chunk);
          try { process.stderr.write(chunk); } catch {}
          const match = text.match(/SOREN91_LOOPBACK_READY=(\{.*\})/);
          if (match) { clearTimeout(timer); resolve(match[1]); }
          if (/SOREN91_LOOPBACK_END=/.test(text) && !match) { clearTimeout(timer); resolve(null); }
        });
        loopback.once('exit', () => { clearTimeout(timer); resolve(null); });
      });
      if (!loopbackReady) throw new Error('Chrome process loopback did not start (fail-closed)');
      console.log(`SOREN91_CDP_HOST_AUDIO_READY=${loopbackReady}`);
    }

    // Constant 30fps output clocked by wall time; repeated frames fill the
    // gaps when the page does not repaint (static screens).
    const pacerStartedAt = Date.now();
    let framesWritten = 0;
    pacer = setInterval(() => {
      processLatest();
      const due = framesDue(pacerStartedAt, Date.now(), framesWritten);
      if (ffmpeg.stdin.writableLength > MAX_FFMPEG_BACKLOG_FRAMES * outputFrame.length) {
        failClosed(`ffmpeg input backlog exceeds ${MAX_FFMPEG_BACKLOG_FRAMES} frames (encoder not real-time)`);
        return;
      }
      for (let i = 0; i < due && !sinkClosed; i += 1) {
        try { ffmpeg.stdin.write(outputFrame); } catch { sinkClosed = true; }
        framesWritten += 1;
      }
    }, 1000 / OUTPUT_FPS / 2);
    const statsTimer = setInterval(() => {
      const elapsed = (Date.now() - pacerStartedAt) / 1000;
      console.error(`[win-cdp-host] stats out_frames=${framesWritten} out_fps=${(framesWritten / elapsed).toFixed(2)} screencast_frames=${screencastFrames}`);
    }, 30_000);
    statsTimer.unref?.();

    console.log(`SOREN91_CDP_HOST_STREAMING=${options.srtUrl.replace(/\/\/.*@/, '//***@')}`);
    fs.writeFileSync(options.resultPath, JSON.stringify({
      ok: true, proxy: `${options.bindIp}:${options.proxyPort}`, encoder, canvas: activeCanvas,
      srt: 'caller-started',
    }, null, 2));

    const remaining = Math.max(0, sessionDeadline - Date.now());
    let deadlineTimer = null;
    const outcome = await Promise.race([
      new Promise((resolve) => { deadlineTimer = setTimeout(() => resolve({ kind: 'deadline' }), remaining); }),
      childExit(ffmpeg, 'ffmpeg-exit', 'close'),
      ...(loopback ? [childExit(loopback, 'audio-tap-exit')] : []),
      childExit(chrome, 'chrome-exit'),
      failClosedPromise,
    ]).finally(() => { clearTimeout(deadlineTimer); clearInterval(statsTimer); });
    const verdict = await resolveSessionEnd({
      kind: outcome.kind,
      value: outcome.value,
      getStderr: () => ffmpegStderr,
      getSinkClosed: () => sinkClosed,
      getFfmpegExit: () => ffmpegExit,
      ffmpegWaitMs: FFMPEG_EXIT_WAIT_MS,
    });
    if (verdict === 'deadline' || verdict === 'consumer-closed') {
      console.log(`SOREN91_CDP_HOST_END=${verdict}`);
      return { ok: true, verdict };
    }
    throw new Error(`session ended without a consumer close (fail-closed): ${outcome.kind}=${JSON.stringify(outcome.value)}`);
  } finally {
    // Teardown closes the page/browser itself; that is not a failure signal.
    failed = true;
    process.removeListener('SIGINT', onSigint);
    process.removeListener('SIGTERM', onSigterm);
    process.removeListener('SIGBREAK', onSigterm);
    process.stdin.removeListener('end', onStdinEnd);
    process.stdin.removeListener('close', onStdinEnd);
    if (browser) await Promise.race([browser.close().catch(() => {}), sleep(3000)]);
    cleanup();
    await Promise.all([waitExit(ffmpeg, 5000), waitExit(loopback, 5000), waitExit(chrome, 5000)]);
    try { process.stdin.pause(); process.stdin.unref?.(); } catch {}
  }
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  main().then(() => {
    process.exit(process.exitCode ?? 0);
  }).catch((error) => {
    console.error(error?.stack || error);
    process.exit(1);
  });
}

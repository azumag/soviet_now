import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFfmpegArgs,
  buildRendererEnv,
  defaults,
  isTailscaleIpv4Hostname,
  parseArgs,
  validateOptions,
} from '../tools/soren91_windows_session.mjs';

const options = validateOptions({
  ...defaults({}),
  srtUrl: 'srt://100.64.0.2:19192?mode=caller&transtype=live&latency=200000',
}, 'win32');

test('Tier -1 Windows defaults target 30 minutes at 960x540/30', () => {
  assert.equal(options.sessionSec, 1800);
  assert.equal(options.hardMaxSec, 2400);
  assert.equal(options.minFps, 30);
  assert.equal(options.width, 960);
  assert.equal(options.height, 540);
});

test('SRT URL must be Tailscale transport without passphrase in argv', () => {
  assert.throws(() => validateOptions({ ...options, srtUrl: 'udp://127.0.0.1:1' }, 'win32'), /srtUrl/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2:1?passphrase=secretsecret' }, 'win32'), /passphrase/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://203.0.113.10:19192?mode=caller' }, 'win32'), /Tailscale IPv4/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.128.0.1:19192?mode=caller' }, 'win32'), /Tailscale IPv4/);
  assert.equal(isTailscaleIpv4Hostname('100.64.0.1'), true);
  assert.equal(isTailscaleIpv4Hostname('100.127.255.255'), true);
  assert.equal(isTailscaleIpv4Hostname('100.128.0.1'), false);
});

test('live execution is Windows-only and needs an SRT target', () => {
  assert.throws(() => validateOptions({ ...options, execute: true }, 'linux'), /Windows-only/);
  assert.throws(() => validateOptions({ ...options, execute: true, srtUrl: '' }, 'win32'), /requires.*SRT/i);
});

test('ffmpeg captures the Chromium window and uses NVENC', () => {
  const args = buildFfmpegArgs(options);
  const rendered = args.join(' ');
  assert.match(rendered, /gdigrab/);
  assert.match(rendered, /title=Soren91-Remote - Chromium/);
  assert.match(rendered, /h264_nvenc/);
  assert.match(rendered, /960:540/);
  assert.match(rendered, /srt:\/\/100\.64\.0\.2:19192/);
  assert.match(rendered, /-an/);
});

test('optional dshow audio device is added without changing video path', () => {
  const args = buildFfmpegArgs({ ...options, audioDevice: 'CABLE Output (VB-Audio Virtual Cable)' });
  const rendered = args.join(' ');
  assert.match(rendered, /dshow/);
  assert.match(rendered, /CABLE Output/);
  assert.match(rendered, /-c:a aac/);
  assert.doesNotMatch(rendered, / -an(?: |$)/);
});

test('renderer env keeps the browser alive beyond stream duration', () => {
  const env = buildRendererEnv(options, {});
  assert.equal(env.SOREN91_LOCAL_RENDER_SEC, String(1800 + 180));
  assert.equal(env.SOREN91_LOCAL_MIN_FPS, '30');
  assert.equal(env.SOREN91_LOCAL_WINDOW_TITLE, 'Soren91-Remote');
});

test('safety caps reject longer or lower-fps sessions', () => {
  assert.throws(() => validateOptions({ ...options, sessionSec: 1801 }, 'win32'), /sessionSec/);
  assert.throws(() => validateOptions({ ...options, hardMaxSec: 2401 }, 'win32'), /hardMaxSec/);
  assert.throws(() => validateOptions({ ...options, minFps: 29 }, 'win32'), /30fps/);
});

test('dry-run args are parsed without requiring Windows', () => {
  const parsed = parseArgs(['--srt-url', options.srtUrl], {});
  assert.equal(parsed.execute, false);
  assert.equal(parsed.srtUrl, options.srtUrl);
});

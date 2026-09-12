import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildFfmpegArgs,
  buildRendererEnv,
  defaults,
  isTailscaleIpv4Hostname,
  parseArgs,
  validateOptions,
} from '../tools/soren91_macos_session.mjs';

const options = validateOptions({
  ...defaults({}),
  srtUrl: 'srt://100.64.0.2:19192?mode=caller&transtype=live&latency=200000',
}, 'darwin');

const crop = { cropX: 0, cropY: 117 };

test('Tier -1 macOS defaults target 30 minutes at 960x540/30', () => {
  assert.equal(options.sessionSec, 1800);
  assert.equal(options.hardMaxSec, 2400);
  assert.equal(options.minFps, 30);
  assert.equal(options.width, 960);
  assert.equal(options.height, 540);
  assert.equal(options.captureDeviceIndex, '1');
});

test('SRT URL must be Tailscale caller transport without credentials in argv', () => {
  assert.throws(() => validateOptions({ ...options, srtUrl: 'udp://127.0.0.1:1' }, 'darwin'), /srtUrl/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2:1?passphrase=secretsecret&mode=caller' }, 'darwin'), /passphrase/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2:1?%70assphrase=secretsecret&mode=caller' }, 'darwin'), /passphrase/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://user:secret@100.64.0.2:19192?mode=caller' }, 'darwin'), /userinfo/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2?mode=caller' }, 'darwin'), /port/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2:19192?mode=listener' }, 'darwin'), /mode=caller/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.64.0.2:19192' }, 'darwin'), /mode=caller/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://203.0.113.10:19192?mode=caller' }, 'darwin'), /Tailscale IPv4/);
  assert.throws(() => validateOptions({ ...options, srtUrl: 'srt://100.128.0.1:19192?mode=caller' }, 'darwin'), /Tailscale IPv4/);
  assert.equal(isTailscaleIpv4Hostname('100.64.0.1'), true);
  assert.equal(isTailscaleIpv4Hostname('100.127.255.255'), true);
  assert.equal(isTailscaleIpv4Hostname('100.128.0.1'), false);
});

test('live execution is macOS-only and needs an SRT target', () => {
  assert.throws(() => validateOptions({ ...options, execute: true }, 'win32'), /macOS-only/);
  assert.throws(() => validateOptions({ ...options, execute: true, srtUrl: '' }, 'darwin'), /requires.*SRT/i);
});

test('ffmpeg captures the calibrated crop and uses VideoToolbox', () => {
  const args = buildFfmpegArgs(options, crop);
  const rendered = args.join(' ');
  assert.match(rendered, /avfoundation/);
  assert.match(rendered, /-i 1:none/);
  assert.match(rendered, /crop=960:540:0:117/);
  assert.match(rendered, /h264_videotoolbox/);
  assert.match(rendered, /srt:\/\/100\.64\.0\.2:19192/);
  assert.match(rendered, /-an/);
});

test('buildFfmpegArgs requires a crop rect from the renderer result', () => {
  assert.throws(() => buildFfmpegArgs(options, null), /crop rect/);
  assert.throws(() => buildFfmpegArgs(options, {}), /crop rect/);
});

test('optional avfoundation audio device is added without changing video path', () => {
  const args = buildFfmpegArgs({ ...options, audioDevice: 'BlackHole 2ch' }, crop);
  const rendered = args.join(' ');
  assert.match(rendered, /-i 1:BlackHole 2ch/);
  assert.match(rendered, /-c:a aac/);
  assert.doesNotMatch(rendered, / -an(?: |$)/);
});

test('renderer env keeps the browser alive beyond stream duration', () => {
  const env = buildRendererEnv(options, {});
  assert.equal(env.SOREN91_LOCAL_RENDER_SEC, String(1800 + 180));
  assert.equal(env.SOREN91_LOCAL_MIN_FPS, '30');
  assert.equal(env.SOREN91_LOCAL_WIDTH, '960');
});

test('safety caps reject longer or lower-fps sessions', () => {
  assert.throws(() => validateOptions({ ...options, sessionSec: 1801 }, 'darwin'), /sessionSec/);
  assert.throws(() => validateOptions({ ...options, hardMaxSec: 2401 }, 'darwin'), /hardMaxSec/);
  assert.throws(() => validateOptions({ ...options, minFps: 29 }, 'darwin'), /30fps/);
});

test('dry-run args are parsed without requiring macOS', () => {
  const parsed = parseArgs(['--srt-url', options.srtUrl], {});
  assert.equal(parsed.execute, false);
  assert.equal(parsed.srtUrl, options.srtUrl);
});

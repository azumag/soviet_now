import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildCaptureArgs,
  buildFfmpegArgs,
  buildRendererEnv,
  defaults,
  isTailscaleIpv4Hostname,
  parseArgs,
  parseCaptureHelperStatus,
  validateOptions,
} from '../tools/soren91_macos_session.mjs';

const options = validateOptions({
  ...defaults({}),
  srtUrl: 'srt://100.64.0.2:19192?mode=caller&transtype=live&latency=200000',
}, 'darwin');

// Window-relative: outer window pixel size + the chrome-band offset inside
// it, as emitted by soren91_macos_renderer.mjs's calibrateWindowBounds. Not
// a screen-position crop — see tools/soren91_macos_session.mjs header.
const capture = {
  bundleId: 'com.google.Chrome',
  windowTitle: 'sorengame91',
  outerWidth: 960,
  outerHeight: 627,
  chromeTop: 87,
  chromeLeft: 0,
};

test('Tier -1 macOS defaults target 30 minutes at 960x540/30', () => {
  assert.equal(options.sessionSec, 1800);
  assert.equal(options.hardMaxSec, 2400);
  assert.equal(options.minFps, 30);
  assert.equal(options.width, 960);
  assert.equal(options.height, 540);
  assert.ok(options.captureHelperBin.endsWith('soren91_window_capture'));
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

test('capture helper args select one window by exact bundle id + title, at its own native size', () => {
  const args = buildCaptureArgs(options, capture);
  assert.deepEqual(args, [
    '--bundle-id', 'com.google.Chrome',
    '--title', 'sorengame91',
    '--width', '960',
    '--height', '627',
    '--fps', '30',
  ]);
});

test('buildCaptureArgs requires bundleId/windowTitle/outer size from the renderer result', () => {
  assert.throws(() => buildCaptureArgs(options, null), /capture target/);
  assert.throws(() => buildCaptureArgs(options, {}), /capture target/);
  assert.throws(() => buildCaptureArgs(options, { bundleId: 'x', windowTitle: 'y' }), /outerWidth/);
});

test('capture helper readiness status is fail-closed', () => {
  assert.deepEqual(parseCaptureHelperStatus('{"ok":true,"windowID":1}'), { ok: true, windowID: 1 });
  assert.throws(() => parseCaptureHelperStatus('{"ok":false,"error":"found 0"}'), /found 0/);
  assert.throws(() => parseCaptureHelperStatus('{"ok":false}'), /fail-closed/);
  assert.throws(() => parseCaptureHelperStatus('not json'), /non-JSON/);
});

test('ffmpeg reads the helper\'s native-size rawvideo pipe and crops only the chrome band, in-window', () => {
  const args = buildFfmpegArgs(options, capture);
  const rendered = args.join(' ');
  assert.match(rendered, /-f rawvideo -pixel_format bgra/);
  assert.match(rendered, /-video_size 960x627/);
  assert.match(rendered, /-i pipe:0/);
  assert.match(rendered, /crop=960:540:0:87/);
  assert.match(rendered, /h264_videotoolbox/);
  assert.match(rendered, /srt:\/\/100\.64\.0\.2:19192/);
  assert.match(rendered, /-an/);
  assert.doesNotMatch(rendered, /avfoundation/);
});

test('buildFfmpegArgs requires the window-relative chrome offsets and outer size from the renderer result', () => {
  assert.throws(() => buildFfmpegArgs(options, null), /chrome offsets/);
  assert.throws(() => buildFfmpegArgs(options, {}), /chrome offsets/);
  assert.throws(() => buildFfmpegArgs(options, { chromeTop: 0, chromeLeft: 0 }), /outerWidth/);
});

test('optional avfoundation audio device is added as a second input without touching the video path', () => {
  const args = buildFfmpegArgs({ ...options, audioDevice: 'BlackHole 2ch' }, capture);
  const rendered = args.join(' ');
  assert.match(rendered, /-f avfoundation -i none:BlackHole 2ch/);
  assert.match(rendered, /-map 0:v -map 1:a/);
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

import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import {
  buildCaptureArgs,
  buildFfmpegArgs,
  buildRendererEnv,
  defaults,
  isTailscaleIpv4Hostname,
  parseArgs,
  parseCaptureHelperStatus,
  parseVirtualDisplayStatus,
  resolveDisplayMode,
  startVirtualDisplay,
  stopVirtualDisplay,
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

// --- Offscreen virtual display (Issue #303) ---

test('offscreen virtual display is on by default; onscreen needs explicit opt-in', () => {
  const base = defaults({});
  assert.equal(base.offscreen, true);
  assert.equal(base.allowOnscreen, false);
  assert.ok(base.virtualDisplayBin.endsWith('soren91_virtual_display'));
  assert.equal(defaults({ SOREN91_LOCAL_OFFSCREEN: '0' }).offscreen, false);
  assert.equal(defaults({ SOREN91_LOCAL_ALLOW_ONSCREEN: '1' }).allowOnscreen, true);
});

test('display mode resolution is fail-closed against silent visible fallback', () => {
  assert.equal(resolveDisplayMode({ offscreen: true, allowOnscreen: false }), 'offscreen');
  assert.equal(resolveDisplayMode({ offscreen: true, allowOnscreen: true }), 'offscreen');
  assert.equal(resolveDisplayMode({ offscreen: false, allowOnscreen: true }), 'onscreen');
  assert.throws(
    () => resolveDisplayMode({ offscreen: false, allowOnscreen: false }),
    /explicit opt-in/,
  );
});

test('offscreen flags are parsed from argv', () => {
  assert.equal(parseArgs(['--no-offscreen'], {}).offscreen, false);
  assert.equal(parseArgs(['--no-offscreen', '--offscreen'], {}).offscreen, true);
  assert.equal(parseArgs(['--allow-onscreen'], {}).allowOnscreen, true);
  assert.equal(
    parseArgs(['--virtual-display-bin', '/tmp/vd'], {}).virtualDisplayBin,
    '/tmp/vd',
  );
});

test('offscreen requires a helper path', () => {
  assert.throws(
    () => validateOptions({ ...options, offscreen: true, virtualDisplayBin: '' }, 'darwin'),
    /virtualDisplayBin/,
  );
});

test('virtual display readiness status is fail-closed', () => {
  const ok = '{"ok":true,"displayID":6,"bounds":{"x":1920,"y":0,"width":1920,"height":1080}}';
  assert.deepEqual(parseVirtualDisplayStatus(ok), JSON.parse(ok));
  assert.throws(() => parseVirtualDisplayStatus('{"ok":false,"error":"boom"}'), /boom/);
  assert.throws(() => parseVirtualDisplayStatus('{"ok":false}'), /fail-closed/);
  assert.throws(() => parseVirtualDisplayStatus('not json'), /non-JSON/);
  assert.throws(() => parseVirtualDisplayStatus('{"ok":true}'), /displayID\/bounds/);
  assert.throws(
    () => parseVirtualDisplayStatus('{"ok":true,"displayID":6,"bounds":{"x":0,"y":0,"width":0,"height":1}}'),
    /displayID\/bounds/,
  );
});

test('renderer env carries the virtual display bin and measured bounds', () => {
  const plain = buildRendererEnv(options, {});
  assert.ok(plain.SOREN91_LOCAL_VIRTUAL_DISPLAY_BIN.endsWith('soren91_virtual_display'));
  assert.equal(plain.SOREN91_LOCAL_VDISPLAY_BOUNDS, undefined);
  const vdisplay = { displayID: 6, bounds: { x: 1920, y: 0, width: 1920, height: 1080 } };
  const withVdisplay = buildRendererEnv(options, {}, { vdisplay });
  assert.deepEqual(JSON.parse(withVdisplay.SOREN91_LOCAL_VDISPLAY_BOUNDS), vdisplay);
});

// Minimal holder stub: emits one stderr status line, stays alive until
// SIGTERM, then exits — mirroring the real helper's lifecycle contract.
function stubHolderOnce({ line, exitCode = 1 } = {}) {
  const child = new EventEmitter();
  child.stderr = Readable.from(line != null ? [`${line}\n`] : []);
  child.exitCode = null;
  child.killed = false;
  child.killSignals = [];
  child.kill = function kill(signal) {
    child.killSignals.push(signal);
    child.killed = true;
    queueMicrotask(() => {
      if (child.exitCode == null) {
        child.exitCode = signal === 'SIGKILL' ? 137 : 0;
        child.emit('exit', child.exitCode);
      }
    });
    return true;
  };
  if (line == null) {
    queueMicrotask(() => {
      if (child.exitCode == null) {
        child.exitCode = exitCode;
        child.emit('exit', exitCode);
      }
    });
  }
  return child;
}

test('holder handshake resolves on ok:true and cleanup sends SIGTERM', async () => {
  let spawned = 0;
  const line = '{"ok":true,"displayID":6,"bounds":{"x":1920,"y":0,"width":1920,"height":1080}}';
  const spawnImpl = (...args) => {
    spawned += 1;
    assert.deepEqual(args[1], []);
    return stubHolderOnce({ line });
  };
  const { child, status } = await startVirtualDisplay('/tmp/soren91_virtual_display', { spawnImpl });
  assert.equal(spawned, 1);
  assert.equal(status.displayID, 6);
  assert.equal(child.exitCode, null);
  await stopVirtualDisplay(child);
  assert.deepEqual(child.killSignals, ['SIGTERM']);
  assert.equal(child.exitCode, 0);
});

test('holder failure rejects fail-closed (no silent onscreen fallback)', async () => {
  const bad = stubHolderOnce({ line: '{"ok":false,"error":"nope"}' });
  await assert.rejects(
    startVirtualDisplay('/tmp/vd', { spawnImpl: () => bad }),
    /nope/,
  );
  const silent = stubHolderOnce({ line: null, exitCode: 1 });
  await assert.rejects(
    startVirtualDisplay('/tmp/vd', { spawnImpl: () => silent }),
    /exited before readiness/,
  );
  await assert.rejects(
    startVirtualDisplay('/tmp/vd', {
      spawnImpl: () => stubHolderOnce({ line: 'garbage' }),
    }),
    /non-JSON/,
  );
});

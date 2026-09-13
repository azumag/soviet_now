import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { PassThrough, Readable } from 'node:stream';
import {
  attachPipeGuards,
  buildCaptureArgs,
  buildFfmpegArgs,
  buildRendererEnv,
  createSessionDeadline,
  createStderrTail,
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

test('capture target tolerates the nested offscreen proof (capture.offscreen per README)', () => {
  const withProof = {
    ...capture,
    offscreen: {
      requested: true,
      displayID: 11,
      bounds: { x: 1920, y: 0, width: 1920, height: 1080 },
      windowBounds: { x: 1940, y: 30, width: 960, height: 627 },
      physicalOverlap: false,
    },
  };
  assert.deepEqual(buildCaptureArgs(options, withProof), buildCaptureArgs(options, capture));
  assert.deepEqual(buildFfmpegArgs(options, withProof), buildFfmpegArgs(options, capture));
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
  // Explicit opt-out (SOREN91_LOCAL_AUDIO_TAP=0): silent path, no audio input.
  const args = buildFfmpegArgs({ ...options, audioTap: false }, capture);
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
  // The legacy device path requires opting out of the default audio tap
  // (the two inputs are mutually exclusive).
  const args = buildFfmpegArgs({ ...options, audioTap: false, audioDevice: 'BlackHole 2ch' }, capture);
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

// --- Chrome-scoped audio tap (Issue #303) ---

test('audio tap is on by default and disabled via SOREN91_LOCAL_AUDIO_TAP=0', () => {
  const base = defaults({});
  assert.equal(base.audioTap, true);
  assert.ok(base.audioTapBin.endsWith('soren91_audio_tap'));
  assert.equal(defaults({ SOREN91_LOCAL_AUDIO_TAP: '1' }).audioTap, true);
  assert.equal(defaults({ SOREN91_LOCAL_AUDIO_TAP: '0' }).audioTap, false);
  assert.equal(defaults({ SOREN91_LOCAL_AUDIO_TAP: '' }).audioTap, true);
});

test('default ffmpeg args carry the audio tap input with an AAC map', () => {
  assert.equal(options.audioTap, true);
  const args = buildFfmpegArgs(options, capture);
  const rendered = args.join(' ');
  assert.match(rendered, /-f s16le -ar 48000 -ac 2 -i pipe:3/);
  assert.match(rendered, /-map 0:v -map 1:a/);
  assert.match(rendered, /-c:a aac -b:a 128k/);
  assert.doesNotMatch(rendered, / -an(?: |$)/);
  // Video path untouched.
  assert.match(rendered, /-f rawvideo -pixel_format bgra/);
  assert.match(rendered, /crop=960:540:0:87/);
  assert.match(rendered, /h264_videotoolbox/);
});

test('SOREN91_LOCAL_AUDIO_TAP=0 restores the legacy silent path (mute + no audio input)', () => {
  const silent = validateOptions({
    ...defaults({ SOREN91_LOCAL_AUDIO_TAP: '0' }),
    srtUrl: 'srt://100.64.0.2:19192?mode=caller&transtype=live&latency=200000',
  }, 'darwin');
  assert.equal(silent.audioTap, false);
  const env = buildRendererEnv(silent, {});
  assert.equal(env.SOREN91_LOCAL_MUTE_AUDIO, '1');
  const rendered = buildFfmpegArgs(silent, capture).join(' ');
  assert.match(rendered, / -an(?: |$)/);
  assert.doesNotMatch(rendered, /pipe:3/);
  assert.doesNotMatch(rendered, /-map 1:a/);
});

test('audio tap adds the s16le fd-3 input with an AAC map, keeping the video path', () => {
  const args = buildFfmpegArgs({ ...options, audioTap: true }, capture);
  const rendered = args.join(' ');
  assert.match(rendered, /-f s16le -ar 48000 -ac 2 -i pipe:3/);
  assert.match(rendered, /-map 0:v -map 1:a/);
  assert.match(rendered, /-c:a aac -b:a 128k/);
  assert.doesNotMatch(rendered, / -an(?: |$)/);
  assert.doesNotMatch(rendered, /avfoundation/);
  // Video path untouched.
  assert.match(rendered, /-f rawvideo -pixel_format bgra/);
  assert.match(rendered, /crop=960:540:0:87/);
  assert.match(rendered, /h264_videotoolbox/);
});

test('audio tap and the legacy avfoundation device are mutually exclusive', () => {
  assert.throws(
    () => buildFfmpegArgs({ ...options, audioTap: true, audioDevice: 'BlackHole 2ch' }, capture),
    /mutually exclusive/,
  );
});

test('renderer env mutes Chrome at the source unless the tap captures it', () => {
  const muted = buildRendererEnv({ ...options, audioTap: false }, {});
  assert.equal(muted.SOREN91_LOCAL_MUTE_AUDIO, '1');
  const tapped = buildRendererEnv({ ...options, audioTap: true }, {});
  assert.equal(tapped.SOREN91_LOCAL_MUTE_AUDIO, undefined);
});

test('empty automation-Chrome PID set fails closed (session must abort, never widen the tap)', async () => {
  const { resolveTapPids } = await import('../tools/soren91_macos_audio.mjs');
  assert.throws(() => resolveTapPids('', 12345), /fail-closed/);
  assert.throws(() => resolveTapPids('  PID  PPID COMMAND\n', 12345), /fail-closed/);
});

test('audio tap cleanup sends SIGTERM so the tap teardown releases the mute', async () => {
  const { stopAudioTap } = await import('../tools/soren91_macos_audio.mjs');
  const child = new EventEmitter();
  child.exitCode = null;
  child.killed = false;
  child.killSignals = [];
  child.kill = function kill(signal) {
    child.killSignals.push(signal);
    child.killed = true;
    queueMicrotask(() => {
      if (child.exitCode == null) {
        child.exitCode = 0;
        child.emit('exit', 0);
      }
    });
    return true;
  };
  await stopAudioTap(child);
  assert.deepEqual(child.killSignals, ['SIGTERM']);
  assert.equal(child.exitCode, 0);
});

// --- Receiver-first close (Issue #303): pipe guards + stderr tail ---

function pipeError(code) {
  return Object.assign(new Error(`write ${code}`), { code });
}

test('createStderrTail keeps a bounded tail of ffmpeg stderr', () => {
  const tail = createStderrTail(8);
  assert.equal(tail.text(), '');
  tail.push('ab');
  tail.push('cdef');
  tail.push('ghij');
  assert.equal(tail.text(), 'cdefghij');
  assert.equal(createStderrTail().text(), '');
});

test('attachPipeGuards swallows benign pipe errors and records the sink as closed', () => {
  const captureStdout = new EventEmitter();
  const ffmpegStdin = new EventEmitter();
  const tracker = attachPipeGuards({
    capture: { stdout: captureStdout },
    ffmpeg: { stdin: ffmpegStdin, stdio: [] },
  });
  assert.equal(tracker.sinkClosed, false);
  // Must not throw (previously an unhandled 'error' crashed node).
  captureStdout.emit('error', pipeError('EPIPE'));
  assert.equal(tracker.sinkClosed, true);
  ffmpegStdin.emit('error', pipeError('ERR_STREAM_DESTROYED'));
  assert.equal(tracker.sinkClosed, true);
  assert.deepEqual(tracker.errors, []);
});

test('attachPipeGuards records non-benign pipe errors without marking sinkClosed', () => {
  const ffmpegStdin = new EventEmitter();
  const tracker = attachPipeGuards({ ffmpeg: { stdin: ffmpegStdin } });
  ffmpegStdin.emit('error', pipeError('ECONNRESET'));
  assert.equal(tracker.sinkClosed, false);
  assert.equal(tracker.errors.length, 1);
  assert.equal(tracker.errors[0].label, 'ffmpeg-stdin');
});

test('attachPipeGuards tolerates missing streams (silent path without audio tap)', () => {
  const tracker = attachPipeGuards({ capture: {}, audiotap: null, ffmpeg: {} });
  assert.equal(tracker.sinkClosed, false);
  assert.deepEqual(attachPipeGuards().sinkClosed, false);
});

test('attachPipeGuards swallows a real EPIPE from a dead pipe reader (not unhandled)', async () => {
  // Recipe: STOP the reader so the kernel pipe buffer fills, then KILL it
  // while writes are in flight — the writer's write() syscall then fails
  // with EPIPE. This is what ffmpeg's death does to the frame/PCM feeds at
  // ~70MB/s (in-flight rawvideo always exists, so EPIPE is near-certain).
  const sleepMs = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  async function runScenario(useGuard) {
    const child = spawn('/bin/cat', [], { stdio: ['pipe', 'ignore', 'ignore'] });
    let src = null;
    let tracker = null;
    let observed = null;
    let writer;
    if (useGuard) {
      src = new PassThrough();
      tracker = attachPipeGuards({
        capture: { stdout: src },
        ffmpeg: { stdin: child.stdin, stdio: [] },
      });
      src.pipe(child.stdin);
      writer = src;
    } else {
      // Persistent listener: proves the recipe emits EPIPE here without
      // ever going unhandled (an unhandled 'error' would crash the runner).
      child.stdin.on('error', (error) => { observed ??= error.code; });
      writer = child.stdin;
    }
    child.kill('SIGSTOP');
    await sleepMs(100);
    for (let i = 0; i < 20; i++) { try { writer.write(Buffer.alloc(1048576)); } catch {} }
    child.kill('SIGKILL');
    const pump = setInterval(() => { try { writer.write(Buffer.alloc(1048576)); } catch {} }, 1);
    const deadline = Date.now() + 5000;
    if (useGuard) {
      while (!tracker.sinkClosed && Date.now() < deadline) await sleepMs(20);
    } else {
      while (observed == null && Date.now() < deadline) await sleepMs(20);
    }
    clearInterval(pump);
    src?.destroy();
    try { child.stdin.destroy(); } catch {}
    return useGuard ? tracker : observed;
  }
  // Sanity: the recipe really emits EPIPE on the writer in this environment.
  assert.equal(await runScenario(false), 'EPIPE');
  // Guarded: swallowed (the test surviving proves no unhandled 'error')
  // and recorded as sinkClosed for classifyFfmpegExit.
  const tracker = await runScenario(true);
  assert.equal(tracker.sinkClosed, true);
  assert.deepEqual(tracker.errors, []);
});

// --- Session teardown (Issue #303): capture-exit classification + no-hang ---

const sleepForTest = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

test('createSessionDeadline resolves the deadline kind on expiry', async () => {
  const deadline = createSessionDeadline(20);
  const outcome = await deadline.promise;
  assert.deepEqual(outcome, { kind: 'deadline' });
  deadline.cancel(); // no-op after firing; must not throw
});

test('createSessionDeadline cancel prevents the late resolve (no lingering timer)', async () => {
  const deadline = createSessionDeadline(60_000);
  deadline.cancel();
  const outcome = await Promise.race([
    deadline.promise.then(() => 'resolved'),
    sleepForTest(50).then(() => 'still-pending'),
  ]);
  assert.equal(outcome, 'still-pending');
});

test('hang regression: immediate capture-exit code 0 ends without waiting out sessionSec', async () => {
  // Mirrors the session's Promise.race shape: an 1800s cancellable deadline
  // vs a capture helper that exits 0 at once (the Swift helper's
  // SIGPIPE-ignored exit when the listener closes first). The old inline
  // sleep(remaining) held the event loop ~30 min after the throw; the
  // cancellable deadline must let this settle in well under a second.
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  const capture = new EventEmitter();
  const waitForExit = (child) => new Promise(
    (resolve) => child.once('exit', (code, signal) => resolve({ code, signal })),
  );
  const deadline = createSessionDeadline(1_800_000);
  const started = Date.now();
  let endReason = null;
  try {
    queueMicrotask(() => capture.emit('exit', 0, null));
    const outcome = await Promise.race([
      deadline.promise,
      waitForExit(capture).then((value) => ({ kind: 'capture-exit', value })),
    ]);
    const verdict = classifySessionEnd({
      kind: outcome.kind,
      value: outcome.value,
      stderr: '',
      sinkClosed: false,
    });
    if (verdict === 'consumer-closed') endReason = 'consumer-closed';
    else throw new Error(`${outcome.kind}: ${JSON.stringify(outcome.value)}`);
  } finally {
    deadline.cancel();
  }
  const elapsed = Date.now() - started;
  assert.equal(endReason, 'consumer-closed');
  assert.ok(elapsed < 2000, `settled in ${elapsed}ms, expected < 2000ms`);
});

test('ffmpegExitWaitMs defaults to 5000ms and is env-configurable', () => {
  assert.equal(defaults({}).ffmpegExitWaitMs, 5000);
  assert.equal(defaults({ SOREN91_LOCAL_FFMPEG_EXIT_WAIT_MS: '1000' }).ffmpegExitWaitMs, 1000);
  assert.equal(defaults({ SOREN91_LOCAL_FFMPEG_EXIT_WAIT_MS: '0' }).ffmpegExitWaitMs, 0);
  assert.equal(defaults({ SOREN91_LOCAL_FFMPEG_EXIT_WAIT_MS: 'junk' }).ffmpegExitWaitMs, 5000);
});

test('race regression: audio-tap SIGPIPE winner still ends consumer-closed via ffmpeg', async () => {
  // Mirrors the session's Promise.race shape live: the audio-tap helper's
  // 'exit' (SIGPIPE — ffmpeg's fd 3 reader died when the OCI listener
  // closed first) fires at once, while ffmpeg's 'close' — carrying the
  // listener-close marker — lands ~50ms later. resolveSessionEnd must wait
  // out the bounded ffmpeg window and report consumer-closed, settling far
  // short of sessionSec.
  const { resolveSessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  const tap = new EventEmitter();
  const ffmpeg = new EventEmitter();
  let ffmpegExit = null;
  ffmpeg.once('close', (code, signal) => { ffmpegExit = { code, signal }; });
  const waitForExit = (child) => new Promise(
    (resolve) => child.once('exit', (code, signal) => resolve({ code, signal })),
  );
  const waitForCloseExit = (child) => new Promise(
    (resolve) => child.once('close', (code, signal) => resolve({ code, signal })),
  );
  const deadline = createSessionDeadline(1_800_000);
  const stderrChunks = [];
  const getStderr = () => stderrChunks.join('');
  let sinkClosed = false;
  const started = Date.now();
  let endReason = null;
  try {
    queueMicrotask(() => tap.emit('exit', null, 'SIGPIPE'));
    setTimeout(() => {
      stderrChunks.push('av_interleaved_write_frame(): Input/output error\n');
      sinkClosed = true;
      ffmpeg.emit('close', 1, null);
    }, 50);
    const outcome = await Promise.race([
      deadline.promise,
      waitForCloseExit(ffmpeg).then((value) => ({ kind: 'ffmpeg-exit', value })),
      waitForExit(tap).then((value) => ({ kind: 'audio-tap-exit', value })),
    ]);
    assert.equal(outcome.kind, 'audio-tap-exit'); // the tap wins, as live
    const verdict = await resolveSessionEnd({
      kind: outcome.kind,
      value: outcome.value,
      getStderr,
      getSinkClosed: () => sinkClosed,
      getFfmpegExit: () => ffmpegExit,
      ffmpegWaitMs: 5000,
    });
    if (verdict === 'consumer-closed') endReason = 'consumer-closed';
    else throw new Error(`${outcome.kind}: ${JSON.stringify(outcome.value)}`);
  } finally {
    deadline.cancel();
  }
  const elapsed = Date.now() - started;
  assert.equal(endReason, 'consumer-closed');
  assert.ok(elapsed < 2000, `settled in ${elapsed}ms, expected < 2000ms`);
});

test('race regression: capture SIGPIPE winner still ends consumer-closed via ffmpeg (exit 0)', async () => {
  // Replays the live FAIL shape: the capture helper dies with
  // `exit {code:null, signal:SIGPIPE}` (fd1 reader — ffmpeg — gone after the
  // OCI listener closed first) at once, while ffmpeg's 'close' — carrying
  // the muxer I/O error marker — lands ~50ms later. resolveSessionEnd must
  // wait out the bounded ffmpeg window and report consumer-closed, which the
  // session maps to exit 0 with SESSION_END=consumer-closed (not exit 1 with
  // the line missing). Settles far short of sessionSec.
  const { resolveSessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  const capture = new EventEmitter();
  const ffmpeg = new EventEmitter();
  let ffmpegExit = null;
  ffmpeg.once('close', (code, signal) => { ffmpegExit = { code, signal }; });
  const waitForExit = (child) => new Promise(
    (resolve) => child.once('exit', (code, signal) => resolve({ code, signal })),
  );
  const waitForCloseExit = (child) => new Promise(
    (resolve) => child.once('close', (code, signal) => resolve({ code, signal })),
  );
  const deadline = createSessionDeadline(1_800_000);
  const stderrChunks = [];
  const getStderr = () => stderrChunks.join('');
  let sinkClosed = false;
  const started = Date.now();
  let exitCode = 1;
  let endLine = null;
  try {
    queueMicrotask(() => capture.emit('exit', null, 'SIGPIPE'));
    setTimeout(() => {
      stderrChunks.push('Error submitting a packet to the muxer: Input/output error\n');
      stderrChunks.push('av_interleaved_write_frame(): Input/output error\n');
      sinkClosed = true;
      ffmpeg.emit('close', 1, null);
    }, 50);
    const outcome = await Promise.race([
      deadline.promise,
      waitForCloseExit(ffmpeg).then((value) => ({ kind: 'ffmpeg-exit', value })),
      waitForExit(capture).then((value) => ({ kind: 'capture-exit', value })),
    ]);
    assert.equal(outcome.kind, 'capture-exit'); // capture wins, as live
    assert.deepEqual(outcome.value, { code: null, signal: 'SIGPIPE' });
    const verdict = await resolveSessionEnd({
      kind: outcome.kind,
      value: outcome.value,
      getStderr,
      getSinkClosed: () => sinkClosed,
      getFfmpegExit: () => ffmpegExit,
      ffmpegWaitMs: 5000,
    });
    // Same mapping the session's main() applies: consumer-closed/deadline ->
    // exit 0 with a SESSION_END line; anything else throws (exit 1, no line).
    if (verdict === 'deadline') {
      endLine = 'SOREN91_LOCAL_SESSION_END=deadline';
      exitCode = 0;
    } else if (verdict === 'consumer-closed') {
      endLine = 'SOREN91_LOCAL_SESSION_END=consumer-closed';
      exitCode = 0;
    } else {
      throw new Error(`${outcome.kind}: ${JSON.stringify(outcome.value)}`);
    }
  } finally {
    deadline.cancel();
  }
  const elapsed = Date.now() - started;
  assert.equal(endLine, 'SOREN91_LOCAL_SESSION_END=consumer-closed');
  assert.equal(exitCode, 0);
  assert.ok(elapsed < 2000, `settled in ${elapsed}ms, expected < 2000ms`);
});

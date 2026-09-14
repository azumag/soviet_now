import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCanvasFfmpegArgs,
  buildCanvasVideoFilter,
  buildChromeArgs,
  canvasSamplesMatch,
  evaluateGeometryDrift,
  findGameTarget,
  isAllowedCdpPeer,
  isExactGameTargetUrl,
  normalizeRemoteAddress,
  parseCanvasGeometry,
  resolveCanvasCropFrame,
  signalExitCode,
  startAudioTapWithRetry,
  validateOptions,
  waitForStableCanvasGeometry,
} from '../tools/soren91_macos_cdp_host.mjs';

function validOptions(overrides = {}) {
  return {
    execute: true,
    cdpPort: 9322,
    proxyPort: 19093,
    bindIp: '100.70.0.2',
    width: 960,
    height: 540,
    videoMbps: 2,
    srtUrl: 'srt://100.70.0.3:9000?mode=caller',
    audioTap: true,
    ...overrides,
  };
}

test('game target matching accepts unityroom game origins only', () => {
  assert.equal(isExactGameTargetUrl('https://play.unityroom.com/games/foo'), true);
  assert.equal(isExactGameTargetUrl('https://74337.play.unityroom.com/games/foo?abc=1'), true);
  assert.equal(isExactGameTargetUrl('https://a.b.play.unityroom.com/'), true);
  assert.equal(isExactGameTargetUrl('http://play.unityroom.com/games/foo'), false);
  assert.equal(isExactGameTargetUrl('http://74337.play.unityroom.com/games/foo'), false);
  assert.equal(isExactGameTargetUrl('https://play.unityroom.com.evil.example/games/foo'), false);
  assert.equal(isExactGameTargetUrl('https://play.unityroom.com.evil.com/'), false);
  assert.equal(isExactGameTargetUrl('https://notplay.unityroom.com/'), false);
  assert.equal(isExactGameTargetUrl('https://evilplay.unityroom.com/'), false);
  assert.equal(isExactGameTargetUrl('https://evil.example/?next=play.unityroom.com'), false);
  assert.equal(isExactGameTargetUrl('not-a-url play.unityroom.com'), false);

  const good = { type: 'page', url: 'https://74337.play.unityroom.com/games/foo' };
  const targets = [
    { type: 'page', url: 'https://play.unityroom.com.evil.example/' },
    { type: 'page', url: 'https://notplay.unityroom.com/' },
    { type: 'page', url: 'https://evil.example/?q=play.unityroom.com' },
    good,
  ];
  assert.equal(findGameTarget(targets), good);
});

test('CDP peer matching accepts only the reviewed OCI Tailscale IPv4', () => {
  assert.equal(normalizeRemoteAddress('::ffff:100.70.0.3'), '100.70.0.3');
  assert.equal(isAllowedCdpPeer('100.70.0.3', '100.70.0.3'), true);
  assert.equal(isAllowedCdpPeer('::ffff:100.70.0.3', '100.70.0.3'), true);
  assert.equal(isAllowedCdpPeer('100.70.0.4', '100.70.0.3'), false);
  assert.equal(isAllowedCdpPeer('127.0.0.1', '100.70.0.3'), false);
  assert.equal(isAllowedCdpPeer('100.70.0.3', '192.168.1.5'), false);
});

test('SRT validation rejects credentials and preserves caller/Tailscale contract', () => {
  assert.doesNotThrow(() => validateOptions(validOptions(), 'darwin'));
  assert.throws(
    () => validateOptions(validOptions({ srtUrl: 'srt://user:secret@100.70.0.3:9000?mode=caller' }), 'darwin'),
    /userinfo credentials/,
  );
  assert.throws(
    () => validateOptions(validOptions({ srtUrl: 'srt://100.70.0.3:9000?mode=caller&passphrase=secret' }), 'darwin'),
    /passphrase credentials/,
  );
  assert.throws(
    () => validateOptions(validOptions({ srtUrl: 'srt://100.70.0.3:9000?mode=caller&%70assphrase=secret' }), 'darwin'),
    /passphrase credentials/,
  );
  assert.throws(
    () => validateOptions(validOptions({ srtUrl: 'srt://100.70.0.3:9000?mode=listener' }), 'darwin'),
    /mode=caller/,
  );
  assert.throws(
    () => validateOptions(validOptions({ srtUrl: 'srt://203.0.113.10:9000?mode=caller' }), 'darwin'),
    /Tailscale IPv4/,
  );
});

test('Chrome is physically muted whenever audio tap is disabled', () => {
  const placement = { left: 100, top: 200 };
  const muted = buildChromeArgs(validOptions({ audioTap: false }), placement, '/tmp/profile');
  const tapped = buildChromeArgs(validOptions({ audioTap: true }), placement, '/tmp/profile');
  assert.equal(muted.includes('--mute-audio'), true);
  assert.equal(tapped.includes('--mute-audio'), false);
  assert.equal(muted.includes('--remote-allow-origins=*'), true);
});

test('signal shutdown uses conventional exit codes', () => {
  assert.equal(signalExitCode('SIGTERM'), 143);
  assert.equal(signalExitCode('SIGINT'), 130);
  assert.equal(signalExitCode('other'), 143);
});

test('cdp-host output is fixed at 960x540 (full-page 1280x720 is rejected)', () => {
  assert.doesNotThrow(() => validateOptions(validOptions(), 'darwin'));
  assert.throws(
    () => validateOptions(validOptions({ width: 1280, height: 720 }), 'darwin'),
    /960x540/,
  );
  assert.throws(
    () => validateOptions(validOptions({ width: 640, height: 360 }), 'darwin'),
    /960x540/,
  );
});

test('canvas geometry is validated fail-closed', () => {
  const good = { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 };
  assert.deepEqual(parseCanvasGeometry(good), good);
  assert.throws(() => parseCanvasGeometry(null), /fail-closed/);
  assert.throws(() => parseCanvasGeometry({ ...good, width: 0 }), /fail-closed/);
  assert.throws(() => parseCanvasGeometry({ ...good, width: 32, height: 32 }), /too small/);
  assert.throws(() => parseCanvasGeometry({ ...good, dpr: 0 }), /devicePixelRatio/);
  assert.throws(() => parseCanvasGeometry({ ...good, dpr: 8 }), /devicePixelRatio/);
  // Canvas larger than the measured viewport (zoomed/unexpected layout).
  assert.throws(() => parseCanvasGeometry({ ...good, width: 2000, height: 450 }), /outside content/);
  assert.throws(() => parseCanvasGeometry({ ...good, x: -50 }), /outside content/);
});

test('canvas crop adds the chrome offset and stays even-sized inside the capture output frame', () => {
  // Content 960x540, chrome band 87px top: canvas at content (80,60) 800x450.
  const base = {
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 },
  };
  const crop = resolveCanvasCropFrame(base);
  assert.deepEqual(crop, { x: 80, y: 146, w: 800, h: 450 });
  // Odd canvas origin/size rounds down to even (yuv420p-safe).
  const odd = resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 81, y: 61, width: 801, height: 451, iw: 960, ih: 540, dpr: 1 },
  });
  assert.equal(odd.x % 2, 0);
  assert.equal(odd.y % 2, 0);
  assert.equal(odd.w % 2, 0);
  assert.equal(odd.h % 2, 0);
  // ScreenCaptureKit scales the desktop-independent window into the explicitly
  // requested outerWidth x outerHeight raw frame. DPR therefore changes the
  // backing scale but must NOT double-scale CSS/DIP crop coordinates.
  const retina2 = resolveCanvasCropFrame({
    ...base,
    canvas: { ...base.canvas, dpr: 2 },
  });
  const retina15 = resolveCanvasCropFrame({
    ...base,
    canvas: { ...base.canvas, dpr: 1.5 },
  });
  assert.deepEqual(retina2, crop);
  assert.deepEqual(retina15, crop);
  // Fail-closed: genuinely out-of-frame geometry, no canvas, no dims.
  assert.throws(() => resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 900, y: 60, width: 100, height: 450, iw: 1000, ih: 540, dpr: 2 },
  }), /outside capture frame/);
  assert.throws(() => resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87, canvas: null,
  }), /fail-closed/);
  assert.throws(() => resolveCanvasCropFrame({
    outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 },
  }), /outerWidth/);
});

test('canvas video filter crops then scales/pads to exactly 960x540', () => {
  const filter = buildCanvasVideoFilter({ x: 80, y: 146, w: 800, h: 450 });
  assert.ok(filter.startsWith('crop=800:450:80:146'), filter);
  assert.ok(filter.includes('scale=960:540:force_original_aspect_ratio=decrease'), filter);
  assert.ok(filter.includes('pad=960:540:(ow-iw)/2:(oh-ih)/2'), filter);
  assert.ok(filter.endsWith('setsar=1'), filter);
  assert.throws(() => buildCanvasVideoFilter({ x: 80, y: 146, w: 800, h: 450 }, { outWidth: 1280, outHeight: 720 }), /960x540/);
});

test('canvas ffmpeg args carry the 960x540 crop/scale filter on the window-sized input', () => {
  const capture = {
    bundleId: 'com.google.Chrome', windowTitle: 'game',
    outerWidth: 960, outerHeight: 627, chromeTop: 87, chromeLeft: 0,
  };
  const crop = { x: 80, y: 146, w: 800, h: 450 };
  const args = buildCanvasFfmpegArgs({ ...validOptions(), audioTap: false }, capture, crop).join(' ');
  assert.ok(args.includes('-video_size 960x627'), args);
  assert.ok(args.includes('-vf crop=800:450:80:146,scale=960:540:'), args);
  assert.ok(args.includes('h264_videotoolbox'), args);
  assert.ok(args.includes(' -an '), args);
  const tapped = buildCanvasFfmpegArgs(validOptions(), capture, crop).join(' ');
  assert.ok(tapped.includes('-map 0:v -map 1:a'), tapped);
  assert.throws(() => buildCanvasFfmpegArgs({ ...validOptions(), audioTap: false }, null, crop), /outerWidth/);
  assert.throws(() => buildCanvasFfmpegArgs({ ...validOptions(), audioTap: false }, capture, null), /crop/);
});

test('measured 1280x720 content crops from live geometry to a 960x540 output', () => {
  // Regression for the Phase 3 timed FAIL: real window content is 1280x720
  // (calibration does not stick) and must NOT fail the run. The crop is
  // computed from measured geometry; the fixed output stays 960x540.
  const canvas = { x: 0, y: 0, width: 1280, height: 720, iw: 1280, ih: 720, dpr: 1 };
  assert.deepEqual(parseCanvasGeometry(canvas), canvas);
  const crop = resolveCanvasCropFrame({
    outerWidth: 1280, outerHeight: 807, chromeLeft: 0, chromeTop: 87,
    canvas,
  });
  assert.deepEqual(crop, { x: 0, y: 86, w: 1280, h: 720 });
  const filter = buildCanvasVideoFilter(crop);
  assert.ok(filter.startsWith('crop=1280:720:0:86'), filter);
  assert.ok(filter.includes('scale=960:540:force_original_aspect_ratio=decrease'), filter);
  assert.ok(filter.includes('pad=960:540:'), filter);
  // A letterboxed canvas inside 1280x720 content works the same way.
  const boxed = resolveCanvasCropFrame({
    outerWidth: 1280, outerHeight: 807, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 160, y: 90, width: 960, height: 540, iw: 1280, ih: 720, dpr: 1 },
  });
  assert.deepEqual(boxed, { x: 160, y: 176, w: 960, h: 540 });
});

test('canvas sample matching ignores subpixel jitter but not dpr flips', () => {
  const base = { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 };
  assert.equal(canvasSamplesMatch(base, { ...base }), true);
  assert.equal(canvasSamplesMatch(base, { ...base, x: 80.5 }), true);
  assert.equal(canvasSamplesMatch(base, { ...base, width: 802 }), false);
  assert.equal(canvasSamplesMatch(base, { ...base, dpr: 2 }), false);
  assert.equal(canvasSamplesMatch(base, null), false);
  assert.equal(canvasSamplesMatch(null, base), false);
});

test('stable geometry wait returns once the rect sits still', async () => {
  const rect = { x: 0, y: 0, width: 1280, height: 720, iw: 1280, ih: 720, dpr: 1 };
  let calls = 0;
  const sample = async () => {
    calls += 1;
    // Unity-load resize: first samples move, then the rect sits still.
    if (calls < 3) return { canvas: { ...rect, width: 640 + calls * 100 }, outerWidth: 1280, outerHeight: 807, chromeLeft: 0, chromeTop: 87 };
    return { canvas: { ...rect }, outerWidth: 1280, outerHeight: 807, chromeLeft: 0, chromeTop: 87 };
  };
  const stable = await waitForStableCanvasGeometry(sample, { pollMs: 5, stableMs: 20, timeoutMs: 2000 });
  assert.deepEqual(stable.canvas, rect);
  assert.equal(calls >= 3, true);
});

test('stable geometry wait fails closed when no canvas ever appears', async () => {
  await assert.rejects(
    () => waitForStableCanvasGeometry(async () => null, { pollMs: 5, stableMs: 10, timeoutMs: 30 }),
    /game canvas not found \(fail-closed/,
  );
});

test('stable geometry wait fails closed when the rect never settles', async () => {
  let n = 0;
  await assert.rejects(
    () => waitForStableCanvasGeometry(
      async () => ({ canvas: { x: n += 10, y: 0, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 } }),
      { pollMs: 5, stableMs: 60, timeoutMs: 50 },
    ),
    /never stabilized \(fail-closed/,
  );
});

test('stable-but-invalid rect still fails closed at crop time', async () => {
  // A rect permanently outside the measured content stabilizes, then
  // parse/resolve rejects it — never silently streamed.
  const bad = { canvas: { x: -50, y: 0, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 } };
  const stable = await waitForStableCanvasGeometry(async () => bad, { pollMs: 5, stableMs: 10, timeoutMs: 1000 });
  assert.throws(() => resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87, canvas: stable.canvas,
  }), /fail-closed/);
});

// Issue #303: the AudioService process may not exist yet at first attempt.
// The tap must retry (re-resolving PIDs) instead of aborting the stream.
const TAP_PS = [
  '  100  1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '  101 100 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Helper',
].join('\n');

test('audio tap attach retries until an audio process appears', async () => {
  let attempts = 0;
  let nowMs = 0;
  const result = await startAudioTapWithRetry({
    audioTapBin: 'helper',
    rootPid: 100,
    budgetMs: 10_000,
    psImpl: () => ({ stdout: TAP_PS }),
    startTapImpl: async () => {
      attempts += 1;
      if (attempts < 3) {
        throw new Error('no given PID has an audio process object (fail-closed): missing=[]');
      }
      return { child: { pid: 999 }, status: { ok: true } };
    },
    sleepImpl: async (ms) => { nowMs += ms; },
    now: () => nowMs,
  });
  assert.equal(result.status.ok, true);
  assert.equal(attempts, 3);
});

test('audio tap attach fails closed after the retry budget', async () => {
  let nowMs = 0;
  await assert.rejects(
    () => startAudioTapWithRetry({
      audioTapBin: 'helper',
      rootPid: 100,
      budgetMs: 3000,
      psImpl: () => ({ stdout: TAP_PS }),
      startTapImpl: async () => { throw new Error('no given PID has an audio process object'); },
      sleepImpl: async (ms) => { nowMs += ms; },
      now: () => nowMs,
    }),
    /audio tap attach failed after 3000ms \(fail-closed\)/,
  );
});

test('audio tap attach does not retry non-audio helper errors', async () => {
  let attempts = 0;
  await assert.rejects(
    () => startAudioTapWithRetry({
      audioTapBin: 'helper',
      rootPid: 100,
      budgetMs: 10_000,
      psImpl: () => ({ stdout: TAP_PS }),
      startTapImpl: async () => { attempts += 1; throw new Error('helper crashed'); },
      sleepImpl: async () => {},
      now: () => 0,
    }),
    /helper crashed/,
  );
  assert.equal(attempts, 1);
});

// --- Runtime offscreen watchdog (Mission Control / Space-switch hardening) ---

const WATCH_DISPLAYS = [
  { id: 1, bounds: { x: -2000, y: 0, width: 1280, height: 720 } }, // virtual (excluded)
  { id: 2, bounds: { x: 0, y: 0, width: 1920, height: 1080 } }, // physical
];
const WATCH_VIRTUAL_ID = 1;
const WATCH_CALIBRATED_RECT = { x: -2000, y: 0, width: 1280, height: 807 };
const WATCH_CONTENT = { iw: 1280, ih: 720 };

function drift(overrides = {}) {
  return evaluateGeometryDrift({
    windowRect: WATCH_CALIBRATED_RECT,
    displays: WATCH_DISPLAYS,
    virtualDisplayId: WATCH_VIRTUAL_ID,
    calibratedRect: WATCH_CALIBRATED_RECT,
    content: WATCH_CONTENT,
    calibratedContent: WATCH_CONTENT,
    ...overrides,
  });
}

test('geometry watchdog stays quiet while the window is offscreen and unchanged', () => {
  assert.deepEqual(drift(), { action: 'ok', reason: null });
});

test('geometry watchdog re-asserts when Mission Control moves the window onto a physical display', () => {
  const decision = drift({ windowRect: { x: 100, y: 100, width: 1280, height: 807 } });
  assert.deepEqual(decision, { action: 'reassert', reason: 'window-on-physical-display' });
});

test('geometry watchdog re-asserts when the offscreen window bounds drift', () => {
  const decision = drift({ windowRect: { x: -1800, y: 0, width: 1280, height: 807 } });
  assert.deepEqual(decision, { action: 'reassert', reason: 'window-bounds-drift' });
});

test('geometry watchdog fails closed when the content size changes (crop would misframe)', () => {
  assert.deepEqual(drift({ content: { iw: 1000, ih: 600 } }), {
    action: 'fail',
    reason: 'content-size-drift',
  });
});

test('geometry watchdog fails closed when a geometry input is missing', () => {
  assert.throws(() => drift({ content: null }), /fail-closed/);
});

test('geometry watchdog fails closed when the virtual display is absent from the list', () => {
  assert.throws(
    () => drift({ displays: [{ id: 2, bounds: { x: 0, y: 0, width: 1920, height: 1080 } }] }),
    /fail-closed/,
  );
});

test('audio gain adds a volume filter only when it differs from 1', () => {
  const capture = { outerWidth: 1280, outerHeight: 807, chromeTop: 87, chromeLeft: 0 };
  const crop = { x: 0, y: 87, w: 1280, h: 720 };
  const base = {
    width: 960, height: 540, videoMbps: 2, audioTap: true,
    srtUrl: 'srt://100.70.0.3:9000?mode=caller',
  };
  const plain = buildCanvasFfmpegArgs({ ...base, audioGain: 1 }, capture, crop);
  assert.ok(!plain.includes('-af'), 'gain 1 must not add a volume filter');
  const boosted = buildCanvasFfmpegArgs({ ...base, audioGain: 2.5 }, capture, crop);
  const idx = boosted.indexOf('-af');
  assert.notEqual(idx, -1);
  assert.equal(boosted[idx + 1], 'volume=2.5');
});

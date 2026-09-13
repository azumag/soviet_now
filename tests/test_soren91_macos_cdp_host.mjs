import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCanvasFfmpegArgs,
  buildCanvasVideoFilter,
  buildChromeArgs,
  findGameTarget,
  isAllowedCdpPeer,
  isExactGameTargetUrl,
  normalizeRemoteAddress,
  parseCanvasGeometry,
  resolveCanvasCropFrame,
  signalExitCode,
  validateOptions,
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

test('canvas crop adds the chrome offset and stays even-sized inside the frame', () => {
  // Content 960x540, chrome band 87px top: canvas at content (80,60) 800x450.
  const crop = resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 1 },
  });
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
  // devicePixelRatio scales CSS px into frame px (physical-pixel frame).
  // chrome offsets arrive in CSS units (Browser bounds minus innerWidth/
  // innerHeight), so the dpr multiply applies to the sum.
  const scaled = resolveCanvasCropFrame({
    outerWidth: 1920, outerHeight: 1254, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 2 },
  });
  assert.deepEqual(scaled, { x: 160, y: 294, w: 1600, h: 900 });
  // Fail-closed: scaled rect outside a bounds-unit frame, no canvas, no dims.
  assert.throws(() => resolveCanvasCropFrame({
    outerWidth: 960, outerHeight: 627, chromeLeft: 0, chromeTop: 87,
    canvas: { x: 80, y: 60, width: 800, height: 450, iw: 960, ih: 540, dpr: 2 },
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

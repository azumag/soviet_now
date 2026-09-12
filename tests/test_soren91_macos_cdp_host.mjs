import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildChromeArgs,
  findGameTarget,
  isAllowedCdpPeer,
  isExactGameTargetUrl,
  normalizeRemoteAddress,
  validateOptions,
} from '../tools/soren91_macos_cdp_host.mjs';

function validOptions(overrides = {}) {
  return {
    execute: true,
    cdpPort: 9322,
    proxyPort: 19093,
    bindIp: '100.70.0.2',
    width: 1280,
    height: 720,
    videoMbps: 2,
    srtUrl: 'srt://100.70.0.3:9000?mode=caller',
    audioTap: true,
    ...overrides,
  };
}

test('game target matching requires exact HTTPS unityroom host', () => {
  assert.equal(isExactGameTargetUrl('https://play.unityroom.com/games/foo'), true);
  assert.equal(isExactGameTargetUrl('http://play.unityroom.com/games/foo'), false);
  assert.equal(isExactGameTargetUrl('https://play.unityroom.com.evil.example/games/foo'), false);
  assert.equal(isExactGameTargetUrl('https://evil.example/?next=play.unityroom.com'), false);
  assert.equal(isExactGameTargetUrl('not-a-url play.unityroom.com'), false);

  const good = { type: 'page', url: 'https://play.unityroom.com/games/foo' };
  const targets = [
    { type: 'page', url: 'https://play.unityroom.com.evil.example/' },
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

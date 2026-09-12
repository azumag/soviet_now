import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { Readable } from 'node:stream';
import {
  buildAudioFfmpegInputArgs,
  buildAudioTapArgs,
  buildFfmpegStdio,
  collectDescendantChromePids,
  parseAudioTapStatus,
  parseProcessTable,
  resolveTapPids,
  shouldTapAudio,
  startAudioTap,
  stopAudioTap,
} from '../tools/soren91_macos_audio.mjs';

// Synthetic `ps -ax -o pid,ppid,command` table: PID 100 = automation
// renderer (node), 101 = automation Chrome main, 102/103 = its helpers,
// PID 500 = everyday Chrome (different tree), 600 = unrelated app.
const PS = `  PID  PPID COMMAND
    1     0 /sbin/launchd
  100    50 node tools/soren91_macos_renderer.mjs
  101   100 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --app=about:blank
  102   101 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) --type=renderer
  103   101 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --type=utility --utility-sub-type=audio.mojom.AudioService
  104   102 /sbin/nonexistent-totally-unrelated
  500     1 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome --profile-directory=Default
  501   500 /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper.app/Contents/MacOS/Google Chrome Helper --type=utility --utility-sub-type=audio.mojom.AudioService
  600     1 /Applications/Safari.app/Contents/MacOS/Safari
`;

test('parseProcessTable skips the header and parses pid/ppid/command', () => {
  const rows = parseProcessTable(PS);
  assert.ok(rows.length >= 8);
  assert.deepEqual(rows[0], { pid: 1, ppid: 0, command: '/sbin/launchd' });
  const helper = rows.find((row) => row.pid === 103);
  assert.equal(helper.ppid, 101);
  assert.match(helper.command, /AudioService/);
  assert.deepEqual(parseProcessTable(''), []);
  assert.deepEqual(parseProcessTable('  PID  PPID COMMAND\n'), []);
});

test('collectDescendantChromePids returns the automation tree only', () => {
  assert.deepEqual(collectDescendantChromePids(PS, 100), [101, 102, 103]);
  // Everyday Chrome (500/501) is parented elsewhere: never included.
  const pids = collectDescendantChromePids(PS, 100);
  assert.ok(!pids.includes(500) && !pids.includes(501));
  assert.ok(!pids.includes(600));
  // Non-Chrome descendants are excluded even inside the tree.
  assert.ok(!pids.includes(104));
  assert.ok(!pids.includes(100));
});

test('collectDescendantChromePids includes the root when it is Chrome itself', () => {
  assert.deepEqual(collectDescendantChromePids(PS, 101), [101, 102, 103]);
  assert.deepEqual(collectDescendantChromePids(PS, 500), [500, 501]);
});

test('collectDescendantChromePids is fail-closed on unknown roots and cycles', () => {
  assert.deepEqual(collectDescendantChromePids(PS, 99999), []);
  assert.deepEqual(collectDescendantChromePids(PS, 0), []);
  assert.deepEqual(collectDescendantChromePids(PS, 'nope'), []);
  assert.deepEqual(collectDescendantChromePids('', 100), []);
  const cyclic = `  PID  PPID COMMAND
  100    50 node renderer
  101   100 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  102   101 /Applications/Google Chrome.app/Helpers/Google Chrome Helper
`;
  // Self-parent cycle on 101: must terminate and still report the tree.
  const cyclicSelf = `${cyclic}  101   101 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
`;
  assert.deepEqual(collectDescendantChromePids(cyclicSelf, 100), [101, 102]);
});

test('resolveTapPids throws fail-closed on an empty PID set', () => {
  assert.deepEqual(resolveTapPids(PS, 100), [101, 102, 103]);
  assert.throws(() => resolveTapPids(PS, 99999), /fail-closed/);
  assert.throws(() => resolveTapPids('', 100), /fail-closed/);
  // A tree with no Chrome processes at all also fails closed.
  const noChrome = `  PID  PPID COMMAND
  100    50 node renderer
  700   100 /usr/bin/say hello
`;
  assert.throws(() => resolveTapPids(noChrome, 100), /fail-closed/);
});

test('buildAudioFfmpegInputArgs targets fd 3 as s16le 48k stereo', () => {
  assert.deepEqual(buildAudioFfmpegInputArgs(), [
    '-f', 's16le', '-ar', '48000', '-ac', '2', '-i', 'pipe:3',
  ]);
});

test('buildAudioTapArgs emits one --pid per PID and rejects empties', () => {
  assert.deepEqual(buildAudioTapArgs([101, 103]), ['--pid', '101', '--pid', '103']);
  assert.deepEqual(buildAudioTapArgs([7], { seconds: 5 }), ['--pid', '7', '--seconds', '5']);
  assert.throws(() => buildAudioTapArgs([]), /fail-closed/);
});

test('shouldTapAudio is on by default (opt out with =0)', () => {
  assert.equal(shouldTapAudio(undefined), true);
  assert.equal(shouldTapAudio(''), true);
  assert.equal(shouldTapAudio('0'), false);
  assert.equal(shouldTapAudio('no'), false);
  assert.equal(shouldTapAudio('1'), true);
  assert.equal(shouldTapAudio('true'), true);
});

test('buildFfmpegStdio adds fd 3 only when tapping', () => {
  assert.deepEqual(buildFfmpegStdio(true), ['pipe', 'inherit', 'inherit', 'pipe']);
  assert.deepEqual(buildFfmpegStdio(false), ['pipe', 'inherit', 'inherit']);
  assert.deepEqual(buildFfmpegStdio(undefined), ['pipe', 'inherit', 'inherit']);
});

test('parseAudioTapStatus is fail-closed', () => {
  const ok = '{"ok":true,"tapUID":"abc","format":{"sampleRate":48000},"pids":[101],"muteBehaviorRequested":2,"muteBehaviorVerified":2}';
  assert.equal(parseAudioTapStatus(ok).tapUID, 'abc');
  assert.throws(() => parseAudioTapStatus('{"ok":false,"error":"boom"}'), /boom/);
  assert.throws(() => parseAudioTapStatus('{"ok":false}'), /fail-closed/);
  assert.throws(() => parseAudioTapStatus('not json'), /non-JSON/);
  assert.throws(() => parseAudioTapStatus('{"ok":true,"tapUID":"","muteBehaviorVerified":2}'), /tapUID/);
  assert.throws(
    () => parseAudioTapStatus('{"ok":true,"tapUID":"abc","muteBehaviorVerified":0}'),
    /muteBehavior/,
  );
  assert.throws(
    () => parseAudioTapStatus('{"ok":true,"tapUID":"abc"}'),
    /muteBehavior/,
  );
});

// Minimal audio-tap stub: emits one stderr status line, stays alive until
// SIGTERM, then exits — mirroring the real helper's lifecycle contract.
function stubAudioTapOnce({ line, exitCode = 1 } = {}) {
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

test('audio tap handshake resolves on ok:true and cleanup sends SIGTERM', async () => {
  const line = '{"ok":true,"tapUID":"t-1","pids":[101],"muteBehaviorRequested":2,"muteBehaviorVerified":2}';
  let seenArgs = null;
  const spawnImpl = (bin, args, opts) => {
    seenArgs = { bin, args, opts };
    return stubAudioTapOnce({ line });
  };
  const { child, status } = await startAudioTap('/tmp/soren91_audio_tap', [101, 103], { spawnImpl });
  assert.equal(seenArgs.bin, '/tmp/soren91_audio_tap');
  assert.deepEqual(seenArgs.args, ['--pid', '101', '--pid', '103']);
  assert.deepEqual(seenArgs.opts.stdio, ['ignore', 'pipe', 'pipe']);
  assert.equal(status.tapUID, 't-1');
  await stopAudioTap(child);
  assert.deepEqual(child.killSignals, ['SIGTERM']);
  assert.equal(child.exitCode, 0);
});

test('audio tap handshake rejects fail-closed (no silent audio wiring)', async () => {
  const bad = stubAudioTapOnce({ line: '{"ok":false,"error":"nope"}' });
  await assert.rejects(
    startAudioTap('/tmp/tap', [101], { spawnImpl: () => bad }),
    /nope/,
  );
  const unverified = stubAudioTapOnce({
    line: '{"ok":true,"tapUID":"t-1","muteBehaviorVerified":0}',
  });
  await assert.rejects(
    startAudioTap('/tmp/tap', [101], { spawnImpl: () => unverified }),
    /muteBehavior/,
  );
  const silent = stubAudioTapOnce({ line: null, exitCode: 1 });
  await assert.rejects(
    startAudioTap('/tmp/tap', [101], { spawnImpl: () => silent }),
    /exited before readiness/,
  );
});

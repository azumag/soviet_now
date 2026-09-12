import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough, Readable } from 'node:stream';
import {
  buildAudioFfmpegInputArgs,
  buildAudioTapArgs,
  buildFfmpegStdio,
  classifyFfmpegExit,
  collectDescendantChromePids,
  isBenignPipeError,
  parseAudioTapStatus,
  parseProcessTable,
  resolveSessionEnd,
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

// --- ffmpeg exit classification (Issue #303: listener-first close) ---

function pipeError(code) {
  return Object.assign(new Error(`write ${code}`), { code });
}

test('isBenignPipeError swallows only receiver-closed codes', () => {
  assert.equal(isBenignPipeError(pipeError('EPIPE')), true);
  assert.equal(isBenignPipeError(pipeError('ERR_STREAM_DESTROYED')), true);
  assert.equal(isBenignPipeError(pipeError('ERR_STREAM_WRITE_AFTER_END')), true);
  assert.equal(isBenignPipeError(pipeError('ECONNRESET')), false);
  assert.equal(isBenignPipeError(new Error('boom')), false);
  assert.equal(isBenignPipeError(null), false);
  assert.equal(isBenignPipeError(undefined), false);
});

test('classifyFfmpegExit: deadline path stays a normal end', () => {
  assert.equal(classifyFfmpegExit({ code: 1, deadlineReached: true }), 'deadline');
  assert.equal(
    classifyFfmpegExit({ code: 1, stderr: 'Error submitting a packet to the muxer', deadlineReached: true }),
    'deadline',
  );
});

test('classifyFfmpegExit: listener-first close is consumer-closed only with explicit output markers', () => {
  // Observed in live E2E when the OCI listener's `-t 120` expires.
  const stderr = 'Error submitting a packet to the muxer: Input/output error\n'
    + 'Last message repeated 3 times\n'
    + 'av_interleaved_write_frame(): Input/output error\n';
  assert.equal(classifyFfmpegExit({ code: 1, stderr }), 'consumer-closed');
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'Broken pipe' }), 'consumer-closed');
  assert.equal(
    classifyFfmpegExit({ code: 1, stderr: 'av_interleaved_write_frame(): Input/output error' }),
    'consumer-closed',
  );
});

test('classifyFfmpegExit: ambiguous early exits fail closed', () => {
  // Producer-side EPIPE only proves ffmpeg went away; every ffmpeg failure
  // closes stdin, so sinkClosed must never turn a genuine failure into success.
  // Likewise a bare code 0 or a generic "muxer" substring (e.g. queue
  // overflow, a real failure) is not receiver-close evidence.
  assert.equal(classifyFfmpegExit({ code: 1, stderr: '', sinkClosed: true }), 'failed');
  // Generic muxer text can describe a real queue/performance failure.
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'muxer queue overflow' }), 'failed');
  // Generic I/O text without the observed SRT output signatures is ambiguous.
  assert.equal(classifyFfmpegExit({ code: 1, stderr: 'Input/output error while decoding stream #0:0' }), 'failed');
  // A clean early exit can also be caused by an upstream EOF; without an
  // explicit receiver-close marker it must not be reported as success.
  assert.equal(classifyFfmpegExit({ code: 0, stderr: '' }), 'failed');
});

test('classifyFfmpegExit: genuine failures still fail', () => {
  const stderr = "Error initializing output stream 0:0 -- Error opening encoder 'h264_videotoolbox'\n";
  assert.equal(classifyFfmpegExit({ code: 1, stderr }), 'failed');
  assert.equal(classifyFfmpegExit({ code: 1, stderr: '' }), 'failed');
  assert.equal(classifyFfmpegExit({ code: 1 }), 'failed');
  assert.equal(classifyFfmpegExit({ code: null, signal: null }), 'failed');
});

test('classifyFfmpegExit: signal death always fails', () => {
  assert.equal(classifyFfmpegExit({ code: null, signal: 'SIGTERM' }), 'failed');
  assert.equal(
    classifyFfmpegExit({ code: null, signal: 'SIGKILL', stderr: 'Broken pipe', sinkClosed: true }),
    'failed',
  );
});

// --- session-end classification (Issue #303: capture-exit wins the race) ---

test('classifySessionEnd: deadline stays a normal end', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  assert.equal(classifySessionEnd({ kind: 'deadline' }), 'deadline');
  assert.equal(classifySessionEnd({ kind: 'ffmpeg-exit', code: 1, deadlineReached: true }), 'deadline');
});

test('classifySessionEnd: ffmpeg-exit delegates to classifyFfmpegExit', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: 1, signal: null }, stderr: 'Broken pipe' }),
    'consumer-closed',
  );
  assert.equal(
    classifySessionEnd({
      kind: 'ffmpeg-exit',
      value: { code: 1, signal: null },
      stderr: 'av_interleaved_write_frame(): Input/output error',
    }),
    'consumer-closed',
  );
  // Ambiguous ffmpeg exits stay fail-closed (tightened semantics: only
  // explicit SRT-output markers count — a bare code 0, sinkClosed alone,
  // or a generic "muxer" substring do not prove listener-first close).
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: 0, signal: null }, stderr: '' }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: 1, signal: null }, stderr: '', sinkClosed: true }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: 1, signal: null }, stderr: 'muxer queue overflow' }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: 1, signal: null }, stderr: 'encoder boom' }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'ffmpeg-exit', value: { code: null, signal: 'SIGTERM' } }),
    'failed',
  );
});

test('classifySessionEnd: capture-exit code 0 is consumer-closed with or without evidence', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  // Listener-first close: the SIGPIPE-ignoring Swift helper exits 0 on
  // 'exit' before ffmpeg's 'close' fires — normal end of stream.
  assert.equal(classifySessionEnd({ kind: 'capture-exit', value: { code: 0, signal: null } }), 'consumer-closed');
  // With receiver-close evidence the reading only gets stronger.
  assert.equal(
    classifySessionEnd({ kind: 'capture-exit', value: { code: 0, signal: null }, sinkClosed: true }),
    'consumer-closed',
  );
  assert.equal(
    classifySessionEnd({
      kind: 'capture-exit', value: { code: 0, signal: null }, stderr: 'av_interleaved_write_frame(): Input/output error',
    }),
    'consumer-closed',
  );
});

test('classifySessionEnd: capture-exit non-zero or signalled still fails', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  assert.equal(classifySessionEnd({ kind: 'capture-exit', value: { code: 1, signal: null } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'capture-exit', value: { code: 2, signal: null }, sinkClosed: true }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'capture-exit', value: { code: null, signal: 'SIGTERM' } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'capture-exit', value: { code: null, signal: 'SIGKILL' } }), 'failed');
});

test('classifySessionEnd: renderer/audio-tap exits are never a normal end', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  assert.equal(classifySessionEnd({ kind: 'renderer-exit', value: { code: 0, signal: null } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'renderer-exit', value: { code: 1, signal: null } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'audio-tap-exit', value: { code: 0, signal: null } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'audio-tap-exit', value: { code: 1, signal: null } }), 'failed');
  assert.equal(classifySessionEnd({ kind: 'bogus-kind', value: { code: 0, signal: null } }), 'failed');
});

// --- audio-tap winner with pipe-guard evidence (Issue #303: tap SIGPIPE) ---
//
// Live, the OCI listener closing first kills ffmpeg (fd 3's reader) and the
// helper's next PCM write dies with SIGPIPE (or exits 0 now that the helper
// ignores SIGPIPE) — beating ffmpeg's `close` racer. With sinkClosed proof
// that the sink went away, that is a normal end; without it the tap alone
// proves nothing and must stay fail-closed.

test('classifySessionEnd: audio-tap SIGPIPE/code-0 with sinkClosed is consumer-closed', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: null, signal: 'SIGPIPE' }, sinkClosed: true }),
    'consumer-closed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: 0, signal: null }, sinkClosed: true }),
    'consumer-closed',
  );
});

test('classifySessionEnd: audio-tap exit without sinkClosed stays failed', async () => {
  const { classifySessionEnd } = await import('../tools/soren91_macos_audio.mjs');
  // No pipe-guard evidence: the tap may have died on its own.
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: null, signal: 'SIGPIPE' } }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: 0, signal: null } }),
    'failed',
  );
  // Foreign signals / real helper errors never become a normal end, even
  // with sinkClosed evidence.
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: null, signal: 'SIGTERM' }, sinkClosed: true }),
    'failed',
  );
  assert.equal(
    classifySessionEnd({ kind: 'audio-tap-exit', value: { code: 1, signal: null }, sinkClosed: true }),
    'failed',
  );
  // The renderer dying is never a normal end, evidence or not.
  assert.equal(
    classifySessionEnd({ kind: 'renderer-exit', value: { code: 0, signal: null }, sinkClosed: true }),
    'failed',
  );
});

// --- resolveSessionEnd: whoever wins the race, ffmpeg's exit decides ---

const LISTENER_CLOSE_STDERR = 'Error submitting a packet to the muxer: Input/output error\n'
  + 'av_interleaved_write_frame(): Input/output error\n';
const ENCODER_FAILURE_STDERR = "Error initializing output stream 0:0 -- Error opening encoder 'h264_videotoolbox'\n";

test('resolveSessionEnd: audio-tap SIGPIPE winner waits for ffmpeg consumer-close', async () => {
  // ffmpeg settles late (after 2 polls) with a listener-close marker: the
  // verdict must be consumer-closed, and the resolver must actually have
  // waited (2 fake-clock sleeps) instead of blaming the tap.
  let polls = 0;
  const sleeps = [];
  const verdict = await resolveSessionEnd({
    kind: 'audio-tap-exit',
    value: { code: null, signal: 'SIGPIPE' },
    getStderr: () => LISTENER_CLOSE_STDERR,
    getSinkClosed: () => true,
    getFfmpegExit: () => {
      polls += 1;
      return polls >= 3 ? { code: 1, signal: null } : null;
    },
    ffmpegWaitMs: 5000,
    sleepImpl: async (ms) => { sleeps.push(ms); },
  });
  assert.equal(verdict, 'consumer-closed');
  assert.equal(polls, 3);
  assert.deepEqual(sleeps, [50, 50]);
});

test('resolveSessionEnd: genuine ffmpeg failure is failed even when the tap wins', async () => {
  // Encoder init failure with the tap winning the race: the tap's SIGPIPE
  // must not mask the real failure.
  let slept = 0;
  const verdict = await resolveSessionEnd({
    kind: 'audio-tap-exit',
    value: { code: null, signal: 'SIGPIPE' },
    getStderr: () => ENCODER_FAILURE_STDERR,
    getSinkClosed: () => true,
    ffmpegExit: { code: 1, signal: null },
    sleepImpl: async () => { slept += 1; },
  });
  assert.equal(verdict, 'failed');
  assert.equal(slept, 0); // already observed: no wait needed
});

test('resolveSessionEnd: capture-exit code 0 winner uses ffmpeg consumer-close', async () => {
  const verdict = await resolveSessionEnd({
    kind: 'capture-exit',
    value: { code: 0, signal: null },
    getStderr: () => LISTENER_CLOSE_STDERR,
    getSinkClosed: () => false,
    ffmpegExit: { code: 1, signal: null },
    sleepImpl: async () => { throw new Error('must not sleep'); },
  });
  assert.equal(verdict, 'consumer-closed');
});

test('resolveSessionEnd: unsettled ffmpeg falls back to winner+sink evidence', async () => {
  // ffmpeg never settles and no poll is supplied: tap SIGPIPE + sinkClosed
  // still ends consumer-closed; without sinkClosed it stays failed.
  assert.equal(
    await resolveSessionEnd({
      kind: 'audio-tap-exit',
      value: { code: null, signal: 'SIGPIPE' },
      stderr: '',
      sinkClosed: true,
    }),
    'consumer-closed',
  );
  assert.equal(
    await resolveSessionEnd({
      kind: 'audio-tap-exit',
      value: { code: null, signal: 'SIGPIPE' },
      stderr: '',
      sinkClosed: false,
    }),
    'failed',
  );
});

test('resolveSessionEnd: renderer-exit and foreign winners never wait for ffmpeg', async () => {
  for (const outcome of [
    { kind: 'renderer-exit', value: { code: 0, signal: null } },
    { kind: 'capture-exit', value: { code: 1, signal: null } },
    { kind: 'audio-tap-exit', value: { code: null, signal: 'SIGTERM' } },
    { kind: 'bogus-kind', value: { code: 0, signal: null } },
  ]) {
    let ffmpegPolls = 0;
    const verdict = await resolveSessionEnd({
      ...outcome,
      getStderr: () => LISTENER_CLOSE_STDERR,
      getSinkClosed: () => true,
      getFfmpegExit: () => {
        ffmpegPolls += 1;
        return { code: 1, signal: null };
      },
      sleepImpl: async () => { throw new Error('must not sleep'); },
    });
    assert.equal(verdict, 'failed', JSON.stringify(outcome));
    assert.equal(ffmpegPolls, 0, JSON.stringify(outcome));
  }
});

test('resolveSessionEnd: deadline never waits for ffmpeg', async () => {
  let ffmpegPolls = 0;
  assert.equal(
    await resolveSessionEnd({
      kind: 'deadline',
      getFfmpegExit: () => {
        ffmpegPolls += 1;
        return null;
      },
      sleepImpl: async () => { throw new Error('must not sleep'); },
    }),
    'deadline',
  );
  assert.equal(ffmpegPolls, 0);
});

test('resolveSessionEnd: ffmpeg wait is bounded and never hangs', async () => {
  // ffmpeg never settles: with a 100ms bound and the REAL clock this must
  // resolve promptly (2 x 50ms polls) instead of hanging.
  const started = Date.now();
  const verdict = await resolveSessionEnd({
    kind: 'audio-tap-exit',
    value: { code: null, signal: 'SIGPIPE' },
    getStderr: () => '',
    getSinkClosed: () => false,
    getFfmpegExit: () => null,
    ffmpegWaitMs: 100,
  });
  const elapsed = Date.now() - started;
  assert.equal(verdict, 'failed');
  assert.ok(elapsed < 2000, `settled in ${elapsed}ms, expected < 2000ms`);
});

// --- Early audio-tap attach (Issue #303, Part B: silence from boot) ---

// Automation renderer up, but no Chrome (and no audio) under it yet —
// the state the early poll sees before Chrome's AudioService appears.
const PS_NO_CHROME = `  PID  PPID COMMAND
    1     0 /sbin/launchd
  100    50 node tools/soren91_macos_renderer.mjs
  700   100 /usr/bin/say hello
`;

function fakeTapChild() {
  const child = new EventEmitter();
  child.stdout = new PassThrough();
  child.killSignals = [];
  child.kill = function kill(signal) {
    child.killSignals.push(signal);
    return true;
  };
  return child;
}

test('earlyAttachAudioTap polls from spawn until Chrome appears, then starts the tap drained', async () => {
  const { earlyAttachAudioTap } = await import('../tools/soren91_macos_audio.mjs');
  let psCalls = 0;
  const psImpl = () => {
    psCalls += 1;
    return { stdout: psCalls < 3 ? PS_NO_CHROME : PS };
  };
  const sleepCalls = [];
  const sleepImpl = async (ms) => { sleepCalls.push(ms); };
  let seenBin = null;
  let seenPids = null;
  const child = fakeTapChild();
  const startTapImpl = async (bin, pids) => {
    seenBin = bin;
    seenPids = [...pids];
    return { child, status: { ok: true, tapUID: 'early-1' } };
  };
  const attached = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/soren91_audio_tap',
    deadlineMs: Date.now() + 60_000,
    psImpl,
    startTapImpl,
    sleepImpl,
  });
  assert.equal(psCalls, 3);
  assert.deepEqual(sleepCalls, [500, 500]); // ~500ms poll cadence
  assert.equal(seenBin, '/tmp/soren91_audio_tap');
  assert.deepEqual(seenPids, [101, 102, 103]);
  assert.equal(attached.child, child);
  assert.equal(attached.status.tapUID, 'early-1');
  assert.equal(typeof attached.detachDrain, 'function');
  // PCM is drain-read while ffmpeg is not yet running (mute stays on).
  assert.ok(child.stdout.listenerCount('data') >= 1);
  attached.detachDrain();
  assert.equal(child.stdout.listenerCount('data'), 0);
  child.stdout.destroy();
});

test('earlyAttachAudioTap retries a no-audio-process helper failure until success', async () => {
  const { earlyAttachAudioTap } = await import('../tools/soren91_macos_audio.mjs');
  let attempts = 0;
  const startTapImpl = async () => {
    attempts += 1;
    if (attempts < 3) throw new Error('audio tap helper failed (fail-closed): no tappable audio process');
    return { child: fakeTapChild(), status: { ok: true, tapUID: 'retry-1' } };
  };
  const attached = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/tap',
    deadlineMs: Date.now() + 60_000,
    psImpl: () => ({ stdout: PS }),
    startTapImpl,
    sleepImpl: async () => {},
  });
  assert.equal(attempts, 3);
  assert.equal(attached.status.tapUID, 'retry-1');
  attached.detachDrain();
  attached.child.stdout.destroy();
});

test('earlyAttachAudioTap returns null on deadline (caller falls back, then fail-closed)', async () => {
  const { earlyAttachAudioTap } = await import('../tools/soren91_macos_audio.mjs');
  // Already-expired deadline: no ps, no tap attempt.
  let psCalls = 0;
  const expired = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/tap',
    deadlineMs: Date.now() - 1,
    psImpl: () => {
      psCalls += 1;
      return { stdout: PS_NO_CHROME };
    },
    startTapImpl: async () => { throw new Error('must not be called'); },
    sleepImpl: async () => {},
  });
  assert.equal(expired, null);
  assert.equal(psCalls, 0);
  // Deadline expiring mid-poll: terminates after the bounded wait.
  const started = Date.now();
  const midPoll = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/tap',
    deadlineMs: Date.now() + 30,
    psImpl: () => ({ stdout: PS_NO_CHROME }),
    startTapImpl: async () => { throw new Error('must not be called'); },
  });
  assert.equal(midPoll, null);
  assert.ok(Date.now() - started < 2000, 'mid-poll deadline must terminate promptly');
});

test('earlyAttachAudioTap honours cancel and never leaks a racy helper', async () => {
  const { earlyAttachAudioTap } = await import('../tools/soren91_macos_audio.mjs');
  // Cancelled before any attempt: no tap spawn, prompt null.
  let started = 0;
  const none = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/tap',
    deadlineMs: Date.now() + 60_000,
    psImpl: () => ({ stdout: PS }),
    startTapImpl: async () => {
      started += 1;
      return { child: fakeTapChild(), status: {} };
    },
    sleepImpl: async () => {},
    isCancelled: () => true,
  });
  assert.equal(none, null);
  assert.equal(started, 0);
  // Cancel landing mid-handshake: the won helper is SIGTERMed, null returned.
  const cancelFlag = { value: false };
  const victim = fakeTapChild();
  const result = await earlyAttachAudioTap({
    rendererPid: 100,
    audioTapBin: '/tmp/tap',
    deadlineMs: Date.now() + 60_000,
    psImpl: () => ({ stdout: PS }),
    startTapImpl: async () => {
      cancelFlag.value = true;
      return { child: victim, status: {} };
    },
    sleepImpl: async () => {},
    isCancelled: () => cancelFlag.value,
  });
  assert.equal(result, null);
  assert.deepEqual(victim.killSignals, ['SIGTERM']);
});

test('PCM drain discards pre-ffmpeg audio; detach hands a live pipe to ffmpeg', async () => {
  const { attachPcmDrain } = await import('../tools/soren91_macos_audio.mjs');
  const src = new PassThrough();
  const detach = attachPcmDrain({ stdout: src });
  src.write(Buffer.from([1, 2, 3, 4]));
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(src.readableLength, 0); // drained, not buffered
  detach();
  // ffmpeg switch-over: pipe the same live stream into fd 3 and audio flows.
  const dest = new PassThrough();
  src.pipe(dest);
  try { src.resume?.(); } catch {}
  src.write(Buffer.from([5, 6]));
  const chunk = await new Promise((resolve) => dest.once('data', resolve));
  assert.deepEqual([...chunk], [5, 6]);
  src.destroy();
  dest.destroy();
});

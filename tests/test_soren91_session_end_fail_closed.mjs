import test from 'node:test';
import assert from 'node:assert/strict';

import { resolveSessionEnd } from '../tools/soren91_macos_audio.mjs';

const LISTENER_CLOSE_STDERR = 'Error submitting a packet to the muxer: Input/output error\n'
  + 'av_interleaved_write_frame(): Input/output error\n';

test('capture code-0 fails closed when the bounded ffmpeg poll never observes an exit', async () => {
  let polls = 0;
  const verdict = await resolveSessionEnd({
    kind: 'capture-exit',
    value: { code: 0, signal: null },
    getStderr: () => '',
    getSinkClosed: () => true,
    getFfmpegExit: () => {
      polls += 1;
      return null;
    },
    ffmpegWaitMs: 120,
    sleepImpl: async () => {},
  });
  assert.equal(verdict, 'failed');
  assert.equal(polls, 4); // initial read + 3 bounded re-reads
});

test('audio producer EPIPE evidence fails closed when ffmpeg exit is unobserved', async () => {
  const verdict = await resolveSessionEnd({
    kind: 'audio-tap-exit',
    value: { code: 0, signal: null },
    getStderr: () => '',
    getSinkClosed: () => true,
    getFfmpegExit: () => null,
    ffmpegWaitMs: 0,
    sleepImpl: async () => {},
  });
  assert.equal(verdict, 'failed');
});

test('an observed ffmpeg exit with the explicit SRT close marker remains a normal consumer close', async () => {
  const verdict = await resolveSessionEnd({
    kind: 'capture-exit',
    value: { code: 0, signal: null },
    getStderr: () => LISTENER_CLOSE_STDERR,
    getSinkClosed: () => true,
    ffmpegExit: { code: 1, signal: null },
    getFfmpegExit: () => null,
    sleepImpl: async () => {
      throw new Error('must not sleep once ffmpeg exit is observed');
    },
  });
  assert.equal(verdict, 'consumer-closed');
});

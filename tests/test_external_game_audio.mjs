import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { ExternalGameAudio, loadExternalGameAudioConfig, parsePactlSinkInputs } from '../external_game_audio.mjs';


class FakeChild extends EventEmitter {
  constructor() {
    super();
    this.kills = [];
  }

  kill(signal) {
    this.kills.push(signal);
    return true;
  }
}

class ExitOnTerminateChild extends FakeChild {
  kill(signal) {
    super.kill(signal);
    if (signal === 'SIGTERM') queueMicrotask(() => this.emit('exit', 0, signal));
    return true;
  }
}

class ExitOnSigkillChild extends FakeChild {
  kill(signal) {
    super.kill(signal);
    if (signal === 'SIGKILL') queueMicrotask(() => this.emit('exit', 0, signal));
    return true;
  }
}

class NeverExitChild extends FakeChild {
  kill(signal) {
    super.kill(signal);
    return true;
  }
}

function fakeClock() {
  let now = 0;
  let sequence = 0;
  const tasks = new Map();
  return {
    nowFn: () => now,
    setTimeoutFn(action, delay) {
      const timer = { id: ++sequence, due: now + delay };
      tasks.set(timer, action);
      return timer;
    },
    clearTimeoutFn(timer) {
      tasks.delete(timer);
    },
    setIntervalFn(action, delay) {
      const timer = { id: ++sequence, due: now + delay, interval: delay };
      tasks.set(timer, action);
      return timer;
    },
    clearIntervalFn(timer) {
      tasks.delete(timer);
    },
    advance(ms) {
      const target = now + ms;
      while (true) {
        const next = [...tasks.keys()]
          .filter((timer) => timer.due <= target)
          .sort((a, b) => a.due - b.due || a.id - b.id)[0];
        if (!next) break;
        now = next.due;
        const action = tasks.get(next);
        if (next.interval) {
          next.due += next.interval;
          tasks.set(next, action);
        } else {
          tasks.delete(next);
        }
        action();
      }
      now = target;
    },
    pending: () => tasks.size,
  };
}

function harness(overrides = {}) {
  const clock = fakeClock();
  const spawns = [];
  const logs = [];
  const pactlCalls = [];
  let pactlListOutput = '';
  const config = {
    enabled: true,
    initialBgmFile: '/audio/International.ogg',
    sovietBgmFile: '/audio/SovietAnthem.ogg',
    dropSeFile: '/audio/drop.wav',
    mergeSeFile: '/audio/merge.wav',
    russiaSeFile: '/audio/russia.wav',
    hammerSickleSeFile: '/audio/hammer.wav',
    bgmVolumePct: 60,
    seVolumePct: 70,
    pulseLatencyMs: 100,
    hammerSickleDelayMs: 1000,
    sovietBgmDelayMs: 5250,
    ...overrides,
  };
  const audio = new ExternalGameAudio(config, {
    ...clock,
    fileExists: () => true,
    pactlFn(args) {
      pactlCalls.push(args);
      if (args[0] === 'list') return { ok: true, stdout: pactlListOutput };
      return { ok: true, stdout: '' };
    },
    spawnFn(command, args, options) {
      const child = new FakeChild();
      child.pid = 4000 + spawns.length + 1;
      spawns.push({ command, args, options, child, file: args.at(-1) });
      return child;
    },
    logger: {
      log(message) { logs.push(String(message)); },
      warn(message) { logs.push(`WARN:${message}`); },
    },
  });
  return {
    audio, clock, spawns, logs, pactlCalls,
    setPactlListOutput(output) { pactlListOutput = output; },
  };
}

function sinkInputList(entries) {
  return entries.map((entry) => [
    `Sink Input #${entry.index}`,
    '\tDriver: protocol-native.c',
    '\tOwner Module: 12',
    `\tMute: ${entry.mute ? 'yes' : 'no'}`,
    '\tVolume: front-left: 39321 /  60% / -13.32 dB,   front-right: 39321 /  60% / -13.32 dB',
    '\tProperties:',
    `\t\tapplication.name = "${entry.appName}"`,
    `\t\tapplication.process.id = "${entry.pid}"`,
    `\t\tmedia.name = "${entry.mediaName}"`,
  ].join('\n')).join('\n');
}

test('config keeps legacy BGM compatibility and is Linux-only', () => {
  const linux = loadExternalGameAudioConfig({
    SOREN_GAME_BGM_FILE: '/legacy.ogg',
    SOREN_GAME_AUDIO_PULSE_LATENCY_MS: '500',
  }, 'linux');
  assert.equal(linux.enabled, true);
  assert.equal(linux.initialBgmFile, '/legacy.ogg');
  assert.equal(linux.pulseLatencyMs, 500);

  const mac = loadExternalGameAudioConfig({ SOREN_GAME_BGM_FILE: '/legacy.ogg' }, 'darwin');
  assert.equal(mac.enabled, false);
});

test('normal game starts International BGM with a buffered PulseAudio ffplay', () => {
  const { audio, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });

  assert.equal(spawns.length, 1);
  assert.equal(spawns[0].command, 'ffplay');
  assert.equal(spawns[0].file, '/audio/International.ogg');
  assert.ok(spawns[0].args.includes('-loop'));
  assert.equal(spawns[0].options.env.SDL_AUDIODRIVER, 'pulse');
  assert.equal(spawns[0].options.env.PULSE_LATENCY_MSEC, '100');
  assert.ok(spawns[0].args.includes('-fflags'));
  assert.ok(spawns[0].args.includes('nobuffer'));
});

test('WAV SE plays through paplay with linear volume', () => {
  const { audio, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  audio.playDrop();

  assert.equal(spawns.at(-1).command, 'paplay');
  assert.equal(spawns.at(-1).file, '/audio/drop.wav');
  assert.ok(spawns.at(-1).args.includes('--device=@DEFAULT_SINK@'));
  assert.ok(spawns.at(-1).args.includes('--volume=45875')); // 70% of 65536
  assert.equal(spawns.at(-1).options.env.PULSE_LATENCY_MSEC, '100');
});

test('first Soviet formation reproduces Unity SE timing and changes BGM', () => {
  const { audio, clock, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });

  audio.observeState({ state: 'STOP', score: 0, makeSorenCount: 1 });
  assert.deepEqual(spawns.map((item) => item.file), [
    '/audio/International.ogg',
    '/audio/russia.wav',
  ]);

  clock.advance(999);
  assert.equal(spawns.length, 2);
  clock.advance(1);
  assert.equal(spawns.at(-1).file, '/audio/hammer.wav');

  clock.advance(4250);
  assert.equal(spawns.at(-1).file, '/audio/SovietAnthem.ogg');
  assert.deepEqual(spawns[0].child.kills, ['SIGTERM']);

  // The delayed score increase belongs to the same first-Soviet animation and
  // must not incorrectly add the ordinary merge SE.
  audio.observeState({ state: 'MOVE', score: 120, makeSorenCount: 1 });
  assert.notEqual(spawns.at(-1).file, '/audio/merge.wav');

  clock.advance(2750);
  audio.observeState({ state: 'MOVE', score: 130, makeSorenCount: 1 });
  assert.equal(spawns.at(-1).file, '/audio/merge.wav');
});

test('drop, mute, and retry control external audio without stale timers', () => {
  const { audio, clock, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  audio.playDrop();
  assert.equal(spawns.at(-1).file, '/audio/drop.wav');

  audio.observeState({ state: 'STOP', score: 0, makeSorenCount: 1 });
  assert.equal(clock.pending(), 4, 'hammer + soviet BGM + startup audible check plus the BGM health interval');
  audio.setMuted(true);
  assert.equal(clock.pending(), 0);
  const spawnCountWhileMuted = spawns.length;
  audio.playDrop();
  assert.equal(spawns.length, spawnCountWhileMuted);

  audio.setMuted(false);
  assert.equal(spawns.at(-1).file, '/audio/SovietAnthem.ogg');
  audio.resetForNewGame();
  assert.equal(spawns.at(-1).file, '/audio/International.ogg');
  clock.advance(10000);
  assert.equal(spawns.at(-1).file, '/audio/International.ogg');
});


test('unexpected BGM exit schedules an auto-restart with backoff', () => {
  const { audio, clock, spawns, logs } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  assert.equal(spawns.length, 1);
  const first = spawns[0].child;

  first.emit('exit', 1, null);
  assert.ok(logs.some((line) => line.includes('BGM unexpected exit')));
  assert.equal(spawns.length, 1, 'restart must wait for the backoff delay');

  clock.advance(1999);
  assert.equal(spawns.length, 1);
  clock.advance(1);
  assert.equal(spawns.length, 2, 'BGM must respawn after the restart delay');
  assert.equal(spawns.at(-1).file, '/audio/International.ogg');

  // Second unexpected exit uses a longer backoff, then recovers on success.
  const second = spawns[1].child;
  second.emit('exit', 1, null);
  clock.advance(1999);
  assert.equal(spawns.length, 2);
  clock.advance(2001);
  assert.equal(spawns.length, 3, 'backoff doubles after repeated failures');
  assert.equal(spawns.at(-1).file, '/audio/International.ogg');
});


test('intentional stop, mute, and shutdown never auto-restart BGM', () => {
  const { audio, clock, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  assert.equal(spawns.length, 1);

  audio.setMuted(true);
  spawns[0].child.emit('exit', 1, null);
  clock.advance(30000);
  assert.equal(spawns.length, 1, 'muted BGM exit must not schedule a restart');

  audio.setMuted(false);
  assert.equal(spawns.length, 2);
  audio.shutdown();
  spawns[1].child.emit('exit', 0, null);
  clock.advance(30000);
  assert.equal(spawns.length, 2, 'shutdown must not schedule a restart');
});


test('shutdownAndWait proves game audio children exited before returning', async () => {
  const { audio } = harness();
  const child = new ExitOnTerminateChild();
  audio.bgmChild = child;
  audio.activeBgmMode = 'initial';

  const result = await audio.shutdownAndWait(100, 100);
  assert.deepEqual(result, { ok: true, child_count: 1, remaining: 0 });
  assert.deepEqual(child.kills, ['SIGTERM']);
});


test('shutdownAndWait falls back to SIGKILL when child ignores SIGTERM', async () => {
  const { audio } = harness();
  // Timeout stages need real timers: the harness fake clock only fires on
  // manual advance, which cannot interleave with the awaited shutdown.
  audio.setTimeoutFn = setTimeout;
  audio.clearTimeoutFn = clearTimeout;
  const child = new ExitOnSigkillChild();
  audio.bgmChild = child;
  audio.activeBgmMode = 'initial';

  const result = await audio.shutdownAndWait(20, 50);
  assert.equal(result.ok, true);
  assert.equal(result.child_count, 1);
  assert.equal(result.remaining, 0);
  assert.ok(child.kills.includes('SIGTERM'));
  assert.ok(child.kills.includes('SIGKILL'), `expected SIGKILL fallback, got ${JSON.stringify(child.kills)}`);
});


test('shutdownAndWait reports failure when child never exits', async () => {
  const { audio } = harness();
  audio.setTimeoutFn = setTimeout;
  audio.clearTimeoutFn = clearTimeout;
  const child = new NeverExitChild();
  audio.bgmChild = child;
  audio.activeBgmMode = 'initial';

  const result = await audio.shutdownAndWait(20, 20);
  assert.deepEqual(result, { ok: false, child_count: 1, remaining: 1 });
  assert.ok(child.kills.includes('SIGTERM'));
  assert.ok(child.kills.includes('SIGKILL'), `expected SIGKILL attempt, got ${JSON.stringify(child.kills)}`);
});


test('periodic health check re-ensures BGM when it silently disappears', () => {
  const { audio, clock, spawns, logs } = harness({ bgmHealthIntervalMs: 30000 });
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  assert.equal(spawns.length, 1);

  // BGM child vanishes without an exit event (e.g., SIGKILL that orphaned the
  // reference). The periodic check must detect the gap and respawn.
  audio.bgmChild = null;
  audio.activeBgmMode = null;
  clock.advance(29999);
  assert.equal(spawns.length, 1, 'health check must respect its interval');
  clock.advance(1);
  assert.equal(spawns.length, 2, 'health check must respawn missing BGM');
  assert.ok(logs.some((line) => line.includes('BGM health check')));
  assert.equal(spawns.at(-1).file, '/audio/International.ogg');

  // With BGM alive, the health check stays quiet and does not duplicate.
  const before = spawns.length;
  clock.advance(60000);
  assert.equal(spawns.length, before, 'healthy BGM must not be duplicated');
});


test('parsePactlSinkInputs reads mute, names, and pid per sink-input', () => {
  const output = [
    'Sink Input #12',
    '\tMute: yes',
    '\tProperties:',
    '\t\tapplication.name = "ffplay"',
    '\t\tapplication.process.id = "4242"',
    '\t\tmedia.name = "old-stream"',
    'Sink Input #13',
    '\tMute: no',
    '\tProperties:',
    '\t\tapplication.name = "soren-game-bgm"',
    '\t\tapplication.process.id = "4343"',
    '\t\tmedia.name = "soren-game-bgm"',
  ].join('\n');
  assert.deepEqual(parsePactlSinkInputs(output), [
    { index: 12, mute: true, appName: 'ffplay', mediaName: 'old-stream', pid: 4242 },
    { index: 13, mute: false, appName: 'soren-game-bgm', mediaName: 'soren-game-bgm', pid: 4343 },
  ]);
  assert.deepEqual(parsePactlSinkInputs('garbage without sink inputs'), []);
  assert.deepEqual(parsePactlSinkInputs(''), []);
});


test('looping BGM carries a fixed PulseAudio stream identity', () => {
  const { audio, spawns } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });

  const bgmEnv = spawns[0].options.env;
  assert.equal(bgmEnv['PULSE_PROP_application.name'], 'soren-game-bgm');
  assert.equal(bgmEnv['PULSE_PROP_media.name'], 'soren-game-bgm');
  assert.equal(bgmEnv['PULSE_PROP_media.role'], 'music');

  // One-shot SE keeps the shared environment unchanged.
  audio.playDrop();
  const seEnv = spawns.at(-1).options.env;
  assert.equal(seEnv['PULSE_PROP_application.name'], undefined);
  assert.equal(seEnv['PULSE_PROP_media.name'], undefined);
});


test('BGM startup unmutes a stream-restore-muted sink-input and pins volume', () => {
  const { audio, clock, spawns, pactlCalls, setPactlListOutput } = harness();
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  const pid = spawns[0].child.pid;
  setPactlListOutput(sinkInputList([
    { index: 7, mute: true, appName: 'soren-game-bgm', pid, mediaName: 'soren-game-bgm' },
    { index: 9, mute: true, appName: 'other-app', pid: 9999, mediaName: 'other' },
  ]));

  clock.advance(1500);

  const mutes = pactlCalls.filter((args) => args[0] === 'set-sink-input-mute');
  assert.ok(mutes.some((args) => args[1] === '7' && args[2] === '0'), 'muted BGM sink-input must be unmuted');
  assert.ok(!mutes.some((args) => args[1] === '9'), 'unrelated sink-inputs must not be touched');
  const volumes = pactlCalls.filter((args) => args[0] === 'set-sink-input-volume');
  assert.ok(volumes.some((args) => args[1] === '7' && args[2] === '60%'), 'BGM volume must be pinned at startup');
  assert.ok(!volumes.some((args) => args[1] === '9'), 'unrelated sink-inputs must not be touched');
  assert.equal(spawns.length, 1, 'audible repair must not respawn BGM');
});


test('health check unmutes a BGM stream muted after startup without respawning', () => {
  const { audio, clock, spawns, pactlCalls, setPactlListOutput, logs } = harness({ bgmHealthIntervalMs: 30000 });
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  clock.advance(1500);
  pactlCalls.length = 0;

  // module-stream-restore mutes the running BGM stream after startup (#493).
  const pid = spawns[0].child.pid;
  setPactlListOutput(sinkInputList([
    { index: 7, mute: true, appName: 'soren-game-bgm', pid, mediaName: 'soren-game-bgm' },
  ]));
  clock.advance(28500);

  const mutes = pactlCalls.filter((args) => args[0] === 'set-sink-input-mute');
  assert.ok(mutes.some((args) => args[1] === '7' && args[2] === '0'), 'health check must unmute the BGM sink-input');
  assert.ok(!pactlCalls.some((args) => args[0] === 'set-sink-input-volume'), 'health check only repairs mute');
  assert.equal(spawns.length, 1, 'alive-but-muted BGM must not be respawned');
  assert.ok(logs.some((line) => line.includes('PulseAudio-muted')), 'unmute repair must be logged');
});


test('health check leaves an unmuted BGM stream alone', () => {
  const { audio, clock, spawns, pactlCalls, setPactlListOutput } = harness({ bgmHealthIntervalMs: 30000 });
  audio.start({ state: 'MOVE', score: 0, makeSorenCount: 0 });
  const pid = spawns[0].child.pid;
  setPactlListOutput(sinkInputList([
    { index: 7, mute: false, appName: 'soren-game-bgm', pid, mediaName: 'soren-game-bgm' },
  ]));
  clock.advance(30000);

  const mutes = pactlCalls.filter((args) => args[0] === 'set-sink-input-mute');
  // Exactly one explicit pin comes from the startup check; the health check
  // itself must not issue further mute commands for an audible stream.
  assert.equal(mutes.length, 1, `expected only the startup pin, got ${JSON.stringify(mutes)}`);
  assert.equal(spawns.length, 1, 'audible BGM must not be respawned');
});

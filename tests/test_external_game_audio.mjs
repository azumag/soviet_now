import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import test from 'node:test';

import { ExternalGameAudio, loadExternalGameAudioConfig } from '../external_game_audio.mjs';


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
    spawnFn(command, args, options) {
      const child = new FakeChild();
      spawns.push({ command, args, options, child, file: args.at(-1) });
      return child;
    },
    logger: {
      log(message) { logs.push(String(message)); },
      warn(message) { logs.push(`WARN:${message}`); },
    },
  });
  return { audio, clock, spawns, logs };
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
  assert.equal(clock.pending(), 3, 'hammer + soviet BGM timers plus the BGM health interval');
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

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
    advance(ms) {
      const target = now + ms;
      while (true) {
        const next = [...tasks.keys()]
          .filter((timer) => timer.due <= target)
          .sort((a, b) => a.due - b.due || a.id - b.id)[0];
        if (!next) break;
        now = next.due;
        const action = tasks.get(next);
        tasks.delete(next);
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
    pulseLatencyMs: 350,
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
  assert.equal(spawns[0].options.env.PULSE_LATENCY_MSEC, '350');
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
  assert.equal(clock.pending(), 2);
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

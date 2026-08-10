import fs from 'node:fs';
import { spawn } from 'node:child_process';


function clampInteger(raw, fallback, min, max) {
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

export function loadExternalGameAudioConfig(env = process.env, platform = process.platform) {
  const initialBgmFile = env.SOREN_GAME_BGM_INITIAL_FILE || env.SOREN_GAME_BGM_FILE || '';
  return {
    enabled: platform === 'linux' && Boolean(initialBgmFile),
    initialBgmFile,
    sovietBgmFile: env.SOREN_GAME_BGM_SOVIET_FILE || '',
    dropSeFile: env.SOREN_GAME_SE_DROP_FILE || '',
    mergeSeFile: env.SOREN_GAME_SE_MERGE_FILE || '',
    russiaSeFile: env.SOREN_GAME_SE_RUSSIA_FILE || '',
    hammerSickleSeFile: env.SOREN_GAME_SE_HAMMER_SICKLE_FILE || '',
    bgmVolumePct: clampInteger(env.SOREN_GAME_BGM_VOLUME_PCT, 60, 0, 100),
    seVolumePct: clampInteger(env.SOREN_GAME_SE_VOLUME_PCT, 70, 0, 100),
    pulseLatencyMs: clampInteger(env.SOREN_GAME_AUDIO_PULSE_LATENCY_MS, 350, 50, 2000),
    hammerSickleDelayMs: clampInteger(env.SOREN_GAME_SE_HAMMER_SICKLE_DELAY_MS, 1000, 0, 10000),
    sovietBgmDelayMs: clampInteger(env.SOREN_GAME_BGM_SOVIET_DELAY_MS, 5250, 0, 15000),
  };
}

function stateNumber(state, key) {
  const value = Number(state && state[key]);
  return Number.isFinite(value) ? value : 0;
}

export class ExternalGameAudio {
  constructor(config, dependencies = {}) {
    this.config = config;
    this.spawnFn = dependencies.spawnFn || spawn;
    this.fileExists = dependencies.fileExists || fs.existsSync;
    this.setTimeoutFn = dependencies.setTimeoutFn || setTimeout;
    this.clearTimeoutFn = dependencies.clearTimeoutFn || clearTimeout;
    this.nowFn = dependencies.nowFn || Date.now;
    this.logger = dependencies.logger || console;

    this.bgmChild = null;
    this.seChildren = new Set();
    this.timers = new Set();
    this.activeBgmMode = null;
    this.desiredBgmMode = 'initial';
    this.lastState = null;
    this.muted = false;
    this.stopped = false;
    this.suppressMergeScoreUntil = 0;
  }

  isEnabled() {
    return Boolean(this.config && this.config.enabled);
  }

  _audioEnvironment() {
    return {
      ...process.env,
      SDL_AUDIODRIVER: 'pulse',
      PULSE_LATENCY_MSEC: String(this.config.pulseLatencyMs),
    };
  }

  _bgmFile(mode) {
    if (mode === 'soviet') return this.config.sovietBgmFile || this.config.initialBgmFile;
    return this.config.initialBgmFile;
  }

  _spawnFfplay(filePath, { loop, volume, label }) {
    if (!filePath) return null;
    if (!this.fileExists(filePath)) {
      this.logger.warn(`[GAME-AUDIO] ${label} file not found: ${filePath}`);
      return null;
    }
    const args = [
      '-nodisp', '-nostats', '-loglevel', 'quiet',
      '-threads', '1',
      '-volume', String(volume),
    ];
    if (loop) args.push('-loop', '0');
    else args.push('-autoexit');
    args.push(filePath);

    try {
      return this.spawnFn('ffplay', args, {
        stdio: 'ignore',
        env: this._audioEnvironment(),
      });
    } catch (error) {
      this.logger.warn(`[GAME-AUDIO] ${label} start failed: ${error && error.message}`);
      return null;
    }
  }

  _stopBgm() {
    const child = this.bgmChild;
    this.bgmChild = null;
    this.activeBgmMode = null;
    if (child) {
      try { child.kill('SIGTERM'); } catch {}
    }
  }

  _ensureBgm(mode = this.desiredBgmMode) {
    this.desiredBgmMode = mode;
    if (!this.isEnabled() || this.muted || this.stopped) return;
    const filePath = this._bgmFile(mode);
    if (!filePath) return;
    if (this.bgmChild && this.activeBgmMode === mode) return;

    this._stopBgm();
    const child = this._spawnFfplay(filePath, {
      loop: true,
      volume: this.config.bgmVolumePct,
      label: `BGM:${mode}`,
    });
    if (!child) return;
    this.bgmChild = child;
    this.activeBgmMode = mode;
    child.on?.('error', (error) => {
      if (this.bgmChild !== child) return;
      this.logger.warn(`[GAME-AUDIO] BGM:${mode} ffplay error: ${error && error.message}`);
      this.bgmChild = null;
      this.activeBgmMode = null;
    });
    child.on?.('exit', (code, signal) => {
      if (this.bgmChild !== child) return;
      this.bgmChild = null;
      this.activeBgmMode = null;
      if (!this.stopped && !this.muted && code !== 0 && code !== null) {
        this.logger.warn(`[GAME-AUDIO] BGM:${mode} exited code=${code} signal=${signal}`);
      }
    });
    this.logger.log(`[GAME-AUDIO] BGM:${mode} started: ${filePath} (vol=${this.config.bgmVolumePct}%, pulse=${this.config.pulseLatencyMs}ms)`);
  }

  _playSe(filePath, label) {
    if (!this.isEnabled() || this.muted || this.stopped || !filePath) return;
    const child = this._spawnFfplay(filePath, {
      loop: false,
      volume: this.config.seVolumePct,
      label: `SE:${label}`,
    });
    if (!child) return;
    this.seChildren.add(child);
    const forget = () => this.seChildren.delete(child);
    child.on?.('error', (error) => {
      this.logger.warn(`[GAME-AUDIO] SE:${label} ffplay error: ${error && error.message}`);
      forget();
    });
    child.on?.('exit', forget);
    this.logger.log(`[GAME-AUDIO] SE:${label}`);
  }

  _schedule(delayMs, action) {
    let timer = null;
    timer = this.setTimeoutFn(() => {
      this.timers.delete(timer);
      if (!this.stopped) action();
    }, delayMs);
    this.timers.add(timer);
    return timer;
  }

  _clearTimers() {
    for (const timer of this.timers) this.clearTimeoutFn(timer);
    this.timers.clear();
  }

  start(initialState = null) {
    if (!this.isEnabled()) return;
    this.stopped = false;
    if (initialState) {
      this.lastState = initialState;
      this.desiredBgmMode = stateNumber(initialState, 'makeSorenCount') > 0 ? 'soviet' : 'initial';
    }
    this._ensureBgm(this.desiredBgmMode);
  }

  setMuted(muted) {
    const next = Boolean(muted);
    if (this.muted === next) return;
    this.muted = next;
    if (next) {
      this._clearTimers();
      this._stopBgm();
      for (const child of this.seChildren) {
        try { child.kill('SIGTERM'); } catch {}
      }
      this.seChildren.clear();
      return;
    }
    this._ensureBgm(this.desiredBgmMode);
  }

  playDrop() {
    this._playSe(this.config.dropSeFile, 'drop');
  }

  resetForNewGame() {
    this._clearTimers();
    this.suppressMergeScoreUntil = 0;
    this.desiredBgmMode = 'initial';
    this.lastState = null;
    this._ensureBgm('initial');
  }

  observeState(state) {
    if (!this.isEnabled() || !state) return;
    const previous = this.lastState;
    this.lastState = state;

    const currentSovietCount = stateNumber(state, 'makeSorenCount');
    if (!previous) {
      this.desiredBgmMode = currentSovietCount > 0 ? 'soviet' : 'initial';
      this._ensureBgm(this.desiredBgmMode);
      return;
    }

    const previousSovietCount = stateNumber(previous, 'makeSorenCount');
    if (currentSovietCount < previousSovietCount || (currentSovietCount === 0 && previousSovietCount > 0)) {
      this.resetForNewGame();
      this.lastState = state;
      return;
    }

    if (currentSovietCount > previousSovietCount) {
      this.desiredBgmMode = 'soviet';
      this.suppressMergeScoreUntil = this.nowFn() + Math.max(8000, this.config.sovietBgmDelayMs + 2000);
      this._playSe(this.config.russiaSeFile, 'russia-merge');
      this._schedule(this.config.hammerSickleDelayMs, () => {
        this._playSe(this.config.hammerSickleSeFile, 'hammer-sickle');
      });
      this._schedule(this.config.sovietBgmDelayMs, () => {
        this._ensureBgm('soviet');
      });
      return;
    }

    const scoreIncreased = stateNumber(state, 'score') > stateNumber(previous, 'score');
    if (scoreIncreased && this.nowFn() >= this.suppressMergeScoreUntil) {
      this._playSe(this.config.mergeSeFile, 'merge');
    }
  }

  shutdown() {
    this.stopped = true;
    this._clearTimers();
    this._stopBgm();
    for (const child of this.seChildren) {
      try { child.kill('SIGTERM'); } catch {}
    }
    this.seChildren.clear();
  }
}

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
    pulseLatencyMs: clampInteger(env.SOREN_GAME_AUDIO_PULSE_LATENCY_MS, 100, 50, 2000),
    hammerSickleDelayMs: clampInteger(env.SOREN_GAME_SE_HAMMER_SICKLE_DELAY_MS, 1000, 0, 10000),
    sovietBgmDelayMs: clampInteger(env.SOREN_GAME_BGM_SOVIET_DELAY_MS, 5250, 0, 15000),
    bgmRestartDelayMs: clampInteger(env.SOREN_GAME_BGM_RESTART_DELAY_MS, 2000, 500, 30000),
    bgmHealthIntervalMs: clampInteger(env.SOREN_GAME_BGM_HEALTH_INTERVAL_MS, 30000, 5000, 300000),
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
    this.setIntervalFn = dependencies.setIntervalFn || setInterval;
    this.clearIntervalFn = dependencies.clearIntervalFn || clearInterval;
    this.nowFn = dependencies.nowFn || Date.now;
    this.logger = dependencies.logger || console;

    this.bgmChild = null;
    this.bgmRestartTimer = null;
    this.bgmRestartBackoff = 1;
    this.bgmRestartDelayMs = this.config.bgmRestartDelayMs ?? 2000;
    this.bgmHealthIntervalMs = this.config.bgmHealthIntervalMs ?? 30000;
    this.bgmHealthTimer = null;
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
    // WAV の SE は paplay を使う。ffplay は起動が遅く、さらに
    // `-fflags nobuffer` と `-autoexit` の併用では短い SE が無音になるため、
    // ループ再生の BGM 以外では nobuffer を付けない。
    if (!loop && filePath.toLowerCase().endsWith('.wav')) {
      const volumeLinear = String(Math.round(Math.min(1, Math.max(0, volume / 100)) * 65536));
      try {
        return this.spawnFn('paplay', [
          '--device=@DEFAULT_SINK@',
          `--volume=${volumeLinear}`,
          filePath,
        ], {
          stdio: 'ignore',
          env: this._audioEnvironment(),
        });
      } catch (error) {
        this.logger.warn(`[GAME-AUDIO] ${label} paplay start failed: ${error && error.message}`);
        return null;
      }
    }
    const args = [
      '-nodisp', '-nostats', '-loglevel', 'quiet',
      '-threads', '1',
      '-volume', String(volume),
    ];
    if (loop) args.splice(4, 0, '-fflags', 'nobuffer');
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
    this._clearBgmRestart();
    if (child) {
      try { child.kill('SIGTERM'); } catch {}
    }
  }

  _clearBgmRestart() {
    if (this.bgmRestartTimer) {
      this.clearTimeoutFn(this.bgmRestartTimer);
      this.bgmRestartTimer = null;
    }
  }

  _startBgmHealthTimer() {
    this._stopBgmHealthTimer();
    if (!this.isEnabled()) return;
    this.bgmHealthTimer = this.setIntervalFn(() => {
      if (this.stopped || this.muted) return;
      if (!this.bgmChild && !this.bgmRestartTimer) {
        this.logger.warn('[GAME-AUDIO] BGM health check: missing, re-ensuring');
        this._ensureBgm(this.desiredBgmMode);
      }
    }, this.bgmHealthIntervalMs);
  }

  _stopBgmHealthTimer() {
    if (this.bgmHealthTimer) {
      this.clearIntervalFn(this.bgmHealthTimer);
      this.bgmHealthTimer = null;
    }
  }

  _scheduleBgmRestart() {
    if (this.stopped || this.muted || this.bgmRestartTimer) return;
    const delay = this.bgmRestartDelayMs * this.bgmRestartBackoff;
    this.bgmRestartBackoff = Math.min(this.bgmRestartBackoff * 2, 16);
    this.logger.warn(`[GAME-AUDIO] BGM unexpected exit; restart in ${delay}ms`);
    this.bgmRestartTimer = this.setTimeoutFn(() => {
      this.bgmRestartTimer = null;
      if (this.stopped || this.muted) return;
      this._ensureBgm(this.desiredBgmMode);
    }, delay);
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
    this.bgmRestartBackoff = 1;
    child.on?.('error', (error) => {
      if (this.bgmChild !== child) return;
      this.logger.warn(`[GAME-AUDIO] BGM:${mode} ffplay error: ${error && error.message}`);
      this.bgmChild = null;
      this.activeBgmMode = null;
      this._scheduleBgmRestart();
    });
    child.on?.('exit', (code, signal) => {
      if (this.bgmChild !== child) return;
      this.bgmChild = null;
      this.activeBgmMode = null;
      if (!this.stopped && !this.muted) this._scheduleBgmRestart();
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
    this.bgmRestartBackoff = 1;
    this._clearBgmRestart();
    this._startBgmHealthTimer();
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
      this._clearBgmRestart();
      this._stopBgmHealthTimer();
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
    this._clearBgmRestart();
    this._stopBgmHealthTimer();
    this._stopBgm();
    for (const child of this.seChildren) {
      try { child.kill('SIGTERM'); } catch {}
    }
    this.seChildren.clear();
  }

  _waitForChildExit(child, timeoutMs) {
    if (!child || child.exitCode != null || child.signalCode != null) {
      return Promise.resolve(true);
    }
    const waitMs = Math.max(0, Number(timeoutMs) || 0);
    return new Promise((resolve) => {
      let settled = false;
      let timer = null;
      const finish = (exited) => {
        if (settled) return;
        settled = true;
        if (timer) this.clearTimeoutFn(timer);
        for (const event of ['exit', 'close', 'error']) {
          try { child.removeListener?.(event, onEvent); } catch {}
        }
        resolve(Boolean(exited));
      };
      const onEvent = () => finish(true);
      for (const event of ['exit', 'close', 'error']) {
        try { child.once?.(event, onEvent); } catch {}
      }
      timer = this.setTimeoutFn(() => finish(false), waitMs);
    });
  }

  // Stop every game-owned audio child and wait for the OS child lifecycle
  // event before the game resource is acknowledged as stopped.  The final
  // SIGKILL is scoped to the exact child objects captured above; it never
  // scans or signals unrelated audio workers.
  async shutdownAndWait(timeoutMs = 2000, forceTimeoutMs = 500) {
    const children = [...new Set([
      this.bgmChild,
      ...this.seChildren,
    ].filter(Boolean))];
    const firstWaits = children.map((child) => this._waitForChildExit(child, timeoutMs));
    this.shutdown();
    const firstResults = await Promise.all(firstWaits);
    const remaining = children.filter((_child, index) => !firstResults[index]);
    if (!remaining.length) {
      return { ok: true, child_count: children.length, remaining: 0 };
    }

    const forceWaits = remaining.map((child) => {
      const wait = this._waitForChildExit(child, forceTimeoutMs);
      try { child.kill('SIGKILL'); } catch {}
      return wait;
    });
    const forceResults = await Promise.all(forceWaits);
    const stillAlive = forceResults.filter((exited) => !exited).length;
    return {
      ok: stillAlive === 0,
      child_count: children.length,
      remaining: stillAlive,
    };
  }
}

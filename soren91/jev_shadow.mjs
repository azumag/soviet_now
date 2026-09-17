#!/usr/bin/env node
/**
 * TypeSafe Jev shadow evaluator for Soren91.
 *
 * This module is intentionally outside strategy.mjs. It tails the existing
 * turn ledger and evaluates decisions asynchronously, so network latency,
 * quota errors, or provider outages can never block gameplay.
 */

import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_BASE_URL = 'https://api.typesafe.ai';
const DEFAULT_MODEL = 'jev-latest';
const DEFAULT_TIMEOUT_MS = 800;
const DEFAULT_POLL_MS = 250;
const DEFAULT_MAX_QUEUE = 4;
const DEFAULT_MAX_INFLIGHT = 1;
const DEFAULT_RATE_LIMIT_COOLDOWN_MS = 60_000;
const SCHEMA_VERSION = 1;

function boolEnv(value, fallback = false) {
  if (value == null || value === '') return fallback;
  return /^(1|true|yes|on)$/i.test(String(value));
}

function positiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback;
}

function fraction(value, fallback) {
  const parsed = Number.parseFloat(String(value ?? ''));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(0, Math.min(1, parsed));
}

export function loadJevShadowConfig(env = process.env) {
  return {
    enabled: boolEnv(env.SOREN91_JEV_SHADOW_ENABLED, false),
    apiKey: String(env.TYPESAFE_API_KEY || '').trim(),
    baseURL: String(env.TYPESAFE_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, ''),
    model: String(env.SOREN91_JEV_MODEL || env.TYPESAFE_DEFAULT_MODEL || DEFAULT_MODEL),
    timeoutMs: positiveInt(env.SOREN91_JEV_TIMEOUT_MS, DEFAULT_TIMEOUT_MS),
    pollMs: positiveInt(env.SOREN91_JEV_POLL_MS, DEFAULT_POLL_MS),
    maxQueue: positiveInt(env.SOREN91_JEV_MAX_QUEUE, DEFAULT_MAX_QUEUE),
    maxInflight: positiveInt(env.SOREN91_JEV_MAX_INFLIGHT, DEFAULT_MAX_INFLIGHT),
    sampleRate: fraction(env.SOREN91_JEV_SAMPLE_RATE, 1),
    // Backfill means "read the active latest_*.jsonl from offset zero on sidecar
    // startup". Finalized historical games are never bulk-enqueued implicitly.
    backfill: boolEnv(env.SOREN91_JEV_BACKFILL, false),
    rateLimitCooldownMs: positiveInt(
      env.SOREN91_JEV_RATE_LIMIT_COOLDOWN_MS,
      DEFAULT_RATE_LIMIT_COOLDOWN_MS,
    ),
  };
}

function finiteOrNull(value, digits = 3) {
  if (!Number.isFinite(value)) return null;
  const scale = 10 ** digits;
  return Math.round(value * scale) / scale;
}

function compactPiece(piece, positioned = true) {
  if (!piece || !Number.isInteger(piece.type)) return null;
  const out = { type: piece.type };
  if (positioned) {
    if (Number.isFinite(piece.x)) out.x = finiteOrNull(piece.x);
    if (Number.isFinite(piece.y)) out.y = finiteOrNull(piece.y);
    if (Number.isFinite(piece.r)) out.r = finiteOrNull(piece.r);
  }
  if (Number.isFinite(piece.confidence)) out.confidence = finiteOrNull(piece.confidence);
  if (piece.fallback === true) out.fallback = true;
  return out;
}

function compactGarbage(garbage) {
  const columns = Array.isArray(garbage?.columns)
    ? garbage.columns.slice(0, 32).map(column => ({
        left: finiteOrNull(column?.left),
        right: finiteOrNull(column?.right),
        top: finiteOrNull(column?.top),
      })).filter(column => Object.values(column).every(value => value != null))
    : [];
  return {
    ratio: finiteOrNull(garbage?.ratio) ?? 0,
    gauge: finiteOrNull(garbage?.gauge) ?? 0,
    columns,
  };
}

function compactDiagnostics(diagnostics) {
  if (!diagnostics || typeof diagnostics !== 'object') return {};
  const numericKeys = [
    'risk', 'pathRisk', 'clearance', 'minFutureClearance', 'landingY', 'merges',
    'heuristicValue', 'searchDepth', 'expandedNodes', 'pairPotential', 'roughness',
    'pocketPenalty', 'knownMergeReservations', 'preservedReservations',
    'lostReservations', 'reservationDepth', 'reservationType',
    'reservationBeforeMergeValue',
  ];
  const out = {};
  for (const key of numericKeys) {
    if (Number.isFinite(diagnostics[key])) out[key] = finiteOrNull(diagnostics[key]);
  }
  if (typeof diagnostics.version === 'string') out.version = diagnostics.version.slice(0, 64);
  if (typeof diagnostics.reservationStatus === 'string') {
    out.reservationStatus = diagnostics.reservationStatus.slice(0, 32);
  }
  return out;
}

export function buildJevState(record, gameNumber) {
  const state = record?.state || {};
  const decision = record?.decision || {};
  return {
    game: Number.isInteger(gameNumber) ? gameNumber : null,
    turn: Number.isInteger(record?.turn) ? record.turn : null,
    pieces: Array.isArray(state.pieces)
      ? state.pieces.slice(0, 256).map(piece => compactPiece(piece, true)).filter(Boolean)
      : [],
    next: compactPiece(state.next, false),
    nextPieces: Array.isArray(state.nextPieces)
      ? state.nextPieces.slice(0, 3).map(piece => compactPiece(piece, false)).filter(Boolean)
      : [],
    hold: compactPiece(state.hold, false),
    holdKnownEmpty: state.holdKnownEmpty === true,
    canHold: state.canHold === true,
    garbage: compactGarbage(state.garbage),
    perception: {
      confidence: finiteOrNull(state.confidence),
    },
    strategyDecision: {
      x: finiteOrNull(decision.x),
      hold: decision.hold === true,
      diagnostics: compactDiagnostics(decision.diagnostics),
    },
  };
}

export function buildJevRequest(record, gameNumber, model = DEFAULT_MODEL) {
  return {
    model,
    state: buildJevState(record, gameNumber),
    questions: {
      decision_quality: {
        type: 'choice',
        instructions: 'Evaluate the current Soren91 strategy decision using the observed board, known next queue, hold availability, safety margin, and near-term merge opportunities.',
        criteria: {
          accept: 'The decision is strategically reasonable and does not need special review.',
          review: 'The decision is plausible but contains a meaningful tradeoff or uncertainty worth reviewing.',
          reject: 'The decision appears to make an avoidable strategic mistake given the supplied state.',
        },
      },
      destroys_near_term_merge: {
        type: 'noul',
        instructions: 'Does the current decision unnecessarily destroy an obvious merge opportunity involving the known next or nextNext pieces and an already visible board piece?',
      },
      survival_override_justified: {
        type: 'noul',
        instructions: 'If this decision sacrifices a merge opportunity, is that sacrifice justified by immediate survival, deadline clearance, or another clearly more urgent safety constraint?',
      },
      hold_preferred: {
        type: 'noul',
        instructions: 'When hold is available, is using hold preferable to the current drop decision for this position?',
      },
      strategic_risk: {
        type: 'score',
        instructions: 'Rate the strategic risk of the current decision. This is a review/triage signal, not a request to generate a replacement x coordinate.',
        criteria: [
          'No clear strategic problem.',
          'Minor concern only.',
          'Meaningful uncertainty; review may help.',
          'High-risk strategic mistake candidate.',
          'Clear avoidable strategic mistake candidate.',
        ],
      },
    },
  };
}

function inUnitInterval(value) {
  return Number.isFinite(value) && value >= 0 && value <= 1;
}

function validateProbabilityMap(value, expectedKeys = null) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const keys = Object.keys(value);
  if (expectedKeys && expectedKeys.some(key => !keys.includes(key))) return false;
  return keys.length > 0 && keys.every(key => inUnitInterval(value[key]));
}

export function validateJevResponse(payload) {
  if (!payload || typeof payload !== 'object' || typeof payload.model !== 'string') return false;
  const answers = payload.answers;
  if (!answers || typeof answers !== 'object') return false;

  const quality = answers.decision_quality;
  if (!quality || quality.type !== 'choice') return false;
  if (!['accept', 'review', 'reject'].includes(quality.choice)) return false;
  if (!inUnitInterval(quality.confidence)) return false;
  if (!validateProbabilityMap(quality.probabilities, ['accept', 'review', 'reject'])) return false;

  for (const name of ['destroys_near_term_merge', 'survival_override_justified', 'hold_preferred']) {
    const answer = answers[name];
    if (!answer || answer.type !== 'noul' || !inUnitInterval(answer.noul)) return false;
  }

  const risk = answers.strategic_risk;
  if (!risk || risk.type !== 'score') return false;
  if (!Number.isFinite(risk.score) || risk.score < 0 || risk.score > 4) return false;
  if (!inUnitInterval(risk.confidence)) return false;
  if (!validateProbabilityMap(risk.probabilities)) return false;

  const usage = payload.usage;
  if (!usage || !Number.isFinite(usage.input_tokens) || usage.input_tokens < 0) return false;
  if (!Number.isFinite(usage.output_tokens) || usage.output_tokens < 0) return false;
  return true;
}

function safeStrategySummary(record) {
  return {
    x: finiteOrNull(record?.decision?.x),
    hold: record?.decision?.hold === true,
    reservationStatus: typeof record?.decision?.diagnostics?.reservationStatus === 'string'
      ? record.decision.diagnostics.reservationStatus.slice(0, 32)
      : null,
  };
}

function statusForHttp(status) {
  if (status === 401 || status === 403) return 'auth_error';
  if (status === 429) return 'rate_limited';
  if (status >= 500) return 'provider_error';
  return 'http_error';
}

export async function evaluateJevTurn({
  record,
  gameNumber,
  config = loadJevShadowConfig(),
  fetchImpl = globalThis.fetch,
  now = Date.now,
}) {
  const started = now();
  const base = {
    schemaVersion: SCHEMA_VERSION,
    game: gameNumber,
    turn: Number.isInteger(record?.turn) ? record.turn : null,
    timestamp: new Date().toISOString(),
    strategy: safeStrategySummary(record),
  };

  if (!config.enabled) return { ...base, status: 'disabled', latencyMs: 0 };
  if (!config.apiKey) return { ...base, status: 'missing_api_key', latencyMs: 0 };
  if (typeof fetchImpl !== 'function') return { ...base, status: 'fetch_unavailable', latencyMs: 0 };

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);
  try {
    const response = await fetchImpl(`${config.baseURL}/v1/systemone`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${config.apiKey}`,
        Accept: 'application/json',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(buildJevRequest(record, gameNumber, config.model)),
      signal: controller.signal,
    });
    const latencyMs = Math.max(0, now() - started);
    if (!response?.ok) {
      return {
        ...base,
        status: statusForHttp(Number(response?.status) || 0),
        httpStatus: Number(response?.status) || null,
        latencyMs,
      };
    }

    let payload;
    try {
      payload = await response.json();
    } catch {
      return { ...base, status: 'invalid_response', latencyMs };
    }
    if (!validateJevResponse(payload)) {
      return { ...base, status: 'invalid_response', latencyMs };
    }
    return {
      ...base,
      status: 'ok',
      latencyMs,
      model: payload.model,
      answers: payload.answers,
      usage: {
        input_tokens: payload.usage.input_tokens,
        output_tokens: payload.usage.output_tokens,
      },
    };
  } catch (error) {
    const latencyMs = Math.max(0, now() - started);
    if (controller.signal.aborted || error?.name === 'AbortError') {
      return { ...base, status: 'timeout', latencyMs };
    }
    return { ...base, status: 'network_error', latencyMs };
  } finally {
    clearTimeout(timer);
  }
}

export function shouldSample(sampleRate, random = Math.random) {
  if (sampleRate <= 0) return false;
  if (sampleRate >= 1) return true;
  return random() < sampleRate;
}

function gameNumberFromHistoryFilename(filename) {
  const match = filename.match(/^latest_(\d+)\.jsonl$/u) || filename.match(/^game_(\d+)\.jsonl$/u);
  return match ? Number.parseInt(match[1], 10) : null;
}

function listActiveHistoryFiles(historyDir) {
  if (!existsSync(historyDir)) return [];
  return readdirSync(historyDir)
    .filter(name => /^latest_\d+\.jsonl$/u.test(name))
    .sort();
}

function finalizedHistoryPath(historyDir, gameNumber) {
  return join(historyDir, `game_${String(gameNumber).padStart(4, '0')}.jsonl`);
}

function readCompleteLines(path, offset) {
  const buf = readFileSync(path);
  if (offset > buf.length) offset = 0;
  const chunk = buf.subarray(offset);
  const lastNewline = chunk.lastIndexOf(0x0a);
  if (lastNewline < 0) return { lines: [], nextOffset: offset };
  const complete = chunk.subarray(0, lastNewline + 1).toString('utf8');
  return {
    lines: complete.split('\n').filter(Boolean),
    nextOffset: offset + lastNewline + 1,
  };
}

function appendShadowRecord(shadowDir, gameNumber, result) {
  mkdirSync(shadowDir, { recursive: true });
  const path = join(shadowDir, `game_${String(gameNumber).padStart(4, '0')}.jsonl`);
  appendFileSync(path, `${JSON.stringify(result)}\n`);
}

export class JevShadowQueue {
  constructor({ config, outputDir, fetchImpl = globalThis.fetch, random = Math.random, log = () => {} }) {
    this.config = config;
    this.outputDir = outputDir;
    this.fetchImpl = fetchImpl;
    this.random = random;
    this.log = log;
    this.queue = [];
    this.inflight = 0;
    this.cooldownUntil = 0;
    this.dropped = 0;
  }

  enqueue(item) {
    if (!shouldSample(this.config.sampleRate, this.random)) return false;
    if (this.queue.length >= this.config.maxQueue) {
      this.dropped += 1;
      return false;
    }
    this.queue.push(item);
    this.#pump();
    return true;
  }

  #pump() {
    while (this.inflight < this.config.maxInflight && this.queue.length > 0) {
      const item = this.queue.shift();
      if (Date.now() < this.cooldownUntil) {
        appendShadowRecord(this.outputDir, item.gameNumber, {
          schemaVersion: SCHEMA_VERSION,
          game: item.gameNumber,
          turn: Number.isInteger(item.record?.turn) ? item.record.turn : null,
          timestamp: new Date().toISOString(),
          status: 'rate_limit_cooldown',
          latencyMs: 0,
          strategy: safeStrategySummary(item.record),
        });
        continue;
      }
      this.inflight += 1;
      evaluateJevTurn({
        record: item.record,
        gameNumber: item.gameNumber,
        config: this.config,
        fetchImpl: this.fetchImpl,
      }).then(result => {
        appendShadowRecord(this.outputDir, item.gameNumber, result);
        if (result.status === 'rate_limited') {
          this.cooldownUntil = Date.now() + this.config.rateLimitCooldownMs;
        }
      }).catch(() => {
        appendShadowRecord(this.outputDir, item.gameNumber, {
          schemaVersion: SCHEMA_VERSION,
          game: item.gameNumber,
          turn: Number.isInteger(item.record?.turn) ? item.record.turn : null,
          timestamp: new Date().toISOString(),
          status: 'internal_error',
          latencyMs: 0,
          strategy: safeStrategySummary(item.record),
        });
      }).finally(() => {
        this.inflight -= 1;
        this.#pump();
      });
    }
  }
}

function enqueueHistoryBatch({ path, gameNumber, offset, queue }) {
  const batch = readCompleteLines(path, offset);
  for (const line of batch.lines) {
    let record;
    try { record = JSON.parse(line); } catch { continue; }
    if (!Number.isInteger(record?.turn) || !record?.state || !record?.decision) continue;
    queue.enqueue({ gameNumber, record });
  }
  return batch.nextOffset;
}

export async function runJevShadowFollower({
  runtimeDir = HERE,
  config = loadJevShadowConfig(),
  fetchImpl = globalThis.fetch,
  log = console.log,
  stopSignal = () => false,
} = {}) {
  if (!config.enabled) {
    log('[jev-shadow] disabled');
    return { status: 'disabled' };
  }
  if (!config.apiKey) {
    log('[jev-shadow] TYPESAFE_API_KEY missing; shadow disabled without affecting gameplay');
    return { status: 'missing_api_key' };
  }

  const historyDir = join(runtimeDir, 'game_history');
  const outputDir = join(runtimeDir, 'tmp', 'jev_shadow');
  mkdirSync(outputDir, { recursive: true });
  const queue = new JevShadowQueue({ config, outputDir, fetchImpl, log });
  // Offset is keyed by game number, not filename, so a latest_ -> game_ rename
  // can be drained without losing the final turns between polling intervals.
  const offsets = new Map();
  const trackedGames = new Set();
  const finalizedGames = new Set();
  const startupFiles = listActiveHistoryFiles(historyDir);

  for (const filename of startupFiles) {
    const gameNumber = gameNumberFromHistoryFilename(filename);
    if (!gameNumber) continue;
    const path = join(historyDir, filename);
    let size = 0;
    try { size = statSync(path).size; } catch {}
    trackedGames.add(gameNumber);
    offsets.set(gameNumber, config.backfill ? 0 : size);
  }

  log(`[jev-shadow] started model=${config.model} sampleRate=${config.sampleRate} backfill=${config.backfill ? 1 : 0}`);

  while (!stopSignal()) {
    const activeGames = new Set();
    for (const filename of listActiveHistoryFiles(historyDir)) {
      const gameNumber = gameNumberFromHistoryFilename(filename);
      if (!gameNumber) continue;
      activeGames.add(gameNumber);
      const path = join(historyDir, filename);
      if (!trackedGames.has(gameNumber)) {
        // This game was created after the sidecar started, so its complete ledger
        // belongs to this live shadow session and starts at offset zero.
        trackedGames.add(gameNumber);
        offsets.set(gameNumber, 0);
      }
      try {
        offsets.set(gameNumber, enqueueHistoryBatch({
          path,
          gameNumber,
          offset: offsets.get(gameNumber) ?? 0,
          queue,
        }));
      } catch {
        // History may be atomically renamed between readdir/stat/read. The
        // finalized path below will drain it on this or the next poll.
      }
    }

    // main.mjs atomically renames latest_XXXX.jsonl to game_XXXX.jsonl at round
    // end. Drain the finalized file once for games we observed live so the last
    // lines cannot disappear in the polling race. Untracked historical games
    // are deliberately ignored to avoid surprise backfill/cost.
    for (const gameNumber of [...trackedGames]) {
      if (activeGames.has(gameNumber) || finalizedGames.has(gameNumber)) continue;
      const finalPath = finalizedHistoryPath(historyDir, gameNumber);
      if (!existsSync(finalPath)) continue;
      try {
        offsets.set(gameNumber, enqueueHistoryBatch({
          path: finalPath,
          gameNumber,
          offset: offsets.get(gameNumber) ?? 0,
          queue,
        }));
        finalizedGames.add(gameNumber);
      } catch {
        // Retry on next poll; gameplay is independent of this sidecar.
      }
    }

    await new Promise(resolvePromise => setTimeout(resolvePromise, config.pollMs));
  }
  return { status: 'stopped', dropped: queue.dropped };
}

function parseArgs(argv) {
  const options = { runtimeDir: HERE };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--runtime-dir') {
      i += 1;
      if (i >= argv.length) throw new Error('--runtime-dir requires a value');
      options.runtimeDir = resolve(argv[i]);
    } else if (argv[i].startsWith('-')) {
      throw new Error(`unknown option: ${argv[i]}`);
    }
  }
  return options;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const options = parseArgs(process.argv.slice(2));
  let stopping = false;
  const stop = () => { stopping = true; };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  runJevShadowFollower({
    ...options,
    stopSignal: () => stopping,
  }).catch(error => {
    console.error(`[jev-shadow] fatal: ${error?.message || 'unknown error'}`);
    process.exitCode = 1;
  });
}

#!/usr/bin/env node
/**
 * Build a fail-closed evidence bundle for the Soren91 daily improvement loop.
 *
 * This module never mutates runtime data. It answers four questions before a
 * strategy model is allowed to propose a change:
 *   1. Which games are safely consumable without skipping a missing game?
 *   2. Which best/worst/latest games should receive detailed attention?
 *   3. Do those games have history, strategy snapshots and visual snapshots?
 *   4. Which turns were actually risky/uncertain enough to deserve visual review?
 */

import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
} from 'fs';
import { join, relative, resolve } from 'path';
import { fileURLToPath } from 'url';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));
const MAX_HISTORY_BYTES = 4 * 1024 * 1024;
const MAX_HISTORY_LINES = 4096;
const LOW_CONFIDENCE_THRESHOLD = 0.65;

function finiteInt(value, fallback = null) {
  const n = Number(value);
  return Number.isInteger(n) ? n : fallback;
}

function finiteNumber(value, fallback = null) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function summaryInt(value) {
  return typeof value === 'number' && Number.isInteger(value) ? value : null;
}

function gameToken(game) {
  return String(game).padStart(4, '0');
}

export function scanSummaryGames(runtimeDir) {
  const dir = join(runtimeDir, 'tmp', 'summaries');
  if (!existsSync(dir)) return [];
  return readdirSync(dir)
    .map(name => {
      const match = name.match(/^game_(\d+)\.json$/);
      return match ? Number.parseInt(match[1], 10) : null;
    })
    .filter(Number.isInteger)
    .sort((a, b) => a - b);
}

/**
 * Return only the contiguous prefix immediately after lastConsumedGame.
 *
 * The old daily runner filtered `game > lastConsumedGame` and could consume
 * game #7 while #4..#6 were still missing from a delayed mirror. Advancing
 * lastConsumedGame to 7 would then make those late files look consumed. This
 * helper refuses to jump over a hole.
 */
export function contiguousFreshGames(games, lastConsumedGame = 0) {
  const consumed = Math.max(0, finiteInt(lastConsumedGame, 0));
  const fresh = [...new Set(games.filter(Number.isInteger).filter(n => n > consumed))]
    .sort((a, b) => a - b);
  const expected = consumed + 1;
  if (fresh.length === 0) {
    return {
      games: [],
      expected,
      blockedByGap: false,
      missingGame: null,
      nextAvailableGame: null,
      deferredGames: [],
    };
  }
  if (fresh[0] !== expected) {
    return {
      games: [],
      expected,
      blockedByGap: true,
      missingGame: expected,
      nextAvailableGame: fresh[0],
      deferredGames: fresh,
    };
  }

  const available = new Set(fresh);
  const contiguous = [];
  let cursor = expected;
  while (available.has(cursor)) {
    contiguous.push(cursor);
    cursor += 1;
  }
  const deferredGames = fresh.filter(n => n >= cursor);
  return {
    games: contiguous,
    expected,
    blockedByGap: deferredGames.length > 0,
    missingGame: deferredGames.length > 0 ? cursor : null,
    nextAvailableGame: deferredGames[0] ?? null,
    deferredGames,
  };
}

function readSummary(runtimeDir, game) {
  const path = join(runtimeDir, 'tmp', 'summaries', `game_${gameToken(game)}.json`);
  try {
    const raw = JSON.parse(readFileSync(path, 'utf-8'));
    return {
      ok: true,
      path,
      raw,
      rank: summaryInt(raw?.rank),
      turns: summaryInt(raw?.turns),
      piecesAtEnd: summaryInt(raw?.piecesAtEnd),
      strategyHash: typeof raw?.strategyHash === 'string' ? raw.strategyHash : null,
      resultScreenOcr: Array.isArray(raw?.resultScreenOcr?.lines)
        ? raw.resultScreenOcr.lines.filter(line => typeof line === 'string').slice(0, 8)
        : [],
    };
  } catch (error) {
    return { ok: false, path, error: error?.message || String(error) };
  }
}

function historyPath(runtimeDir, game) {
  const token = gameToken(game);
  const archived = join(runtimeDir, 'game_history', `game_${token}.jsonl`);
  if (existsSync(archived)) return archived;
  const latest = join(runtimeDir, 'game_history', `latest_${token}.jsonl`);
  return existsSync(latest) ? latest : null;
}

function strategySnapshotPath(runtimeDir, game) {
  const path = join(runtimeDir, 'tmp', 'strategy_snapshots', `game_${gameToken(game)}_strategy.mjs`);
  return existsSync(path) ? path : null;
}

function boundedReason(value) {
  if (typeof value !== 'string') return null;
  const normalized = value.trim().slice(0, 64);
  return /^[A-Za-z0-9._:-]+$/.test(normalized) ? normalized : 'other';
}

function minFinite(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? Math.min(...xs) : null;
}

function selectCriticalTurns(records) {
  if (!records.length) return [];
  const withTurn = records.filter(record => Number.isInteger(record.turn));
  if (!withTurn.length) return [];

  const riskRecord = [...withTurn].sort((a, b) => {
    const ar = Math.max(finiteNumber(a?.decision?.diagnostics?.risk, 0), finiteNumber(a?.decision?.diagnostics?.pathRisk, 0));
    const br = Math.max(finiteNumber(b?.decision?.diagnostics?.risk, 0), finiteNumber(b?.decision?.diagnostics?.pathRisk, 0));
    const ac = minFinite([
      finiteNumber(a?.decision?.diagnostics?.clearance),
      finiteNumber(a?.decision?.diagnostics?.minFutureClearance),
    ]) ?? Infinity;
    const bc = minFinite([
      finiteNumber(b?.decision?.diagnostics?.clearance),
      finiteNumber(b?.decision?.diagnostics?.minFutureClearance),
    ]) ?? Infinity;
    return br - ar || ac - bc || b.turn - a.turn;
  })[0];

  const confidenceRecord = withTurn
    .filter(record => Number.isFinite(finiteNumber(record?.state?.confidence)))
    .sort((a, b) => finiteNumber(a.state.confidence) - finiteNumber(b.state.confidence) || b.turn - a.turn)[0] ?? null;

  const clearanceRecord = withTurn
    .filter(record => Number.isFinite(minFinite([
      finiteNumber(record?.decision?.diagnostics?.clearance),
      finiteNumber(record?.decision?.diagnostics?.minFutureClearance),
    ])))
    .sort((a, b) => {
      const ac = minFinite([
        finiteNumber(a?.decision?.diagnostics?.clearance),
        finiteNumber(a?.decision?.diagnostics?.minFutureClearance),
      ]);
      const bc = minFinite([
        finiteNumber(b?.decision?.diagnostics?.clearance),
        finiteNumber(b?.decision?.diagnostics?.minFutureClearance),
      ]);
      return ac - bc || b.turn - a.turn;
    })[0] ?? null;

  const latest = [...withTurn].sort((a, b) => b.turn - a.turn)[0];
  return [...new Set([riskRecord?.turn, confidenceRecord?.turn, clearanceRecord?.turn, latest?.turn]
    .filter(Number.isInteger))].slice(0, 3);
}

export function analyzeHistoryText(text) {
  const lines = String(text || '').split(/\r?\n/).filter(line => line.trim().length > 0);
  if (lines.length > MAX_HISTORY_LINES) {
    return { ok: false, error: `history line limit exceeded (${lines.length}>${MAX_HISTORY_LINES})` };
  }

  const records = [];
  for (let i = 0; i < lines.length; i += 1) {
    try {
      const record = JSON.parse(lines[i]);
      if (!record || typeof record !== 'object' || Array.isArray(record)) {
        return { ok: false, error: `history line ${i + 1} is not an object` };
      }
      records.push(record);
    } catch {
      return { ok: false, error: `history line ${i + 1} is invalid JSON` };
    }
  }

  const confidences = [];
  const clearances = [];
  const futureClearances = [];
  const perceptionReasons = new Map();
  let decisionCount = 0;
  let riskyDecisions = 0;
  let fatalDecisions = 0;
  let holdDecisions = 0;
  let merges = 0;
  let lowConfidenceTurns = 0;
  let temporalNextObservations = 0;
  let knownEmptyHoldObservations = 0;

  for (const record of records) {
    const confidence = finiteNumber(record?.state?.confidence);
    if (Number.isFinite(confidence)) {
      confidences.push(confidence);
      if (confidence < LOW_CONFIDENCE_THRESHOLD) lowConfidenceTurns += 1;
    }

    const reason = boundedReason(record?.state?.perception?.reason);
    if (reason) {
      perceptionReasons.set(reason, (perceptionReasons.get(reason) || 0) + 1);
      if (reason.includes('temporal-next')) temporalNextObservations += 1;
      if (reason.includes('hold-empty')) knownEmptyHoldObservations += 1;
    }

    const decision = record?.decision;
    if (!decision || typeof decision !== 'object' || Array.isArray(decision)) continue;
    decisionCount += 1;
    if (decision.hold === true) holdDecisions += 1;
    const diagnostics = decision.diagnostics && typeof decision.diagnostics === 'object'
      ? decision.diagnostics : {};
    const risk = Math.max(finiteNumber(diagnostics.risk, 0), finiteNumber(diagnostics.pathRisk, 0));
    if (risk >= 1) riskyDecisions += 1;
    if (risk >= 2) fatalDecisions += 1;
    const mergeCount = finiteInt(diagnostics.merges, 0);
    if (mergeCount > 0) merges += mergeCount;
    const clearance = finiteNumber(diagnostics.clearance);
    const futureClearance = finiteNumber(diagnostics.minFutureClearance);
    if (Number.isFinite(clearance)) clearances.push(clearance);
    if (Number.isFinite(futureClearance)) futureClearances.push(futureClearance);
  }

  const topPerceptionReasons = [...perceptionReasons.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 6)
    .map(([reason, count]) => `${reason}:${count}`);

  return {
    ok: true,
    records: records.length,
    criticalTurns: selectCriticalTurns(records),
    metrics: {
      decisionCount,
      lowConfidenceTurns,
      riskyDecisions,
      fatalDecisions,
      holdDecisions,
      merges,
      minConfidence: minFinite(confidences),
      minClearance: minFinite(clearances),
      minFutureClearance: minFinite(futureClearances),
      temporalNextObservations,
      knownEmptyHoldObservations,
      topPerceptionReasons,
    },
  };
}

function readHistory(runtimeDir, game) {
  const path = historyPath(runtimeDir, game);
  if (!path) return { ok: false, path: null, error: 'missing history' };
  try {
    const size = statSync(path).size;
    if (!Number.isFinite(size) || size < 0 || size > MAX_HISTORY_BYTES) {
      return { ok: false, path, error: `history byte limit exceeded (${size}>${MAX_HISTORY_BYTES})` };
    }
    return { path, ...analyzeHistoryText(readFileSync(path, 'utf-8')) };
  } catch (error) {
    return { ok: false, path, error: error?.message || String(error) };
  }
}

export function selectSnapshotPaths(runtimeDir, game, maxShots = 3, preferredTurns = []) {
  const dir = join(runtimeDir, 'tmp', 'game_screenshots', `game_${gameToken(game)}`);
  if (!existsSync(dir) || maxShots <= 0) return [];
  const files = readdirSync(dir)
    .map(name => {
      const match = name.match(/^turn_(\d+).*\.png$/i);
      return match ? { name, turn: Number.parseInt(match[1], 10) } : null;
    })
    .filter(Boolean)
    .sort((a, b) => a.turn - b.turn || a.name.localeCompare(b.name));
  if (files.length <= maxShots) return files.map(file => join(dir, file.name));

  const selected = new Set();
  for (const target of [...new Set(preferredTurns.filter(Number.isInteger))]) {
    const nearest = files.reduce((best, file) => {
      if (!best) return file;
      const delta = Math.abs(file.turn - target);
      const bestDelta = Math.abs(best.turn - target);
      return delta < bestDelta || (delta === bestDelta && file.turn > best.turn) ? file : best;
    }, null);
    if (nearest) selected.add(nearest.name);
    if (selected.size >= maxShots) break;
  }

  if (selected.size < maxShots) {
    const remaining = maxShots - selected.size;
    const indexes = remaining === 1
      ? [files.length - 1]
      : Array.from({ length: remaining }, (_, i) => Math.round(i * (files.length - 1) / (remaining - 1)));
    for (const index of indexes) {
      selected.add(files[index].name);
      if (selected.size >= maxShots) break;
    }
  }

  if (selected.size < maxShots) {
    for (const file of files) {
      selected.add(file.name);
      if (selected.size >= maxShots) break;
    }
  }

  return files.filter(file => selected.has(file.name)).slice(0, maxShots).map(file => join(dir, file.name));
}

function rankValue(entry, fallback) {
  return Number.isInteger(entry.rank) && entry.rank >= 1 ? entry.rank : fallback;
}

export function selectFocusGames(entries) {
  if (!entries.length) return { worst: null, best: null, latest: null, games: [] };
  const ranked = entries.filter(entry => Number.isInteger(entry.rank) && entry.rank >= 1);
  const byWorst = [...ranked].sort((a, b) =>
    rankValue(b, -Infinity) - rankValue(a, -Infinity)
    || (finiteInt(a.turns, 0) - finiteInt(b.turns, 0))
    || (b.game - a.game));
  const byBest = [...ranked].sort((a, b) =>
    rankValue(a, Infinity) - rankValue(b, Infinity)
    || (finiteInt(b.turns, 0) - finiteInt(a.turns, 0))
    || (b.game - a.game));
  const latest = [...entries].sort((a, b) => b.game - a.game)[0];
  const worst = byWorst[0] ?? latest;
  const best = byBest[0] ?? latest;
  const games = [...new Set([worst?.game, best?.game, latest?.game].filter(Number.isInteger))];
  return { worst: worst?.game ?? null, best: best?.game ?? null, latest: latest?.game ?? null, games };
}

function mean(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? xs.reduce((sum, value) => sum + value, 0) / xs.length : null;
}

function median(values) {
  const xs = values.filter(Number.isFinite).sort((a, b) => a - b);
  if (!xs.length) return null;
  const middle = Math.floor(xs.length / 2);
  return xs.length % 2 ? xs[middle] : (xs[middle - 1] + xs[middle]) / 2;
}

function sumHistoryMetric(entries, key) {
  return entries.reduce((sum, entry) => sum + (finiteNumber(entry.historyMetrics?.[key], 0) || 0), 0);
}

export function buildDailyEvidence(runtimeDir, lastConsumedGame = 0, { maxShotsPerFocus = 3 } = {}) {
  const allGames = scanSummaryGames(runtimeDir);
  const range = contiguousFreshGames(allGames, lastConsumedGame);
  if (range.games.length === 0) {
    return {
      schemaVersion: 2,
      status: range.blockedByGap ? 'blocked' : 'no-data',
      lastConsumedGame: Math.max(0, finiteInt(lastConsumedGame, 0)),
      range,
      entries: [],
      focus: { worst: null, best: null, latest: null, games: [] },
      metrics: {
        games: 0,
        meanRank: null,
        medianRank: null,
        meanTurns: null,
        historyRecords: 0,
        lowConfidenceTurns: 0,
        riskyDecisions: 0,
        fatalDecisions: 0,
        holdDecisions: 0,
        merges: 0,
        minConfidence: null,
        minClearance: null,
        minFutureClearance: null,
      },
      warnings: range.blockedByGap
        ? [`missing game #${range.missingGame} before available game #${range.nextAvailableGame}`]
        : [],
    };
  }

  const entries = range.games.map(game => {
    const summary = readSummary(runtimeDir, game);
    const history = readHistory(runtimeDir, game);
    const strategySnapshot = strategySnapshotPath(runtimeDir, game);
    return {
      game,
      summaryOk: summary.ok,
      summaryError: summary.ok ? null : summary.error,
      rank: summary.ok ? summary.rank : null,
      turns: summary.ok ? summary.turns : null,
      piecesAtEnd: summary.ok ? summary.piecesAtEnd : null,
      strategyHash: summary.ok ? summary.strategyHash : null,
      resultScreenOcr: summary.ok ? summary.resultScreenOcr : [],
      history: history.path ? relative(runtimeDir, history.path) : null,
      historyOk: history.ok,
      historyError: history.ok ? null : history.error,
      historyRecords: history.ok ? history.records : 0,
      historyMetrics: history.ok ? history.metrics : null,
      criticalTurns: history.ok ? history.criticalTurns : [],
      strategySnapshot: strategySnapshot ? relative(runtimeDir, strategySnapshot) : null,
      screenshots: [],
    };
  });
  const focus = selectFocusGames(entries);
  const focusSet = new Set(focus.games);
  for (const entry of entries) {
    if (!focusSet.has(entry.game)) continue;
    entry.screenshots = selectSnapshotPaths(
      runtimeDir,
      entry.game,
      maxShotsPerFocus,
      entry.criticalTurns,
    ).map(path => relative(runtimeDir, path));
  }

  const warnings = [];
  const invalidSummaries = entries.filter(entry => !entry.summaryOk).map(entry => entry.game);
  const missingHistory = entries.filter(entry => !entry.history).map(entry => entry.game);
  const invalidHistory = entries.filter(entry => entry.history && !entry.historyOk).map(entry => entry.game);
  const missingStrategySnapshots = entries.filter(entry => !entry.strategySnapshot).map(entry => entry.game);
  const focusMissingScreenshots = entries
    .filter(entry => focusSet.has(entry.game) && entry.screenshots.length === 0)
    .map(entry => entry.game);
  if (invalidSummaries.length) warnings.push(`invalid summaries: ${invalidSummaries.join(',')}`);
  if (missingHistory.length) warnings.push(`missing histories: ${missingHistory.join(',')}`);
  if (invalidHistory.length) warnings.push(`invalid histories: ${invalidHistory.join(',')}`);
  if (missingStrategySnapshots.length) warnings.push(`missing strategy snapshots: ${missingStrategySnapshots.join(',')}`);
  if (focusMissingScreenshots.length) warnings.push(`focus games without screenshots: ${focusMissingScreenshots.join(',')}`);
  if (range.blockedByGap) {
    warnings.push(`deferred after gap: missing #${range.missingGame}, next available #${range.nextAvailableGame}`);
  }

  const hardIncomplete = invalidSummaries.length > 0 || missingHistory.length > 0 || invalidHistory.length > 0;
  return {
    schemaVersion: 2,
    status: hardIncomplete ? 'blocked' : 'ready',
    lastConsumedGame: Math.max(0, finiteInt(lastConsumedGame, 0)),
    range,
    entries,
    focus,
    metrics: {
      games: entries.length,
      meanRank: mean(entries.map(entry => entry.rank)),
      medianRank: median(entries.map(entry => entry.rank)),
      meanTurns: mean(entries.map(entry => entry.turns)),
      historyRecords: entries.reduce((sum, entry) => sum + entry.historyRecords, 0),
      lowConfidenceTurns: sumHistoryMetric(entries, 'lowConfidenceTurns'),
      riskyDecisions: sumHistoryMetric(entries, 'riskyDecisions'),
      fatalDecisions: sumHistoryMetric(entries, 'fatalDecisions'),
      holdDecisions: sumHistoryMetric(entries, 'holdDecisions'),
      merges: sumHistoryMetric(entries, 'merges'),
      minConfidence: minFinite(entries.map(entry => entry.historyMetrics?.minConfidence)),
      minClearance: minFinite(entries.map(entry => entry.historyMetrics?.minClearance)),
      minFutureClearance: minFinite(entries.map(entry => entry.historyMetrics?.minFutureClearance)),
    },
    warnings,
  };
}

function fmt(value, digits = 2) {
  return Number.isFinite(value) ? Number(value).toFixed(digits) : '?';
}

export function formatEvidenceForPrompt(bundle) {
  const lines = [
    `## Daily evidence (schema v${bundle.schemaVersion})`,
    `status=${bundle.status}, consumedThrough=${bundle.lastConsumedGame}`,
  ];
  if (bundle.range?.games?.length) {
    lines.push(`contiguous games=${bundle.range.games[0]}..${bundle.range.games.at(-1)} (${bundle.range.games.length})`);
  }
  if (bundle.focus?.games?.length) {
    lines.push(`focus: worst=#${bundle.focus.worst}, best=#${bundle.focus.best}, latest=#${bundle.focus.latest}`);
  }
  if (bundle.metrics?.historyRecords > 0) {
    lines.push(
      `history: records=${bundle.metrics.historyRecords}, lowConfidence=${bundle.metrics.lowConfidenceTurns}, risky=${bundle.metrics.riskyDecisions}, fatal=${bundle.metrics.fatalDecisions}, hold=${bundle.metrics.holdDecisions}, merges=${bundle.metrics.merges}, minConf=${fmt(bundle.metrics.minConfidence)}, minClearance=${fmt(bundle.metrics.minClearance)}, minFutureClearance=${fmt(bundle.metrics.minFutureClearance)}`,
    );
  }
  for (const entry of bundle.entries || []) {
    const screenshots = entry.screenshots?.length ? entry.screenshots.join(', ') : 'none';
    const hm = entry.historyMetrics;
    const historyDetail = hm
      ? `historyRecords=${entry.historyRecords}, lowConf=${hm.lowConfidenceTurns}, risk=${hm.riskyDecisions}/${hm.fatalDecisions}, hold=${hm.holdDecisions}, merges=${hm.merges}, minConf=${fmt(hm.minConfidence)}, minClr=${fmt(hm.minClearance)}, reasons=${hm.topPerceptionReasons.join('|') || 'none'}, criticalTurns=${entry.criticalTurns.join(',') || 'none'}`
      : `history=${entry.history ? 'invalid' : 'missing'}`;
    lines.push(`- #${entry.game}: rank=${entry.rank ?? '?'}, turns=${entry.turns ?? '?'}, ${historyDetail}, strategySnapshot=${entry.strategySnapshot ?? 'missing'}, screenshots=${screenshots}`);
  }
  for (const warning of bundle.warnings || []) lines.push(`WARNING: ${warning}`);
  return lines.join('\n');
}

function parseArgs(argv) {
  const opts = { runtimeDir: HERE, lastConsumedGame: 0, json: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const value = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };
    if (arg === '--runtime-dir') opts.runtimeDir = resolve(value());
    else if (arg === '--last-consumed') opts.lastConsumedGame = finiteInt(value(), 0);
    else if (arg === '--json') opts.json = true;
    else throw new Error(`unknown option: ${arg}`);
  }
  return opts;
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  const bundle = buildDailyEvidence(opts.runtimeDir, opts.lastConsumedGame);
  console.log(opts.json ? JSON.stringify(bundle, null, 2) : formatEvidenceForPrompt(bundle));
  // Gap or corrupt/missing history is a fail-closed condition for an automatic
  // strategy mutation. Missing visual/strategy snapshots stay visible as a
  // warning so an operator can improve capture coverage without losing games.
  return bundle.status === 'blocked' ? 2 : 0;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  try {
    process.exit(main());
  } catch (error) {
    console.error(error?.stack || error);
    process.exit(1);
  }
}

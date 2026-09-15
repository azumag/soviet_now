#!/usr/bin/env node
/**
 * Build a fail-closed evidence bundle for the Soren91 daily improvement loop.
 *
 * This module never mutates runtime data. It answers three questions before a
 * strategy model is allowed to propose a change:
 *   1. Which games are safely consumable without skipping a missing game?
 *   2. Which best/worst/latest games should receive detailed attention?
 *   3. Do those games have history, strategy snapshots and visual snapshots?
 */

import {
  existsSync,
  readFileSync,
  readdirSync,
} from 'fs';
import { join, relative, resolve } from 'path';
import { fileURLToPath } from 'url';

const HERE = resolve(fileURLToPath(new URL('.', import.meta.url)));

function finiteInt(value, fallback = null) {
  const n = Number(value);
  return Number.isInteger(n) ? n : fallback;
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

export function selectSnapshotPaths(runtimeDir, game, maxShots = 3) {
  const dir = join(runtimeDir, 'tmp', 'game_screenshots', `game_${gameToken(game)}`);
  if (!existsSync(dir) || maxShots <= 0) return [];
  const files = readdirSync(dir)
    .filter(name => /^turn_.*\.png$/i.test(name))
    .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  if (files.length <= maxShots) return files.map(name => join(dir, name));

  const indexes = maxShots === 1
    ? [files.length - 1]
    : Array.from({ length: maxShots }, (_, i) => Math.round(i * (files.length - 1) / (maxShots - 1)));
  return [...new Set(indexes)].map(index => join(dir, files[index]));
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

export function buildDailyEvidence(runtimeDir, lastConsumedGame = 0, { maxShotsPerFocus = 3 } = {}) {
  const allGames = scanSummaryGames(runtimeDir);
  const range = contiguousFreshGames(allGames, lastConsumedGame);
  if (range.games.length === 0) {
    return {
      schemaVersion: 1,
      status: range.blockedByGap ? 'blocked' : 'no-data',
      lastConsumedGame: Math.max(0, finiteInt(lastConsumedGame, 0)),
      range,
      entries: [],
      focus: { worst: null, best: null, latest: null, games: [] },
      metrics: { games: 0, meanRank: null, medianRank: null, meanTurns: null },
      warnings: range.blockedByGap
        ? [`missing game #${range.missingGame} before available game #${range.nextAvailableGame}`]
        : [],
    };
  }

  const entries = range.games.map(game => {
    const summary = readSummary(runtimeDir, game);
    const history = historyPath(runtimeDir, game);
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
      history: history ? relative(runtimeDir, history) : null,
      strategySnapshot: strategySnapshot ? relative(runtimeDir, strategySnapshot) : null,
      screenshots: [],
    };
  });
  const focus = selectFocusGames(entries);
  const focusSet = new Set(focus.games);
  for (const entry of entries) {
    if (!focusSet.has(entry.game)) continue;
    entry.screenshots = selectSnapshotPaths(runtimeDir, entry.game, maxShotsPerFocus)
      .map(path => relative(runtimeDir, path));
  }

  const warnings = [];
  const invalidSummaries = entries.filter(entry => !entry.summaryOk).map(entry => entry.game);
  const missingHistory = entries.filter(entry => !entry.history).map(entry => entry.game);
  const missingStrategySnapshots = entries.filter(entry => !entry.strategySnapshot).map(entry => entry.game);
  const focusMissingScreenshots = entries
    .filter(entry => focusSet.has(entry.game) && entry.screenshots.length === 0)
    .map(entry => entry.game);
  if (invalidSummaries.length) warnings.push(`invalid summaries: ${invalidSummaries.join(',')}`);
  if (missingHistory.length) warnings.push(`missing histories: ${missingHistory.join(',')}`);
  if (missingStrategySnapshots.length) warnings.push(`missing strategy snapshots: ${missingStrategySnapshots.join(',')}`);
  if (focusMissingScreenshots.length) warnings.push(`focus games without screenshots: ${focusMissingScreenshots.join(',')}`);
  if (range.blockedByGap) {
    warnings.push(`deferred after gap: missing #${range.missingGame}, next available #${range.nextAvailableGame}`);
  }

  const hardIncomplete = invalidSummaries.length > 0 || missingHistory.length > 0;
  return {
    schemaVersion: 1,
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
    },
    warnings,
  };
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
  for (const entry of bundle.entries || []) {
    const screenshots = entry.screenshots?.length ? entry.screenshots.join(', ') : 'none';
    lines.push(`- #${entry.game}: rank=${entry.rank ?? '?'}, turns=${entry.turns ?? '?'}, history=${entry.history ?? 'missing'}, strategySnapshot=${entry.strategySnapshot ?? 'missing'}, screenshots=${screenshots}`);
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

/**
 * lineage.mjs - soren91 向けの系統樹改善基盤
 *
 * rank を主指標にしつつ、欠損時の fallback / 補助として turns を使う。
 * soren91 では実質的な成績指標は順位なので、rank が取れたゲームを最優先で評価する。
 */

import { createHash } from 'crypto';
import { appendFileSync, copyFileSync, existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from 'fs';
import { basename, dirname, join } from 'path';

const STRATEGY_PATH = 'strategy.mjs';
const VERSIONS_DIR = 'strategy_versions';
const HASH_ARCHIVE_DIR = join(VERSIONS_DIR, 'by_hash');
const HALL_OF_FAME_MANIFEST = join(VERSIONS_DIR, 'hall_of_fame.json');
const TMP_STATE_DIR = 'tmp/state';
const STRATEGY_SNAPSHOTS_DIR = 'tmp/strategy_snapshots';
const SUMMARIES_DIR = 'tmp/summaries';
const HISTORY_DIR = 'game_history';
const ROLLING_RANKS_FILE = join(TMP_STATE_DIR, 'rolling_ranks.json');
const CURRENT_STRATEGY_RUN_FILE = join(TMP_STATE_DIR, 'current_strategy_run.json');
const BEST_STRATEGY_ANCHOR_FILE = join(TMP_STATE_DIR, 'best_strategy_anchor.json');
const LINEAGE_SUMMARY_FILE = join(TMP_STATE_DIR, 'lineage_summary.md');
const PHYROGENETIC_TREE_FILE = join(TMP_STATE_DIR, 'strategy_phylogeny.mmd');
const PHYROGENETIC_EVENTS_FILE = 'phyrogenetic-events.jsonl';

const METRIC_LCB_Z = 1.28;
const METRIC_WEIGHT_P50 = 0.55;
const METRIC_WEIGHT_P25 = 0.30;
const METRIC_WEIGHT_LCB = 0.15;
const TURN_SUPPORT_WEIGHT = 0.12;
const TURN_ONLY_FALLBACK_WEIGHT = 0.20;
const RESULT_KEEP = 24;
const RECENT_PATH_KEEP = 50;
const MIN_GAMES_FOR_ANCHOR = Number.parseInt(process.env.SOREN91_MIN_GAMES_FOR_ANCHOR || '8', 10);

function ensureDirs() {
  [TMP_STATE_DIR, HASH_ARCHIVE_DIR, STRATEGY_SNAPSHOTS_DIR].forEach(dir => {
    mkdirSync(dir, { recursive: true });
  });
}

function readJson(path, fallback) {
  if (!existsSync(path)) return fallback;
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return fallback;
  }
}

function writeJson(path, payload) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`);
}

function padGame(gameNumber) {
  return String(gameNumber).padStart(4, '0');
}

function quantile(values, p) {
  const xs = [...values].sort((a, b) => a - b);
  if (xs.length === 0) return 0;
  if (xs.length === 1) return xs[0];
  const pos = (xs.length - 1) * p;
  const lo = Math.floor(pos);
  const hi = Math.min(lo + 1, xs.length - 1);
  const frac = pos - lo;
  return xs[lo] * (1 - frac) + xs[hi] * frac;
}

function calcHigherBetterMetrics(values) {
  if (!Array.isArray(values) || values.length === 0) return null;
  const nums = values.map(v => Number(v)).filter(v => Number.isFinite(v));
  if (nums.length === 0) return null;
  const mean = nums.reduce((sum, value) => sum + value, 0) / nums.length;
  const p25 = quantile(nums, 0.25);
  const p50 = quantile(nums, 0.50);
  const variance = nums.length > 1
    ? nums.reduce((sum, value) => sum + ((value - mean) ** 2), 0) / nums.length
    : 0;
  const std = Math.sqrt(variance);
  const lcb = mean - (METRIC_LCB_Z * (std / Math.sqrt(nums.length)));
  const comp = (METRIC_WEIGHT_P50 * p50) + (METRIC_WEIGHT_P25 * p25) + (METRIC_WEIGHT_LCB * lcb);
  return { n: nums.length, mean, p25, p50, lcb, comp };
}

function normalizeRank(rank) {
  const value = Number.parseInt(String(rank), 10);
  if (!Number.isInteger(value)) return null;
  return value >= 1 && value <= 91 ? value : null;
}

function rankToPoints(rank) {
  return 92 - rank;
}

function shortHash(hashValue) {
  return String(hashValue || '').slice(0, 8) || 'unknown';
}

function formatMetric(value, digits = 1) {
  return Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
}

function formatRank(value) {
  return Number.isFinite(value) ? value.toFixed(1) : 'n/a';
}

function readHallOfFameManifest() {
  const payload = readJson(HALL_OF_FAME_MANIFEST, []);
  if (!Array.isArray(payload)) return [];
  return payload.map(entry => ({
    ...entry,
    strategyHash: String(entry?.strategyHash || '').slice(0, 12),
  }));
}

function makeEmptyEntry() {
  return {
    results: [],
    parents: [],
    prev_hash: '',
    games_total: 0,
    first_game: null,
    last_game: null,
    _recent_archives: [],
    _recent_summaries: [],
  };
}

function ensureRollingEntry(rolling, strategyHash) {
  if (!rolling[strategyHash] || typeof rolling[strategyHash] !== 'object') {
    rolling[strategyHash] = makeEmptyEntry();
  }
  const entry = rolling[strategyHash];
  if (!Array.isArray(entry.results)) entry.results = [];
  if (!Array.isArray(entry.parents)) entry.parents = [];
  if (!Array.isArray(entry._recent_archives)) entry._recent_archives = [];
  if (!Array.isArray(entry._recent_summaries)) entry._recent_summaries = [];
  if (!Number.isInteger(entry.games_total)) entry.games_total = entry.results.length;
  if (typeof entry.prev_hash !== 'string') entry.prev_hash = '';
  return entry;
}

function summarizeEntry(strategyHash, entry) {
  const results = Array.isArray(entry?.results) ? entry.results : [];
  const turns = results
    .map(result => Number(result?.turns))
    .filter(value => Number.isFinite(value) && value >= 0);
  const validRanks = results
    .map(result => normalizeRank(result?.rank))
    .filter(value => value != null);
  const turnMetrics = calcHigherBetterMetrics(turns);
  const rankPointMetrics = calcHigherBetterMetrics(validRanks.map(rankToPoints));
  const rankCoverage = results.length > 0 ? validRanks.length / results.length : 0;
  const comp = rankPointMetrics
    ? (rankPointMetrics.comp + ((turnMetrics?.comp || 0) * TURN_SUPPORT_WEIGHT))
    : ((turnMetrics?.comp || 0) * TURN_ONLY_FALLBACK_WEIGHT);
  return {
    hash: strategyHash,
    games_total: Number(entry?.games_total || results.length || 0),
    sample_n: results.length,
    first_game: entry?.first_game ?? null,
    last_game: entry?.last_game ?? null,
    turn_metrics: turnMetrics,
    rank_sample_n: validRanks.length,
    rank_coverage: rankCoverage,
    rank_metrics: rankPointMetrics,
    rank_p50: rankPointMetrics ? (92 - rankPointMetrics.p50) : null,
    rank_p25: rankPointMetrics ? (92 - rankPointMetrics.p25) : null,
    best_rank: validRanks.length > 0 ? Math.min(...validRanks) : null,
    comp,
  };
}

function sortRankedEntries(entries) {
  return [...entries].sort((a, b) => {
    if (b.metrics.comp !== a.metrics.comp) return b.metrics.comp - a.metrics.comp;
    const aRank = a.metrics.rank_p50 ?? 999;
    const bRank = b.metrics.rank_p50 ?? 999;
    if (aRank !== bRank) return aRank - bRank;
    const aRankCoverage = a.metrics.rank_coverage || 0;
    const bRankCoverage = b.metrics.rank_coverage || 0;
    if (bRankCoverage !== aRankCoverage) return bRankCoverage - aRankCoverage;
    const aTurns = a.metrics.turn_metrics?.p50 || 0;
    const bTurns = b.metrics.turn_metrics?.p50 || 0;
    if (bTurns !== aTurns) return bTurns - aTurns;
    return (b.metrics.games_total || 0) - (a.metrics.games_total || 0);
  });
}

function buildRankedEntries(rolling) {
  return sortRankedEntries(
    Object.entries(rolling)
      .map(([hash, entry]) => ({ hash, entry, metrics: summarizeEntry(hash, entry) }))
      .filter(item => item.metrics.sample_n > 0),
  );
}

function updateEntryWithGame(entry, summary, archivePath = '', summaryPath = '') {
  const gameNumber = Number.parseInt(String(summary?.gameNumber), 10);
  if (!Number.isInteger(gameNumber) || gameNumber <= 0) return false;
  const summaryKey = summaryPath || `game_${padGame(gameNumber)}.json`;
  if (entry._recent_summaries.includes(summaryKey)) return false;
  if (entry.results.some(result => Number(result?.gameNumber) === gameNumber)) return false;

  entry.results.push({
    gameNumber,
    turns: Number(summary?.turns || 0),
    rank: normalizeRank(summary?.rank),
  });
  entry.results = entry.results
    .sort((a, b) => Number(a.gameNumber || 0) - Number(b.gameNumber || 0))
    .slice(-RESULT_KEEP);
  entry.games_total = Number(entry.games_total || 0) + 1;
  entry.first_game = entry.first_game == null ? gameNumber : Math.min(entry.first_game, gameNumber);
  entry.last_game = entry.last_game == null ? gameNumber : Math.max(entry.last_game, gameNumber);
  if (archivePath) entry._recent_archives = [...entry._recent_archives, archivePath].slice(-RECENT_PATH_KEEP);
  entry._recent_summaries = [...entry._recent_summaries, summaryKey].slice(-RECENT_PATH_KEEP);
  return true;
}

function currentRunFromEntry(strategyHash, entry) {
  return {
    hash: strategyHash,
    results: [...(entry.results || [])],
    games_total: Number(entry.games_total || 0),
    _recent_archives: [...(entry._recent_archives || [])],
    _recent_summaries: [...(entry._recent_summaries || [])],
  };
}

function readCurrentRun() {
  return readJson(CURRENT_STRATEGY_RUN_FILE, {
    hash: '',
    results: [],
    games_total: 0,
    _recent_archives: [],
    _recent_summaries: [],
  });
}

export function computeStrategyHashFromSource(source) {
  const normalized = String(source || '').replace(/\r\n/g, '\n').trim();
  if (!normalized) return '';
  return createHash('sha256').update(normalized).digest('hex').slice(0, 12);
}

export function computeStrategyHashFromFile(filePath = STRATEGY_PATH) {
  if (!existsSync(filePath)) return '';
  return computeStrategyHashFromSource(readFileSync(filePath, 'utf-8'));
}

export function archiveStrategySnapshotByHash(sourceFile, strategyHash = '') {
  ensureDirs();
  if (!existsSync(sourceFile)) return '';
  const resolvedHash = strategyHash || computeStrategyHashFromFile(sourceFile);
  if (!resolvedHash) return '';
  const dstPath = join(HASH_ARCHIVE_DIR, `${resolvedHash}.mjs`);
  if (!existsSync(dstPath)) {
    copyFileSync(sourceFile, dstPath);
  }
  return dstPath;
}

export function snapshotCurrentStrategyForGame(gameNumber) {
  ensureDirs();
  const snapshotPath = join(STRATEGY_SNAPSHOTS_DIR, `game_${padGame(gameNumber)}_strategy.mjs`);
  copyFileSync(STRATEGY_PATH, snapshotPath);
  return {
    snapshotPath,
    strategyHash: computeStrategyHashFromFile(snapshotPath),
  };
}

export function resetCurrentStrategyRun(strategyHash) {
  ensureDirs();
  writeJson(CURRENT_STRATEGY_RUN_FILE, {
    hash: strategyHash,
    results: [],
    games_total: 0,
    _recent_archives: [],
    _recent_summaries: [],
  });
}

export function seedCurrentStrategyRunFromRolling(strategyHash) {
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  const entry = rolling[strategyHash];
  if (!entry || typeof entry !== 'object') return false;
  writeJson(CURRENT_STRATEGY_RUN_FILE, currentRunFromEntry(strategyHash, entry));
  return true;
}

function updateCurrentRunWithGame(strategyHash, summary, archivePath = '', summaryPath = '') {
  const currentRun = readCurrentRun();
  if (currentRun.hash && currentRun.hash !== strategyHash) return false;
  const runEntry = {
    results: Array.isArray(currentRun.results) ? currentRun.results : [],
    games_total: Number(currentRun.games_total || 0),
    _recent_archives: Array.isArray(currentRun._recent_archives) ? currentRun._recent_archives : [],
    _recent_summaries: Array.isArray(currentRun._recent_summaries) ? currentRun._recent_summaries : [],
  };
  const updated = updateEntryWithGame(runEntry, summary, archivePath, summaryPath);
  if (!updated && currentRun.hash) return false;
  writeJson(CURRENT_STRATEGY_RUN_FILE, {
    hash: strategyHash,
    results: runEntry.results,
    games_total: runEntry.games_total,
    _recent_archives: runEntry._recent_archives,
    _recent_summaries: runEntry._recent_summaries,
  });
  return true;
}

function readSummaryPayload(summaryOrPath) {
  if (!summaryOrPath) return null;
  if (typeof summaryOrPath === 'string') {
    return readJson(summaryOrPath, null);
  }
  if (typeof summaryOrPath === 'object') return summaryOrPath;
  return null;
}

function appendEvent(payload) {
  appendFileSync(PHYROGENETIC_EVENTS_FILE, `${JSON.stringify(payload)}\n`);
}

function currentHashIfAvailable() {
  return computeStrategyHashFromFile(STRATEGY_PATH);
}

export function refreshBestStrategyAnchor(currentHash = '') {
  ensureDirs();
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  const ranked = buildRankedEntries(rolling);
  const mature = ranked.filter(item => item.metrics.games_total >= MIN_GAMES_FOR_ANCHOR);
  const best = mature[0] || ranked[0] || null;
  const resolvedCurrentHash = currentHash || currentHashIfAvailable();
  if (!best) {
    writeJson(BEST_STRATEGY_ANCHOR_FILE, {
      hash: resolvedCurrentHash || '',
      note: 'no ranked strategies yet',
      generatedAt: new Date().toISOString(),
    });
    return null;
  }

  const payload = {
    hash: best.hash,
    comp: best.metrics.comp,
    gamesTotal: best.metrics.games_total,
    sampleN: best.metrics.sample_n,
    turnsP50: best.metrics.turn_metrics?.p50 ?? null,
    turnsP25: best.metrics.turn_metrics?.p25 ?? null,
    rankP50: best.metrics.rank_p50 ?? null,
    rankCoverage: best.metrics.rank_coverage ?? 0,
    firstGame: best.metrics.first_game,
    lastGame: best.metrics.last_game,
    file: join(HASH_ARCHIVE_DIR, `${best.hash}.mjs`),
    generatedAt: new Date().toISOString(),
    currentHash: resolvedCurrentHash || '',
  };
  writeJson(BEST_STRATEGY_ANCHOR_FILE, payload);
  return payload;
}

function resolveStrategyFileForHash(strategyHash) {
  if (!strategyHash) return '';
  const byHashPath = join(HASH_ARCHIVE_DIR, `${strategyHash}.mjs`);
  if (existsSync(byHashPath)) return byHashPath;
  if (computeStrategyHashFromFile(STRATEGY_PATH) === strategyHash) return STRATEGY_PATH;
  for (const entry of readHallOfFameManifest()) {
    if (entry?.strategyHash === strategyHash) {
      const hallPath = join(VERSIONS_DIR, entry.file);
      if (existsSync(hallPath)) return hallPath;
    }
  }
  return '';
}

function extractHeaderComment(filePath) {
  if (!filePath || !existsSync(filePath)) return '';
  const source = readFileSync(filePath, 'utf-8');
  const match = source.match(/^\s*(\/\*[\s\S]*?\*\/)/);
  if (!match) return '';
  return match[1].split('\n').slice(0, 12).join('\n').trim();
}

function readRecentEvents(limit = 6) {
  if (!existsSync(PHYROGENETIC_EVENTS_FILE)) return [];
  const lines = readFileSync(PHYROGENETIC_EVENTS_FILE, 'utf-8')
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
  return lines.slice(-limit).map(line => {
    try {
      return JSON.parse(line);
    } catch {
      return null;
    }
  }).filter(Boolean);
}

export function buildImproveReferenceContext(currentHash = '') {
  ensureLineageInitialized();
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  const currentRun = readCurrentRun();
  const anchor = readJson(BEST_STRATEGY_ANCHOR_FILE, null);
  const ranked = buildRankedEntries(rolling);
  const resolvedCurrentHash = currentHash || currentRun.hash || currentHashIfAvailable();
  const lines = [];

  lines.push('## Strategy Lineage Snapshot');
  lines.push('- soren91 では rank を主指標として使う。良い戦略かどうかは、まず順位で判断する。');
  lines.push('- turns は rank 欠損時の fallback と、同程度の rank を分ける補助指標としてのみ使う。');
  lines.push('');

  if (resolvedCurrentHash) {
    const currentEntry = rolling[resolvedCurrentHash];
    const currentMetrics = currentEntry ? summarizeEntry(resolvedCurrentHash, currentEntry) : null;
    lines.push('### Current Strategy');
    if (currentMetrics) {
      lines.push(`- hash=${resolvedCurrentHash} comp=${formatMetric(currentMetrics.comp)} games=${currentMetrics.games_total} sample=${currentMetrics.sample_n}`);
      lines.push(`- rank_p50=${formatRank(currentMetrics.rank_p50)} rank_coverage=${formatMetric(currentMetrics.rank_coverage * 100)}%`);
      lines.push(`- turns_p50=${formatMetric(currentMetrics.turn_metrics?.p50)} turns_p25=${formatMetric(currentMetrics.turn_metrics?.p25)}`);
    } else {
      lines.push(`- hash=${resolvedCurrentHash} (まだ rolling データなし)`);
    }
    lines.push('');
  }

  lines.push('### Best Anchor');
  if (anchor?.hash) {
    lines.push(`- hash=${anchor.hash} comp=${formatMetric(anchor.comp)} games=${anchor.gamesTotal}`);
    lines.push(`- rank_p50=${formatRank(anchor.rankP50)} turns_p50=${formatMetric(anchor.turnsP50)} turns_p25=${formatMetric(anchor.turnsP25)}`);
  } else {
    lines.push('- まだ anchor なし');
  }
  lines.push('');

  lines.push('### Mature Ranking Top 5');
  const mature = ranked.filter(item => item.metrics.games_total >= MIN_GAMES_FOR_ANCHOR).slice(0, 5);
  if (mature.length === 0) {
    lines.push('- mature 戦略はまだない');
  } else {
    for (const item of mature) {
      lines.push(
        `- ${shortHash(item.hash)} comp=${formatMetric(item.metrics.comp)} games=${item.metrics.games_total} ` +
        `rank_p50=${formatRank(item.metrics.rank_p50)} turns_p50=${formatMetric(item.metrics.turn_metrics?.p50)} ` +
        `rank_cov=${formatMetric(item.metrics.rank_coverage * 100)}%`
      );
    }
  }
  lines.push('');

  lines.push('### Manual Hall Of Fame');
  const hallEntries = readHallOfFameManifest().slice(-3).reverse();
  if (hallEntries.length === 0) {
    lines.push('- manual hall of fame なし');
  } else {
    for (const entry of hallEntries) {
      lines.push(
        `- ${entry.file} hash=${shortHash(entry.strategyHash)} note=${entry.note || 'n/a'} ` +
        `latest_game=${entry.latestGameNumber ?? 'n/a'} latest_turns=${entry.latestTurns ?? 'n/a'}`
      );
    }
  }
  lines.push('');

  lines.push('### Recent Transitions');
  const recentEvents = readRecentEvents(5);
  if (recentEvents.length === 0) {
    lines.push('- transition log なし');
  } else {
    for (const event of recentEvents) {
      lines.push(
        `- ${event.eventType || 'event'} ${shortHash(event.fromHash)} -> ${shortHash(event.toHash)} ` +
        `games=${event.gameStart ?? '?'}-${event.gameEnd ?? '?'} note=${event.note || 'n/a'}`
      );
    }
  }
  lines.push('');

  lines.push('### Reference Strategy Headers');
  const referenceHashes = [];
  if (anchor?.hash) referenceHashes.push(anchor.hash);
  for (const item of mature) {
    if (!referenceHashes.includes(item.hash)) referenceHashes.push(item.hash);
    if (referenceHashes.length >= 2) break;
  }
  for (const entry of hallEntries) {
    if (entry?.strategyHash && !referenceHashes.includes(entry.strategyHash)) {
      referenceHashes.push(entry.strategyHash);
      break;
    }
  }
  if (resolvedCurrentHash && !referenceHashes.includes(resolvedCurrentHash)) {
    referenceHashes.push(resolvedCurrentHash);
  }
  let headerCount = 0;
  for (const strategyHash of referenceHashes) {
    const refPath = resolveStrategyFileForHash(strategyHash);
    const header = extractHeaderComment(refPath);
    if (!header) continue;
    headerCount += 1;
    lines.push(`#### ${shortHash(strategyHash)} (${basename(refPath)})`);
    lines.push('```javascript');
    lines.push(header);
    lines.push('```');
  }
  if (headerCount === 0) {
    lines.push('- 参照ヘッダーなし');
  }

  return `${lines.join('\n')}\n`;
}

export function writeLineageSummary(currentHash = '') {
  const summary = buildImproveReferenceContext(currentHash);
  writeFileSync(LINEAGE_SUMMARY_FILE, summary);
  return summary;
}

export function writePhyrogeneticTree(currentHash = '') {
  ensureLineageInitialized();
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  const anchor = readJson(BEST_STRATEGY_ANCHOR_FILE, null);
  const hallHashes = new Set(readHallOfFameManifest().map(entry => entry?.strategyHash).filter(Boolean));
  const resolvedCurrentHash = currentHash || currentHashIfAvailable();
  const ranked = buildRankedEntries(rolling);
  const seenNodeHashes = new Set(ranked.map(item => item.hash));

  const lines = ['graph TD'];
  for (const item of ranked) {
    const id = `h_${item.hash}`;
    const labelParts = [
      shortHash(item.hash),
      `g=${item.metrics.games_total}`,
      `t50=${formatMetric(item.metrics.turn_metrics?.p50)}`,
    ];
    if (item.metrics.rank_p50 != null) labelParts.push(`r50=${formatRank(item.metrics.rank_p50)}`);
    lines.push(`  ${id}["${labelParts.join('<br/>')}"]`);
  }
  for (const hallHash of hallHashes) {
    if (seenNodeHashes.has(hallHash)) continue;
    lines.push(`  h_${hallHash}["${shortHash(hallHash)}<br/>manual HOF"]`);
  }

  const emitted = new Set();
  for (const item of ranked) {
    const parents = Array.isArray(item.entry.parents) ? item.entry.parents : [];
    const parentHashes = parents.length > 0 ? parents : (item.entry.prev_hash ? [item.entry.prev_hash] : []);
    for (const parentHash of parentHashes) {
      if (!rolling[parentHash]) continue;
      const edgeKey = `${parentHash}->${item.hash}`;
      if (emitted.has(edgeKey)) continue;
      emitted.add(edgeKey);
      lines.push(`  h_${parentHash} --> h_${item.hash}`);
    }
  }

  if (anchor?.hash) {
    lines.push(`  class h_${anchor.hash} anchor;`);
  }
  if (resolvedCurrentHash && rolling[resolvedCurrentHash]) {
    lines.push(`  class h_${resolvedCurrentHash} current;`);
  }
  for (const hallHash of hallHashes) {
    if (rolling[hallHash]) lines.push(`  class h_${hallHash} hof;`);
  }
  lines.push('  classDef anchor fill:#ffe5b4,stroke:#d17a00,stroke-width:3px;');
  lines.push('  classDef current fill:#cde9ff,stroke:#1e5aa8,stroke-width:3px;');
  lines.push('  classDef hof fill:#d7f5dc,stroke:#2c8f4a,stroke-width:2px;');

  const output = `${lines.join('\n')}\n`;
  writeFileSync(PHYROGENETIC_TREE_FILE, output);
  return output;
}

export function refreshLineageArtifacts(currentHash = '') {
  const resolvedCurrentHash = currentHash || currentHashIfAvailable();
  refreshBestStrategyAnchor(resolvedCurrentHash);
  writeLineageSummary(resolvedCurrentHash);
  writePhyrogeneticTree(resolvedCurrentHash);
}

export function recordCompletedGame({ strategySnapshotPath, strategyHash = '', summary, archivePath = '', summaryPath = '' }) {
  ensureDirs();
  ensureLineageInitialized();
  const summaryPayload = readSummaryPayload(summary);
  if (!summaryPayload) return { status: 'no_summary' };
  const resolvedHash = strategyHash || computeStrategyHashFromFile(strategySnapshotPath || STRATEGY_PATH);
  if (!resolvedHash) return { status: 'no_hash' };

  if (strategySnapshotPath && existsSync(strategySnapshotPath)) {
    archiveStrategySnapshotByHash(strategySnapshotPath, resolvedHash);
  } else {
    archiveStrategySnapshotByHash(STRATEGY_PATH, resolvedHash);
  }

  const rolling = readJson(ROLLING_RANKS_FILE, {});
  const entry = ensureRollingEntry(rolling, resolvedHash);
  const updated = updateEntryWithGame(entry, summaryPayload, archivePath, summaryPath);
  if (updated) {
    writeJson(ROLLING_RANKS_FILE, rolling);
    updateCurrentRunWithGame(resolvedHash, summaryPayload, archivePath, summaryPath);
    refreshLineageArtifacts(resolvedHash);
    return { status: 'updated', strategyHash: resolvedHash };
  }
  return { status: 'duplicate', strategyHash: resolvedHash };
}

export function recordImprovementTransition({ fromHash = '', toHash = '', note = '', gameStart = null, gameEnd = null, bestGame = null }) {
  ensureDirs();
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  if (fromHash) ensureRollingEntry(rolling, fromHash);
  if (toHash) {
    const entry = ensureRollingEntry(rolling, toHash);
    if (fromHash && fromHash !== toHash) {
      if (!entry.parents.includes(fromHash)) entry.parents.push(fromHash);
      if (!entry.prev_hash) entry.prev_hash = fromHash;
    }
  }
  writeJson(ROLLING_RANKS_FILE, rolling);

  if (toHash) {
    if (!seedCurrentStrategyRunFromRolling(toHash)) {
      resetCurrentStrategyRun(toHash);
    }
  }

  appendEvent({
    eventType: 'improve',
    fromHash,
    toHash,
    note,
    gameStart,
    gameEnd,
    bestGame,
    recordedAt: new Date().toISOString(),
  });
  refreshLineageArtifacts(toHash || fromHash);
}

export function ensureLineageInitialized() {
  ensureDirs();
  const rolling = readJson(ROLLING_RANKS_FILE, {});
  if (Object.keys(rolling).length > 0) return;
  const summaryFiles = readdirSync(SUMMARIES_DIR, { withFileTypes: true })
    .filter(entry => entry.isFile() && /^game_\d+\.json$/.test(entry.name));
  if (summaryFiles.length === 0) return;
  rebuildLineageFromHistory();
}

export function rebuildLineageFromHistory() {
  ensureDirs();
  const rolling = {};
  const versionFiles = readdirSync(VERSIONS_DIR, { withFileTypes: true })
    .filter(entry => entry.isFile() && /^v\d+.*_strategy\.mjs$/.test(entry.name))
    .map(entry => {
      const match = entry.name.match(/^v(\d+)/);
      return {
        path: join(VERSIONS_DIR, entry.name),
        endGame: match ? Number.parseInt(match[1], 10) : null,
      };
    })
    .filter(entry => Number.isInteger(entry.endGame))
    .sort((a, b) => a.endGame - b.endGame);

  const summaryFiles = readdirSync(SUMMARIES_DIR, { withFileTypes: true })
    .filter(entry => entry.isFile() && /^game_\d+\.json$/.test(entry.name))
    .map(entry => join(SUMMARIES_DIR, entry.name))
    .sort();

  const summaries = summaryFiles.map(path => readJson(path, null)).filter(Boolean);
  const latestGame = summaries.reduce((max, summary) => Math.max(max, Number(summary.gameNumber || 0)), 0);
  const segments = [];
  let startGame = 1;
  let prevHash = '';

  for (const versionFile of versionFiles) {
    const strategyHash = computeStrategyHashFromFile(versionFile.path);
    if (!strategyHash || versionFile.endGame < startGame) continue;
    archiveStrategySnapshotByHash(versionFile.path, strategyHash);
    const previous = segments[segments.length - 1];
    if (previous && previous.hash === strategyHash) {
      previous.endGame = versionFile.endGame;
      continue;
    }
    segments.push({
      hash: strategyHash,
      startGame,
      endGame: versionFile.endGame,
      parents: prevHash ? [prevHash] : [],
    });
    prevHash = strategyHash;
    startGame = versionFile.endGame + 1;
  }

  const currentHash = computeStrategyHashFromFile(STRATEGY_PATH);
  if (currentHash) {
    archiveStrategySnapshotByHash(STRATEGY_PATH, currentHash);
    if (latestGame >= startGame) {
      segments.push({
        hash: currentHash,
        startGame,
        endGame: latestGame,
        parents: prevHash && prevHash !== currentHash ? [prevHash] : [],
      });
    }
  }

  for (const segment of segments) {
    const entry = ensureRollingEntry(rolling, segment.hash);
    for (const parentHash of segment.parents) {
      if (!entry.parents.includes(parentHash)) entry.parents.push(parentHash);
      if (!entry.prev_hash) entry.prev_hash = parentHash;
    }
  }

  for (const summaryPath of summaryFiles) {
    const summary = readJson(summaryPath, null);
    if (!summary) continue;
    const gameNumber = Number(summary.gameNumber || 0);
    const segment = segments.find(item => gameNumber >= item.startGame && gameNumber <= item.endGame);
    if (!segment) continue;
    const entry = ensureRollingEntry(rolling, segment.hash);
    updateEntryWithGame(
      entry,
      summary,
      join(HISTORY_DIR, `game_${padGame(gameNumber)}.jsonl`),
      summaryPath,
    );
  }

  writeJson(ROLLING_RANKS_FILE, rolling);
  if (currentHash) {
    if (!seedCurrentStrategyRunFromRolling(currentHash)) {
      resetCurrentStrategyRun(currentHash);
    }
  }
  refreshLineageArtifacts(currentHash);
  return { segments: segments.length, latestGame };
}

if (import.meta.url === new URL(process.argv[1] || '', 'file:').href) {
  const command = process.argv[2] || 'summary';
  if (command === 'rebuild') {
    const result = rebuildLineageFromHistory();
    console.log(JSON.stringify({ status: 'rebuilt', ...result }));
  } else if (command === 'tree') {
    ensureLineageInitialized();
    console.log(writePhyrogeneticTree());
  } else if (command === 'summary') {
    ensureLineageInitialized();
    console.log(writeLineageSummary());
  } else {
    console.error('Usage: node lineage.mjs [rebuild|tree|summary]');
    process.exit(1);
  }
}

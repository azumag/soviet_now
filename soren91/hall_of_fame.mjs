/**
 * hall_of_fame.mjs - current soren91 strategy を手動で殿堂入り保存する
 *
 * Usage:
 *   node hall_of_fame.mjs
 *   node hall_of_fame.mjs --note "very strong run"
 */

import { createHash } from 'crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'fs';
import { basename, join } from 'path';

const STRATEGY_PATH = 'strategy.mjs';
const VERSIONS_DIR = 'strategy_versions';
const MANIFEST_PATH = join(VERSIONS_DIR, 'hall_of_fame.json');
const SUMMARIES_DIR = 'tmp/summaries';

function parseArgs(argv) {
  let note = '';
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--note') {
      note = argv[i + 1] || '';
      i += 1;
    }
  }
  return { note: note.trim() };
}

function readCurrentStrategy() {
  if (!existsSync(STRATEGY_PATH)) {
    throw new Error(`strategy not found: ${STRATEGY_PATH}`);
  }
  return readFileSync(STRATEGY_PATH, 'utf-8');
}

function extractStrategyVersion(strategyText) {
  const match = strategyText.match(/strategy\.mjs\s*-\s*[^(]+\((v[0-9]+)\)/);
  return match ? match[1] : 'unknown';
}

function readLatestSummary() {
  if (!existsSync(SUMMARIES_DIR)) return null;
  const files = readdirSync(SUMMARIES_DIR)
    .filter(name => /^game_\d+\.json$/.test(name))
    .sort();
  const latest = files.at(-1);
  if (!latest) return null;
  try {
    const payload = JSON.parse(readFileSync(join(SUMMARIES_DIR, latest), 'utf-8'));
    return payload;
  } catch {
    return null;
  }
}

function safeMetric(value, width = 0) {
  if (value == null || Number.isNaN(Number(value))) return 'na';
  const num = Math.trunc(Number(value));
  return width > 0 ? String(num).padStart(width, '0') : String(num);
}

function buildHallFilename(strategyVersion, summary) {
  const game = safeMetric(summary?.gameNumber, 4);
  const turns = safeMetric(summary?.turns, 3);
  const rank = summary?.rank == null ? 'na' : safeMetric(summary.rank, 2);
  const timestamp = new Date().toISOString().replace(/[-:]/g, '').replace(/\..*/, 'Z');
  return `best_manual_${timestamp}_${strategyVersion}_g${game}_t${turns}_r${rank}_strategy.mjs`;
}

function readManifest() {
  if (!existsSync(MANIFEST_PATH)) return [];
  try {
    const payload = JSON.parse(readFileSync(MANIFEST_PATH, 'utf-8'));
    return Array.isArray(payload) ? payload : [];
  } catch {
    return [];
  }
}

function sha256(text) {
  return createHash('sha256').update(text).digest('hex');
}

function saveManifest(entries) {
  writeFileSync(MANIFEST_PATH, `${JSON.stringify(entries, null, 2)}\n`);
}

function main() {
  mkdirSync(VERSIONS_DIR, { recursive: true });

  const { note } = parseArgs(process.argv.slice(2));
  const strategyText = readCurrentStrategy();
  const strategyHash = sha256(strategyText);
  const strategyVersion = extractStrategyVersion(strategyText);
  const latestSummary = readLatestSummary();
  const manifest = readManifest();
  const duplicate = manifest.find(entry => entry.strategyHash === strategyHash);

  if (duplicate) {
    console.log(JSON.stringify({
      status: 'already_hall_of_fame',
      file: duplicate.file,
      strategyVersion: duplicate.strategyVersion,
      strategyHash,
    }));
    return;
  }

  const hallFile = buildHallFilename(strategyVersion, latestSummary);
  const hallPath = join(VERSIONS_DIR, hallFile);
  copyFileSync(STRATEGY_PATH, hallPath);

  const entry = {
    file: hallFile,
    strategyVersion,
    strategyHash,
    savedAt: new Date().toISOString(),
    source: 'manual',
    note,
    latestGameNumber: latestSummary?.gameNumber ?? null,
    latestTurns: latestSummary?.turns ?? null,
    latestRank: latestSummary?.rank ?? null,
    latestSummaryFile: latestSummary?.gameNumber != null
      ? basename(join(SUMMARIES_DIR, `game_${String(latestSummary.gameNumber).padStart(4, '0')}.json`))
      : null,
  };
  manifest.push(entry);
  saveManifest(manifest);

  console.log(JSON.stringify({
    status: 'saved',
    file: hallFile,
    strategyVersion,
    latestGameNumber: entry.latestGameNumber,
    latestTurns: entry.latestTurns,
    latestRank: entry.latestRank,
  }));
}

main();

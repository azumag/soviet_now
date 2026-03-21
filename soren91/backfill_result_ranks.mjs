import { existsSync, readdirSync, readFileSync, writeFileSync } from 'fs';
import { join } from 'path';
import { analyzeResultScreen } from './result_screen_ocr.mjs';
import { rebuildLineageFromHistory } from './lineage.mjs';

const SUMMARIES_DIR = 'tmp/summaries';

function padGame(gameNumber) {
  return String(gameNumber).padStart(4, '0');
}

function readJson(path) {
  return JSON.parse(readFileSync(path, 'utf-8'));
}

function writeJson(path, payload) {
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`);
}

async function backfill() {
  const summaryFiles = readdirSync(SUMMARIES_DIR, { withFileTypes: true })
    .filter(entry => entry.isFile() && /^game_\d+\.json$/.test(entry.name))
    .map(entry => join(SUMMARIES_DIR, entry.name))
    .sort();

  let scanned = 0;
  let updated = 0;
  let filledRank = 0;

  for (const summaryPath of summaryFiles) {
    const summary = readJson(summaryPath);
    const gameNumber = Number(summary?.gameNumber || 0);
    if (!Number.isInteger(gameNumber) || gameNumber <= 0) continue;
    const rankingImagePath = join(SUMMARIES_DIR, `ranking_${padGame(gameNumber)}.png`);
    if (!existsSync(rankingImagePath)) continue;
    scanned += 1;

    const ocr = await analyzeResultScreen(rankingImagePath);
    const next = { ...summary };
    let dirty = false;

    if (ocr.rank != null && summary.rank !== ocr.rank) {
      next.rank = ocr.rank;
      dirty = true;
      if (summary.rank == null) filledRank += 1;
    }

    const nextOcr = {
      imagePath: ocr.imagePath || null,
      rank: ocr.rank ?? null,
      rankSource: ocr.rankSource || null,
      lines: (ocr.lines || []).slice(0, 8),
    };
    if (JSON.stringify(summary.resultScreenOcr || null) !== JSON.stringify(nextOcr)) {
      next.resultScreenOcr = nextOcr;
      dirty = true;
    }

    if (dirty) {
      writeJson(summaryPath, next);
      updated += 1;
    }
  }

  const rebuild = rebuildLineageFromHistory();
  return { scanned, updated, filledRank, rebuild };
}

if (import.meta.url === new URL(process.argv[1] || '', 'file:').href) {
  const result = await backfill();
  console.log(JSON.stringify({ status: 'ok', ...result }, null, 2));
}

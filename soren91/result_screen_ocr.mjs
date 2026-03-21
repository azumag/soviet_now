import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, join } from 'node:path';
import sharp from 'sharp';

function safeNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function normalizeLine(line) {
  return String(line || '')
    .replace(/[|¦]/g, 'I')
    .replace(/[“”]/g, '"')
    .replace(/[’]/g, "'")
    .replace(/\s+/g, ' ')
    .trim();
}

function usefulLine(line) {
  const text = normalizeLine(line);
  if (!text || text.length < 3) return false;
  const signal = (text.match(/[A-Za-z0-9ぁ-んァ-ヶ一-龠々]/g) || []).length;
  if (signal < 4) return false;
  if (!/\s/.test(text) && text.length <= 3) return false;
  if (signal / text.length < 0.45) return false;
  if (!/[ぁ-んァ-ヶ一-龠々]/.test(text)) {
    const hasLongToken = text.split(' ').some(token => /[A-Za-z0-9]{4,}/.test(token));
    if (!hasLongToken) return false;
  }
  return true;
}

function parseTsvRows(tsv) {
  const lines = String(tsv || '').split('\n');
  const rows = [];
  for (let i = 1; i < lines.length; i++) {
    const cols = lines[i].split('\t');
    if (cols.length < 12) continue;
    rows.push({
      level: safeNumber(cols[0]),
      pageNum: safeNumber(cols[1]),
      blockNum: safeNumber(cols[2]),
      parNum: safeNumber(cols[3]),
      lineNum: safeNumber(cols[4]),
      wordNum: safeNumber(cols[5]),
      left: safeNumber(cols[6]),
      top: safeNumber(cols[7]),
      width: safeNumber(cols[8]),
      height: safeNumber(cols[9]),
      conf: safeNumber(cols[10], -1),
      text: cols[11] || '',
    });
  }
  return rows;
}

function rowsToLines(rows, minConf = 45) {
  const buckets = new Map();
  for (const row of rows) {
    const text = normalizeLine(row.text);
    if (!text) continue;
    if (row.conf >= 0 && row.conf < minConf) continue;
    const key = [row.pageNum, row.blockNum, row.parNum, row.lineNum].join(':');
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(row);
  }
  const merged = [];
  for (const parts of buckets.values()) {
    parts.sort((a, b) => a.left - b.left);
    const text = normalizeLine(parts.map(p => p.text).join(' '));
    if (usefulLine(text)) merged.push(text);
  }
  return [...new Set(merged)];
}

function extractRankCandidates(rows, minConf = 55) {
  const weighted = [];
  for (const row of rows) {
    const text = normalizeLine(row.text);
    if (!text) continue;
    if (row.conf >= 0 && row.conf < minConf) continue;
    const matches = text.match(/\b([1-9]|[1-8][0-9]|9[01])\b/g) || [];
    for (const m of matches) {
      weighted.push({ value: Number.parseInt(m, 10), conf: row.conf });
    }
  }
  return weighted;
}

function chooseBestRank(candidates) {
  if (!candidates.length) return null;
  const counts = new Map();
  for (const item of candidates) {
    const current = counts.get(item.value) || { count: 0, confSum: 0 };
    current.count += 1;
    current.confSum += item.conf > 0 ? item.conf : 0;
    counts.set(item.value, current);
  }
  const ranked = [...counts.entries()]
    .map(([value, meta]) => ({ value, count: meta.count, confSum: meta.confSum }))
    .sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      if (b.confSum !== a.confSum) return b.confSum - a.confSum;
      return a.value - b.value;
    });
  const best = ranked[0];
  if (!best) return null;
  if (best.count >= 2) return best.value;
  if (ranked.length === 1 && best.confSum >= 70) return best.value;
  return null;
}

function runTesseractTsv(imagePath, args = []) {
  try {
    return execFileSync('tesseract', [imagePath, 'stdout', ...args, 'tsv'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    });
  } catch {
    return '';
  }
}

async function writeVariant(sourcePath, tempDir, name, transform) {
  const outPath = join(tempDir, `${name}.png`);
  let pipeline = sharp(sourcePath);
  pipeline = transform(pipeline);
  await pipeline.png().toFile(outPath);
  return outPath;
}

export async function analyzeResultScreen(imagePath) {
  if (!imagePath || !existsSync(imagePath)) {
    return {
      imagePath,
      detected: false,
      rank: null,
      lines: [],
      rankCandidates: [],
    };
  }

  const meta = await sharp(imagePath).metadata();
  const width = meta.width || 1280;
  const height = meta.height || 720;
  const tempDir = mkdtempSync(join(tmpdir(), 'soren91_result_ocr_'));

  try {
    const variants = [
      {
        name: 'full_rank_raw',
        path: imagePath,
        tesseractArgs: ['-l', 'eng', '--psm', '6'],
        lineMinConf: 100,
        rankMinConf: 70,
        allowRank: true,
      },
      {
        name: 'full_sparse',
        path: await writeVariant(imagePath, tempDir, 'full_sparse', img =>
          img.grayscale().normalize().sharpen()
        ),
        tesseractArgs: ['-l', 'eng+jpn', '--psm', '11'],
        lineMinConf: 45,
        rankMinConf: 65,
        allowRank: false,
      },
      {
        name: 'full_standard',
        path: await writeVariant(imagePath, tempDir, 'full_standard', img =>
          img.grayscale().normalize().sharpen().threshold(150)
        ),
        tesseractArgs: ['-l', 'eng', '--psm', '6'],
        lineMinConf: 55,
        rankMinConf: 70,
        allowRank: false,
      },
      {
        name: 'header_center',
        path: await writeVariant(imagePath, tempDir, 'header_center', img =>
          img
            .extract({
              left: Math.floor(width * 0.22),
              top: 0,
              width: Math.max(1, Math.floor(width * 0.56)),
              height: Math.max(1, Math.floor(height * 0.28)),
            })
            .resize({ width: Math.floor(width * 1.4) })
            .grayscale()
            .normalize()
            .sharpen()
            .threshold(165)
        ),
        tesseractArgs: ['-l', 'eng', '--psm', '6'],
        lineMinConf: 45,
        rankMinConf: 60,
        allowRank: false,
      },
    ];

    const collectedLines = [];
    const rankCandidates = [];
    for (const variant of variants) {
      const tsv = runTesseractTsv(variant.path, variant.tesseractArgs);
      const rows = parseTsvRows(tsv);
      collectedLines.push(...rowsToLines(rows, variant.lineMinConf));
      if (variant.allowRank) {
        rankCandidates.push(...extractRankCandidates(rows, variant.rankMinConf));
      }
    }

    const lines = [...new Set(collectedLines)].slice(0, 10);
    const rank = chooseBestRank(rankCandidates);
    return {
      imagePath: basename(imagePath),
      detected: true,
      rank,
      rankCandidates: [...new Set(rankCandidates.map(item => item.value))].sort((a, b) => a - b),
      lines,
    };
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const imagePath = process.argv[2];
  const result = await analyzeResultScreen(imagePath);
  writeFileSync(process.stdout.fd, `${JSON.stringify(result, null, 2)}\n`);
}

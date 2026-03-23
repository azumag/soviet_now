import { execFileSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';
import { detectRankingScreen } from './screenshot_analyzer.mjs';

const MODULE_DIR = dirname(fileURLToPath(import.meta.url));
const LOCAL_TESSDATA_DIR = resolve(MODULE_DIR, 'tessdata');

const RANKING_LIST_REGION = {
  left: 0.29,
  top: 0.26,
  width: 0.44,
  height: 0.66,
};

const RANKING_NAME_REGION = {
  left: 0.39,
  top: 0.26,
  width: 0.31,
  height: 0.66,
};

let tesseractLangCache = null;
let warnedMissingJapanese = false;

function safeNumber(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function clampInt(value, min, max) {
  return Math.max(min, Math.min(max, Math.round(value)));
}

function relativeRect(width, height, spec) {
  const left = clampInt(width * spec.left, 0, Math.max(0, width - 1));
  const top = clampInt(height * spec.top, 0, Math.max(0, height - 1));
  const right = clampInt(width * (spec.left + spec.width), left + 1, width);
  const bottom = clampInt(height * (spec.top + spec.height), top + 1, height);
  return {
    left,
    top,
    width: Math.max(1, right - left),
    height: Math.max(1, bottom - top),
  };
}

function getTesseractEnv() {
  const env = { ...process.env };
  const tessdataDir = process.env.SOREN91_TESSDATA_DIR
    ? resolve(process.env.SOREN91_TESSDATA_DIR)
    : LOCAL_TESSDATA_DIR;
  if (existsSync(tessdataDir)) {
    env.TESSDATA_PREFIX = tessdataDir.endsWith('/') ? tessdataDir : `${tessdataDir}/`;
  }
  return env;
}

function getTesseractLanguages() {
  if (tesseractLangCache) return tesseractLangCache;
  try {
    const output = execFileSync('tesseract', ['--list-langs'], {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
      env: getTesseractEnv(),
    });
    tesseractLangCache = new Set(
      String(output || '')
        .split(/\r?\n/)
        .map(line => line.trim())
        .filter(line => /^[A-Za-z0-9_]+$/.test(line))
    );
  } catch {
    tesseractLangCache = new Set(['eng']);
  }
  return tesseractLangCache;
}

function hasTesseractLanguage(lang) {
  return getTesseractLanguages().has(lang);
}

function resolveTesseractLanguages(preferred) {
  const available = getTesseractLanguages();
  const picked = preferred.filter(lang => available.has(lang));
  if (picked.length > 0) return picked.join('+');
  if (available.has('eng')) return 'eng';
  return null;
}

function maybeWarnMissingJapanese() {
  if (warnedMissingJapanese || hasTesseractLanguage('jpn')) return;
  warnedMissingJapanese = true;
  console.log('[result_ocr] Tesseract language "jpn" is not available; ranking name OCR is ASCII-only. Install jpn.traineddata system-wide or place it in soren91/tessdata/.');
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
  const signal = (text.match(/[A-Za-z0-9ぁ-んァ-ヶー一-龠々]/g) || []).length;
  if (signal < 4) return false;
  if (!/\s/.test(text) && text.length <= 3) return false;
  if (signal / text.length < 0.45) return false;
  if (!/[ぁ-んァ-ヶー一-龠々]/.test(text)) {
    const hasLongToken = text.split(' ').some(token => /[A-Za-z0-9]{4,}/.test(token));
    if (!hasLongToken) return false;
  }
  return true;
}

function normalizePlayerName(line) {
  return normalizeLine(line)
    .replace(/[•･]/g, '・')
    .replace(/[—―ｰ]/g, 'ー')
    .replace(/^\d{1,2}\s+/, '')
    .replace(/^[#@]?\d{1,2}[.)]?\s*/, '')
    .replace(/\s*・\s*/g, '・')
    .replace(/\s*[:：]\s*/g, ':')
    .replace(/\s+/g, ' ')
    .replace(/^[\s.:'"-]+|[\s.:'"-]+$/g, '')
    .trim();
}

function plausibleLatinPlayerName(text) {
  const compact = String(text || '').replace(/\s+/g, '');
  if (!/^[A-Za-z0-9:._-]+$/.test(compact)) return false;
  const letters = compact.match(/[A-Za-z]/g) || [];
  if (letters.length < 4) return false;
  if (!/[a-z]/.test(compact)) return false;
  const vowels = (compact.match(/[AEIOUaeiou]/g) || []).length;
  return vowels >= 2 && (vowels / letters.length) >= 0.28;
}

function usefulPlayerName(line, { japaneseAvailable = true } = {}) {
  const text = normalizePlayerName(line);
  if (!text || text.length < 3) return false;
  if (/(RANK|WAITING|NEXT|GAME|YOUR|HOLD|K\.?O\.?)/i.test(text)) return false;
  if (/^\d+$/.test(text)) return false;
  const signal = (text.match(/[A-Za-z0-9ぁ-んァ-ヶー一-龠々]/g) || []).length;
  if (signal < 3) return false;
  if (signal / text.length < 0.5) return false;
  if (/[ぁ-んァ-ヶー一-龠々]/.test(text)) return true;
  if (!japaneseAvailable) {
    if (!/^[\x20-\x7E]+$/.test(text)) return false;
    return plausibleLatinPlayerName(text);
  }
  return plausibleLatinPlayerName(text);
}

function canonicalizePlayerName(text) {
  return normalizePlayerName(text)
    .replace(/\s+/g, '')
    .replace(/[:：]/g, ':')
    .replace(/[・･]/g, '・');
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

function mergeRowsToLineObjects(rows, minConf = 0) {
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
    const validConf = parts.map(p => p.conf).filter(conf => conf >= 0);
    merged.push({
      text,
      top: Math.min(...parts.map(p => p.top)),
      left: Math.min(...parts.map(p => p.left)),
      conf: validConf.length > 0
        ? (validConf.reduce((sum, conf) => sum + conf, 0) / validConf.length)
        : 0,
    });
  }
  return merged.sort((a, b) => {
    if (a.top !== b.top) return a.top - b.top;
    return a.left - b.left;
  });
}

function lineObjectsToUniqueTexts(lineObjects, filterFn = usefulLine) {
  const seen = new Set();
  const texts = [];
  for (const line of lineObjects) {
    const text = normalizeLine(line?.text || '');
    if (!filterFn(text)) continue;
    if (seen.has(text)) continue;
    seen.add(text);
    texts.push(text);
  }
  return texts;
}

function rowsToLines(rows, minConf = 45) {
  return lineObjectsToUniqueTexts(mergeRowsToLineObjects(rows, minConf), usefulLine);
}

function extractPlayerNamesFromLineObjects(lineObjects) {
  const japaneseAvailable = hasTesseractLanguage('jpn');
  const buckets = new Map();
  for (const line of lineObjects) {
    const name = normalizePlayerName(line?.text || '');
    if (!usefulPlayerName(name, { japaneseAvailable })) continue;
    const key = canonicalizePlayerName(name);
    if (!key) continue;
    const score = safeNumber(line?.conf, 0) + (name.length * 0.5);
    const current = buckets.get(key) || {
      text: name,
      count: 0,
      confSum: 0,
      bestScore: Number.NEGATIVE_INFINITY,
      top: safeNumber(line?.top, 0),
    };
    current.count += 1;
    current.confSum += Math.max(0, safeNumber(line?.conf, 0));
    current.top = Math.min(current.top, safeNumber(line?.top, current.top));
    if (score >= current.bestScore) {
      current.text = name;
      current.bestScore = score;
    }
    buckets.set(key, current);
  }
  return [...buckets.values()]
    .sort((a, b) => {
      if (b.count !== a.count) return b.count - a.count;
      if (b.confSum !== a.confSum) return b.confSum - a.confSum;
      return a.top - b.top;
    })
    .map(item => item.text)
    .slice(0, 12);
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

function normalizeDetectedRankString(rawDigits) {
  const digits = String(rawDigits || '').replace(/\D+/g, '');
  if (!digits) return null;
  const direct = Number.parseInt(digits, 10);
  if (direct >= 1 && direct <= 91) return direct;
  if (digits.length >= 2) {
    const tail2 = Number.parseInt(digits.slice(-2), 10);
    if (tail2 >= 1 && tail2 <= 91) return tail2;
  }
  if (digits.length >= 1) {
    const tail1 = Number.parseInt(digits.slice(-1), 10);
    if (tail1 >= 1 && tail1 <= 9) return tail1;
  }
  return null;
}

function extractRankFromRankingLines(lineObjects, height) {
  const topLimit = Math.floor((height || 720) * 0.28);
  const candidates = [];
  for (const line of lineObjects) {
    const text = normalizeLine(line?.text || '');
    if (!text) continue;
    if (line.top > topLimit) continue;
    const compact = text.toUpperCase().replace(/\s+/g, '');
    if (!compact.includes('RANK')) continue;
    const rankSlice = compact.slice(compact.indexOf('RANK'));
    const rank = normalizeDetectedRankString(rankSlice);
    if (rank == null) continue;
    candidates.push({
      rank,
      conf: Number(line.conf || 0),
      top: Number(line.top || 0),
    });
  }
  candidates.sort((a, b) => {
    if (b.conf !== a.conf) return b.conf - a.conf;
    return a.top - b.top;
  });
  return candidates[0]?.rank ?? null;
}

function runTesseractTsv(imagePath, options = {}) {
  const languages = resolveTesseractLanguages(options.languages || ['eng']);
  if (!languages) return '';
  try {
    return execFileSync(
      'tesseract',
      [
        imagePath,
        'stdout',
        '-l',
        languages,
        '--psm',
        String(options.psm || 6),
        ...(options.extraArgs || []),
        'tsv',
      ],
      {
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'ignore'],
        env: getTesseractEnv(),
      }
    );
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
      playerNames: [],
      rankCandidates: [],
    };
  }

  maybeWarnMissingJapanese();

  const meta = await sharp(imagePath).metadata();
  const width = meta.width || 1280;
  const height = meta.height || 720;
  const tempDir = mkdtempSync(join(tmpdir(), 'soren91_result_ocr_'));
  const rankingListRect = relativeRect(width, height, RANKING_LIST_REGION);
  const rankingNameRect = relativeRect(width, height, RANKING_NAME_REGION);

  try {
    const variants = [
      {
        name: 'full_rank_raw',
        path: imagePath,
        languages: ['eng'],
        psm: 6,
        lineMinConf: 100,
        rankMinConf: 70,
        allowRank: true,
        collectRankLines: true,
        collectGeneralLines: true,
      },
      {
        name: 'full_standard',
        path: await writeVariant(imagePath, tempDir, 'full_standard', img =>
          img.grayscale().normalize().sharpen().threshold(150)
        ),
        languages: ['eng'],
        psm: 6,
        lineMinConf: 55,
        rankMinConf: 70,
        allowRank: false,
        collectRankLines: true,
        collectGeneralLines: true,
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
        languages: ['eng'],
        psm: 6,
        lineMinConf: 45,
        rankMinConf: 60,
        allowRank: false,
        collectRankLines: true,
        collectGeneralLines: false,
      },
      {
        name: 'ranking_list_gray',
        path: await writeVariant(imagePath, tempDir, 'ranking_list_gray', img =>
          img
            .extract(rankingListRect)
            .resize({ width: rankingListRect.width * 2 })
            .grayscale()
            .normalize()
            .sharpen()
        ),
        languages: ['jpn', 'eng'],
        psm: 4,
        lineMinConf: 18,
        allowRank: false,
        collectGeneralLines: false,
        collectPlayerNames: true,
        extraArgs: ['-c', 'preserve_interword_spaces=1'],
      },
      {
        name: 'ranking_list_threshold',
        path: await writeVariant(imagePath, tempDir, 'ranking_list_threshold', img =>
          img
            .extract(rankingListRect)
            .resize({ width: rankingListRect.width * 2 })
            .grayscale()
            .normalize()
            .sharpen()
            .threshold(150)
        ),
        languages: ['jpn', 'eng'],
        psm: 4,
        lineMinConf: 18,
        allowRank: false,
        collectGeneralLines: false,
        collectPlayerNames: true,
        extraArgs: ['-c', 'preserve_interword_spaces=1'],
      },
      {
        name: 'ranking_names_gray',
        path: await writeVariant(imagePath, tempDir, 'ranking_names_gray', img =>
          img
            .extract(rankingNameRect)
            .resize({ width: rankingNameRect.width * 3 })
            .grayscale()
            .normalize()
            .sharpen()
        ),
        languages: ['jpn', 'eng'],
        psm: 6,
        lineMinConf: 12,
        allowRank: false,
        collectGeneralLines: false,
        collectPlayerNames: true,
        extraArgs: ['-c', 'preserve_interword_spaces=1'],
      },
      {
        name: 'ranking_names_threshold',
        path: await writeVariant(imagePath, tempDir, 'ranking_names_threshold', img =>
          img
            .extract(rankingNameRect)
            .resize({ width: rankingNameRect.width * 3 })
            .grayscale()
            .normalize()
            .sharpen()
            .threshold(150)
        ),
        languages: ['jpn', 'eng'],
        psm: 6,
        lineMinConf: 12,
        allowRank: false,
        collectGeneralLines: false,
        collectPlayerNames: true,
        extraArgs: ['-c', 'preserve_interword_spaces=1'],
      },
    ];

    const collectedLines = [];
    const rankCandidates = [];
    const rankLineObjects = [];
    const playerNameLineObjects = [];

    for (const variant of variants) {
      const tsv = runTesseractTsv(variant.path, {
        languages: variant.languages,
        psm: variant.psm,
        extraArgs: variant.extraArgs,
      });
      if (!tsv) continue;
      const rows = parseTsvRows(tsv);
      const lineObjects = mergeRowsToLineObjects(rows, variant.lineMinConf ?? 0);

      if (variant.collectRankLines) {
        rankLineObjects.push(...lineObjects);
      }
      if (variant.collectGeneralLines) {
        collectedLines.push(...lineObjectsToUniqueTexts(lineObjects, usefulLine));
      }
      if (variant.collectPlayerNames) {
        playerNameLineObjects.push(...lineObjects);
      }
      if (variant.allowRank) {
        rankCandidates.push(...extractRankCandidates(rows, variant.rankMinConf));
      }
    }

    const playerNames = extractPlayerNamesFromLineObjects(playerNameLineObjects);
    const lines = [...new Set([...playerNames, ...collectedLines])].slice(0, 12);
    const starRank = await detectRankingScreen(imagePath);
    const rankingLineRank = extractRankFromRankingLines(rankLineObjects, height);
    const rank = (starRank != null && starRank > 0)
      ? starRank
      : (rankingLineRank ?? chooseBestRank(rankCandidates));

    return {
      imagePath: basename(imagePath),
      detected: true,
      rank,
      rankSource: (starRank != null && starRank > 0)
        ? 'red_star'
        : (rankingLineRank != null ? 'ranking_line' : (rank != null ? 'raw_digit' : null)),
      rankCandidates: [...new Set(rankCandidates.map(item => item.value))].sort((a, b) => a - b),
      lines,
      playerNames,
    };
  } finally {
    rmSync(tempDir, { recursive: true, force: true });
  }
}

if (import.meta.url === new URL(process.argv[1] || '', 'file:').href) {
  const imagePath = process.argv[2];
  const result = await analyzeResultScreen(imagePath);
  writeFileSync(process.stdout.fd, `${JSON.stringify(result, null, 2)}\n`);
}

import {
  closeSync,
  constants,
  fstatSync,
  lstatSync,
  openSync,
  readSync,
  unlinkSync,
  writeFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
} from 'fs';
import { basename, dirname, join } from 'path';

const MAX_HISTORY_LINES = 4096;
const MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024;
const MAX_STRATEGY_BYTES = 256 * 1024;
const TURN_FILE_RE = /^turn_(\d+)(?:[._-][A-Za-z0-9._-]+)?\.(?:png|jpe?g)$/i;

function finiteNumber(value, fallback = null) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

function minFinite(values) {
  const xs = values.filter(Number.isFinite);
  return xs.length ? Math.min(...xs) : null;
}

function hasContiguousTurnLineage(records) {
  if (!records.length) return true;
  if (!records.every(record => Number.isInteger(record.turn))) return false;
  if (records[0].turn !== 0) return false;
  for (let i = 1; i < records.length; i += 1) {
    if (records[i].turn !== records[i - 1].turn + 1) return false;
  }
  return true;
}

function selectCriticalTurns(records) {
  if (!records.length) return [];
  const withTurn = records.filter(record => Number.isInteger(record.turn));
  if (!withTurn.length) return [];

  const riskRecord = [...withTurn].sort((a, b) => {
    const ar = Math.max(
      finiteNumber(a?.decision?.diagnostics?.risk, 0),
      finiteNumber(a?.decision?.diagnostics?.pathRisk, 0),
    );
    const br = Math.max(
      finiteNumber(b?.decision?.diagnostics?.risk, 0),
      finiteNumber(b?.decision?.diagnostics?.pathRisk, 0),
    );
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
  return [...new Set([
    riskRecord?.turn,
    confidenceRecord?.turn,
    clearanceRecord?.turn,
    latest?.turn,
  ].filter(Number.isInteger))].slice(0, 3);
}

function analyzeHistoryText(text) {
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

  return {
    ok: true,
    contiguousTurns: hasContiguousTurnLineage(records),
    criticalTurns: selectCriticalTurns(records),
  };
}

function parsedTurnFile(name) {
  const match = typeof name === 'string' ? name.match(TURN_FILE_RE) : null;
  const turn = match ? Number(match[1]) : NaN;
  return Number.isSafeInteger(turn) ? { name, turn } : null;
}

/**
 * Pick at most maxShots bounded gameplay frames. When critical turns are
 * available they win first; remaining slots retain the established
 * early/middle/late coverage used by the runtime before critical-turn
 * evidence was introduced.
 */
export function selectCriticalSnapshotNames(fileNames, maxShots = 3, preferredTurns = []) {
  if (!Number.isInteger(maxShots) || maxShots <= 0) return [];

  // The archive has already chosen the freshest safe file per turn. Keep the
  // name-only API deterministic too: aliases must not consume extra slots.
  const byTurn = new Map();
  for (const file of [...new Set(fileNames)].sort().map(parsedTurnFile).filter(Boolean)) {
    byTurn.set(file.turn, file);
  }
  const files = [...byTurn.values()].sort((a, b) => a.turn - b.turn);
  if (files.length <= maxShots) return files.map(file => file.name);

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
    const baselineIndexes = [
      Math.min(2, files.length - 1),
      Math.floor(files.length / 2),
      files.length - 1,
    ];
    for (const index of baselineIndexes) {
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

  return files.filter(file => selected.has(file.name)).slice(0, maxShots).map(file => file.name);
}

/**
 * snapshotCurrentStrategyForGame() writes this per-game file synchronously
 * before the first capture, including the first game and every normal round.
 * Reuse that existing lifecycle boundary instead of adding I/O to the drop
 * loop. Missing, ambiguous or future boundaries suppress images, never guess.
 */
function gameStartBoundary(screenshotDir, outputDir, historyFile, nowMs) {
  const gameName = basename(outputDir);
  const match = gameName.match(/^game_(\d+)$/);
  if (!match) return null;
  const game = Number(match[1]);
  if (!Number.isSafeInteger(game) || game <= 0 || String(game).padStart(4, '0') !== match[1]) return null;
  if (historyFile && ![`latest_${match[1]}.jsonl`, `${gameName}.jsonl`].includes(basename(historyFile))) return null;
  const markerDir = join(dirname(screenshotDir), 'strategy_snapshots');
  try {
    if (!lstatSync(markerDir).isDirectory()) return null;
    const info = lstatSync(join(markerDir, `${gameName}_strategy.mjs`));
    if (!info.isFile() || info.size <= 0 || info.size > MAX_STRATEGY_BYTES
        || !Number.isFinite(info.mtimeMs) || info.mtimeMs < 0 || info.mtimeMs > nowMs) return null;
    return info.mtimeMs;
  } catch {
    return null;
  }
}

function sameFileRevision(a, b) {
  return ['dev', 'ino', 'size', 'mtimeMs', 'ctimeMs'].every(key => a[key] === b[key]);
}

function readSelectedScreenshot(path, expected) {
  let fd;
  try {
    fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
    const before = fstatSync(fd);
    if (!before.isFile() || !sameFileRevision(before, expected)) return null;
    // Bounded read, even if a live writer grows the file after the first stat.
    const data = Buffer.alloc(before.size + 1);
    let size = 0;
    while (size < data.length) {
      const count = readSync(fd, data, size, data.length - size, null);
      if (!count) break;
      size += count;
    }
    const after = fstatSync(fd);
    if (size !== before.size || !sameFileRevision(before, after)) return null;
    return data.subarray(0, size);
  } catch {
    return null;
  } finally {
    if (fd !== undefined) closeSync(fd);
  }
}

/**
 * Archive bounded visual evidence for one completed game. Malformed/missing
 * history keeps early/middle/late sampling, but only inside an established
 * game boundary. Discontinuous history still suppresses ambiguous evidence.
 * The shared capture directory is never mutated. Only this game's managed
 * archive images are replaced; strategy and gameplay gates are untouched.
 */
export function archiveCriticalTurnScreenshots({
  screenshotDir,
  outputDir,
  historyFile,
  maxShots = 3,
}) {
  let preferredTurns = [];
  let historyStatus = 'missing';

  if (historyFile && existsSync(historyFile)) {
    try {
      const analyzed = analyzeHistoryText(readFileSync(historyFile, 'utf-8'));
      if (analyzed.ok) {
        if (analyzed.contiguousTurns) {
          preferredTurns = analyzed.criticalTurns;
          historyStatus = 'ok';
        } else {
          historyStatus = 'discontinuous';
        }
      } else {
        historyStatus = 'invalid';
      }
    } catch {
      historyStatus = 'invalid';
    }
  }

  if (!lstatSync(screenshotDir).isDirectory()) throw new Error('screenshot directory is not a real directory');
  // Date.now() has millisecond precision; include the rest of the current ms.
  const nowMs = Date.now() + 1;
  const startedAtMs = gameStartBoundary(screenshotDir, outputDir, historyFile, nowMs);
  const sessionStatus = startedAtMs === null ? 'missing-or-invalid' : 'ok';
  const byTurn = new Map();
  if (startedAtMs !== null && historyStatus !== 'discontinuous') {
    for (const name of readdirSync(screenshotDir)) {
      const file = parsedTurnFile(name);
      if (!file) continue;
      try {
        const info = lstatSync(join(screenshotDir, name));
        // Equality at the boundary cannot prove which game wrote the frame.
        if (!info.isFile() || info.size <= 0 || info.size > MAX_SCREENSHOT_BYTES
            || info.mtimeMs <= startedAtMs || info.mtimeMs > nowMs) continue;
        const previous = byTurn.get(file.turn);
        if (!previous || info.mtimeMs > previous.info.mtimeMs
            || (info.mtimeMs === previous.info.mtimeMs && name > previous.name)) {
          byTurn.set(file.turn, { ...file, info });
        }
      } catch { /* A disappeared capture is optional evidence. */ }
    }
  }
  const candidates = new Map([...byTurn.values()].map(file => [file.name, file]));
  const selected = selectCriticalSnapshotNames([...candidates.keys()], maxShots, preferredTurns);
  const snapshots = [];
  for (const name of selected) {
    const data = readSelectedScreenshot(join(screenshotDir, name), candidates.get(name).info);
    if (data) snapshots.push({ name, data });
  }

  mkdirSync(outputDir, { recursive: true });
  if (!lstatSync(outputDir).isDirectory()) throw new Error('archive directory is not a real directory');
  // Re-archiving after a format change must not leave old PNG/JPEG aliases for
  // the exporter. Remove only managed turn files/links, never unrelated files.
  for (const entry of readdirSync(outputDir, { withFileTypes: true })) {
    if (parsedTurnFile(entry.name) && (entry.isFile() || entry.isSymbolicLink())) {
      unlinkSync(join(outputDir, entry.name));
    }
  }
  const names = [];
  for (const { name, data } of snapshots) {
    writeFileSync(join(outputDir, name), data, { flag: 'wx', mode: 0o600 });
    names.push(name);
  }

  return {
    archived: names.length,
    names,
    preferredTurns,
    historyStatus,
    sessionStatus,
  };
}

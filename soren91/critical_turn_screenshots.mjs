import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
} from 'fs';
import { join } from 'path';

const MAX_HISTORY_LINES = 4096;

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
  const match = String(name).match(/^turn_(\d+).*\.png$/i);
  return match ? { name: String(name), turn: Number.parseInt(match[1], 10) } : null;
}

/**
 * Pick at most maxShots bounded gameplay frames. When critical turns are
 * available they win first; remaining slots retain the established
 * early/middle/late coverage used by the runtime before critical-turn
 * evidence was introduced.
 */
export function selectCriticalSnapshotNames(fileNames, maxShots = 3, preferredTurns = []) {
  if (!Number.isInteger(maxShots) || maxShots <= 0) return [];

  const files = [...new Set(fileNames)]
    .map(parsedTurnFile)
    .filter(Boolean)
    .sort((a, b) => a.turn - b.turn || a.name.localeCompare(b.name));
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
 * Archive bounded visual evidence for one completed game. History parsing is
 * fail-soft for archival only: malformed/missing history falls back to the
 * established early/middle/late sample. A syntactically valid history whose
 * turn lineage resets/skips is different: turn_N no longer names one unique
 * session observation, so pairing screenshots with those turns would create
 * misleading evidence. In that case visual evidence is suppressed fail-closed.
 * This helper never mutates strategy.
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

  const sourceNames = readdirSync(screenshotDir)
    .filter(name => /^turn_\d+.*\.png$/i.test(name));
  const names = historyStatus === 'discontinuous'
    ? []
    : selectCriticalSnapshotNames(sourceNames, maxShots, preferredTurns);

  mkdirSync(outputDir, { recursive: true });
  for (const name of names) {
    copyFileSync(join(screenshotDir, name), join(outputDir, name));
  }

  return {
    archived: names.length,
    names,
    preferredTurns,
    historyStatus,
  };
}

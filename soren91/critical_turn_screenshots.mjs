import {
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
} from 'fs';
import { join } from 'path';

import { analyzeHistoryText } from './daily_evidence.mjs';

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
 * Archive the bounded visual evidence for one completed game. History parsing
 * is fail-soft for archival only: malformed/missing history falls back to the
 * existing early/middle/late sample, while the daily evidence builder itself
 * remains fail-closed on invalid histories before any strategy mutation.
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
        preferredTurns = analyzed.criticalTurns;
        historyStatus = 'ok';
      } else {
        historyStatus = 'invalid';
      }
    } catch {
      historyStatus = 'invalid';
    }
  }

  const sourceNames = readdirSync(screenshotDir)
    .filter(name => /^turn_\d+.*\.png$/i.test(name));
  const names = selectCriticalSnapshotNames(sourceNames, maxShots, preferredTurns);

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

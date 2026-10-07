#!/usr/bin/env node
/**
 * Preserve incomplete Soren91 histories before a new player process starts.
 *
 * main.mjs deliberately resets its in-memory turn/session state on process
 * startup. Reusing a pre-existing latest_N.jsonl would therefore concatenate
 * two distinct runtime sessions into one completed game_N.jsonl. Move those
 * partial files aside atomically instead; completed-history consumers continue
 * to see only a single contiguous session while the abandoned evidence remains
 * available inside the normal retention window.
 */

import { existsSync, lstatSync, mkdirSync, readdirSync, renameSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const LATEST_HISTORY_RE = /^latest_(\d+)\.jsonl$/;

function chooseArchivePath(historyDir, gameNumber, now) {
  for (let collision = 0; collision < 1000; collision += 1) {
    const candidate = join(historyDir, `abandoned_${gameNumber}_${now}_${collision}.jsonl`);
    if (!existsSync(candidate)) return candidate;
  }
  throw new Error('archive_name_exhausted');
}

function resolveNow(value) {
  return Number.isSafeInteger(value) && value >= 0 ? value : Date.now();
}

/**
 * Archive one game's pre-existing latest_N.jsonl as an abandoned partial
 * session. Returns the archive path, or null when there is nothing to move.
 * Throws (fail closed) on unsafe filesystem entries instead of deleting them.
 *
 * A restarted process always resets turn/session state to zero, so the old
 * file is a different logical session: the caller must write the new session
 * to a fresh latest_N.jsonl afterwards.
 *
 * @param {string} [historyDir]
 * @param {number|string} gameNumber
 * @param {object} [options]
 * @param {number} [options.now]
 * @param {(message: string) => void} [options.log]
 * @returns {string|null}
 */
export function archiveLatestHistory(historyDir, gameNumber, options = {}) {
  const dir = historyDir || 'game_history';
  const now = resolveNow(options.now);
  const log = options.log || (() => {});
  const padded = String(gameNumber).padStart(4, '0');

  const source = join(dir, `latest_${padded}.jsonl`);
  if (!existsSync(source)) return null;

  // Never follow or rewrite unexpected filesystem objects. A fixed latest_N
  // history created by main.mjs is always a regular, single-link file.
  const stat = lstatSync(source);
  if (!stat.isFile() || stat.nlink !== 1) {
    throw new Error('unsafe_latest_history_entry');
  }

  const target = chooseArchivePath(dir, padded, now);
  renameSync(source, target);
  log(`[history] archived_partial_session=${padded} -> ${target}`);
  return target;
}

/**
 * @param {object} [options]
 * @param {string} [options.historyDir]
 * @param {number} [options.now]
 * @param {(message: string) => void} [options.log]
 * @returns {{ archived: number }}
 */
export function archivePartialHistories(options = {}) {
  const historyDir = options.historyDir || 'game_history';
  const now = resolveNow(options.now);
  const log = options.log || (() => {});

  mkdirSync(historyDir, { recursive: true });
  const entries = readdirSync(historyDir, { withFileTypes: true })
    .filter(entry => LATEST_HISTORY_RE.test(entry.name))
    .sort((a, b) => a.name.localeCompare(b.name));

  let archived = 0;
  for (const entry of entries) {
    const match = entry.name.match(LATEST_HISTORY_RE);
    const source = join(historyDir, entry.name);
    const stat = lstatSync(source);

    // Never follow or rewrite unexpected filesystem objects. A fixed latest_N
    // history created by main.mjs is always a regular, single-link file.
    if (!entry.isFile() || !stat.isFile() || stat.nlink !== 1) {
      throw new Error('unsafe_latest_history_entry');
    }

    const target = chooseArchivePath(historyDir, match[1], now);
    renameSync(source, target);
    archived += 1;
  }

  log(`[history] archived_partial_sessions=${archived}`);
  return { archived };
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  try {
    archivePartialHistories({ log: console.log });
  } catch {
    console.error('[history] partial_history_archive_failed');
    process.exitCode = 1;
  }
}

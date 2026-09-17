#!/usr/bin/env node
/**
 * cleanup_retention.mjs — Soren91 runtime evidence retention.
 *
 * Automatic strategy improvement was retired in 2026-09.  Retention therefore
 * no longer depends on an improve_daily consumption ledger or pending PR state.
 * Match histories, summaries, screenshots, strategy snapshots and optional Jev
 * shadow ledgers are retained for N days (default: 3) so they remain available
 * for explicit/manual review, then removed by age to keep runtime storage bounded.
 *
 * Never touches strategy.mjs, strategy_versions/, tmp/state/, or advice91.md.
 *
 * CLI: node cleanup_retention.mjs [--runtime-dir DIR] [--days N] [--dry-run]
 * env: SOREN91_RETENTION_DAYS (default 3)
 */

import { existsSync, readdirSync, statSync, rmSync } from 'fs';
import { join, dirname, resolve } from 'path';
import { fileURLToPath } from 'url';

const HERE = dirname(fileURLToPath(import.meta.url));

export const RETENTION_TARGETS = [
  'tmp/summaries',
  'game_history',
  'tmp/game_screenshots',
  'tmp/strategy_snapshots',
  'tmp/screenshots',
  'tmp/jev_shadow',
];

function isManagedArtifact(rel, name) {
  const patterns = rel === 'tmp/summaries'
    ? [/^game_\d+\.json$/, /^ranking_\d+\.png$/]
    : rel === 'game_history'
      ? [/^game_\d+\.jsonl$/, /^latest_\d+\.jsonl$/]
      : rel === 'tmp/game_screenshots'
        ? [/^game_\d+$/]
        : rel === 'tmp/strategy_snapshots'
          ? [/^game_\d+_strategy\.mjs$/]
          : rel === 'tmp/jev_shadow'
            ? [/^game_\d+\.jsonl$/]
            : [/^game_\d+(?:[._-].*)?$/];
  return patterns.some(pattern => pattern.test(name));
}

/**
 * @param {object} options
 * @param {string} options.runtimeDir
 * @param {number} [options.days]
 * @param {number} [options.now]
 * @param {boolean} [options.dryRun]
 * @param {(msg: string) => void} [options.log]
 * @returns {{ removed: number, kept: number, errors: number }}
 */
export function cleanupRetention(options) {
  const {
    runtimeDir,
    days = 3,
    now = Date.now(),
    dryRun = false,
    log = () => {},
  } = options || {};
  if (!runtimeDir) throw new Error('cleanupRetention requires runtimeDir');
  const safeDays = Number.isFinite(days) && days >= 0 ? days : 3;
  const cutoff = now - safeDays * 24 * 60 * 60 * 1000;

  let removed = 0;
  let kept = 0;
  let errors = 0;

  for (const rel of RETENTION_TARGETS) {
    const dir = join(runtimeDir, rel);
    if (!existsSync(dir)) continue;
    let entries;
    try {
      entries = readdirSync(dir, { withFileTypes: true });
    } catch {
      errors += 1;
      continue;
    }
    for (const entry of entries) {
      if (!isManagedArtifact(rel, entry.name)) {
        kept += 1;
        continue;
      }
      const path = join(dir, entry.name);
      let mtimeMs;
      try {
        mtimeMs = statSync(path).mtimeMs;
      } catch {
        errors += 1;
        continue;
      }
      if (mtimeMs >= cutoff) {
        kept += 1;
        continue;
      }
      if (!dryRun) {
        try {
          rmSync(path, { recursive: true, force: true });
        } catch {
          errors += 1;
          continue;
        }
      }
      removed += 1;
    }
  }

  log(`[retention] removed=${removed} kept=${kept} errors=${errors} (days=${safeDays}${dryRun ? ', dry-run' : ''})`);
  return { removed, kept, errors };
}

function parseArgs(argv) {
  const opts = {
    runtimeDir: HERE,
    days: Number.parseInt(process.env.SOREN91_RETENTION_DAYS || '3', 10),
    dryRun: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };
    switch (arg) {
      case '--runtime-dir': opts.runtimeDir = resolve(next()); break;
      case '--days': opts.days = Number.parseInt(next(), 10); break;
      case '--dry-run': opts.dryRun = true; break;
      default:
        if (arg.startsWith('-')) throw new Error(`unknown option: ${arg}`);
    }
  }
  return opts;
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  const opts = parseArgs(process.argv.slice(2));
  cleanupRetention({ ...opts, log: console.log });
}

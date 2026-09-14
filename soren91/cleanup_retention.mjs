#!/usr/bin/env node
/**
 * cleanup_retention.mjs — soren91 ランタイムの保持ポリシー
 *
 * コーナー起動時に呼び、**直近 N 日 (既定 3 日) より古い** 試合ログ/スクショ/
 * スナップショットを削除する。改善フローの成否に依存せず、ディスク使用量を
 * 有界にするための安全弁 (2026-09-15 追加)。
 *
 * 消す対象 (runtimeDir 配下):
 *   - tmp/summaries/            (game_*.json, ranking_*.png)
 *   - game_history/             (game_*.jsonl, latest_*.jsonl)
 *   - tmp/game_screenshots/     (game_NNNN/ ディレクトリ)
 *   - tmp/strategy_snapshots/   (game_NNNN_strategy.mjs)
 *   - tmp/screenshots/
 *
 * 消さない: strategy.mjs / strategy_versions/ / tmp/state/ / advice91.md など
 * (これらは保持対象・単一の正本)。
 *
 * CLI: node cleanup_retention.mjs [--runtime-dir DIR] [--days N] [--dry-run]
 * env: SOREN91_RETENTION_DAYS (既定 3)
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
];

/**
 * @param {object} options
 * @param {string} options.runtimeDir
 * @param {number} [options.days]   保持日数 (既定 3)
 * @param {number} [options.now]    現在時刻 (ms, テスト用)
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
      const path = join(dir, entry.name);
      let mtimeMs;
      try {
        mtimeMs = statSync(path).mtimeMs;
      } catch {
        errors += 1;
        continue;
      }
      if (mtimeMs < cutoff) {
        if (!dryRun) {
          try {
            rmSync(path, { recursive: true, force: true });
          } catch {
            errors += 1;
            continue;
          }
        }
        removed += 1;
      } else {
        kept += 1;
      }
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

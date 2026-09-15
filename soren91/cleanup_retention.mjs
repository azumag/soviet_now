#!/usr/bin/env node
/**
 * cleanup_retention.mjs — soren91 ランタイムの保持ポリシー
 *
 * コーナー起動時に呼び、改善フローで消費済みになった試合のうち
 * 直近 N 日 (既定 3 日) より古いログ/スクショ/スナップショットだけを削除する。
 * 外部改善runnerが停止・遅延しても、未消費またはpending PR対象の入力は削除しない。
 *
 * 消す対象 (runtimeDir 配下):
 *   - tmp/summaries/            (game_*.json, ranking_*.png)
 *   - game_history/             (game_*.jsonl, latest_*.jsonl)
 *   - tmp/game_screenshots/     (game_NNNN/ ディレクトリ)
 *   - tmp/strategy_snapshots/   (game_NNNN_strategy.mjs)
 *   - tmp/screenshots/          (game_NNNN... のみ。識別不能な項目は保持)
 *
 * 消さない: strategy.mjs / strategy_versions/ / tmp/state/ / advice91.md など。
 * improve_daily state が欠落/不正な場合も fail-closed で何も削除しない。
 *
 * CLI: node cleanup_retention.mjs [--runtime-dir DIR] [--days N] [--dry-run]
 * env: SOREN91_RETENTION_DAYS (既定 3)
 */

import { existsSync, readdirSync, readFileSync, statSync, rmSync } from 'fs';
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

function parseGameNumber(rel, name) {
  const patterns = rel === 'tmp/summaries'
    ? [/^game_(\d+)\.json$/, /^ranking_(\d+)\.png$/]
    : rel === 'game_history'
      ? [/^game_(\d+)\.jsonl$/, /^latest_(\d+)\.jsonl$/]
      : rel === 'tmp/game_screenshots'
        ? [/^game_(\d+)$/]
        : rel === 'tmp/strategy_snapshots'
          ? [/^game_(\d+)_strategy\.mjs$/]
          : [/^game_(\d+)(?:[._-].*)?$/];
  for (const pattern of patterns) {
    const match = name.match(pattern);
    if (match) return Number.parseInt(match[1], 10);
  }
  return null;
}

function readConsumptionState(runtimeDir) {
  const path = join(runtimeDir, 'tmp', 'state', 'improve_daily.json');
  try {
    const state = JSON.parse(readFileSync(path, 'utf-8'));
    const lastConsumedGame = state?.lastConsumedGame;
    if (!Number.isInteger(lastConsumedGame) || lastConsumedGame < 0) return null;
    let pending = null;
    const fromGame = state?.pendingPr?.fromGame;
    const toGame = state?.pendingPr?.toGame;
    if (Number.isInteger(fromGame) && Number.isInteger(toGame) && fromGame >= 0 && toGame >= fromGame) {
      pending = { fromGame, toGame };
    }
    return { lastConsumedGame, pending };
  } catch {
    return null;
  }
}

function isConsumedAndNotPending(game, state) {
  if (!Number.isInteger(game) || game > state.lastConsumedGame) return false;
  if (state.pending && game >= state.pending.fromGame && game <= state.pending.toGame) return false;
  return true;
}

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
  const state = readConsumptionState(runtimeDir);
  if (!state) {
    log('[retention] skipped: improve_daily state missing or invalid; refusing to delete unconsumed inputs');
    return { removed: 0, kept: 0, errors: 1 };
  }
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
      const game = parseGameNumber(rel, entry.name);
      if (!isConsumedAndNotPending(game, state)) {
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

  log(`[retention] removed=${removed} kept=${kept} errors=${errors} (days=${safeDays}, lastConsumed=${state.lastConsumedGame}${dryRun ? ', dry-run' : ''})`);
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

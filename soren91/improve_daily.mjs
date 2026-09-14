#!/usr/bin/env node
/**
 * improve_daily.mjs — 外部日次改善ランナー (VM外・1日1回)
 *
 * ねらい (2026-09-15 設計):
 *   - bot 内の改善は止める (docich 側が SOREN91_EXTERNAL_IMPROVE=1 を渡す)。
 *   - 改善は「VM外部から1日1回、ファイルを見て改善PRを作る」方式にする。
 *   - 改善に使った試合ログ/スクショは、**改善PRのマージ確定時に消す**
 *     (未マージ分を二重に取り込まないため、クリア範囲は PR に埋め込む)。
 *
 * 想定運用: VM の soren91 ランタイムを repo チェックアウトの soren91/ へ同期してから
 * このスクリプトを実行する (同期はスケジューラ側の責務)。実行後は改善PRが作られ、
 * PR本文の `<!-- improve-daily: from=.. to=.. -->` を scheduler が読み、マージ確認後に
 * `--reconcile` で消費済みファイルを消す。
 *
 * サブコマンド:
 *   propose     (既定) 新しい試合から改善候補を作り、strategy.mjs を書き換えて PR を作る
 *   reconcile   直近の改善PRがマージ済みなら、消費済みの入力を消して state を進める
 *
 * 主なオプション:
 *   --runtime-dir <dir>  ランタイム (default: このスクリプトのあるディレクトリ)
 *   --repo-dir <dir>     git ルート (default: <runtime-dir>/..)
 *   --state <path>       state ファイル (default: <runtime-dir>/tmp/state/improve_daily.json)
 *   --dry-run            外部呼び出し/git/gh/削除をせず、やることだけ表示
 *   --no-pr              PR を作らず、候補と manifest の書き出しだけ行う
 *   --clear-cmd <cmd>    reconcile 時のクリア実行コマンド (例: 'ssh vm bash -s')
 */

import 'dotenv/config';
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, unlinkSync } from 'fs';
import { join, dirname, resolve } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';

const HERE = dirname(fileURLToPath(import.meta.url));

// ------------------------------- utilities --------------------------------

function parseArgs(argv) {
  const opts = {
    command: 'propose',
    runtimeDir: HERE,
    repoDir: null,
    state: null,
    dryRun: false,
    noPr: false,
    clearCmd: process.env.SOREN91_DAILY_CLEAR_CMD || '',
    base: process.env.SOREN91_DAILY_BASE || 'main',
  };
  const positional = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };
    switch (arg) {
      case 'propose':
      case 'reconcile':
        opts.command = arg;
        break;
      case '--runtime-dir': opts.runtimeDir = resolve(next()); break;
      case '--repo-dir': opts.repoDir = resolve(next()); break;
      case '--state': opts.state = resolve(next()); break;
      case '--clear-cmd': opts.clearCmd = next(); break;
      case '--base': opts.base = next(); break;
      case '--dry-run': opts.dryRun = true; break;
      case '--no-pr': opts.noPr = true; break;
      default:
        if (arg.startsWith('-')) throw new Error(`unknown option: ${arg}`);
        positional.push(arg);
    }
  }
  if (!opts.repoDir) opts.repoDir = resolve(opts.runtimeDir, '..');
  if (!opts.state) opts.state = join(opts.runtimeDir, 'tmp', 'state', 'improve_daily.json');
  opts.positional = positional;
  return opts;
}

function readJson(path, fallback = null) {
  try {
    return JSON.parse(readFileSync(path, 'utf-8'));
  } catch {
    return fallback;
  }
}

function writeJson(path, data) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, JSON.stringify(data, null, 2) + '\n');
}

function log(...args) {
  console.log('[improve_daily]', ...args);
}

// ------------------------------ range scanning ------------------------------

/**
 * 利用可能な game_*.json (要約) の範囲を返す。
 * @returns {{ games: number[], from: number|null, to: number|null }}
 */
export function scanGameRange(runtimeDir) {
  const dir = join(runtimeDir, 'tmp', 'summaries');
  if (!existsSync(dir)) return { games: [], from: null, to: null };
  const games = readdirSync(dir)
    .map(name => {
      const m = name.match(/^game_(\d+)\.json$/);
      return m ? Number.parseInt(m[1], 10) : null;
    })
    .filter(n => Number.isFinite(n))
    .sort((a, b) => a - b);
  return { games, from: games[0] ?? null, to: games[games.length - 1] ?? null };
}

/** 未消費 (lastConsumedGame より後) の試合があるか。 */
export function pendingRange(runtimeDir, lastConsumedGame) {
  const { games, from, to } = scanGameRange(runtimeDir);
  const consumed = Number.isFinite(lastConsumedGame) ? lastConsumedGame : 0;
  const fresh = games.filter(n => n > consumed);
  return { games, from, to, fresh, hasFresh: fresh.length > 0 };
}

/**
 * 改善PRに埋め込むクリア manifest を作る。**消費した試合だけ**を範囲指定する
 * (glob 全消しはしない: PRレビュー中に増えた未消費の試合を消さないため)。
 * なおディスクの恒常的な有界化は、コーナー起動時の cleanup_retention.mjs が担う。
 */
export function buildClearManifest(fromGame, toGame) {
  const pad = n => String(n).padStart(4, '0');
  const games = [];
  const targets = [];
  for (let i = fromGame; i <= toGame; i += 1) {
    games.push(i);
    targets.push(
      `tmp/summaries/game_${pad(i)}.json`,
      `tmp/summaries/ranking_${pad(i)}.png`,
      `game_history/game_${pad(i)}.jsonl`,
      `tmp/game_screenshots/game_${pad(i)}`,
      `tmp/strategy_snapshots/game_${pad(i)}_strategy.mjs`,
    );
  }
  return { fromGame, toGame, games, targets };
}

export function formatPrMarker(fromGame, toGame) {
  return `<!-- improve-daily: from=${fromGame} to=${toGame} -->`;
}

export function parsePrMarker(text) {
  const m = String(text || '').match(/<!--\s*improve-daily:\s*from=(\d+)\s+to=(\d+)\s*-->/);
  if (!m) return null;
  return { fromGame: Number.parseInt(m[1], 10), toGame: Number.parseInt(m[2], 10) };
}

// ------------------------------- state -------------------------------------

function loadState(opts) {
  return readJson(opts.state, { lastConsumedGame: 0, pendingPr: null });
}

function saveState(opts, state) {
  writeJson(opts.state, state);
}

// ------------------------------- propose ------------------------------------

async function propose(opts) {
  const state = loadState(opts);
  if (state.pendingPr && !opts.dryRun) {
    log(`pending PR #${state.pendingPr.number} (未マージ) があるため propose を保留します。`);
    log('先に reconcile するか、PR をマージ/クローズしてください。');
    return 0;
  }

  const pending = pendingRange(opts.runtimeDir, state.lastConsumedGame);
  if (!pending.hasFresh) {
    log(`新しい試合がありません (lastConsumed=${state.lastConsumedGame}, to=${pending.to ?? '-'})。`);
    return 0;
  }
  const fromGame = state.lastConsumedGame + 1;
  const toGame = pending.to;
  log(`改善対象: game #${fromGame}..#${toGame} (${pending.fresh.length} 試合)`);

  const manifest = buildClearManifest(fromGame, toGame);
  const marker = formatPrMarker(fromGame, toGame);

  if (opts.dryRun) {
    log('[dry-run] モデル呼び出し・strategy.mjs 書き換え・PR作成はスキップします。');
    log('[dry-run] clear manifest:', JSON.stringify(manifest.targets));
    log('[dry-run] PR marker:', marker);
    return 0;
  }

  // ランタイム相対で improve.mjs の関数を使う (cwd を runtimeDir へ)。
  const prevCwd = process.cwd();
  process.chdir(opts.runtimeDir);
  let candidate = null;
  let validation = { valid: false, error: 'not-run' };
  try {
    const imp = await import(new URL('./improve.mjs', import.meta.url).href);
    const summariesDir = join(opts.runtimeDir, 'tmp', 'summaries');
    const historyDir = join(opts.runtimeDir, 'game_history');
    // 最新試合を代表にしてプロンプトを組む (standalone と同じ要領の簡略版)。
    const bestGame = toGame;
    const bestHistory = join(historyDir, `game_${String(bestGame).padStart(4, '0')}.jsonl`);
    const bestSummaryPath = join(summariesDir, `game_${String(bestGame).padStart(4, '0')}.json`);
    if (!existsSync(bestHistory) || !existsSync(bestSummaryPath)) {
      log(`代表試合 #${bestGame} の history/summary が見つかりません。同期を確認してください。`);
      return 1;
    }
    const currentStrategy = readFileSync(join(opts.runtimeDir, 'strategy.mjs'), 'utf-8');
    const aggregate = imp.generateAggregateSummary(fromGame - 1, toGame);
    const gameSummary = imp.generateSummary(bestHistory, bestSummaryPath);
    const viewerAdvice = imp.readViewerAdvice(
      process.env.SOREN91_STRATEGY_ADVICE_FILE || join(opts.runtimeDir, '..', 'advice91.md'),
      80,
    );
    const promptText = imp.buildPromptText(
      `${aggregate}\n\n## Latest Game Details\n${gameSummary}`,
      currentStrategy,
      '',
      [],
      viewerAdvice,
    );
    log('opencode で改善候補を生成します…');
    candidate = await imp.callStrategyModelWithFallback(promptText, [], 'improve_daily');
    if (!candidate) {
      log('改善候補が得られませんでした。');
      return 1;
    }
    validation = await imp.validateStrategy(candidate);
    if (!validation.valid) {
      log(`改善候補が validation に失敗: ${validation.error}`);
      return 1;
    }
  } finally {
    process.chdir(prevCwd);
  }

  // 候補を strategy.mjs へ書き込み、PR を作る。
  const strategyPath = join(opts.runtimeDir, 'strategy.mjs');
  const backup = `${strategyPath}.bak.improve_daily`;
  try { writeFileSync(backup, readFileSync(strategyPath)); } catch {}
  writeFileSync(strategyPath, candidate);
  log(`strategy.mjs を更新しました (backup: ${backup})`);

  if (opts.noPr) {
    log('--no-pr のため PR は作成しません。');
    saveState(opts, { ...state, pendingPr: null, lastConsumedGame: toGame, note: 'no-pr' });
    return 0;
  }

  const branch = `soren91/daily-improve-${fromGame}-${toGame}`;
  const prBody = [
    `Soren91 の外部日次改善 (games #${fromGame}..#${toGame})。`,
    '',
    marker,
    '',
    'マージ確定後に scheduler が `improve_daily.mjs --reconcile` を実行し、',
    `消費済みの入力を消します: ${manifest.targets.join(', ')}`,
  ].join('\n');

  const git = (args, { allowFail = false } = {}) => {
    const r = spawnSync('git', ['-C', opts.repoDir, ...args], { encoding: 'utf-8' });
    if (r.status !== 0 && !allowFail) {
      throw new Error(`git ${args.join(' ')} failed: ${(r.stderr || r.stdout || '').trim()}`);
    }
    return r;
  };
  git(['fetch', 'origin', opts.base]);
  git(['checkout', '-B', branch, `origin/${opts.base}`]);
  git(['add', 'soren91/strategy.mjs']);
  git(['commit', '-m', `soren91: daily strategy improvement (games ${fromGame}-${toGame})`]);
  git(['push', '-u', 'origin', branch]);
  const pr = spawnSync('gh', ['pr', 'create', '--base', opts.base, '--head', branch,
    '--title', `soren91: daily strategy improvement (games ${fromGame}-${toGame})`,
    '--body', prBody], { cwd: opts.repoDir, encoding: 'utf-8' });
  if (pr.status !== 0) {
    throw new Error(`gh pr create failed: ${(pr.stderr || pr.stdout || '').trim()}`);
  }
  const url = (pr.stdout || '').trim().split('\n').pop();
  const numMatch = url.match(/\/pull\/(\d+)/);
  const number = numMatch ? Number.parseInt(numMatch[1], 10) : null;
  log(`改善PRを作成: ${url}`);
  saveState(opts, {
    ...state,
    pendingPr: { number, url, branch, fromGame, toGame, createdAt: new Date().toISOString() },
  });
  return 0;
}

// ------------------------------ reconcile -----------------------------------

function runClear(opts, manifest) {
  const cmds = manifest.targets.map(t => `rm -f ${t} 2>/dev/null; rm -rf ${t} 2>/dev/null`);
  if (!opts.clearCmd) {
    log('clear コマンドが未設定です。以下をスケジューラ側で実行してください:');
    for (const c of cmds) log('  ' + c);
    return false;
  }
  const script = cmds.join('\n') + '\n';
  const r = spawnSync(opts.clearCmd, { shell: true, input: script, encoding: 'utf-8' });
  if (r.status !== 0) {
    log(`clear 実行が失敗: ${(r.stderr || '').trim()}`);
    return false;
  }
  log('消費済み入力をクリアしました。');
  return true;
}

function reconcile(opts) {
  const state = loadState(opts);
  const pending = state.pendingPr;
  if (!pending || !pending.number) {
    log('保留中の改善PRはありません。');
    return 0;
  }
  const view = spawnSync('gh', ['pr', 'view', String(pending.number), '--json', 'state,body'],
    { cwd: opts.repoDir, encoding: 'utf-8' });
  if (view.status !== 0) {
    log(`PR #${pending.number} の状態を取得できません: ${(view.stderr || '').trim()}`);
    return 1;
  }
  const info = JSON.parse(view.stdout || '{}');
  const prState = String(info.state || '').toUpperCase();
  log(`改善PR #${pending.number} state=${prState}`);
  if (prState === 'OPEN') {
    log('未マージのため、まだクリアしません。');
    return 0;
  }
  if (prState !== 'MERGED') {
    // CLOSED (未マージ): 消費せず、次回の propose に回す。
    log('PR はマージされていないため、入力を残して pending を解除します。');
    saveState(opts, { ...state, pendingPr: null });
    return 0;
  }
  const manifest = buildClearManifest(pending.fromGame, pending.toGame);
  if (opts.dryRun) {
    log('[dry-run] clear manifest:', JSON.stringify(manifest.targets));
    return 0;
  }
  const cleared = runClear(opts, manifest);
  saveState(opts, {
    ...state,
    pendingPr: null,
    lastConsumedGame: Math.max(state.lastConsumedGame || 0, pending.toGame),
    ...(cleared ? {} : { clearPending: manifest }),
  });
  return 0;
}

// -------------------------------- main --------------------------------------

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  log(`command=${opts.command} runtime=${opts.runtimeDir} repo=${opts.repoDir}`,
    opts.dryRun ? '(dry-run)' : '');
  if (opts.command === 'reconcile') return reconcile(opts);
  return propose(opts);
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main()
    .then(code => process.exit(code || 0))
    .catch(err => { console.error(err?.stack || err); process.exit(1); });
}

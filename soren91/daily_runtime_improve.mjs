#!/usr/bin/env node
/**
 * daily_runtime_improve.mjs — production-safe Soren91 daily improvement runner.
 *
 * The player writes match evidence below /home/ubuntu/soren/soren91.  This
 * runner keeps that live tree read-only, copies only a bounded allowlist of
 * evidence into a private temporary work directory, re-analyzes the selected
 * critical-turn screenshots, and opens a strategy-only PR from the managed
 * /home/ubuntu/soren-persist clone.
 *
 * The caller MUST serialize this process with strategy/persist.sh by holding
 * /home/ubuntu/soren-persist/.git/persist.lock (the docich workflow does so
 * with flock).  No production strategy file is ever written by this runner.
 */

import 'dotenv/config';
import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, resolve, sep } from 'node:path';
import { fileURLToPath } from 'node:url';

import { buildDailyEvidence, formatEvidenceForPrompt } from './daily_evidence.mjs';
import { validateAndRepairCandidate } from './daily_candidate_repair.mjs';
import { calibrate } from './calibration.mjs';
import { analyzeScreenshot } from './screenshot_analyzer.mjs';
import {
  parseOpencodeModels,
  resolveOpencodeModel,
  resolveTextAiConfig,
  stripAnsi,
} from './text_ai.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const DEFAULT_RUNTIME_DIR = resolve(process.env.SOREN91_DAILY_RUNTIME_DIR || HERE);
const DEFAULT_REPO_DIR = resolve(process.env.SOREN91_DAILY_REPO_DIR || '/home/ubuntu/soren-persist');
const DEFAULT_STATE = join(DEFAULT_RUNTIME_DIR, 'tmp', 'state', 'improve_daily.json');
const REPO_FULL_NAME = 'azumag/soviet_now';

const MAX_FILES = 4096;
const MAX_FILE_BYTES = 16 * 1024 * 1024;
const MAX_TOTAL_BYTES = 256 * 1024 * 1024;
const MAX_VISION_SHOTS = 6;
const MAX_HISTORY_BYTES = 4 * 1024 * 1024;

// If any of these differ between the live runtime and origin/main, generating a
// candidate against origin/main could silently mix incompatible code.  Refuse
// and wait for docich to deploy the reviewed upstream instead.
const COMPAT_FILES = [
  'strategy.mjs',
  'strategy_contract.mjs',
  'daily_evidence.mjs',
  'daily_candidate_repair.mjs',
  'daily_runtime_improve.mjs',
  'improve.mjs',
  'calibration.mjs',
  'screenshot_analyzer.mjs',
  'observation_guard.mjs',
  'text_ai.mjs',
];

function log(...args) {
  console.log('[soren91_daily_runtime]', ...args);
}

function finiteInt(value, fallback = 0) {
  const n = Number(value);
  return Number.isInteger(n) && n >= 0 ? n : fallback;
}

function gameToken(game) {
  return String(game).padStart(4, '0');
}

export function formatPrMarker(fromGame, toGame) {
  return `<!-- improve-daily: from=${fromGame} to=${toGame} -->`;
}

export function parseStrategyCode(text) {
  const cleaned = stripAnsi(String(text || '')).trim();
  const block = cleaned.match(/```(?:javascript|js)?\s*\n([\s\S]*?)```/i);
  if (block?.[1]?.includes('export function decide')) return block[1].trim();
  const at = cleaned.indexOf('export function decide');
  return at >= 0 ? cleaned.slice(at).trim() : null;
}

export function isAllowedEvidenceRelative(relPath) {
  const rel = String(relPath || '').replaceAll('\\', '/');
  if (/^game_history\/(?:game|latest)_\d+\.jsonl$/.test(rel)) return true;
  if (/^tmp\/summaries\/(?:game_\d+\.json|ranking_\d+\.png)$/.test(rel)) return true;
  if (/^tmp\/strategy_snapshots\/game_\d+_strategy\.mjs$/.test(rel)) return true;
  if (/^tmp\/game_screenshots\/game_\d+\/turn_[^/]+\.png$/.test(rel)) return true;
  return false;
}

function assertInside(root, path) {
  const base = resolve(root) + sep;
  const target = resolve(path);
  if (!target.startsWith(base)) throw new Error('path_escape');
}

function walkEvidenceRoot(runtimeDir, relRoot) {
  const root = join(runtimeDir, relRoot);
  if (!existsSync(root)) return [];
  const rootStat = lstatSync(root);
  if (rootStat.isSymbolicLink() || !rootStat.isDirectory()) throw new Error(`unsafe_evidence_root:${relRoot}`);
  const out = [];
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const path = join(dir, entry.name);
      const rel = relative(runtimeDir, path).replaceAll('\\', '/');
      const st = lstatSync(path);
      if (st.isSymbolicLink()) throw new Error(`evidence_symlink:${rel}`);
      if (st.isDirectory()) {
        if (relRoot === 'tmp/game_screenshots' && !/^tmp\/game_screenshots\/game_\d+$/.test(rel)) {
          throw new Error(`unexpected_evidence_directory:${rel}`);
        }
        stack.push(path);
        continue;
      }
      if (!st.isFile()) throw new Error(`non_regular_evidence:${rel}`);
      if (!isAllowedEvidenceRelative(rel)) continue;
      out.push({ path, rel, size: st.size });
    }
  }
  return out;
}

export function listEvidenceFiles(runtimeDir, {
  maxFiles = MAX_FILES,
  maxFileBytes = MAX_FILE_BYTES,
  maxTotalBytes = MAX_TOTAL_BYTES,
} = {}) {
  const roots = ['game_history', 'tmp/summaries', 'tmp/game_screenshots', 'tmp/strategy_snapshots'];
  const files = roots.flatMap(root => walkEvidenceRoot(runtimeDir, root));
  files.sort((a, b) => a.rel.localeCompare(b.rel, undefined, { numeric: true }));
  if (files.length > maxFiles) throw new Error(`evidence_file_limit:${files.length}`);
  let total = 0;
  for (const item of files) {
    if (item.size > maxFileBytes) throw new Error(`evidence_file_too_large:${item.rel}`);
    if (item.rel.endsWith('.jsonl') && item.size > MAX_HISTORY_BYTES) {
      throw new Error(`history_too_large:${item.rel}`);
    }
    total += item.size;
    if (total > maxTotalBytes) throw new Error(`evidence_total_limit:${total}`);
  }
  return { files, totalBytes: total };
}

export function copyEvidence(runtimeDir, workDir, limits = {}) {
  const manifest = listEvidenceFiles(runtimeDir, limits);
  for (const item of manifest.files) {
    const dest = join(workDir, item.rel);
    assertInside(workDir, dest);
    mkdirSync(dirname(dest), { recursive: true, mode: 0o700 });
    copyFileSync(item.path, dest);
    chmodSync(dest, 0o600);
  }
  return manifest;
}

function readJson(path, fallback) {
  try {
    const value = JSON.parse(readFileSync(path, 'utf8'));
    return value && typeof value === 'object' ? value : fallback;
  } catch {
    return fallback;
  }
}

function writeJsonAtomic(path, value) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const tmp = `${path}.tmp.${process.pid}`;
  writeFileSync(tmp, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  chmodSync(tmp, 0o600);
  renameSync(tmp, path);
  chmodSync(path, 0o600);
}

function run(bin, args, { cwd, timeout = 120000, input = null, encoding = 'utf8', allowFail = false } = {}) {
  const result = spawnSync(bin, args, {
    cwd,
    timeout,
    input,
    encoding,
    maxBuffer: 16 * 1024 * 1024,
    env: process.env,
  });
  if (result.error && !allowFail) throw result.error;
  if (result.status !== 0 && !allowFail) {
    const err = String(result.stderr || result.stdout || '').slice(0, 800).trim();
    throw new Error(`${bin} ${args.join(' ')} failed rc=${result.status}: ${err}`);
  }
  return result;
}

function git(repoDir, args, options = {}) {
  return run('git', ['-C', repoDir, ...args], options);
}

function gitShowBuffer(repoDir, spec) {
  const result = git(repoDir, ['show', spec], { encoding: null });
  return Buffer.isBuffer(result.stdout) ? result.stdout : Buffer.from(result.stdout || '');
}

function assertRepoClean(repoDir) {
  const status = git(repoDir, ['status', '--porcelain', '--untracked-files=no']).stdout.trim();
  if (status) throw new Error('persist_repo_tracked_dirty');
}

function preparePersistRepo(repoDir) {
  if (!existsSync(join(repoDir, '.git'))) throw new Error('persist_repo_missing');
  assertRepoClean(repoDir);
  git(repoDir, ['fetch', '--quiet', 'origin', 'main'], { timeout: 120000 });
  git(repoDir, ['checkout', '--quiet', '-B', 'soren91-daily-base', 'origin/main']);
  assertRepoClean(repoDir);
  return git(repoDir, ['rev-parse', 'origin/main']).stdout.trim();
}

function assertRuntimeCompatible(runtimeDir, repoDir) {
  for (const name of COMPAT_FILES) {
    const livePath = join(runtimeDir, name);
    if (!existsSync(livePath)) throw new Error(`runtime_compat_missing:${name}`);
    const live = readFileSync(livePath);
    const reviewed = gitShowBuffer(repoDir, `origin/main:soren91/${name}`);
    if (!live.equals(reviewed)) throw new Error(`runtime_not_current:${name}`);
  }
}

function reconcilePending(repoDir, statePath) {
  const state = readJson(statePath, { lastConsumedGame: 0, pendingPr: null });
  state.lastConsumedGame = finiteInt(state.lastConsumedGame, 0);
  const pending = state.pendingPr;
  if (!pending || !Number.isInteger(pending.number)) return { state, blocked: false };
  const view = run('gh', ['pr', 'view', String(pending.number), '--repo', REPO_FULL_NAME, '--json', 'state'], {
    cwd: repoDir,
    timeout: 30000,
    allowFail: true,
  });
  if (view.status !== 0) throw new Error('pending_pr_lookup_failed');
  const info = JSON.parse(view.stdout || '{}');
  const prState = String(info.state || '').toUpperCase();
  if (prState === 'OPEN') return { state, blocked: true };
  if (prState === 'MERGED') {
    state.lastConsumedGame = Math.max(state.lastConsumedGame, finiteInt(pending.toGame, state.lastConsumedGame));
    state.pendingPr = null;
    writeJsonAtomic(statePath, state);
    return { state, blocked: false };
  }
  if (prState === 'CLOSED') {
    state.pendingPr = null;
    writeJsonAtomic(statePath, state);
    return { state, blocked: false };
  }
  throw new Error(`unexpected_pr_state:${prState || 'empty'}`);
}

function safeNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function summarizeBoardState(pathLabel, calibration, state) {
  const pieces = Array.isArray(state?.pieces) ? state.pieces : [];
  const maxY = pieces.length ? Math.max(...pieces.map(piece => safeNumber(piece?.y) ?? -5)) : -5;
  const next = Array.isArray(state?.nextPieces)
    ? state.nextPieces.map(piece => piece?.type ?? null)
    : [];
  return {
    screenshot: pathLabel,
    calibrationConfidence: safeNumber(calibration?.confidence),
    calibrationMethod: typeof calibration?.method === 'string' ? calibration.method : null,
    state: typeof state?.state === 'string' ? state.state : null,
    perceptionReason: typeof state?.perception?.reason === 'string' ? state.perception.reason : null,
    confidence: safeNumber(state?.confidence),
    pieces: pieces.length,
    maxY: Math.round(maxY * 100) / 100,
    next,
    hold: state?.hold?.type ?? null,
    holdKnownEmpty: state?.holdKnownEmpty === true,
    garbageRatio: safeNumber(state?.garbage?.ratio),
    garbageHeight: safeNumber(state?.garbage?.height),
    garbageGauge: safeNumber(state?.garbage?.gauge),
  };
}

async function buildVisualEvidence(workDir, evidence) {
  const focus = new Set(evidence.focus?.games || []);
  const relPaths = [];
  for (const entry of evidence.entries || []) {
    if (!focus.has(entry.game)) continue;
    for (const path of entry.screenshots || []) {
      if (!relPaths.includes(path)) relPaths.push(path);
    }
  }
  const selected = relPaths.slice(0, MAX_VISION_SHOTS);
  const notes = [];
  const attachments = [];
  for (const rel of selected) {
    if (!isAllowedEvidenceRelative(rel)) continue;
    const path = join(workDir, rel);
    assertInside(workDir, path);
    if (!existsSync(path) || !statSync(path).isFile()) continue;
    attachments.push(path);
    try {
      const calibration = await calibrate(path);
      const state = await analyzeScreenshot(path, calibration);
      notes.push(summarizeBoardState(rel, calibration, state));
    } catch (error) {
      notes.push({ screenshot: rel, analysisError: error?.name || 'Error' });
    }
  }
  return { attachments, notes };
}

function formatVisualNotes(notes) {
  if (!notes.length) return '## Snapshot re-analysis\nNo focus screenshots were available.';
  const lines = ['## Snapshot re-analysis',
    'These values were re-derived from the retained PNG files by the current calibration/screenshot analyzer; use them together with the original turn history.'];
  for (const note of notes) lines.push(`- ${JSON.stringify(note)}`);
  return lines.join('\n');
}

function shellSingleQuote(value) {
  return `'${String(value).replace(/'/g, `'\\''`)}'`;
}

function cleanOpencodeOutput(raw) {
  return stripAnsi(String(raw || ''))
    .split(/\r?\n/)
    .filter(line => !line.startsWith('Script started on ') && !line.startsWith('Script done on '))
    .join('\n')
    .trim();
}

function callOpencodeVision(promptText, attachments, workDir) {
  if (!attachments.length) return null;
  const config = resolveTextAiConfig();
  const specs = parseOpencodeModels(process.env.AI_COMMON_AGENTS || process.env.RADIO_AGENTS);
  const models = specs.map(resolveOpencodeModel).filter(Boolean);
  const totalMs = Math.max(30000, Number.parseInt(process.env.SOREN91_IMPROVE_OPENCODE_TIMEOUT || '240', 10) * 1000);
  const perModelMs = Math.max(10000, Math.min(config.opencodePerModelTimeoutMs || 45000, totalMs));
  const promptDir = mkdtempSync(join(tmpdir(), 'soren91-vision-prompt-'));
  const promptFile = join(promptDir, 'prompt.txt');
  writeFileSync(promptFile, promptText, { mode: 0o600 });
  try {
    for (const model of models) {
      const rawFile = join(promptDir, `raw-${models.indexOf(model)}.txt`);
      const fileArgs = attachments.map(path => ` --file ${shellSingleQuote(path)}`).join('');
      const command = `LC_ALL=en_US.UTF-8 opencode run --model ${shellSingleQuote(model)}${fileArgs} "$(cat ${shellSingleQuote(promptFile)})" 2>&1`;
      const scriptCommand = `bash -lc ${shellSingleQuote(command)}`;
      const result = spawnSync('script', ['-q', '-e', '-c', scriptCommand, rawFile], {
        cwd: workDir,
        timeout: perModelMs,
        encoding: 'utf8',
        env: { ...process.env, OPENCODE_PERMISSION: config.opencodePermission },
        maxBuffer: 4 * 1024 * 1024,
      });
      const raw = existsSync(rawFile) ? readFileSync(rawFile, 'utf8') : `${result.stdout || ''}\n${result.stderr || ''}`;
      const code = parseStrategyCode(cleanOpencodeOutput(raw));
      if (result.status === 0 && code) return { code, model };
    }
  } finally {
    rmSync(promptDir, { recursive: true, force: true });
  }
  return null;
}

function copyPrompt(runtimeDir, workDir) {
  const source = join(runtimeDir, 'prompts', 'improve_strategy.md');
  if (!existsSync(source)) return;
  const dest = join(workDir, 'prompts', 'improve_strategy.md');
  mkdirSync(dirname(dest), { recursive: true, mode: 0o700 });
  copyFileSync(source, dest);
  chmodSync(dest, 0o600);
}

function focusDetailsForPrompt(imp, workDir, evidence) {
  const summariesDir = join(workDir, 'tmp', 'summaries');
  const sections = [];
  for (const game of evidence.focus?.games || []) {
    const entry = evidence.entries.find(item => item.game === game);
    const history = entry?.history ? join(workDir, entry.history) : null;
    const summary = join(summariesDir, `game_${gameToken(game)}.json`);
    if (!history || !existsSync(history) || !existsSync(summary)) throw new Error(`focus_evidence_missing:${game}`);
    const roles = [];
    if (game === evidence.focus.worst) roles.push('worst');
    if (game === evidence.focus.best) roles.push('best');
    if (game === evidence.focus.latest) roles.push('latest');
    sections.push(`## Focus Game #${game} (${roles.join(', ')})\n${imp.generateSummary(history, summary)}`);
  }
  return sections.join('\n\n');
}

function createStrategyPr(repoDir, candidate, fromGame, toGame, expectedStrategy, statePath, state, evidence) {
  // Re-fetch after model work.  Unrelated main changes are fine, but the
  // strategy being edited must still be exactly the one we analyzed.
  git(repoDir, ['fetch', '--quiet', 'origin', 'main'], { timeout: 120000 });
  const latestStrategy = gitShowBuffer(repoDir, 'origin/main:soren91/strategy.mjs');
  if (!latestStrategy.equals(expectedStrategy)) throw new Error('strategy_changed_during_analysis');

  const baseSha = git(repoDir, ['rev-parse', 'origin/main']).stdout.trim();
  const branch = `soren91/daily-improve-${fromGame}-${toGame}-${Date.now()}`;
  git(repoDir, ['checkout', '--quiet', '-B', branch, 'origin/main']);
  const target = join(repoDir, 'soren91', 'strategy.mjs');
  writeFileSync(target, candidate);
  git(repoDir, ['add', 'soren91/strategy.mjs']);
  const diff = git(repoDir, ['diff', '--cached', '--quiet'], { allowFail: true });
  if (diff.status === 0) {
    git(repoDir, ['checkout', '--quiet', 'soren91-daily-base']);
    return { created: false, reason: 'no_strategy_diff' };
  }
  if (diff.status !== 1) throw new Error('git_diff_failed');
  git(repoDir, ['commit', '--quiet', '-m', `soren91: daily strategy improvement (games ${fromGame}-${toGame})`]);
  git(repoDir, ['push', '--quiet', '-u', 'origin', branch], { timeout: 120000 });
  const marker = formatPrMarker(fromGame, toGame);
  const body = [
    `Soren91 production evidence daily improvement (games #${fromGame}..#${toGame}).`,
    '', marker, '',
    `Evidence: ${evidence.metrics?.games ?? 0} games; median rank=${evidence.metrics?.medianRank ?? 'unknown'}; mean turns=${evidence.metrics?.meanTurns ?? 'unknown'}.`,
    'Critical-turn PNGs were re-analyzed locally and also attached to the OpenCode request when the selected model accepted image input.',
    'Production strategy is unchanged until this PR is reviewed, merged, synced by docich, and deployed.',
  ].join('\n');
  const pr = run('gh', ['pr', 'create', '--repo', REPO_FULL_NAME, '--base', 'main', '--head', branch,
    '--title', `soren91: daily strategy improvement (games ${fromGame}-${toGame})`, '--body', body], {
    cwd: repoDir,
    timeout: 60000,
  });
  const url = String(pr.stdout || '').trim().split(/\r?\n/).filter(Boolean).at(-1) || '';
  const number = Number.parseInt(url.match(/\/pull\/(\d+)/)?.[1] || '', 10);
  if (!Number.isInteger(number)) throw new Error('pr_number_parse_failed');
  writeJsonAtomic(statePath, {
    ...state,
    pendingPr: { number, url, branch, fromGame, toGame, baseSha, createdAt: new Date().toISOString() },
  });
  return { created: true, number, url };
}

function parseArgs(argv) {
  const opts = {
    runtimeDir: DEFAULT_RUNTIME_DIR,
    repoDir: DEFAULT_REPO_DIR,
    statePath: DEFAULT_STATE,
    dryRun: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`${arg} requires a value`);
      return argv[i];
    };
    if (arg === '--runtime-dir') opts.runtimeDir = resolve(next());
    else if (arg === '--repo-dir') opts.repoDir = resolve(next());
    else if (arg === '--state') opts.statePath = resolve(next());
    else if (arg === '--dry-run') opts.dryRun = true;
    else throw new Error(`unknown option:${arg}`);
  }
  return opts;
}

export async function runDailyRuntimeImprovement(opts) {
  const runtimeDir = resolve(opts.runtimeDir);
  const repoDir = resolve(opts.repoDir);
  const statePath = resolve(opts.statePath);
  if (!existsSync(join(runtimeDir, 'strategy.mjs'))) throw new Error('runtime_missing');

  preparePersistRepo(repoDir);
  assertRuntimeCompatible(runtimeDir, repoDir);
  const reconciled = reconcilePending(repoDir, statePath);
  if (reconciled.blocked) {
    log(`pending PR #${reconciled.state.pendingPr.number} is still open`);
    return { status: 'pending' };
  }
  const state = reconciled.state;

  const workDir = mkdtempSync(join(tmpdir(), 'soren91-daily-runtime-'));
  chmodSync(workDir, 0o700);
  let previousCwd = process.cwd();
  try {
    const copied = copyEvidence(runtimeDir, workDir);
    const reviewedStrategy = gitShowBuffer(repoDir, 'origin/main:soren91/strategy.mjs');
    writeFileSync(join(workDir, 'strategy.mjs'), reviewedStrategy, { mode: 0o600 });
    mkdirSync(join(workDir, 'tmp'), { recursive: true, mode: 0o700 });
    copyPrompt(runtimeDir, workDir);

    const evidence = buildDailyEvidence(workDir, finiteInt(state.lastConsumedGame, 0));
    if (evidence.status === 'no-data') return { status: 'no-data', copied: copied.files.length };
    if (evidence.status !== 'ready') {
      throw new Error(`evidence_blocked:${(evidence.warnings || []).join(';')}`);
    }
    const fromGame = evidence.range.games[0];
    const toGame = evidence.range.games.at(-1);
    if (opts.dryRun) return { status: 'ready', fromGame, toGame, copied: copied.files.length };

    previousCwd = process.cwd();
    process.chdir(workDir);
    const imp = await import(new URL('./improve.mjs', import.meta.url).href);
    const currentStrategy = reviewedStrategy.toString('utf8');
    const aggregate = imp.generateAggregateSummary(fromGame - 1, toGame);
    const focus = focusDetailsForPrompt(imp, workDir, evidence);
    const visual = await buildVisualEvidence(workDir, evidence);
    const visualText = formatVisualNotes(visual.notes);
    const evidenceText = formatEvidenceForPrompt(evidence);
    const viewerAdvice = imp.readViewerAdvice(
      process.env.SOREN91_STRATEGY_ADVICE_FILE || join(runtimeDir, '..', 'advice91.md'),
      80,
    );
    const gameContext = `${evidenceText}\n\n${aggregate}\n\n${focus}\n\n${visualText}`;
    const markers = visual.attachments.map(path => ({ filename: basename(path) }));
    const visionPrompt = imp.buildPromptText(gameContext, currentStrategy, '', markers, viewerAdvice);
    const textPrompt = imp.buildPromptText(gameContext, currentStrategy, '', [], viewerAdvice);

    let candidate = null;
    const visionResult = callOpencodeVision(visionPrompt, visual.attachments, workDir);
    if (visionResult?.code) {
      candidate = visionResult.code;
      log(`vision candidate model=${visionResult.model} attachments=${visual.attachments.length}`);
    } else {
      // Even when none of the configured models accepts images, the fallback
      // still receives deterministic values re-derived from the PNGs above.
      candidate = await imp.callStrategyModelWithFallback(textPrompt, [], 'improve_daily_runtime');
    }
    if (!candidate) throw new Error('model_no_candidate');
    const reviewedCandidate = await validateAndRepairCandidate(imp, candidate, { maxRepairs: 1 });
    candidate = reviewedCandidate.candidate;
    if (reviewedCandidate.repairs > 0) log(`candidate repair attempts=${reviewedCandidate.repairs}`);
    if (!reviewedCandidate.validation.valid) {
      throw new Error(`candidate_invalid:${reviewedCandidate.validation.error}`);
    }

    process.chdir(previousCwd);
    const pr = createStrategyPr(repoDir, candidate, fromGame, toGame, reviewedStrategy, statePath, state, evidence);
    return { status: pr.created ? 'pr-created' : pr.reason, fromGame, toGame, pr: pr.number ?? null };
  } finally {
    try { process.chdir(previousCwd); } catch {}
    rmSync(workDir, { recursive: true, force: true });
  }
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const result = await runDailyRuntimeImprovement(opts);
  log(JSON.stringify(result));
}

if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main().catch(error => {
    console.error(`[soren91_daily_runtime] ${error?.message || error}`);
    process.exit(1);
  });
}

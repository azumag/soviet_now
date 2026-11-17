#!/usr/bin/env node
// Local-only laboratory. One browser, isolated contexts and persistent Python workers.
import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';
import { parseArgs } from 'node:util';
import { createHash, randomUUID } from 'node:crypto';
import { createStaticFileServer } from '../lib/static_file_server.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sleep = ms => new Promise(r => setTimeout(r, ms));
export function positive(value, name, max = Infinity) {
  const n = Number(value);
  if (!Number.isInteger(n) || n < 1 || n > max) throw new Error(`invalid ${name}`);
  return n;
}
export function jobsFor(seeds, candidate) {
  return seeds.flatMap((seed, i) => (candidate ? (i % 2 ? ['B', 'A'] : ['A', 'B']) : ['A'])
    .map(arm => ({ seed, arm })));
}
export function summarize(results, paired = false, expectedSeeds = []) {
  const valid = r => r.status === 'GAMEOVER' && !r.error && r.evidence_complete;
  const missing = [], pairs = [];
  const bundles = {};
  for (const arm of ['A', 'B']) bundles[arm] = new Set(results.filter(r => r.arm === arm).map(r => r.policy_bundle));
  if (paired) for (const seed of expectedSeeds) {
    const a = results.filter(r => r.seed === seed && r.arm === 'A');
    const b = results.filter(r => r.seed === seed && r.arm === 'B');
    let reason;
    if (a.length !== 1 || b.length !== 1 || !valid(a[0]) || !valid(b[0])) reason = 'missing_or_incomplete_arm';
    else if (!a[0].seeded || !b[0].seeded) reason = 'unseeded';
    else if (bundles.A.size !== 1 || bundles.B.size !== 1) reason = 'mixed_policy_bundles';
    else {
      const n = Math.min(a[0].queue.length, b[0].queue.length);
      if (!n || a[0].queue.slice(0, n).some((t, i) => t !== b[0].queue[i])) reason = 'queue_mismatch';
    }
    if (reason) missing.push({ seed, reason });
    else pairs.push({ seed, a: a[0], b: b[0], score_delta: b[0].score - a[0].score });
  }
  const included = paired ? pairs.flatMap(p => [p.a, p.b]) : results.filter(valid);
  const arms = {};
  for (const arm of ['A', 'B']) {
    const games = included.filter(r => r.arm === arm);
    if (!games.length) continue;
    arms[arm] = { games: games.length, mean_score: games.reduce((a, r) => a + r.score, 0) / games.length,
      t14: games.filter(r => r.max_type >= 14).length, t15: games.filter(r => r.max_type >= 15).length,
      soviet: games.filter(r => r.makeSorenCount > 0).length };
  }
  return { completed: results.filter(valid).length, incomplete: results.filter(r => !valid(r)).length,
    comparison_valid: paired && missing.length === 0 && pairs.length === expectedSeeds.length && pairs.length > 0,
    paired_games: pairs.length, missing_pairs: missing,
    paired_deltas: pairs.map(({ seed, score_delta }) => ({ seed, score_delta })), arms };
}

function localEnv() {
  const env = {};
  for (const key of ['PATH', 'HOME', 'TMPDIR', 'LANG', 'PLAYWRIGHT_BROWSERS_PATH'])
    if (process.env[key]) env[key] = process.env[key];
  return env;
}
export function snapshotPolicies(out, policies) {
  const snapshot = path.join(path.resolve(out), 'policy-input');
  fs.mkdirSync(snapshot);
  const copy = (src, dest) => {
    if (!fs.lstatSync(src).isFile()) throw new Error('snapshot requires regular files');
    fs.mkdirSync(path.dirname(dest), { recursive: true }); fs.copyFileSync(src, dest);
  };
  for (const name of ['strategy_runner.py', 'analyze_board.py']) copy(path.join(ROOT, name), path.join(snapshot, name));
  for (const name of ['simulation_policy.py', 'policy_bundle.py']) copy(path.join(ROOT, 'tools', name), path.join(snapshot, 'tools', name));
  for (const dir of ['strategy_helpers', 'lib']) {
    for (const e of fs.readdirSync(path.join(ROOT, dir), { recursive: true, withFileTypes: true })) {
      if (e.isFile() && e.name.endsWith('.py')) {
        const src = path.join(e.parentPath, e.name); copy(src, path.join(snapshot, path.relative(ROOT, src)));
      }
    }
  }
  for (const [arm, source] of Object.entries(policies)) {
    const dest = path.join(snapshot, 'policies', `${arm}.py`); copy(path.resolve(source), dest);
    // Reuse the existing non-executing AST gate. Dynamic imports occur only in Docker.
    const gate = spawnSync('bash', ['-c', 'log() { :; }; source "$1"; validate_strategy "$2" "$3" "$4" || { printf "%s\\n" "$VALIDATE_ERROR" >&2; exit 1; }',
      'simulation-ast', path.join(ROOT, 'strategy/sandbox.sh'), dest, path.join(snapshot, 'strategy_helpers'),
      arm === 'A' && path.resolve(source) === path.join(ROOT, 'strategy.py') ? 'active' : 'candidate'],
      { cwd: snapshot, env: { PATH: process.env.PATH }, encoding: 'utf8', timeout: 30000 });
    if (gate.status !== 0) throw new Error(`AST validation rejected ${arm}: ${(gate.stdout || '') + (gate.stderr || '')}`);
  }
  return snapshot;
}

export function policyWorker(arm, snapshot, image, logPath, seed = 0) {
  logPath = path.resolve(logPath);
  const stderr = fs.openSync(logPath, 'w');
  const container = `soren-lab-${randomUUID()}`;
  const cidFile = logPath.replace('.stderr.log', '.cid');
  if (fs.existsSync(cidFile)) { fs.closeSync(stderr); throw new Error('container receipt already exists'); }
  fs.writeFileSync(logPath.replace('.stderr.log', '.container.json'), JSON.stringify({ container, cidFile }));
  const proc = spawn('docker', ['run', '--rm', '--init', '--pull=never', '--log-driver', 'none',
    '--name', container, '--cidfile', cidFile, '--network', 'none', '--read-only',
    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '--pids-limit', '16', '--memory', '512m',
    '--cpus', '1', '--user', '65534:65534', '--interactive', '--mount', `type=bind,source=${snapshot},target=/input,readonly`,
    '--workdir', '/input', '--env', 'ANALYZE_BOARD_VERTICAL_LANE_DIRECT=1', '--env', 'ANALYZE_BOARD_MERGE_TOP_MODEL=2',
    '--env', 'ANALYZE_BOARD_WALL_CLAMP=1', '--env', 'ANALYZE_BOARD_LANDING_ARC=0', '--env', 'SOREN_SETTLE_REQUIRED=3',
    '--env', 'PYTHONHASHSEED=0', image, 'python3', '-s', '-B', '/input/tools/simulation_policy.py', `/input/policies/${arm}.py`, String(seed)],
    { cwd: snapshot, env: localEnv(), stdio: ['pipe', 'pipe', 'pipe'] });
  let pending;
  const completed = new Promise(resolve => proc.once('close', resolve));
  let outputBytes = 0, stderrBytes = 0, lineBytes = 0, closed = false;
  function removeContainer() {
    const removed = spawnSync('docker', ['rm', '-f', container], { env: localEnv(), timeout: 10000, encoding: 'utf8' });
    if (removed.status !== 0 && !String(removed.stderr).includes('No such container'))
      throw new Error('container cleanup failed; see ' + logPath.replace('.stderr.log', '.container.json'));
  }
  proc.stdout.on('data', chunk => {
    outputBytes += chunk.length;
    for (const byte of chunk) { lineBytes = byte === 10 ? 0 : lineBytes + 1; if (lineBytes > 262144) break; }
    if (outputBytes > 16 * 1024 * 1024 || lineBytes > 262144) {
      fail(new Error('policy stdout limit')); proc.kill('SIGKILL');
    }
  });
  proc.stderr.on('data', chunk => {
    stderrBytes += chunk.length;
    if (stderrBytes <= 1024 * 1024) fs.writeSync(stderr, chunk);
    else { fail(new Error('policy stderr limit')); proc.kill('SIGKILL'); }
  });
  const lines = createInterface({ input: proc.stdout });
  lines.on('line', line => {
    if (!pending) return;
    const { resolve, reject, timer } = pending; pending = undefined; clearTimeout(timer);
    try { const r = JSON.parse(line); if (r.error) reject(new Error(r.error)); else resolve(r); }
    catch (e) { reject(e); }
  });
  function fail(e) { if (pending) { clearTimeout(pending.timer); pending.reject(e); pending = undefined; } }
  proc.on('error', fail);
  proc.stdin.on('error', fail);
  proc.on('exit', code => fail(new Error(`policy exited ${code}`)));
  return { proc, request(op, state) {
    if (pending) throw new Error('concurrent request to policy');
    return new Promise((resolve, reject) => {
      pending = { resolve, reject, timer: setTimeout(() => {
        fail(new Error('policy timeout')); proc.kill('SIGKILL');
      }, 30000) };
      if (proc.exitCode !== null) { fail(new Error('policy is not running')); return; }
      proc.stdin.write(JSON.stringify({ op, state }) + '\n', e => { if (e) fail(e); });
    });
  }, async close() {
    if (closed) return; closed = true;
    // Force-remove only this uniquely named container, even if its policy ignores TERM.
    let cleanupError;
    try { removeContainer(); } catch (e) { cleanupError = e; }
    try {
      proc.stdin.end();
      if (proc.exitCode === null) {
        const kill = setTimeout(() => proc.kill('SIGKILL'), 2000);
        proc.kill('SIGTERM'); await completed; clearTimeout(kill);
      } else {
        await completed;
      }
    } finally { lines.close(); fs.closeSync(stderr); }
    if (cleanupError) throw cleanupError;
  } };
}

export async function runGame(browser, url, job, args, signal, createWorker = policyWorker) {
  const id = `${job.arm}-seed${job.seed}`;
  let worker, context, history;
  const started = performance.now();
  let turns = 0, maxType = 0, state, status = 'error', error, analysisMs = 0, decisionMs = 0, config, evidenceComplete = false;
  const queue = [];
  try {
    worker = createWorker(job.arm, args.snapshot, args.image, path.join(args.out, `${id}.stderr.log`), job.seed);
    context = await browser.newContext({ viewport: { width: 1280, height: 720 } });
    history = fs.openSync(path.join(args.out, `${id}.jsonl`), 'w');
    // Reject all external requests, including any game ranking/analytics calls.
    await context.route('**/*', route => {
      const u = new URL(route.request().url());
      return u.origin === new URL(url).origin || ['data:', 'blob:'].includes(u.protocol)
        ? route.continue() : route.abort();
    });
    await context.addInitScript(({ seed, speed }) => {
      window.__sorenSimulationConfig = { seed, speed };
      window.__sorenSimulationPaused = false;
    }, { seed: job.seed, speed: args.speed });
    const page = await context.newPage();
    config = await worker.request('config');
    await page.goto(url);
    await page.waitForFunction(() => window.__sorenGameState?.state, null, { timeout: 120000 });
    state = await page.evaluate(() => window.__sorenGameState);
    if (!args.legacy && (state.simulation?.schema !== 1 || state.simulation.seed !== job.seed || state.simulation.speed !== args.speed))
      throw new Error('requires a simulation build with matching seed and speed; use --legacy only for unpaired baseline probes');
    let moveAt, sampleAt = -Infinity, settle = 0;
    while (performance.now() - started < args.timeout * 1000) {
      if (signal.aborted) { status = 'interrupted'; break; }
      state = await page.evaluate(() => window.__sorenGameState);
      maxType = Math.max(maxType, ...state.pieces.map(p => p.type));
      if (state.state === 'GAMEOVER') { status = 'GAMEOVER'; break; }
      if (turns >= args['turn-limit']) { status = 'turn_limit'; break; }
      if (state.state !== 'MOVE') { moveAt = undefined; settle = 0; await sleep(10); continue; }
      const t = args.legacy ? (performance.now() - started) / 1000 : state.simulation.time;
      if (moveAt === undefined) moveAt = t;
      if (t - sampleAt < 0.15) { await sleep(5); continue; }
      sampleAt = t;
      const motion = await worker.request('observe', state);
      settle = motion.settled ? settle + 1 : 0;
      if (!(config.fast_drop && motion.contact) && settle < 3 && t - moveAt < 30) continue;
      if (!args.legacy) {
        await page.evaluate(() => { window.__sorenSimulationPaused = true; });
        await page.waitForFunction(() => window.__sorenGameState.simulation.paused || window.__sorenGameState.state !== 'MOVE', null, { timeout: 10000 });
        state = await page.evaluate(() => window.__sorenGameState);
        if (state.state !== 'MOVE') { await page.evaluate(() => { window.__sorenSimulationPaused = false; }); continue; }
      }
      const result = await worker.request('decide', state);
      queue.push(state.next.type);
      analysisMs += result.analysis_ms; decisionMs += result.decision_ms;
      fs.writeSync(history, JSON.stringify({ turn: turns + 1, state, ...result }) + '\n');
      await page.evaluate(x => { window.__sorenCommand = `DROP:${x}`; window.__sorenSimulationPaused = false; }, result.decision.x);
      // Acknowledge command consumption before considering another drop.
      await page.waitForFunction(() => !window.__sorenCommand, null, { timeout: 10000 });
      turns++; moveAt = undefined; sampleAt = -Infinity; settle = 0;
      if (turns % 20 === 0) console.log(`${id}: ${turns} turns score=${state.score} max=T${maxType}`);
    }
    if (status === 'error') status = 'timeout';
    await page.screenshot({ path: path.join(args.out, `${id}.png`) });
    fs.writeSync(history, JSON.stringify({ terminal: status, state }) + '\n');
    evidenceComplete = true;
  } catch (e) { error = e.message; status = 'error'; }
  finally {
    const cleanups = await Promise.allSettled([
      Promise.resolve().then(() => { if (history !== undefined) fs.closeSync(history); }),
      Promise.resolve().then(() => context?.close()), Promise.resolve().then(() => worker?.close())]);
    for (const result of cleanups) if (result.status === 'rejected') { error = 'cleanup: ' + result.reason.message; status = 'error'; }
  }
  const elapsed = (performance.now() - started) / 1000;
  return { ...job, status, error, policy_bundle: config?.policy_bundle,
    seeded: !args.legacy, speed: args.speed, evidence_complete: evidenceComplete, queue, turns, score: state?.score, max_type: maxType,
    makeSorenCount: state?.makeSorenCount, elapsed_sec: elapsed, simulation_sec: state?.simulation?.time,
    analysis_ms: analysisMs, decision_ms: decisionMs,
    effective_speed: state?.simulation?.time / elapsed,
    turns_per_sec: turns / elapsed, history: `${id}.jsonl` };
}

export async function main(argv = process.argv.slice(2)) {
  const { values: args } = parseArgs({ args: argv, options: {
    build: { type: 'string', default: 'tmp/local-simulation/build' },
    policy: { type: 'string', default: 'strategy.py' }, candidate: { type: 'string' },
    seeds: { type: 'string', default: '1,2' }, slots: { type: 'string', default: '1' },
    speed: { type: 'string', default: '8' }, out: { type: 'string', default: 'tmp/simulation-run' },
    image: { type: 'string', default: 'python:3.12-slim' }, timeout: { type: 'string', default: '600' },
    channel: { type: 'string' },
    'turn-limit': { type: 'string', default: '10000' }, legacy: { type: 'boolean', default: false }
  } });
  args.slots = positive(args.slots, 'slots', 4); args.speed = positive(args.speed, 'speed', 16);
  args.timeout = positive(args.timeout, 'timeout'); args['turn-limit'] = positive(args['turn-limit'], 'turn-limit');
  const seeds = args.seeds.split(',').map(s => {
    const seed = Number(s); if (!Number.isInteger(seed) || seed < 0 || seed > 2147483647) throw new Error('invalid seed'); return seed;
  });
  if (new Set(seeds).size !== seeds.length) throw new Error('duplicate seeds');
  if (args.legacy && (args.candidate || args.speed !== 1)) throw new Error('--legacy requires speed 1 and no candidate');
  args.out = path.resolve(args.out); fs.mkdirSync(args.out, { recursive: false });
  const inspected = spawnSync('docker', ['image', 'inspect', args.image, '--format', '{{.Id}}'], { env: localEnv(), encoding: 'utf8', timeout: 10000 });
  if (inspected.status !== 0 || !/^sha256:[a-f0-9]{64}$/.test(inspected.stdout.trim()))
    throw new Error('Docker isolation requires an available local Python image; no host-execution fallback');
  args.image = inspected.stdout.trim();
  const engine = spawnSync('docker', ['version', '--format', '{{.Server.Version}}'], { env: localEnv(), encoding: 'utf8', timeout: 10000 });
  if (engine.status !== 0) throw new Error('Docker daemon unavailable; no host-execution fallback');
  args.snapshot = snapshotPolicies(args.out, args.candidate ? { A: args.policy, B: args.candidate } : { A: args.policy });
  const hashFiles = dir => Object.fromEntries(fs.readdirSync(dir, { recursive: true, withFileTypes: true })
    .filter(e => e.isFile()).map(e => { const f = path.join(e.parentPath, e.name); return [path.relative(dir, f), createHash('sha256').update(fs.readFileSync(f)).digest('hex')]; }));
  fs.writeFileSync(path.join(args.out, 'manifest.json'), JSON.stringify({ schema: 1, options: args, docker_engine: engine.stdout.trim(),
    policy_sha256: hashFiles(args.snapshot), build_sha256: hashFiles(fs.realpathSync(args.build)) }, null, 2));
  const staticServer = createStaticFileServer({ buildDir: args.build, bindAddress: '127.0.0.1', env: {} });
  const controller = new AbortController();
  const stop = () => controller.abort();
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
  let browser;
  const results = [], jobs = jobsFor(seeds, args.candidate);
  try {
    await staticServer.listen(0);
    const url = `http://127.0.0.1:${staticServer.server.address().port}`;
    browser = await chromium.launch({ headless: true, channel: args.channel, env: localEnv(), timeout: 45000,
      args: ['--mute-audio', '--password-store=basic', '--use-mock-keychain', '--disable-translate',
        '--disable-background-timer-throttling', '--disable-renderer-backgrounding', '--disable-backgrounding-occluded-windows'] });
    await Promise.all(Array.from({ length: Math.min(args.slots, jobs.length) }, async () => {
      while (jobs.length && !controller.signal.aborted) {
        const rec = await runGame(browser, url, jobs.shift(), args, controller.signal);
        results.push(rec); fs.appendFileSync(path.join(args.out, 'results.jsonl'), JSON.stringify(rec) + '\n');
        console.log(JSON.stringify(rec));
      }
    }));
    const report = summarize(results, Boolean(args.candidate), seeds);
    fs.writeFileSync(path.join(args.out, 'summary.json'), JSON.stringify(report, null, 2));
    console.log(JSON.stringify(report));
    if (controller.signal.aborted || report.incomplete || jobs.length || (args.candidate && !report.comparison_valid)) process.exitCode = 1;
  } finally {
    await browser?.close();
    staticServer.server.closeAllConnections();
    await new Promise(r => staticServer.server.close(r));
    process.off('SIGINT', stop); process.off('SIGTERM', stop);
  }
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main().catch(e => { console.error(e.message); process.exitCode = 1; });

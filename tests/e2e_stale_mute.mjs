// E2E: the local-BGM mute flag self-heals after a dead soren91 session.
//
// Reproduces the 2026-09-17 failure end to end with a REAL Chromium and a REAL
// ownership record:
//
//   1. a "soren91 tab" is open  -> the record must stay (foreign page present);
//   2. the owner is SIGKILLed   -> the record must still stay while the tab lives;
//   3. the tab is gone          -> the bridge reaps the record (compare-and-swap
//      in lib/mute_flag.py) and restores the lifecycle freeze soren91 applied to
//      the local game page.
//
// It drives the real reader functions from soviet_local.mjs (imported with
// SOREN_LOCAL_CONTROLLER_IMPORT_ONLY=1) against a real CDP browser, so it covers
// `Target.getTargets`, `Page.setWebLifecycleState` and the CLI wiring that the
// unit tests cannot.
//
// Run: node tests/e2e_stale_mute.mjs   (rc=0 on success, rc=1 on failure)

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, openSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const HELPER = join(REPO_ROOT, 'lib', 'mute_flag.py');
const TOKEN = `e2e-${Date.now()}`;

// Grab an ephemeral port rather than guessing one: CI runs other jobs on the
// same machine and a busy port would look like a Chrome launch failure.
function freePort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

const CDP_PORT = await freePort();

const workDir = mkdtempSync(join(tmpdir(), 'stale-mute-e2e-'));
mkdirSync(join(workDir, 'tmp'), { recursive: true });
const flagPath = join(workDir, 'tmp', 'mute_local_bgm');
const userDataDir = join(workDir, 'chrome-profile');

// This E2E needs a real Chromium; CI jobs that do not install one must not fail
// on it, so it reports SKIP (rc=0) instead.
let chromiumPath = '';
try { chromiumPath = chromium.executablePath(); } catch { chromiumPath = ''; }
if (!chromiumPath || !existsSync(chromiumPath)) {
  rmSync(workDir, { recursive: true, force: true });
  console.log(`SKIP: no Chromium build available (${chromiumPath || 'none'}); run \`npx playwright install chromium\` to run this end-to-end test.`);
  process.exit(0);
}

// The reader resolves the flag relative to its cwd and the CDP port from the
// environment at import time, so both must be set before importing the module.
process.chdir(workDir);
process.env.SOREN_LOCAL_CONTROLLER_IMPORT_ONLY = '1';
process.env.SOREN_CDP_PORT = String(CDP_PORT);

const failures = [];
function check(label, condition, detail = '') {
  if (condition) {
    console.log(`  ok   ${label}`);
  } else {
    failures.push(label);
    console.log(`  FAIL ${label}${detail ? ` (${detail})` : ''}`);
  }
}

function cli(...args) {
  const result = spawnSync('python3', [HELPER, ...args, '--flag', flagPath], { encoding: 'utf-8' });
  if (result.status !== 0) throw new Error(`mute_flag.py ${args[0]} failed: ${result.stderr}`);
  return JSON.parse(result.stdout);
}

function spawnOwner() {
  return spawn(process.execPath, ['-e', 'setTimeout(() => {}, 300000)'], { stdio: 'ignore' });
}

async function waitForCdpVersion(port, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  let lastError = null;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return await response.json();
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw lastError || new Error(`CDP did not start on port ${port}`);
}

function freezeLocalPage(page, context) {
  return context.newCDPSession(page).then(async (session) => {
    await page.evaluate(() => {
      const canvas = document.querySelector('canvas');
      globalThis.__sorenRenderPaused = true;
      globalThis.__soren91NormalCanvasStyle = canvas
        ? { visibility: canvas.style.visibility, pointerEvents: canvas.style.pointerEvents }
        : undefined;
      if (canvas) {
        canvas.style.visibility = 'hidden';
        canvas.style.pointerEvents = 'none';
      }
    });
    await session.send('Emulation.setCPUThrottlingRate', { rate: 8 });
    await session.send('Page.setWebLifecycleState', { state: 'frozen' });
    return session;
  });
}

async function readLocalPageState(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    return {
      visibility: canvas ? canvas.style.visibility : null,
      pointerEvents: canvas ? canvas.style.pointerEvents : null,
      renderPaused: globalThis.__sorenRenderPaused === true,
      frozenStyle: Boolean(globalThis.__soren91NormalCanvasStyle),
    };
  });
}

const owner = spawnOwner();
let browser = null;
let chrome = null;
let chromeLogPath = '';
let exitCode = 0;

try {
  console.log('# stale mute flag E2E');
  const executable = chromiumPath;
  chromeLogPath = join(workDir, 'chrome.log');
  const chromeLog = openSync(chromeLogPath, 'a');
  chrome = spawn(executable, [
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${userDataDir}`,
    '--headless=new',
    // CI containers: no user namespaces / small /dev/shm.
    '--no-sandbox',
    '--disable-setuid-sandbox',
    '--disable-dev-shm-usage',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-gpu',
    '--disable-features=Translate,BackForwardCache',
    'about:blank',
  ], { stdio: ['ignore', chromeLog, chromeLog], detached: false });

  const version = await waitForCdpVersion(CDP_PORT).catch((error) => {
    let tail = '';
    try { tail = readFileSync(chromeLogPath, 'utf-8').split('\n').slice(-40).join('\n'); } catch {}
    throw new Error(`${error.message}\n--- chrome.log (tail) ---\n${tail}`);
  });
  const browserId = new URL(version.webSocketDebuggerUrl).pathname;
  console.log(`# browser_id=${browserId}`);
  check('CDP /json/version exposes a browser-scoped websocket pathname',
    Boolean(browserId) && browserId !== '/');

  browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
  const context = browser.contexts()[0];
  const localPage = context.pages()[0] || await context.newPage();
  await localPage.setContent('<html><body><canvas id="game" width="320" height="180"></canvas></body></html>');
  const foreignPage = await context.newPage();
  await foreignPage.setContent('<html><body>soren91 tab</body></html>');

  // The reader module is imported only now: it reads cwd and SOREN_CDP_PORT.
  const reader = await import(pathToFileURL(join(REPO_ROOT, 'soviet_local.mjs')).href);
  const { decideMuteAction } = await import(pathToFileURL(join(REPO_ROOT, 'lib', 'mute_flag_reader.mjs')).href);

  const begin = cli('begin', '--token', TOKEN, '--browser-id', browserId);
  check('begin creates revision 1', begin.revision === 1, JSON.stringify(begin));
  cli('join', '--token', TOKEN, '--role', 'runner', '--pid', String(owner.pid));
  const status = cli('status');
  check('the joined owner is alive', status.owners[0]?.state === 'alive', JSON.stringify(status.owners));

  // 1. Live owner + soren91 tab still open: the flag must stay.
  const kept = await reader.evaluateMuteFlag({ page: localPage, context, log: () => {} });
  check('live owner keeps the record', kept.muted === true, JSON.stringify(kept));
  check('flag file still present', existsSync(flagPath));

  // 2. Owner SIGKILLed but the soren91 tab still exists: still kept.
  owner.kill('SIGKILL');
  await new Promise((resolve) => owner.once('exit', resolve));
  const afterKill = cli('status');
  check('the SIGKILLed owner is detected as dead', afterKill.all_owners_dead === true, JSON.stringify(afterKill));
  const foreignKept = await reader.evaluateMuteFlag({ page: localPage, context, log: () => {} });
  check('a foreign CDP page keeps the record', foreignKept.muted === true && foreignKept.reason === 'foreign-pages',
    JSON.stringify(foreignKept));

  // 3. soren91's tab is gone: the bridge must reap and restore the page.
  const frozenSession = await freezeLocalPage(localPage, context);
  check('local page frozen (canvas hidden)', (await readLocalPageState(localPage)).visibility === 'hidden');
  await foreignPage.close();

  const reaped = await reader.evaluateMuteFlag({ page: localPage, context, log: () => {} });
  check('stale record is reaped', reaped.muted === false && reaped.reason === 'reaped', JSON.stringify(reaped));
  check('flag file is gone after the reap', !existsSync(flagPath));
  const evidence = readdirSync(join(workDir, 'tmp')).filter((name) => name.includes('.reaped-'));
  check('the reaped record is kept as evidence', evidence.length === 1, evidence.join(','));

  await reader.restoreLocalPageLifecycleAfterReap(localPage, context);
  const restored = await readLocalPageState(localPage);
  check('canvas is visible again after the restore', restored.visibility !== 'hidden', JSON.stringify(restored));
  check('render pause flag cleared', restored.renderPaused === false, JSON.stringify(restored));
  check('soren91 canvas style snapshot cleared', restored.frozenStyle === false, JSON.stringify(restored));

  // The decision that produced the reap must also be reachable through the pure
  // reader decision (same status/foreign/browser triple).
  const decision = decideMuteAction({
    status: { ok: true, state: 'owned', armed: true, browser_id: browserId, token: TOKEN, revision: 1,
      owners: [{ role: 'runner', pid: 1, state: 'dead' }], all_owners_dead: true, muted: true },
    ownBrowserId: browserId,
    foreignPages: 0,
  });
  check('reader decision proposes the compare-and-swap reap',
    decision.reap !== null && decision.reason === 'reapable', JSON.stringify(decision));

  await frozenSession.detach().catch(() => {});
  writeFileSync(join(workDir, 'e2e-result.json'), JSON.stringify({ token: TOKEN, browserId, failures }, null, 2));

  if (failures.length > 0) {
    console.log(`\nFAIL: ${failures.length} check(s) failed: ${failures.join(' | ')}`);
    exitCode = 1;
  } else {
    console.log('\nPASS: stale mute record self-heal verified end to end');
  }
} catch (error) {
  console.log(`\nFAIL: unexpected error: ${(error && error.stack) || error}`);
  exitCode = 1;
} finally {
  try { if (owner.exitCode === null && !owner.killed) owner.kill('SIGKILL'); } catch {}
  try { if (browser) await browser.close(); } catch {}
  try { if (chrome && chrome.exitCode === null) chrome.kill('SIGKILL'); } catch {}
  try { rmSync(workDir, { recursive: true, force: true }); } catch {}
}

process.exit(exitCode);

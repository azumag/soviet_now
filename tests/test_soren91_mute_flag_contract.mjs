// Contract tests for the local-BGM mute flag ownership record.
//
// Regression context (soviet_now#514): the 2026-09-17 incident left
// tmp/mute_local_bgm behind after soren91 died, and soviet_local.mjs skipped
// every page interaction while it existed, so the main broadcast stopped.  The
// 9/18 fix was implemented but lost before commit, so this test pins BOTH the
// fail-closed release decision (via the real lib/mute_flag.py CLI) and the
// writer/reader wiring in the tracked sources.

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { browserIdFromWebSocketUrl, decideMuteAction } from '../lib/mute_flag_reader.mjs';

const REPO_ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const HELPER = join(REPO_ROOT, 'lib', 'mute_flag.py');

function tempFlag() {
  const dir = mkdtempSync(join(tmpdir(), 'soren91-mute-'));
  return { dir, flag: join(dir, 'mute_local_bgm') };
}

function cli(flag, ...args) {
  const result = spawnSync('python3', [HELPER, ...args, '--flag', flag], { encoding: 'utf-8' });
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

function spawnOwner() {
  return spawn(process.execPath, ['-e', 'setTimeout(() => {}, 300000)'], { stdio: 'ignore' });
}

function killOwner(child) {
  child.kill('SIGKILL');
  return new Promise((resolve) => child.once('exit', resolve));
}

// --- reader decision (pure) ------------------------------------------------

const OWNED_DEAD = {
  ok: true,
  state: 'owned',
  muted: true,
  token: 't1',
  revision: 3,
  browser_id: '/devtools/browser/abc',
  armed: true,
  owners: [{ role: 'runner', pid: 4242, state: 'dead' }],
  all_owners_dead: true,
  reapable: true,
};

test('reader unmutes only when the record is absent', () => {
  const decision = decideMuteAction({
    status: { ok: true, state: 'absent', muted: false },
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: 0,
  });
  assert.equal(decision.muted, false);
  assert.equal(decision.reap, null);
  assert.equal(decision.reason, 'absent');
});

test('reader keeps legacy and corrupt records and never reaps them', () => {
  for (const state of ['legacy', 'corrupt', 'unreadable']) {
    const decision = decideMuteAction({
      status: { ok: true, state, muted: true },
      ownBrowserId: '/devtools/browser/abc',
      foreignPages: 0,
    });
    assert.equal(decision.muted, true, state);
    assert.equal(decision.reap, null, state);
    assert.equal(decision.reason, state);
  }
});

test('reader keeps the record while any owner is alive or unknown', () => {
  for (const ownerState of ['alive', 'unknown']) {
    const decision = decideMuteAction({
      status: { ...OWNED_DEAD, owners: [{ role: 'runner', pid: 1, state: ownerState }] },
      ownBrowserId: '/devtools/browser/abc',
      foreignPages: 0,
    });
    assert.equal(decision.muted, true);
    assert.equal(decision.reap, null);
    assert.equal(decision.reason, `owner-${ownerState}`);
  }
});

test('reader keeps an unarmed record or one with no owners', () => {
  const unarmed = decideMuteAction({
    status: { ...OWNED_DEAD, armed: false },
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: 0,
  });
  assert.equal(unarmed.reason, 'unarmed');
  const ownerless = decideMuteAction({
    status: { ...OWNED_DEAD, owners: [] },
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: 0,
  });
  assert.equal(ownerless.reason, 'no-owners');
});

test('reader refuses to reap a record from another browser or an unknown page count', () => {
  const otherBrowser = decideMuteAction({
    status: OWNED_DEAD,
    ownBrowserId: '/devtools/browser/other',
    foreignPages: 0,
  });
  assert.equal(otherBrowser.reason, 'browser-mismatch');
  const unknownPages = decideMuteAction({
    status: OWNED_DEAD,
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: -1,
  });
  assert.equal(unknownPages.reason, 'unknown-pages');
});

test('reader keeps the record while another page is open, and proposes the CAS reap otherwise', () => {
  const foreign = decideMuteAction({
    status: OWNED_DEAD,
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: 1,
  });
  assert.equal(foreign.muted, true);
  assert.equal(foreign.reap, null);
  assert.equal(foreign.reason, 'foreign-pages');

  const stale = decideMuteAction({
    status: OWNED_DEAD,
    ownBrowserId: '/devtools/browser/abc',
    foreignPages: 0,
  });
  assert.equal(stale.muted, true, 'stays muted until the reap CLI confirms');
  assert.deepEqual(stale.reap, {
    token: 't1',
    revision: 3,
    browserId: '/devtools/browser/abc',
  });
});

test('reader fails closed on a status error', () => {
  for (const status of [null, {}, { ok: false, reason: 'cli-error' }]) {
    const decision = decideMuteAction({ status, ownBrowserId: '/devtools/browser/abc', foreignPages: 0 });
    assert.equal(decision.muted, true);
    assert.equal(decision.reap, null);
    assert.equal(decision.reason, 'status-error');
  }
});

test('browser id is the CDP /json/version websocket pathname', () => {
  assert.equal(browserIdFromWebSocketUrl('ws://127.0.0.1:9222/devtools/browser/uuid-1'),
    '/devtools/browser/uuid-1');
  assert.equal(browserIdFromWebSocketUrl(''), '');
  assert.equal(browserIdFromWebSocketUrl(undefined), '');
});

// --- writer/reader flow through the real CLI -------------------------------

test('ownership record round trip: begin, join, SIGKILL, reap', async () => {
  const { flag } = tempFlag();
  const begin = cli(flag, 'begin', '--token', 'gen-1', '--browser-id', '/devtools/browser/abc');
  assert.equal(begin.revision, 1);
  assert.equal(existsSync(flag), true);

  const owner = spawnOwner();
  const joined = cli(flag, 'join', '--token', 'gen-1', '--role', 'runner', '--pid', String(owner.pid));
  assert.equal(joined.armed, true);
  assert.equal(joined.owners[0].state, 'alive');

  const alive = cli(flag, 'reap', '--token', 'gen-1', '--revision', '1',
    '--browser-id', '/devtools/browser/abc', '--foreign-pages', '0');
  assert.equal(alive.ok, false);
  assert.equal(alive.reason, 'owner-alive');

  await killOwner(owner);

  const status = cli(flag, 'status');
  assert.equal(status.all_owners_dead, true);
  assert.equal(status.reapable, true);
  assert.equal(status.muted, true, 'the record stays muted until the reap succeeds');

  const reaped = cli(flag, 'reap', '--token', 'gen-1', '--revision', '1',
    '--browser-id', '/devtools/browser/abc', '--foreign-pages', '0');
  assert.equal(reaped.ok, true);
  assert.equal(existsSync(flag), false);
  // The record is renamed aside as evidence, never unlinked.
  const evidence = readdirSync(dirname(flag)).filter((name) => name.includes('.reaped-'));
  assert.equal(evidence.length, 1);
  assert.match(readFileSync(join(dirname(flag), evidence[0]), 'utf-8'), /gen-1/);
});

test('replacement generation defeats a stale reap decision', () => {
  const { flag } = tempFlag();
  cli(flag, 'begin', '--token', 'gen-1', '--browser-id', '/devtools/browser/abc');
  const owner = spawnOwner();
  cli(flag, 'join', '--token', 'gen-1', '--role', 'runner', '--pid', String(owner.pid));
  const stale = cli(flag, 'status');

  // A new soren91 generation takes over the record.
  const second = cli(flag, 'begin', '--token', 'gen-2', '--browser-id', '/devtools/browser/abc');
  assert.equal(second.revision, 2);
  const live = spawnOwner();
  cli(flag, 'join', '--token', 'gen-2', '--role', 'runner', '--pid', String(live.pid));

  const refused = cli(flag, 'reap', '--token', stale.token, '--revision', String(stale.revision),
    '--browser-id', stale.browser_id, '--foreign-pages', '0');
  assert.equal(refused.ok, false);
  assert.equal(refused.reason, 'stale');
  assert.equal(existsSync(flag), true);

  owner.kill('SIGKILL');
  live.kill('SIGKILL');
});

test('a legacy empty touch flag is never released automatically', () => {
  const { flag } = tempFlag();
  writeFileSync(flag, '');
  const status = cli(flag, 'status');
  assert.equal(status.state, 'legacy');
  assert.equal(status.muted, true);
  const reap = cli(flag, 'reap', '--token', 't', '--revision', '1', '--browser-id', 'b', '--foreign-pages', '0');
  assert.equal(reap.ok, false);
  assert.equal(reap.reason, 'legacy');
  assert.equal(existsSync(flag), true);
});

test('leave releases the record only when the last owner leaves', () => {
  const { flag } = tempFlag();
  cli(flag, 'begin', '--token', 'gen-1', '--browser-id', '/devtools/browser/abc');
  const runner = spawnOwner();
  const main = spawnOwner();
  cli(flag, 'join', '--token', 'gen-1', '--role', 'runner', '--pid', String(runner.pid));
  cli(flag, 'join', '--token', 'gen-1', '--role', 'main', '--pid', String(main.pid));

  const first = cli(flag, 'leave', '--token', 'gen-1', '--role', 'runner', '--pid', String(runner.pid));
  assert.equal(first.released, false);
  assert.equal(existsSync(flag), true);

  const last = cli(flag, 'leave', '--token', 'gen-1', '--role', 'main', '--pid', String(main.pid));
  assert.equal(last.released, true);
  assert.equal(existsSync(flag), false);

  runner.kill('SIGKILL');
  main.kill('SIGKILL');
});

test('abort releases only a record no owner ever joined', () => {
  const { flag } = tempFlag();
  cli(flag, 'begin', '--token', 'gen-1', '--browser-id', '/devtools/browser/abc');
  assert.equal(cli(flag, 'abort', '--token', 'gen-1').released, true);
  assert.equal(existsSync(flag), false);

  cli(flag, 'begin', '--token', 'gen-2', '--browser-id', '/devtools/browser/abc');
  const owner = spawnOwner();
  cli(flag, 'join', '--token', 'gen-2', '--role', 'runner', '--pid', String(owner.pid));
  const refused = cli(flag, 'abort', '--token', 'gen-2');
  assert.equal(refused.ok, false);
  assert.equal(refused.reason, 'armed');
  assert.equal(existsSync(flag), true);
  owner.kill('SIGKILL');
});

// --- writer / reader wiring in the tracked sources -------------------------

function readSource(relativePath) {
  return readFileSync(join(REPO_ROOT, relativePath), 'utf-8');
}

test('soren91_control.sh no longer writes or deletes the flag directly', () => {
  const source = readSource('soren91_control.sh');
  assert.doesNotMatch(source, /touch\s+"\$ELOOP_LIB_DIR\/tmp\/mute_local_bgm"/);
  assert.doesNotMatch(source, /rm -f\s+"\$ELOOP_LIB_DIR\/tmp\/mute_local_bgm"/);
  assert.match(source, /_soren91_mute_begin/);
  assert.match(source, /_soren91_mute_abort/);
  assert.match(source, /SOREN91_MUTE_TOKEN='\$SOREN91_MUTE_TOKEN'/);
  assert.match(source, /SOREN91_MUTE_TOKEN="\$SOREN91_MUTE_TOKEN" \\/);
});

test('run_player_loop.sh joins and leaves as the durable runner owner', () => {
  const source = readSource('soren91/run_player_loop.sh');
  assert.match(source, /_mute_join/);
  assert.match(source, /_mute_leave/);
  assert.match(source, /--role runner/);
});

test('soren91/main.mjs joins the record as main owner before connecting to the browser', () => {
  const source = readSource('soren91/main.mjs');
  assert.match(source, /muteFlagOwnerJoin\(\);/);
  assert.match(source, /'--role', 'main'/);
  assert.match(source, /process\.on\('exit', \(\) => muteFlagOwnerLeave\(\)\)/);
});

test('soviet_local.mjs reads the record through lib/mute_flag.py instead of existsSync', () => {
  const source = readSource('soviet_local.mjs');
  assert.match(source, /from '\.\/lib\/mute_flag_reader\.mjs'/);
  assert.match(source, /function evaluateMuteFlag/);
  assert.match(source, /Target\.getTargets/);
  assert.match(source, /Page\.setWebLifecycleState/);
  // The unmute transition must restore the lifecycle freeze soren91 applied.
  assert.match(source, /await restoreLocalPageLifecycleAfterReap\(page, context\);/);
  // A record that appears/disappears gates the very next iteration; only the
  // record contents are re-read on the throttle.
  assert.match(source, /const muteFlagPresent = fs\.existsSync\(MUTE_FLAG_FILE\);/);
  assert.match(source, /muteFlagPresent !== muteFlagMuted/);
  assert.match(source, /MUTE_FLAG_RECHECK_MS/);
  assert.doesNotMatch(source, /const shouldMute = fs\.existsSync\(MUTE_FLAG_FILE\)/);
  assert.doesNotMatch(source, /rm -f/);
});

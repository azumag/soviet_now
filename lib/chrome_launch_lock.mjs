// chrome_launch_lock.mjs — cross-process mutex for Chrome-for-Testing LAUNCHES.
//
// WHY: macOS aborts (SIGABRT) inside `_RegisterApplication` /
// `+[NSApplication sharedApplication] -> -[NSApplication init]` when two
// Chrome-for-Testing apps run their app/window-server registration concurrently
// (crash report 2026-06-02 16:23: main bridge relaunch during a soren91 handoff;
// also the 11:47 wildcard candidate burst). The wildcard orchestrator already
// serializes its OWN slot threads in-process, but that cannot coordinate with the
// main soviet_local bridge (or a standalone soren91), so a main-bridge relaunch
// can still race a candidate / sibling launch. Every site that SPAWNS a
// Chrome-for-Testing instance must hold THIS one filesystem lock through the
// registration-sensitive window.
//
// Mirrors lib/obs_source_lock.mjs (atomic mkdir, stale-owner steal, post-op
// settle). The settle is held AFTER initiating a launch so the next launcher
// waits out the new process's NSApplication-init registration before starting its
// own. Best-effort: on unreachable lock dir or wait-timeout we proceed UNLOCKED
// rather than hang a launch. acquire() returns whether the lock is held; pass it
// to release(). Gate off with CHROME_LAUNCH_LOCK_ENABLED=0.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOCK_DIR = process.env.CHROME_LAUNCH_LOCK_DIR
  || path.join(REPO_ROOT, 'tmp', 'state', 'chrome_launch.lock');
const OWNER_FILE = path.join(LOCK_DIR, 'owner');
const ENABLED = process.env.CHROME_LAUNCH_LOCK_ENABLED !== '0';
const STALE_SEC = Number(process.env.CHROME_LAUNCH_LOCK_STALE_SEC || 60);
const SETTLE_MS = Math.round(Number(process.env.CHROME_LAUNCH_LOCK_SETTLE_SEC || 2.5) * 1000);
const MAX_WAIT_MS = Number(process.env.CHROME_LAUNCH_LOCK_MAX_WAIT_MS || 60000);
const POLL_MS = 150;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function ownerAlive(pid) {
  if (!pid || !/^\d+$/.test(String(pid))) return false;
  try {
    process.kill(Number(pid), 0);
    return true;
  } catch (err) {
    return err && err.code === 'EPERM';
  }
}

export async function acquireChromeLaunchLock() {
  if (!ENABLED) return false;
  const deadline = Date.now() + MAX_WAIT_MS;
  try { fs.mkdirSync(path.dirname(LOCK_DIR), { recursive: true }); } catch {}
  for (;;) {
    try {
      fs.mkdirSync(LOCK_DIR);
      try { fs.writeFileSync(OWNER_FILE, `${process.pid}:${Math.floor(Date.now() / 1000)}`); } catch {}
      return true;
    } catch (err) {
      if (!err || err.code !== 'EEXIST') return false;
    }
    let raw = '';
    try { raw = fs.readFileSync(OWNER_FILE, 'utf8'); } catch {}
    const pid = (raw.split(':')[0] || '').trim();
    let ageSec = 0;
    try {
      const st = fs.statSync(LOCK_DIR);
      ageSec = (Date.now() - st.mtimeMs) / 1000;
    } catch {}
    if (!ownerAlive(pid) && ageSec > STALE_SEC) {
      try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch {}
      continue;
    }
    if (Date.now() > deadline) return false;
    await sleep(POLL_MS);
  }
}

export async function releaseChromeLaunchLock(held = true) {
  if (held === false) return;
  // Settle while STILL holding the lock so the next launcher waits out the new
  // Chrome's NSApplication-init / _RegisterApplication before it starts its own.
  if (SETTLE_MS > 0) await sleep(SETTLE_MS);
  try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch {}
}

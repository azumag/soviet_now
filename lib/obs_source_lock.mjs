// obs_source_lock.mjs — cross-process mutex for OBS mac-capture source updates.
//
// WHY: macOS mac-capture (ScreenCaptureKit) double-frees / heap-corrupts inside
// obs_source_update when two SetInputSettings calls race on SCStream teardown —
// even across DIFFERENT sources and DIFFERENT OS processes. OBS crash reports show
// SIGABRT (___BUG_IN_CLIENT_OF_LIBMALLOC_POINTER_BEING_FREED_WAS_NOT_ALLOCATED) in
// mac-capture from the obs-websocket thread. During a param-parallel / wildcard
// session the game-capture watchdog (sorengame rebind/bounce) and the main
// soviet_local bridge fire SetInputSettings concurrently with the candidate
// updates (wildcardParallelCandN) — that cross-process race crashes OBS entirely.
//
// So EVERY Node site that issues SetInputSettings on a mac-capture source must
// hold THIS one filesystem lock (the same lock obs_window_capture_source.sh holds
// via lib/obs_source_lock.sh). Atomic mkdir (macOS-compatible), stale-owner
// detection, and a post-op settle so macOS finishes tearing down the old SCStream
// before the next holder runs.
//
// Best-effort by design: if the lock dir is unreachable or we wait past the
// deadline we proceed UNLOCKED rather than hang the broadcast (a frozen stream is
// worse than degraded serialization for one call). acquire() returns whether the
// lock is actually held; pass that to release().
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LOCK_DIR = process.env.OBS_SOURCE_LOCK_DIR
  || path.join(REPO_ROOT, 'tmp', 'state', 'obs_source_update.lock');
const OWNER_FILE = path.join(LOCK_DIR, 'owner');
const STALE_SEC = Number(process.env.OBS_SOURCE_LOCK_STALE_SEC || 30);
const SETTLE_MS = Math.round(Number(process.env.OBS_SOURCE_LOCK_SETTLE_SEC || 2) * 1000);
const MAX_WAIT_MS = Number(process.env.OBS_SOURCE_LOCK_MAX_WAIT_MS || 30000);
const POLL_MS = 150;

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function ownerAlive(pid) {
  if (!pid || !/^\d+$/.test(String(pid))) return false;
  try {
    process.kill(Number(pid), 0);
    return true;
  } catch (err) {
    // EPERM means the process exists but we can't signal it -> still alive.
    return err && err.code === 'EPERM';
  }
}

export async function acquireObsSourceLock() {
  const deadline = Date.now() + MAX_WAIT_MS;
  try { fs.mkdirSync(path.dirname(LOCK_DIR), { recursive: true }); } catch {}
  for (;;) {
    try {
      fs.mkdirSync(LOCK_DIR);
      try { fs.writeFileSync(OWNER_FILE, `${process.pid}:${Math.floor(Date.now() / 1000)}`); } catch {}
      return true;
    } catch (err) {
      if (!err || err.code !== 'EEXIST') {
        // Cannot create the lock dir at all (bad path/permissions). Proceed
        // best-effort unlocked rather than fail the OBS update outright.
        return false;
      }
    }
    // Held by someone else — steal only if the owner is dead AND it is stale.
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

export async function releaseObsSourceLock(held = true) {
  if (held === false) return;
  // Settle while STILL holding the lock so the next holder waits out the macOS
  // SCStream teardown before issuing its own SetInputSettings.
  if (SETTLE_MS > 0) await sleep(SETTLE_MS);
  try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch {}
}

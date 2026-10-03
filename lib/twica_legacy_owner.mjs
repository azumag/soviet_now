// Single-purpose legacy iframe guard. This does not own a game or the encoder.
import fs from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

const guards = new WeakMap();
export const DEFAULT_TWICA_STATE_DIR = '/home/ubuntu/docich/run/twica-common';
const limit = 4096;

function directorySafe(directory) {
  const stat = fs.lstatSync(directory);
  return stat.isDirectory() && !stat.isSymbolicLink() && stat.uid === process.getuid()
    && !(stat.mode & 0o077) && fs.realpathSync(directory) === path.resolve(directory);
}

function readPrivate(file) {
  let fd;
  try {
    fd = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
    const stat = fs.fstatSync(fd);
    if (!stat.isFile() || stat.size > limit || stat.nlink !== 1
        || stat.uid !== process.getuid() || (stat.mode & 0o077)) return null;
    const bytes = Buffer.alloc(limit + 1);
    const length = fs.readSync(fd, bytes, 0, bytes.length, 0);
    if (length > limit) return null;
    const value = JSON.parse(bytes.subarray(0, length).toString('utf8'));
    return value && !Array.isArray(value) && typeof value === 'object' ? value : null;
  } catch { return null; }
  finally { if (fd !== undefined) fs.closeSync(fd); }
}

function identity() {
  const boot = fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim();
  const stat = fs.readFileSync(`/proc/${process.pid}/stat`, 'utf8');
  return `${boot}:${stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19]}`;
}

function writeHeartbeat(directory, filename, fields) {
  if (!directorySafe(directory)) return;
  const temporary = path.join(directory, `.legacy-${randomUUID()}`);
  try {
    fs.writeFileSync(temporary, JSON.stringify({
      schema: 1, pid: process.pid, identity: identity(),
      monotonic_ns: Number(process.hrtime.bigint()), ...fields,
    }), { flag: 'wx', mode: 0o600 });
    fs.renameSync(temporary, path.join(directory, filename));
  } finally {
    try { fs.unlinkSync(temporary); } catch {}
  }
}

export function readLegacyPolicy(directory, previouslyManaged = false) {
  let exists = false;
  try {
    try { fs.lstatSync(path.join(directory, 'control.json')); exists = true; }
    catch (error) { if (error.code !== 'ENOENT') exists = true; }
    if (exists && directorySafe(directory)) {
      const value = readPrivate(path.join(directory, 'control.json'));
      if (value?.schema === 1 && ['legacy', 'none', 'common'].includes(value.owner)
          && /^[0-9a-f]{32}$/.test(value.generation)) {
        return { ...value, managed: true };
      }
    }
  } catch {}
  return { owner: exists || previouslyManaged ? 'none' : 'legacy',
    generation: '', managed: exists || previouslyManaged };
}

// Runs in the page's main document only. Removing the iframe also terminates
// its WebSocket/polling/audio; hiding its CSS would not transfer ownership.
export function reconcileLegacyFrame({ item, enabled }) {
  if (window.top !== window) return { subscribed: false };
  const id = 'soren-direct-stream-overlay-twica';
  if (!enabled || !item?.srcUrl) {
    document.getElementById(id)?.remove();
    document.getElementById(`${id}-buffer`)?.remove();
    return { subscribed: false };
  }
  if (!document.body) return { subscribed: false };
  let frame = document.getElementById(id);
  if (!frame) {
    frame = document.createElement('iframe');
    frame.id = id;
    frame.title = item.title;
    frame.setAttribute('aria-hidden', 'true');
    frame.setAttribute('allowtransparency', 'true');
    frame.setAttribute('frameborder', '0');
    frame.dataset.sorenExternalOverlay = '1';
    Object.assign(frame.style, { position: 'fixed', border: '0', margin: '0', padding: '0',
      background: 'transparent', pointerEvents: 'none', opacity: '1', visibility: 'visible', ...item.style });
    frame.src = item.srcUrl;
    document.body.appendChild(frame);
  }
  return { subscribed: true };
}

export async function installTwicaLegacyOwner(page, item, options = {}) {
  const existing = guards.get(page);
  if (existing) { existing.item = item; return existing; }
  const directory = path.resolve(options.directory || process.env.DOCICH_TWICA_STATE_DIR || DEFAULT_TWICA_STATE_DIR);
  const role = options.role || (process.env.SOREN_DIRECT_TWICA_PROXY_PORT === '18081' ? 'shared' : 'game');
  if (!['shared', 'game'].includes(role)) throw new Error('invalid TwiCa legacy role');
  const filename = `legacy-${process.pid}-${randomUUID()}.json`;
  const guard = { item, stopped: false, managed: false, timer: null, subscribed: false };
  guards.set(page, guard);
  const close = () => {
    guard.stopped = true;
    clearTimeout(guard.timer);
    // Delete only this exact client's marker, never other process markers.
    try { fs.unlinkSync(path.join(directory, filename)); } catch {}
  };
  guard.close = close;
  page.once('close', close);
  const tick = async () => {
    if (guard.stopped || page.isClosed()) { close(); return; }
    const policy = readLegacyPolicy(directory, guard.managed);
    guard.managed ||= policy.managed;
    const legacyRole = policy.legacy_role || 'game';
    // Preparation preserves the existing clients until readiness is checked.
    // After the first handoff, rollback selects only one legacy role.
    const enabled = policy.owner === 'legacy' && (!policy.managed || policy.preserve_legacy === true || legacyRole === role);
    try {
      const result = await page.evaluate(reconcileLegacyFrame, { item: guard.item, enabled });
      guard.subscribed = result?.subscribed === true;
      if (guard.managed && !guard.stopped) {
        writeHeartbeat(directory, filename, { role, generation: policy.generation,
          subscribed: guard.subscribed, protocol: 1 });
      }
    } catch {
      // Navigation or a stalled page is NOT an acknowledgement. The operator
      // requires fresh acknowledgements from every still-live participant.
    } finally {
      if (!guard.stopped) {
        guard.timer = setTimeout(tick, options.intervalMs || 250);
        guard.timer.unref?.();
      }
    }
  };
  await tick();
  return guard;
}

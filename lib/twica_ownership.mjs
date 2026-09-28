// Game-independent TwiCa ownership. Only fixed metadata crosses the browser binding.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ZERO = '0'.repeat(32);
const pages = new WeakMap();
const guards = new Map();
const MAX_BYTES = 8192;

export function ownershipConfig(env = process.env) {
  const enabled = ['1', 'true', 'on'].includes(String(env.DOCICH_TWICA_COMMON_ENABLED || '0').toLowerCase());
  const directory = env.DOCICH_TWICA_STATE_DIR || path.join(ROOT, 'tmp/state/twica-common');
  if (!path.isAbsolute(directory)) throw new Error('TwiCa ownership directory must be absolute');
  return { enabled, directory };
}

function privateDirectory(directory) {
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  const s = fs.lstatSync(directory);
  if (!s.isDirectory() || s.uid !== process.getuid() || (s.mode & 0o077)
      || fs.realpathSync(directory) !== path.resolve(directory)) {
    throw new Error('TwiCa ownership directory is not private');
  }
}
function readRecord(filename) {
  const fd = fs.openSync(filename, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK);
  try {
    const s = fs.fstatSync(fd);
    if (!s.isFile() || s.uid !== process.getuid() || s.nlink !== 1 || (s.mode & 0o077)
        || s.size < 1 || s.size > MAX_BYTES) throw new Error('invalid TwiCa record');
    return JSON.parse(fs.readFileSync(fd, 'utf8'));
  } finally { fs.closeSync(fd); }
}
export function readOwnership(directory) {
  try {
    const v = readRecord(path.join(directory, 'owner.json'));
    if (v.protocol !== 1 || !['legacy', 'draining', 'common'].includes(v.mode)
        || !/^[a-f0-9]{32}$/.test(v.generation)) throw new Error('invalid ownership');
    return { mode: v.mode, generation: v.generation };
  } catch (e) {
    return { mode: e.code === 'ENOENT' ? 'legacy' : 'blocked', generation: ZERO };
  }
}
function writeRecord(filename, value) {
  const temporary = path.join(path.dirname(filename), `.state-${crypto.randomUUID()}`);
  try {
    fs.writeFileSync(temporary, JSON.stringify(value), { flag: 'wx', mode: 0o600 });
    fs.renameSync(temporary, filename);
  } finally { try { fs.unlinkSync(temporary); } catch {} }
}
function birth() {
  const text = fs.readFileSync(`/proc/${process.pid}/stat`, 'utf8');
  return text.slice(text.lastIndexOf(')') + 2).split(/\s+/)[19];
}
function monoNs() { return Number(process.hrtime.bigint()); }

// The upgraded host may keep its proxy alive while its game page is closed.
// With the guard explicitly enabled, zero pages means no legacy subscribers;
// without that enable flag, an unguarded host must never report ready.
export function twicaGuardHealth() {
  const now = monoNs();
  const registered = [...guards.values()];
  return { protocol: 1, guard_ready: ownershipConfig().enabled
    && registered.every(g => now - g.updated_ns <= 5e9 && g.state !== 'pending'),
    guards: registered.length };
}

// This callback is serialized into a browser; keep it dependency-free.
export function twicaGuardBrowser({ binding, surface }) {
  if (window.top !== window) return;
  const key = '__docichTwicaOwnerGuard';
  const previous = window[key];
  if (previous?.binding === binding) return;
  previous?.stop?.();
  let stopped = false;
  let timer;
  const remove = () => {
    for (const id of [surface.elementId, `${surface.elementId}-buffer`]) {
      const node = document.getElementById(id);
      if (node) {
        node.src = 'about:blank';
        node.remove();
      }
    }
  };
  const create = () => {
    if (document.getElementById(surface.elementId) || !document.body || !surface.srcUrl) return;
    const frame = document.createElement('iframe');
    frame.id = surface.elementId;
    frame.title = surface.title;
    frame.setAttribute('aria-hidden', 'true');
    frame.setAttribute('allowtransparency', 'true');
    frame.setAttribute('frameborder', '0');
    frame.dataset.sorenExternalOverlay = '1';
    Object.assign(frame.style, { position: 'fixed', border: '0', margin: '0', padding: '0',
      background: 'transparent', pointerEvents: 'none', ...surface.style });
    frame.src = surface.srcUrl;
    document.body.appendChild(frame);
  };
  const tick = async () => {
    try {
      const choice = await window[binding](null);
      if (stopped) return;
      if (choice.mode === 'legacy') create(); else remove();
      const frames = [surface.elementId, `${surface.elementId}-buffer`]
        .filter(id => document.getElementById(id)).length;
      await window[binding]({ generation: choice.generation,
        state: frames ? 'legacy' : 'retired', frames });
    } catch {
      remove();
    } finally {
      if (!stopped) timer = setTimeout(tick, 250);
    }
  };
  window[key] = { binding, stop() { stopped = true; clearTimeout(timer); remove(); } };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => { if (!stopped) void tick(); }, { once: true });
  } else void tick();
}

export async function installTwicaOwnership(page, config) {
  const options = config.twicaOwnership;
  const surface = config.surfaces.find(item => item.key === 'twica');
  if (!options?.enabled || !surface) return false;
  if (pages.has(page)) return true;
  privateDirectory(options.directory);
  const records = path.join(options.directory, 'consumers');
  privateDirectory(records);
  const id = crypto.randomBytes(16).toString('hex');
  const filename = path.join(records, `${id}.json`);
  const binding = `__docichTwica_${id}`;
  const processIdentity = { pid: process.pid, birth: birth(),
    boot: fs.readFileSync('/proc/sys/kernel/random/boot_id', 'utf8').trim() };
  let record = { protocol: 1, ...processIdentity, updated_ns: monoNs(),
    generation: ZERO, state: 'pending', frames: 0 };
  writeRecord(filename, record);
  guards.set(id, record);
  const cleanup = () => {
    guards.delete(id);
    try { fs.unlinkSync(filename); } catch {}
  };
  try {
    await page.exposeBinding(binding, (source, ack) => {
      if (source.frame !== page.mainFrame()) throw new Error('top frame required');
      const choice = readOwnership(options.directory);
      if (ack && ack.generation === choice.generation
          && ['retired', 'legacy'].includes(ack.state)
          && Number.isInteger(ack.frames) && ack.frames >= 0 && ack.frames <= 2
          && (ack.state !== 'retired' || ack.frames === 0)) {
        record = { ...record, generation: ack.generation, state: ack.state,
          frames: ack.frames, updated_ns: monoNs() };
        writeRecord(filename, record);
        guards.set(id, record);
      }
      return choice;
    });
    const payload = { binding, surface: { elementId: surface.elementId, title: surface.title,
      srcUrl: surface.srcUrl, style: surface.style } };
    await page.addInitScript(twicaGuardBrowser, payload);
    await page.evaluate(twicaGuardBrowser, payload);
    page.once('close', cleanup);
    pages.set(page, { id });
    return true;
  } catch (error) {
    cleanup();
    throw error;
  }
}

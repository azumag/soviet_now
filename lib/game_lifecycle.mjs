import crypto from 'node:crypto';
import fs from 'fs';
import path from 'path';

const SCHEMA_VERSION = 1;
const DEFAULT_DIR = path.resolve(process.env.SOREN_GAME_LIFECYCLE_DIR || 'tmp/state/game_lifecycle');
const REQUEST_ID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

function directoryFor(directory = DEFAULT_DIR) {
  return path.resolve(String(directory));
}

function readObject(filePath) {
  try {
    const value = JSON.parse(fs.readFileSync(filePath, 'utf8'));
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function lifecyclePath(directory, name) {
  return path.join(directoryFor(directory), name);
}

export function readGameLifecycleRequest(directory = DEFAULT_DIR) {
  return readObject(lifecyclePath(directory, 'request.json'));
}

export function readGameLifecycleAck(directory = DEFAULT_DIR) {
  return readObject(lifecyclePath(directory, 'ack.json'));
}

export function readGameLifecycleControl(directory = DEFAULT_DIR) {
  return readObject(lifecyclePath(directory, 'control.json'));
}

export function readGameLifecycleResource(directory = DEFAULT_DIR) {
  return readObject(lifecyclePath(directory, 'game_resource.json'));
}

const IDENTITY_FIELDS = ['request_id', 'game', 'generation', 'deadline_epoch', 'deadline_at'];

// Every durable lifecycle record is copied from the request.  Comparing the
// full identity (rather than only the UUID) prevents a late response from a
// reused controller or a different generation from stopping the next game.
export function lifecycleRecordMatches(record, request) {
  if (!record || !request) return false;
  if (record.schema !== SCHEMA_VERSION || request.schema !== SCHEMA_VERSION) return false;
  if (!REQUEST_ID_RE.test(String(record.request_id || ''))
      || String(record.request_id) !== String(request.request_id)) return false;
  if (typeof request.game !== 'string' || !request.game
      || String(record.game || '') !== request.game) return false;
  if ((record.generation ?? null) !== (request.generation ?? null)) return false;
  if (!Number.isFinite(Number(request.deadline_epoch))
      || !Number.isFinite(Number(record.deadline_epoch))
      || Number(record.deadline_epoch) !== Number(request.deadline_epoch)) return false;
  if (typeof request.deadline_at !== 'string' || !request.deadline_at
      || record.deadline_at !== request.deadline_at) return false;
  return IDENTITY_FIELDS.every((field) => Object.prototype.hasOwnProperty.call(record, field)
    && Object.prototype.hasOwnProperty.call(request, field));
}

export function lifecycleAckMatches(control, ack, statuses = []) {
  if (!lifecycleRecordMatches(ack, control)) return false;
  return !statuses.length || statuses.includes(ack.status);
}

export function lifecycleControlMatches(control, request, action = null) {
  if (!lifecycleRecordMatches(control, request)) return false;
  return action == null || control.action === action;
}

export function writeGameLifecycleResource(
  control,
  status,
  evidence = {},
  directory = DEFAULT_DIR,
) {
  if (!control || !REQUEST_ID_RE.test(String(control.request_id || ''))) return null;
  if (control.schema !== SCHEMA_VERSION
      || typeof control.game !== 'string'
      || !Object.prototype.hasOwnProperty.call(control, 'generation')
      || !Number.isFinite(Number(control.deadline_epoch))
      || typeof control.deadline_at !== 'string'
      || !control.deadline_at) return null;
  const dir = directoryFor(directory);
  fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
  try { fs.chmodSync(dir, 0o700); } catch {}
  const value = {
    ...evidence,
    schema: SCHEMA_VERSION,
    request_id: String(control.request_id),
    game: control.game || null,
    generation: control.generation ?? null,
    deadline_epoch: Number(control.deadline_epoch),
    deadline_at: control.deadline_at,
    status: String(status || 'failed'),
    updated_at: new Date().toISOString(),
  };
  const target = lifecyclePath(dir, 'game_resource.json');
  const temporary = `${target}.tmp.${process.pid}.${Date.now()}.${crypto.randomBytes(8).toString('hex')}`;
  fs.writeFileSync(temporary, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  try { fs.chmodSync(temporary, 0o600); } catch {}
  const fd = fs.openSync(temporary, 'r');
  try { fs.fsyncSync(fd); } finally { fs.closeSync(fd); }
  try {
    fs.renameSync(temporary, target);
  } catch (err) {
    try { fs.unlinkSync(temporary); } catch {}
    throw err;
  }
  const dirFd = fs.openSync(dir, 'r');
  try { fs.fsyncSync(dirFd); } finally { fs.closeSync(dirFd); }
  return value;
}

export function clearGameLifecycleResource(directory = DEFAULT_DIR) {
  try { fs.unlinkSync(lifecyclePath(directory, 'game_resource.json')); } catch {}
}

export const GAME_LIFECYCLE_DIR = DEFAULT_DIR;

/** Optional, bounded retention of already captured frames rejected by observation. */
import {
  closeSync, constants, fstatSync, lstatSync, linkSync, mkdirSync, openSync, readSync,
  readdirSync, unlinkSync, writeFileSync,
} from 'node:fs';
import { join, resolve } from 'node:path';

export const MAX_REJECTED_FRAMES_PER_SESSION = 3;
export const MAX_REJECTED_FRAME_BYTES = 8 * 1024 * 1024;
const ELIGIBLE_REASONS = new Set(['non-move', 'unknown-current', 'confirm-frame']);
const UUID_RE = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const RUN_DIR_RE = /^run_[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const FRAME_NAME_RE = /^frame_(\d{2})\.(png|jpg)$/;

export function rejectedFrameReason(state, perceptionReason) {
  if (ELIGIBLE_REASONS.has(perceptionReason)) return perceptionReason;
  if (typeof perceptionReason === 'string') {
    for (const candidate of ['unknown-current', 'confirm-frame']) {
      if (perceptionReason.startsWith(`${candidate}-`)) return candidate;
    }
  }
  return state !== 'MOVE' ? 'non-move' : null;
}

function ensureDirectory(path, requirePrivate = false) {
  try {
    const info = lstatSync(path);
    if (!info.isDirectory() || info.isSymbolicLink()) return false;
    if (requirePrivate && (info.mode & 0o077) !== 0) return false;
  } catch (error) {
    if (error.code !== 'ENOENT') return false;
    try { mkdirSync(path, { mode: 0o700 }); } catch (mkdirError) {
      if (mkdirError.code !== 'EEXIST') return false;
    }
    try {
      const info = lstatSync(path);
      if (!info.isDirectory() || info.isSymbolicLink()) return false;
      if (requirePrivate && (info.mode & 0o077) !== 0) return false;
    } catch { return false; }
  }
  return true;
}

function regularEntries(directory) {
  try {
    const entries = readdirSync(directory, { withFileTypes: true });
    if (entries.some(entry => entry.isSymbolicLink()
        || (!entry.isFile() && !entry.isDirectory()))) return null;
    return entries;
  } catch { return null; }
}

function safeMetadata({ game, sessionId, reason, turn, format, width, height, now, fileMtimeMs, rawConfidence }) {
  const confidence = Number.isFinite(rawConfidence)
    ? Math.max(0, Math.min(1, rawConfidence)) : null;
  return {
    schema: 1,
    game,
    turn,
    sessionId,
    reason,
    imageFormat: format,
    confidence,
    image: { width, height },
    fileMtimeMs: Number.isFinite(fileMtimeMs) ? Math.round(fileMtimeMs) : null,
    recordedAtMs: now,
  };
}

function hasFormatSignature(buffer, format) {
  if (format === 'png') return buffer.length >= 8
    && buffer.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  return format === 'jpeg' && buffer.length >= 4
    && buffer[0] === 0xff && buffer[1] === 0xd8
    && buffer[buffer.length - 2] === 0xff && buffer[buffer.length - 1] === 0xd9;
}

/**
 * Save one eligible frame from the buffer returned by the capture already used
 * for analysis. This function never captures or reads another image.
 * @returns {{saved: boolean, reason?: string}}
 */
export function saveRejectedFrame({
  enabled = false, runtimeDir = process.cwd(), game, turn, sessionId,
  reason, observation, confidence = null, now = Date.now(),
} = {}) {
  if (!enabled) return { saved: false, reason: 'disabled' };
  if (!ELIGIBLE_REASONS.has(reason)) return { saved: false, reason: 'ineligible' };
  if (!Number.isSafeInteger(game) || game <= 0 || !Number.isSafeInteger(turn) || turn < 0
      || typeof sessionId !== 'string' || !UUID_RE.test(sessionId)
      || !Number.isFinite(now) || now < 0) return { saved: false, reason: 'invalid-metadata' };
  if (!Buffer.isBuffer(observation?.buffer)
      || observation.buffer.length <= 0 || observation.buffer.length > MAX_REJECTED_FRAME_BYTES
      || !['png', 'jpeg'].includes(observation.format)
      || !hasFormatSignature(observation.buffer, observation.format)) return { saved: false, reason: 'invalid-frame' };

  const image = observation.buffer;
  const width = observation.width;
  const height = observation.height;
  if (!Number.isInteger(width) || width <= 0 || width > 16_384
      || !Number.isInteger(height) || height <= 0 || height > 16_384) {
    return { saved: false, reason: 'invalid-dimensions' };
  }

  const root = resolve(runtimeDir, 'tmp', 'rejected_frame_diagnostics');
  const runtimeTmp = resolve(runtimeDir, 'tmp');
  if (!ensureDirectory(runtimeTmp) || !ensureDirectory(root, true)) {
    return { saved: false, reason: 'unsafe-directory' };
  }
  const runName = `run_${sessionId}`;
  if (!RUN_DIR_RE.test(runName)) return { saved: false, reason: 'invalid-session' };
  const runDir = join(root, runName);
  if (!ensureDirectory(runDir, true)) return { saved: false, reason: 'unsafe-directory' };
  const entries = regularEntries(runDir);
  if (!entries) return { saved: false, reason: 'unsafe-entry' };

  const frames = entries.filter(entry => FRAME_NAME_RE.test(entry.name));
  if (frames.length >= MAX_REJECTED_FRAMES_PER_SESSION) return { saved: false, reason: 'limit' };
  const metadataEntries = entries.filter(entry => /^frame_\d{2}\.json$/.test(entry.name));
  if (entries.some(entry => !FRAME_NAME_RE.test(entry.name) && !/^frame_\d{2}\.json$/.test(entry.name))) {
    return { saved: false, reason: 'unsafe-entry' };
  }
  if (frames.length !== metadataEntries.length) return { saved: false, reason: 'incomplete-existing-evidence' };
  const indexes = frames.map(entry => Number(entry.name.match(FRAME_NAME_RE)[1])).sort((a, b) => a - b);
  const metadataIndexes = metadataEntries.map(entry => Number(entry.name.match(/^frame_(\d{2})\.json$/)[1])).sort((a, b) => a - b);
  if (indexes.some((value, index) => value !== index)
      || metadataIndexes.some((value, index) => value !== index)) {
    return { saved: false, reason: 'incomplete-existing-evidence' };
  }

  let seenReasons = new Set();
  for (const entry of metadataEntries) {
    try {
      const fd = openSync(join(runDir, entry.name), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const info = fstatSync(fd);
        if (!info.isFile() || info.size <= 0 || info.size > 16 * 1024) return { saved: false, reason: 'unsafe-entry' };
      } finally { closeSync(fd); }
      // Metadata is an internal fixed schema; unknown or malformed data closes the save path.
      const parsed = JSON.parse(readMetadata(join(runDir, entry.name)));
      if (!ELIGIBLE_REASONS.has(parsed?.reason)) return { saved: false, reason: 'unsafe-entry' };
      seenReasons.add(parsed.reason);
    } catch { return { saved: false, reason: 'unsafe-entry' }; }
  }
  if (seenReasons.has(reason)) return { saved: false, reason: 'reason-already-saved' };

  const index = frames.length;
  const extension = observation.format === 'jpeg' ? 'jpg' : 'png';
  const imageName = `frame_${String(index).padStart(2, '0')}.${extension}`;
  const metadataName = `frame_${String(index).padStart(2, '0')}.json`;
  const imagePath = join(runDir, imageName);
  const metadataPath = join(runDir, metadataName);
  const temporaryImage = join(runDir, `.frame_${String(index).padStart(2, '0')}.${sessionId}.tmp`);
  const temporaryMetadata = join(runDir, `.frame_${String(index).padStart(2, '0')}.${sessionId}.json.tmp`);
  let fd;
  try {
    fd = openSync(temporaryImage, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(fd, image);
    closeSync(fd);
    fd = undefined;
    const imageInfo = lstatSync(temporaryImage);
    if (!imageInfo.isFile() || imageInfo.isSymbolicLink() || imageInfo.size !== image.length) {
      return { saved: false, reason: 'write-validation-failed' };
    }
    const metadata = safeMetadata({
      game, sessionId, reason, turn, format: observation.format, width, height, now,
      fileMtimeMs: imageInfo.mtimeMs, rawConfidence: confidence,
    });
    fd = openSync(temporaryMetadata, constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(fd, JSON.stringify(metadata) + '\n');
    closeSync(fd);
    fd = undefined;
    // Hard-link creation is atomic and refuses to replace an existing frame.
    linkSync(temporaryImage, imagePath);
    unlinkSync(temporaryImage);
    linkSync(temporaryMetadata, metadataPath);
    unlinkSync(temporaryMetadata);
    return { saved: true, image: imageName, metadata: metadataName };
  } catch {
    return { saved: false, reason: 'write-failed' };
  } finally {
    if (fd !== undefined) closeSync(fd);
    for (const path of [temporaryImage, temporaryMetadata]) {
      try { unlinkSync(path); } catch {}
    }
  }
}

function readMetadata(path) {
  // Delayed import-free safe read: no following a replacement symlink.
  const fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
  try {
    const info = fstatSync(fd);
    if (!info.isFile() || info.size <= 0 || info.size > 16 * 1024) throw new Error('unsafe metadata');
    let position = 0;
    const buffer = Buffer.alloc(Math.min(info.size, 16 * 1024));
    while (position < buffer.length) {
      const count = readSync(fd, buffer, position, buffer.length - position, position);
      if (!count) break;
      position += count;
    }
    return buffer.subarray(0, position).toString('utf8');
  } finally { closeSync(fd); }
}

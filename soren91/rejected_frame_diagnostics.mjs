/** Optional, bounded retention of already captured frames rejected by observation. */
import {
  closeSync, constants, fstatSync, lstatSync, mkdirSync, openSync, readSync,
  readdirSync, renameSync, rmSync, writeFileSync, fsyncSync,
} from 'node:fs';
import { join, resolve } from 'node:path';

export const MAX_REJECTED_FRAMES_PER_SESSION = 3;
export const MAX_REJECTED_FRAME_BYTES = 8 * 1024 * 1024;
const ELIGIBLE_REASONS = new Set(['non-move', 'unknown-current', 'confirm-frame']);
const UUID_RE = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const RUN_DIR_RE = /^run_[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const FRAME_DIR_RE = /^frame_(\d{2})$/;
const STAGING_DIR_RE = /^\.frame_(\d{2})\.([0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12})\.tmp$/i;
const IMAGE_NAME = 'image.png';
const JPEG_IMAGE_NAME = 'image.jpg';
const PAIR_IMAGE_RE = /^image\.(png|jpg)$/;
const METADATA_NAME = 'metadata.json';
const CAPTURE_GEOMETRY_KEYS = [
  'x', 'y', 'width', 'height', 'scrollX', 'scrollY', 'dpr',
  'viewportWidth', 'viewportHeight', 'viewportScale',
];

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

function safeMetadata({ game, sessionId, reason, turn, format, width, height, now,
  fileMtimeMs, boardConfidence, currentPieceConfidence, observation }) {
  const boundedConfidence = value => Number.isFinite(value)
    ? Math.max(0, Math.min(1, value)) : null;
  const captureGeometry = {};
  for (const key of CAPTURE_GEOMETRY_KEYS) {
    const value = observation?.geometry?.[key];
    if (!Number.isFinite(value)) return null;
    captureGeometry[key] = value;
  }
  if (!Number.isFinite(observation?.capturedAt) || observation.capturedAt < 0
      || !Number.isFinite(observation?.captureMs) || observation.captureMs < 0) return null;
  return {
    schema: 1,
    game,
    turn,
    sessionId,
    reason,
    imageFormat: format,
    boardConfidence: boundedConfidence(boardConfidence),
    currentPieceConfidence: boundedConfidence(currentPieceConfidence),
    image: { width, height },
    fileMtimeMs: Number.isFinite(fileMtimeMs) ? Math.round(fileMtimeMs) : null,
    recordedAtMs: now,
    capture: {
      capturedAtMs: observation.capturedAt,
      captureMs: observation.captureMs,
      geometry: captureGeometry,
    },
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
  reason, observation, boardConfidence = null, currentPieceConfidence = null,
  now = Date.now(), stageHook = null,
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
  let entries = regularEntries(runDir);
  if (!entries) return { saved: false, reason: 'unsafe-entry' };
  let frames = entries.filter(entry => FRAME_DIR_RE.test(entry.name));
  const indexes = frames.map(entry => Number(entry.name.match(FRAME_DIR_RE)[1])).sort((a, b) => a - b);
  if (indexes.length > MAX_REJECTED_FRAMES_PER_SESSION
      || indexes.some((value, index) => value !== index)) {
    return { saved: false, reason: 'incomplete-existing-evidence' };
  }

  // A power loss can leave only a private staging directory. It is never
  // treated as evidence: validate its exact owned shape, discard it, then
  // continue with committed frame directories. Completed frames stay intact.
  const staging = entries.filter(entry => STAGING_DIR_RE.test(entry.name));
  const unknownEntries = entries.filter(entry => !FRAME_DIR_RE.test(entry.name)
    && !STAGING_DIR_RE.test(entry.name));
  if (unknownEntries.length || staging.length > 1) return { saved: false, reason: 'unsafe-entry' };
  if (staging.length) {
    const match = staging[0].name.match(STAGING_DIR_RE);
    const stageIndex = Number(match[1]);
    if (stageIndex !== indexes.length || match[2].toLowerCase() !== sessionId.toLowerCase()) {
      return { saved: false, reason: 'incomplete-existing-evidence' };
    }
    const stagePath = join(runDir, staging[0].name);
    try {
      const stageInfo = lstatSync(stagePath);
      if (!stageInfo.isDirectory() || stageInfo.isSymbolicLink() || (stageInfo.mode & 0o077) !== 0) {
        return { saved: false, reason: 'unsafe-entry' };
      }
      const stageEntries = regularEntries(stagePath);
      if (!stageEntries || stageEntries.some(entry => ![IMAGE_NAME, JPEG_IMAGE_NAME, METADATA_NAME].includes(entry.name))) {
        return { saved: false, reason: 'unsafe-entry' };
      }
      for (const entry of stageEntries) {
        const fileInfo = lstatSync(join(stagePath, entry.name));
        if (!fileInfo.isFile() || fileInfo.isSymbolicLink() || fileInfo.nlink !== 1
            || (fileInfo.mode & 0o077) !== 0) return { saved: false, reason: 'unsafe-entry' };
      }
      rmSync(stagePath, { recursive: true, force: false });
    } catch { return { saved: false, reason: 'incomplete-existing-evidence' }; }
    entries = regularEntries(runDir);
    if (!entries || entries.length !== indexes.length) return { saved: false, reason: 'unsafe-entry' };
  }
  if (indexes.length >= MAX_REJECTED_FRAMES_PER_SESSION) return { saved: false, reason: 'limit' };

  let seenReasons = new Set();
  for (const entry of frames) {
    try {
      const pairDir = join(runDir, entry.name);
      const pairInfo = lstatSync(pairDir);
      if (!pairInfo.isDirectory() || pairInfo.isSymbolicLink() || (pairInfo.mode & 0o077) !== 0) {
        return { saved: false, reason: 'unsafe-entry' };
      }
      const pairEntries = regularEntries(pairDir);
      const pairImage = pairEntries?.filter(item => PAIR_IMAGE_RE.test(item.name)) || [];
      if (!pairEntries || pairEntries.length !== 2 || pairImage.length !== 1
          || pairEntries.some(item => ![pairImage[0].name, METADATA_NAME].includes(item.name))) {
        return { saved: false, reason: 'incomplete-existing-evidence' };
      }
      const fd = openSync(join(pairDir, METADATA_NAME), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const info = fstatSync(fd);
        if (!info.isFile() || info.size <= 0 || info.size > 16 * 1024
            || info.nlink !== 1 || (info.mode & 0o077) !== 0) return { saved: false, reason: 'unsafe-entry' };
      } finally { closeSync(fd); }
      const imageFd = openSync(join(pairDir, pairImage[0].name), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK);
      try {
        const info = fstatSync(imageFd);
        if (!info.isFile() || info.size <= 0 || info.size > MAX_REJECTED_FRAME_BYTES
            || info.nlink !== 1 || (info.mode & 0o077) !== 0) return { saved: false, reason: 'unsafe-entry' };
      } finally { closeSync(imageFd); }
      const parsed = JSON.parse(readMetadata(join(pairDir, METADATA_NAME)));
      if (!ELIGIBLE_REASONS.has(parsed?.reason) || parsed.sessionId !== sessionId
          || parsed.imageFormat !== (pairImage[0].name.endsWith('.png') ? 'png' : 'jpeg')) {
        return { saved: false, reason: 'unsafe-entry' };
      }
      seenReasons.add(parsed.reason);
    } catch { return { saved: false, reason: 'unsafe-entry' }; }
  }
  if (seenReasons.has(reason)) return { saved: false, reason: 'reason-already-saved' };

  const index = indexes.length;
  const frameName = `frame_${String(index).padStart(2, '0')}`;
  const framePath = join(runDir, frameName);
  const temporaryDirectory = join(runDir, `.${frameName}.${sessionId}.tmp`);
  const imageName = observation.format === 'jpeg' ? JPEG_IMAGE_NAME : IMAGE_NAME;
  let fd;
  try {
    mkdirSync(temporaryDirectory, { mode: 0o700 });
    fd = openSync(join(temporaryDirectory, imageName), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(fd, image);
    fsyncSync(fd);
    closeSync(fd);
    fd = undefined;
    if (typeof stageHook === 'function') stageHook('image-written');
    const imageInfo = lstatSync(join(temporaryDirectory, imageName));
    if (!imageInfo.isFile() || imageInfo.isSymbolicLink() || imageInfo.size !== image.length) {
      return { saved: false, reason: 'write-validation-failed' };
    }
    const metadata = safeMetadata({
      game, sessionId, reason, turn, format: observation.format, width, height, now,
      fileMtimeMs: imageInfo.mtimeMs, boardConfidence, currentPieceConfidence, observation,
    });
    if (!metadata) return { saved: false, reason: 'invalid-capture-metadata' };
    fd = openSync(join(temporaryDirectory, METADATA_NAME), constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW, 0o600);
    writeFileSync(fd, JSON.stringify(metadata) + '\n');
    fsyncSync(fd);
    closeSync(fd);
    fd = undefined;
    if (typeof stageHook === 'function') stageHook('pair-written');
    const completedPair = readdirSync(temporaryDirectory);
    if (completedPair.length !== 2 || !completedPair.includes(imageName)
        || !completedPair.includes(METADATA_NAME)) return { saved: false, reason: 'write-validation-failed' };
    // One directory rename publishes the complete image/sidecar pair atomically.
    renameSync(temporaryDirectory, framePath);
    return { saved: true, image: `${frameName}/${imageName}`, metadata: `${frameName}/${METADATA_NAME}` };
  } catch {
    return { saved: false, reason: 'write-failed' };
  } finally {
    if (fd !== undefined) closeSync(fd);
    try { rmSync(temporaryDirectory, { recursive: true, force: true }); } catch {}
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

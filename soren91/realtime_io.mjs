/** Bounded canvas-only I/O. Never resizes/raises the browser or clicks on timeout. */
import { performance } from 'node:perf_hooks';

export function postDropProbeEnabled(env = process.env) {
  if (env.SOREN91_RANK_POSTDROP_PROBE === '0') return false;
  if (env.SOREN91_RANK_POSTDROP_PROBE === '1') return true;
  return !String(env.SOREN91_REMOTE_CDP_URL || '').trim();
}

export function captureImageFormat(env = process.env) {
  const explicit = String(env?.SOREN91_CAPTURE_FORMAT || '').trim().toLowerCase();
  if (explicit === 'jpg' || explicit === 'jpeg') return 'jpeg';
  if (explicit === 'png') return 'png';
  return String(env?.SOREN91_REMOTE_CDP_URL || '').trim() ? 'jpeg' : 'png';
}

export function captureJpegQuality(env = process.env) {
  const raw = Number(env?.SOREN91_CAPTURE_JPEG_QUALITY);
  return Number.isFinite(raw) ? Math.max(60, Math.min(95, Math.round(raw))) : 85;
}

export function boundedMs(value, fallback, min = 200, max = 5000) {
  const n = Number(value);
  return value != null && String(value).trim() !== '' && Number.isFinite(n)
    ? Math.min(max, Math.max(min, n)) : fallback;
}

// Per-capture wall-clock budget. Measured remote capture cost is ~1.9s p50 /
// 4.6s p95 per frame, so the old 3s default tripped consecutive
// capture-timeouts under load and the bot stopped for the rest of the corner
// (2026-09-29). 6s default, env up to 9s.
export function captureTimeoutMs(env = process.env, fallback = 6000) {
  return boundedMs(env?.SOREN91_CAPTURE_TIMEOUT_MS, fallback, 200, 9000);
}

// Consecutive-error policy: transient remote-capture slowness must not end the
// run. Back off exponentially (bounded) and only stop after a generous limit.
export function captureErrorBackoffMs(consecutive, env = process.env) {
  const max = boundedMs(env?.SOREN91_ERROR_BACKOFF_MAX_MS, 15000, 1000, 60000);
  const step = Math.max(1, Number(consecutive) || 1);
  return Math.min(1000 * 2 ** (step - 1), max);
}

export function captureErrorLimit(env = process.env) {
  const raw = Number(env?.SOREN91_ERROR_LIMIT);
  return Number.isFinite(raw) && raw >= 1 ? Math.min(Math.floor(raw), 200) : 30;
}

/** A duration is a wall-clock budget, not a number of slow screenshots. */
export function probeBudget(durationMs, intervalMs, now = () => performance.now()) {
  const duration = boundedMs(durationMs, 1200, 40, 5000);
  const interval = boundedMs(intervalMs, 75, 40, duration);
  const deadline = now() + duration;
  return {
    frames: Math.min(64, Math.max(1, Math.ceil(duration / interval))),
    remaining: () => Math.max(0, deadline - now()),
    sleepMs: () => Math.max(0, Math.min(interval, deadline - now())),
  };
}

// Runs in the dedicated game page; no requestAnimationFrame/font/actionability wait.
export function canvasGeometryInPage() {
  const canvas = document.querySelector('canvas');
  if (!canvas) return null;
  const style = getComputedStyle(canvas);
  if (style.display === 'none' || style.visibility !== 'visible' || Number(style.opacity) === 0) return null;
  // A rotated/skewed canvas cannot be mapped by an axis-aligned screenshot.
  for (let element = canvas; element; element = element.parentElement) {
    const transform = getComputedStyle(element).transform;
    if (transform && transform !== 'none') {
      const m = new DOMMatrixReadOnly(transform);
      if (!m.is2D || m.b !== 0 || m.c !== 0 || m.a <= 0 || m.d <= 0) return null;
    }
  }
  const box = canvas.getBoundingClientRect();
  if (!globalThis.__soren91CaptureIds) globalThis.__soren91CaptureIds = { ids: new WeakMap(), next: 1 };
  const ids = globalThis.__soren91CaptureIds;
  if (!ids.ids.has(canvas)) ids.ids.set(canvas, ids.next++);
  return {
    canvasId: ids.ids.get(canvas), documentId: performance.timeOrigin,
    x: box.x, y: box.y, width: box.width, height: box.height,
    scrollX, scrollY, dpr: devicePixelRatio,
    viewportWidth: innerWidth, viewportHeight: innerHeight,
    viewportScale: visualViewport?.scale ?? 1,
  };
}

const GEOMETRY_KEYS = ['canvasId', 'documentId', 'x', 'y', 'width', 'height', 'scrollX', 'scrollY',
  'dpr', 'viewportWidth', 'viewportHeight', 'viewportScale'];

export function validGeometry(g) {
  return !!g && GEOMETRY_KEYS.every(k => Number.isFinite(g[k]))
    && g.canvasId > 0 && g.dpr > 0 && g.dpr <= 4 && g.viewportScale === 1
    && g.width >= 20 && g.height >= 20 && g.width <= 4096 && g.height <= 4096
    && g.x >= 0 && g.y >= 0 && g.scrollX >= 0 && g.scrollY >= 0
    && g.x + g.width <= g.viewportWidth + 0.01 && g.y + g.height <= g.viewportHeight + 0.01;
}

export function sameGeometry(a, b) {
  return validGeometry(a) && validGeometry(b) && GEOMETRY_KEYS.every(k => a[k] === b[k]);
}

function pngSize(buffer) {
  if (buffer.length < 24 || buffer.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a'
      || buffer.toString('ascii', 12, 16) !== 'IHDR') throw new Error('capture-invalid-png');
  return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
}

function jpegSize(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 4 || buffer[0] !== 0xff || buffer[1] !== 0xd8) {
    throw new Error('capture-invalid-jpeg');
  }
  let offset = 2;
  while (offset + 4 <= buffer.length) {
    if (buffer[offset] !== 0xff) throw new Error('capture-invalid-jpeg');
    const marker = buffer[offset + 1];
    if (marker === 0xff) { offset += 1; continue; }
    if (marker === 0xd8 || marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) {
      offset += 2;
      continue;
    }
    const length = buffer.readUInt16BE(offset + 2);
    const isSof = marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker);
    if (isSof) {
      if (offset + 9 > buffer.length) throw new Error('capture-invalid-jpeg');
      return { height: buffer.readUInt16BE(offset + 5), width: buffer.readUInt16BE(offset + 7) };
    }
    if (length < 2 || offset + 2 + length > buffer.length) throw new Error('capture-invalid-jpeg');
    offset += 2 + length;
  }
  throw new Error('capture-invalid-jpeg');
}

function imageSize(buffer, type) {
  return type === 'jpeg' ? jpegSize(buffer) : pngSize(buffer);
}

/** One CDP session and at most one operation per game page. Timed-out sessions retire. */
export function createCanvasIO({ now = () => performance.now() } = {}) {
  const sessions = new WeakMap();
  const expression = `(${canvasGeometryInPage.toString()})()`;

  function retire(page, entry) {
    entry.retiring = true;
    // Do not start another attach if this one never completes. No orphan storm.
    void entry.pending.then(async session => {
      try {
        await session.detach();
      } finally {
        // A disconnected session may reject detach. Still retire it, but only
        // after its in-flight operation settles so retries cannot overlap it.
        await entry.work?.catch(() => {});
        if (sessions.get(page) === entry) sessions.delete(page);
      }
    }).catch(() => {});
  }

  async function run(page, timeoutMs, operation) {
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new Error('capture-budget-exhausted');
    let entry = sessions.get(page);
    if (!entry) {
      entry = { busy: false, retiring: false };
      entry.pending = Promise.resolve().then(() => page.context().newCDPSession(page));
      sessions.set(page, entry);
      entry.pending.catch(() => { if (sessions.get(page) === entry) sessions.delete(page); });
    }
    if (entry.busy || entry.retiring) throw new Error('capture-session-busy');
    entry.busy = true;
    let expired = false;
    let timer;
    const deadline = now() + timeoutMs;
    const check = () => {
      if (expired || now() >= deadline) throw new Error('capture-timeout');
    };
    const work = entry.pending.then(async session => {
      check();
      const result = await operation(session, check, () => Math.max(1, deadline - now()));
      check();
      return result;
    });
    entry.work = work;
    try {
      return await Promise.race([work, new Promise((_, reject) => {
        timer = setTimeout(() => {
          expired = true;
          reject(new Error('capture-timeout'));
        }, timeoutMs);
      })]);
    } catch (error) {
      if (expired || error.message === 'capture-timeout') retire(page, entry);
      throw error;
    } finally {
      clearTimeout(timer);
      entry.busy = false;
    }
  }

  async function geometry(session, check) {
    check();
    const result = await session.send('Runtime.evaluate', { expression, returnByValue: true });
    check();
    const g = result.result?.value;
    if (result.exceptionDetails || !validGeometry(g)) throw new Error('capture-invalid-geometry');
    return g;
  }

  return {
    async capture(page, { timeoutMs = 3000, type = 'png', quality = 85 } = {}) {
      if (!['png', 'jpeg'].includes(type)) throw new Error('capture-invalid-image');
      return run(page, timeoutMs, async (session, check, remaining) => {
        const geometryBeforeStartedAt = now();
        const before = await geometry(session, check);
        const geometryBeforeEndedAt = now();
        const capturedAt = geometryBeforeEndedAt; // Preserve existing freshness semantics.
        // Page-level clipping skips locator actionability/scroll/stability waits.
        // Use Playwright's own screenshot session: raw capture on a NEW CDP
        // session can reset DPR emulation belonging to the host's session.
        const screenshotStartedAt = capturedAt;
        const screenshotOptions = {
          type, scale: 'css', timeout: remaining(),
          clip: { x: before.x, y: before.y, width: before.width, height: before.height },
        };
        if (type === 'jpeg') screenshotOptions.quality = Math.max(60, Math.min(95, Math.round(quality)));
        const buffer = await page.screenshot(screenshotOptions);
        check();
        const screenshotEndedAt = now();
        const imageValidateStartedAt = screenshotEndedAt;
        if (!Buffer.isBuffer(buffer) || buffer.length > 24 * 1024 * 1024) throw new Error('capture-invalid-image');
        const size = imageSize(buffer, type);
        // Attached browsers may not expose their native DPR in context options.
        // CSS-pixel and native-DPR PNGs are both valid;
        // input maps from actual PNG dimensions instead of guessing from DPR.
        const matchesScale = scale => Math.abs(size.width - before.width * scale) <= 1
          && Math.abs(size.height - before.height * scale) <= 1;
        if ((!matchesScale(1) && !matchesScale(before.dpr)) || size.width * size.height > 16_777_216) {
          throw new Error('capture-pixel-scale-mismatch');
        }
        const imageValidateEndedAt = now();
        const geometryAfterStartedAt = imageValidateEndedAt;
        const after = await geometry(session, check);
        const geometryAfterEndedAt = now();
        if (!sameGeometry(before, after)) throw new Error('capture-geometry-changed');
        return {
          buffer, format: type, geometry: after, capturedAt, captureMs: now() - capturedAt, ...size,
          captureStageMs: {
            geometryBefore: Math.max(0, geometryBeforeEndedAt - geometryBeforeStartedAt),
            screenshot: Math.max(0, screenshotEndedAt - screenshotStartedAt),
            imageValidate: Math.max(0, imageValidateEndedAt - imageValidateStartedAt),
            geometryAfter: Math.max(0, geometryAfterEndedAt - geometryAfterStartedAt),
          },
        };
      });
    },
    frameBox(frame, calibration) {
      if (!frame || !validGeometry(frame.geometry)) throw new Error('input-geometry-changed');
      if (calibration?.screen?.width !== frame.width || calibration?.screen?.height !== frame.height) {
        throw new Error('input-calibration-mismatch');
      }
      return frame.geometry;
    },
    async validateInput(page, frame, calibration, { timeoutMs = 1500, maxAgeMs = null } = {}) {
      // A slow host must not have every drop fail-closed: the budget grows with
      // THIS frame's own capture cost. Explicit configuration still wins.
      const budgetMs = maxAgeMs ?? Math.max(2500, Math.round((frame?.captureMs || 0) * 3 + 500));
      const fresh = () => {
        if (!frame || !Number.isFinite(frame.capturedAt) || now() < frame.capturedAt
            || now() - frame.capturedAt > budgetMs) throw new Error('input-stale-observation');
        if (calibration?.screen?.width !== frame.width || calibration?.screen?.height !== frame.height) {
          throw new Error('input-calibration-mismatch');
        }
      };
      fresh();
      return run(page, timeoutMs, async (session, check) => {
        const current = await geometry(session, check);
        fresh();
        if (!sameGeometry(frame.geometry, current)) throw new Error('input-geometry-changed');
        return current;
      });
    },
    close(page) {
      const entry = sessions.get(page);
      if (entry && !entry.retiring) retire(page, entry);
    },
  };
}

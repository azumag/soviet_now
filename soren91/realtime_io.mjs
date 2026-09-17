/** Bounded canvas-only I/O. Never resizes/raises the browser or clicks on timeout. */
import { performance } from 'node:perf_hooks';

export function postDropProbeEnabled(env = process.env) {
  if (env.SOREN91_RANK_POSTDROP_PROBE === '0') return false;
  if (env.SOREN91_RANK_POSTDROP_PROBE === '1') return true;
  return !String(env.SOREN91_REMOTE_CDP_URL || '').trim();
}

export function boundedMs(value, fallback, min = 200, max = 5000) {
  const n = Number(value);
  return value != null && String(value).trim() !== '' && Number.isFinite(n)
    ? Math.min(max, Math.max(min, n)) : fallback;
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

/** One CDP session and at most one operation per game page. Timed-out sessions retire. */
export function createCanvasIO({ now = () => performance.now() } = {}) {
  const sessions = new WeakMap();
  const expression = `(${canvasGeometryInPage.toString()})()`;

  function retire(page, entry) {
    entry.retiring = true;
    // Do not start another attach if this one never completes. No orphan storm.
    void entry.pending.then(async session => {
      await session.detach();
      await entry.work?.catch(() => {});
      if (sessions.get(page) === entry) sessions.delete(page);
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
    async capture(page, { timeoutMs = 3000 } = {}) {
      return run(page, timeoutMs, async (session, check, remaining) => {
        const before = await geometry(session, check);
        const capturedAt = now(); // Conservative age: request start, NOT transfer completion.
        // Page-level clipping skips locator actionability/scroll/stability waits.
        // Use Playwright's own screenshot session: raw capture on a NEW CDP
        // session can reset DPR emulation belonging to the host's session.
        const buffer = await page.screenshot({
          type: 'png', scale: 'css', timeout: remaining(),
          clip: { x: before.x, y: before.y, width: before.width, height: before.height },
        });
        check();
        if (!Buffer.isBuffer(buffer) || buffer.length > 24 * 1024 * 1024) throw new Error('capture-invalid-image');
        const size = pngSize(buffer);
        // Attached browsers may not expose their native DPR in context options.
        // CSS-pixel and native-DPR PNGs are both valid;
        // input maps from actual PNG dimensions instead of guessing from DPR.
        const matchesScale = scale => Math.abs(size.width - before.width * scale) <= 1
          && Math.abs(size.height - before.height * scale) <= 1;
        if ((!matchesScale(1) && !matchesScale(before.dpr)) || size.width * size.height > 16_777_216) {
          throw new Error('capture-pixel-scale-mismatch');
        }
        const after = await geometry(session, check);
        if (!sameGeometry(before, after)) throw new Error('capture-geometry-changed');
        return { buffer, geometry: after, capturedAt, captureMs: now() - capturedAt, ...size };
      });
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

// This function is serialized into the page by Playwright. Keep it completely
// self-contained: module-scope values are not available inside the browser.
export function installAnimationFrameLimit(cfg) {
  if (!cfg || cfg.renderFps <= 0 || window.__sorenRenderLimiterInstalled) return;
  const nativeRequestAnimationFrame = window.requestAnimationFrame.bind(window);
  const nativeCancelAnimationFrame = window.cancelAnimationFrame.bind(window);
  const callbacks = new Map();
  const intervalMs = 1000 / cfg.renderFps;
  let nextHandle = 1;
  let nativeHandle = 0;
  let lastRenderAt = Number.NEGATIVE_INFINITY;
  let statsStartedAt = 0;
  let statsFrames = 0;
  window.__sorenRenderStats = {
    limitedToFps: cfg.renderFps,
    measuredFps: 0,
    lastFrameAt: 0,
  };
  const pump = (timestamp) => {
    nativeHandle = 0;
    if (timestamp - lastRenderAt >= intervalMs - 0.5) {
      lastRenderAt = timestamp;
      if (!statsStartedAt) statsStartedAt = timestamp;
      statsFrames += 1;
      const statsElapsed = timestamp - statsStartedAt;
      if (statsElapsed >= 1000) {
        window.__sorenRenderStats.measuredFps = Number((statsFrames * 1000 / statsElapsed).toFixed(1));
        statsStartedAt = timestamp;
        statsFrames = 0;
      }
      window.__sorenRenderStats.lastFrameAt = Date.now();
      const ready = [...callbacks.entries()];
      callbacks.clear();
      for (const [, callback] of ready) {
        try { callback(timestamp); } catch (error) { setTimeout(() => { throw error; }, 0); }
      }
    }
    if (callbacks.size > 0 && !nativeHandle) {
      nativeHandle = nativeRequestAnimationFrame(pump);
    }
  };
  window.requestAnimationFrame = (callback) => {
    const handle = nextHandle++;
    callbacks.set(handle, callback);
    if (!nativeHandle) nativeHandle = nativeRequestAnimationFrame(pump);
    return handle;
  };
  window.cancelAnimationFrame = (handle) => {
    callbacks.delete(handle);
    if (callbacks.size === 0 && nativeHandle) {
      nativeCancelAnimationFrame(nativeHandle);
      nativeHandle = 0;
    }
  };
  window.__sorenRenderLimiterInstalled = true;
}

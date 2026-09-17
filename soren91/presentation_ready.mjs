/** Wait for each inline rail to render state, not merely for its iframe to exist. */
export async function waitForInlineRails(page, config) {
  const surfaces = (config?.surfaces || []).filter(item => item.region && item.htmlFile);
  if (!config?.enabled || !config?.broadcast || surfaces.length === 0) return;
  await page.waitForFunction((items) => items.every(({ elementId, region }) => {
    const frame = document.getElementById(elementId);
    try {
      const health = frame?.contentWindow?.__sorenBroadcastOverlayHealth;
      return frame?.contentDocument?.readyState === 'complete'
        && health?.merged === true && !health.error && health.region === region
        && health.updatedAt === window.__sorenBroadcastState?.updatedAt;
    } catch { return false; }
  }), surfaces.map(({ elementId, region }) => ({ elementId, region })), {
    timeout: 10000, polling: 100,
  });
}

// Opt-in, isolated headless browser; no live game/CDP/stream or external network.
import test from 'node:test';
import assert from 'node:assert/strict';
import { loadDirectOverlayConfig, installDirectGameStage, installInlineDirectBroadcastOverlay, revealStagePanels } from '../lib/direct_overlay.mjs';
import { waitForInlineRails } from '../soren91/presentation_ready.mjs';

test('actual inline rail HTML renders seeded state before presentation readiness', {
  skip: !process.env.SOREN91_TEST_BROWSER,
}, async () => {
  const { chromium } = await import('playwright');
  const browser = await chromium.launch({ executablePath: process.env.SOREN91_TEST_BROWSER, headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
    const requests = [];
    await page.route('**/*', route => { requests.push(route.request().url()); return route.abort(); });
    await page.setContent('<div id="unity-container"><canvas id="unity-canvas" width="480" height="270"></canvas></div>');
    const fullscreenConfig = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'ffmpeg', SOREN_DIRECT_STAGE_LAYOUT: 'fullscreen' }, 'linux');
    const originalHtml = await page.content();
    assert.deepEqual(await installDirectGameStage(page, fullscreenConfig), { stageMode: 'fullscreen' });
    await revealStagePanels(page, fullscreenConfig);
    assert.equal(await page.content(), originalHtml, 'reveal without stage panels is a no-op');

    const config = loadDirectOverlayConfig({ SOREN_STREAM_BACKEND: 'ffmpeg', SOREN_DIRECT_STAGE_LAYOUT: 'dashboard' }, 'linux');
    const panelStyles = () => page.evaluate(() => [...document.querySelectorAll('.soren-stage-placeholder')].map(panel => {
      const style = getComputedStyle(panel);
      return {
        opacity: style.opacity, background: style.backgroundImage,
        left: style.left, top: style.top, width: style.width, height: style.height,
        borderLeft: style.borderLeft, borderTop: style.borderTop, borderBottom: style.borderBottom,
      };
    }));
    await installDirectGameStage(page, config, { drawBufferWidth: 480, drawBufferHeight: 270 });
    const hiddenPanels = await panelStyles();
    assert.equal(hiddenPanels.length, 3);
    for (const panel of hiddenPanels) assert.equal(panel.opacity, '0');
    await page.evaluate(() => {
      window.__sorenBroadcastState = { updatedAt: 123, feeds: {}, notifications: {} };
    });
    await installInlineDirectBroadcastOverlay(page, config);
    await waitForInlineRails(page, config);
    assert.deepEqual(await panelStyles(), hiddenPanels, 'panels stay hidden until explicitly revealed');
    await revealStagePanels(page, config);
    const visiblePanels = await panelStyles();
    assert.deepEqual(visiblePanels, hiddenPanels.map(panel => ({ ...panel, opacity: '1' })),
      'reveal changes only opacity, preserving geometry, gradients, and borders');
    assert.deepEqual(visiblePanels.map(panel => panel.background), [
      'linear-gradient(rgb(7, 17, 31) 0%, rgb(3, 9, 20) 100%)',
      'linear-gradient(rgb(7, 17, 31) 0%, rgb(5, 11, 21) 100%)',
      'linear-gradient(rgb(5, 11, 21) 0%, rgb(7, 17, 31) 100%)',
    ]);
    const rails = await page.evaluate(() => [...document.querySelectorAll('iframe[data-soren-overlay-region]')].map(frame => ({
      region: frame.dataset.sorenOverlayRegion,
      health: frame.contentWindow.__sorenBroadcastOverlayHealth,
      neutral: frame.contentDocument.documentElement.dataset.sorenNeutral,
      background: frame.contentWindow.getComputedStyle(frame.contentDocument.querySelector(
        frame.dataset.sorenOverlayRegion === 'sidebar' ? '#broadcast-sidebar' : `#${frame.dataset.sorenOverlayRegion}-rail`
      )).backgroundColor,
    })));
    assert.equal(rails.length, 3);
    for (const rail of rails) {
      assert.equal(rail.health.updatedAt, 123);
      assert.equal(rail.health.error, '');
      assert.equal(rail.health.region, rail.region);
      assert.equal(rail.neutral, '1');
      assert.equal(rail.background, 'rgb(5, 5, 5)');
    }
    assert.deepEqual(requests, [], 'seeded iframe must not fetch state from the game origin');
  } finally {
    await browser.close();
  }
});

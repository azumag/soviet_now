import test from 'node:test';
import assert from 'node:assert/strict';
import { createCanvasIO } from '../soren91/realtime_io.mjs';

// Opt-in: never downloads a browser or touches the operator's running Chrome.
const executable = process.env.SOREN91_TEST_BROWSER;
const opts = { skip: !executable && 'Set SOREN91_TEST_BROWSER to a disposable Chromium executable' };
async function fixture(dpr, run, scroll = false) {
  const { chromium } = await import(process.env.SOREN91_TEST_PLAYWRIGHT || 'playwright');
  const browser = await chromium.launch({ executablePath: executable, headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: dpr });
    const page = await context.newPage();
    await page.setContent(`<style>body{margin:0;background:blue;height:2000px}</style>
      <canvas width="800" height="450" style="position:absolute;left:15px;top:${scroll ? 325 : 25}px;width:800px;height:450px"></canvas>`);
    await page.evaluate(scroll => {
      const c = document.querySelector('canvas'); const ctx = c.getContext('2d');
      ctx.fillStyle = '#e12010'; ctx.fillRect(0, 0, 800, 450);
      ctx.fillStyle = '#00ff00'; ctx.fillRect(0, 0, 20, 20);
      if (scroll) window.scrollTo(0, 300);
    }, scroll);
    await run(page, createCanvasIO());
  } finally { await browser.close(); }
}
for (const dpr of [1, 2]) {
  for (const scroll of [false, true]) {
    test(`real Chromium: canvas-only crop, unchanged host geometry, DPR=${dpr}, scroll=${scroll}`, opts, async () => {
      await fixture(dpr, async (page, io) => {
        const before = await page.evaluate(() => ({ dpr: devicePixelRatio, w: innerWidth, h: innerHeight, scrollY }));
        const expected = await page.locator('canvas').screenshot({ scale: 'css' });
        const frame = await io.capture(page);
        assert.equal(frame.width, 800); assert.equal(frame.height, 450);
        assert.ok(frame.buffer.equals(expected), 'only the canvas pixels, not page background, must be captured');
        const cal = { screen: { width: frame.width, height: frame.height } };
        await io.validateInput(page, frame, cal);
        const after = await page.evaluate(() => ({ dpr: devicePixelRatio, w: innerWidth, h: innerHeight, scrollY }));
        assert.deepEqual(after, before);
        io.close(page);
      }, scroll);
    });
  }
}
for (const dpr of [1]) {
  test(`real Chromium: cdp backend canvas crop is byte-identical to the locator screenshot (DPR=${dpr})`, opts, async () => {
    await fixture(dpr, async (page, io) => {
      const expectedPng = await page.locator('canvas').screenshot({ scale: 'css', type: 'png' });
      const expectedJpeg = await page.locator('canvas').screenshot({ scale: 'css', type: 'jpeg', quality: 85 });
      const pngFrame = await io.capture(page, { backend: 'cdp', type: 'png' });
      const jpegFrame = await io.capture(page, { backend: 'cdp', type: 'jpeg', quality: 85 });
      assert.equal(pngFrame.width, 800); assert.equal(pngFrame.height, 450);
      assert.ok(pngFrame.buffer.equals(expectedPng), 'cdp png must be the canvas pixels only');
      assert.ok(jpegFrame.buffer.equals(expectedJpeg), 'cdp jpeg must be the canvas pixels only');
      io.close(page);
    });
  });
}
test('real Chromium: cdp backend never leaves an emulated device scale disturbed', opts, async () => {
  await fixture(2, async (page, io) => {
    const before = await page.evaluate(() => ({ dpr: devicePixelRatio, w: innerWidth, h: innerHeight }));
    assert.equal(before.dpr, 2);
    // A foreign raw capture resets the emulated metrics; the capture must refuse
    // the disturbed frame, put the metrics back and stop using the raw backend.
    await assert.rejects(io.capture(page, { backend: 'cdp' }), /geometry-changed/);
    const after = await page.evaluate(() => ({ dpr: devicePixelRatio, w: innerWidth, h: innerHeight }));
    assert.deepEqual(after, before);
    const expected = await page.locator('canvas').screenshot({ scale: 'css' });
    const frame = await io.capture(page, { backend: 'cdp' });
    assert.equal(frame.width, 800);
    assert.ok(frame.buffer.equals(expected), 'the downgraded capture is the plain canvas screenshot');
    io.close(page);
  });
});
test('real Chromium: same-size canvas replacement invalidates the old frame before input', opts, async () => {
  await fixture(2, async (page, io) => {
    const frame = await io.capture(page);
    await page.evaluate(() => { const old = document.querySelector('canvas'); old.replaceWith(old.cloneNode()); });
    await assert.rejects(io.validateInput(page, frame, { screen: { width: 800, height: 450 } }), /geometry-changed/);
    io.close(page);
  });
});
test('real Chromium: viewport resize invalidates the old frame before input', opts, async () => {
  await fixture(1, async (page, io) => {
    const frame = await io.capture(page);
    await page.setViewportSize({ width: 1200, height: 700 });
    await assert.rejects(io.validateInput(page, frame, { screen: { width: 800, height: 450 } }), /geometry-changed/);
    io.close(page);
  });
});
test('real Chromium: skewed and invisible canvases fail closed', opts, async () => {
  await fixture(1, async (page, io) => {
    await page.evaluate(() => { document.querySelector('canvas').style.transform = 'skewX(10deg)'; });
    await assert.rejects(io.capture(page), /invalid-geometry/);
    await page.evaluate(() => { document.querySelector('canvas').style.display = 'none'; });
    await assert.rejects(io.capture(page), /invalid-geometry/);
    io.close(page);
  });
});

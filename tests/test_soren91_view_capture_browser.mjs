import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { createCanvasIO } from '../soren91/realtime_io.mjs';
const require = createRequire(import.meta.url);
const executablePath = process.env.SOREN91_TEST_BROWSER;
const canvasHtml = `<!doctype html><style>
html,body{margin:0;overflow:hidden}canvas{display:block;width:100vw;height:100vh}
</style><canvas width="1280" height="720"></canvas><script>
const canvas = document.querySelector('canvas');
const ctx = canvas.getContext('2d');
window.paint = (first = '#ff0000') => {
  for (const [x,y,c] of [[0,0,first],[640,0,'#00ff00'],[0,360,'#0000ff'],[640,360,'#ffff00']]) {
    ctx.fillStyle = c; ctx.fillRect(x,y,640,360);
  }
}; paint();
</script>`;
const metrics = page => page.evaluate(() => ({
  dpr: devicePixelRatio, width: innerWidth, height: innerHeight,
  screenWidth: screen.width, screenHeight: screen.height,
  scrollX, scrollY, visible: document.visibilityState,
}));

const webglHtml = `<!doctype html><style>
html,body{margin:0;overflow:hidden}canvas{display:block;width:100vw;height:100vh}
</style><canvas width="1280" height="720"></canvas><script>
const canvas = document.querySelector('canvas');
const gl = canvas.getContext('webgl');
if (!gl) throw new Error('WebGL fixture is unavailable');
let firstColor = [1,0,0,1];
window.paint = () => { firstColor = [1,1,1,1]; };
function draw() {
  gl.enable(gl.SCISSOR_TEST);
  for (const [x,y,c] of [[0,360,firstColor],[640,360,[0,1,0,1]],
      [0,0,[0,0,1,1]],[640,0,[1,1,0,1]]]) {
    gl.scissor(x,y,640,360); gl.clearColor(...c); gl.clear(gl.COLOR_BUFFER_BIT);
  }
  requestAnimationFrame(draw);
}
draw();
</script>`;

for (const [renderer, html] of [['2d', canvasHtml], ['webgl', webglHtml]])
for (const dpr of [1, 2]) for (const type of ['png', 'jpeg']) {
  test(`real Chromium view preserves ${renderer} owner metrics and pixels: DPR=${dpr} ${type}`, {
    skip: !executablePath, timeout: 30000,
  }, async t => {
    const { chromium } = await import('playwright');
    const sharp = require('sharp');
    const browser = await chromium.launch({ executablePath,
      headless: process.env.SOREN91_TEST_HEADED !== '1', args: ['--no-sandbox', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'] });
    try {
      const context = await browser.newContext({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: dpr });
      const page = await context.newPage(); await page.setContent(html);
      if (renderer === 'webgl' && process.env.SOREN91_TEST_HEADED !== '1'
          && !(await page.evaluate(() => typeof window.paint === 'function'))) {
        t.skip('This headless Chromium cannot initialize the WebGL fixture; run headed/Xvfb.');
        return;
      }
      await page.evaluate(async () => {
        if (typeof window.paint !== 'function') throw new Error('canvas fixture did not initialize');
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      const before = await metrics(page);
      const io = createCanvasIO();
      const legacy = await io.capture(page, { view: false, type, timeoutMs: 9000 });
      const candidate = await io.capture(page, { view: true, type, timeoutMs: 9000 });
      assert.equal(candidate.captureBackend, 'view');
      assert.deepEqual(await metrics(page), before);
      assert.equal(candidate.width, legacy.width); assert.equal(candidate.height, legacy.height);
      const pixels = buffer => sharp(buffer).removeAlpha().raw().toBuffer();
      assert.deepEqual(await pixels(candidate.buffer), await pixels(legacy.buffer));
      await io.validateInput(page, candidate, { screen: { width: candidate.width, height: candidate.height } });
      await page.evaluate(async () => {
        paint('#ffffff');
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      });
      const changed = await io.capture(page, { view: true, type, timeoutMs: 9000 });
      assert.equal(changed.captureBackend, 'view');
      assert.notDeepEqual(await pixels(changed.buffer), await pixels(candidate.buffer),
        'must capture new pixels, never return a cached frame');
      assert.deepEqual(await metrics(page), before);
      // A canvas that no longer fills the viewport must use the established clip.
      await page.evaluate(() => { document.querySelector('canvas').style.width = '800px'; });
      const clipped = await io.capture(page, { view: true, type, timeoutMs: 9000 });
      assert.equal(clipped.captureBackend, 'playwright'); assert.equal(clipped.width, 800);
      io.close(page);
    } finally { await browser.close(); }
  });
}

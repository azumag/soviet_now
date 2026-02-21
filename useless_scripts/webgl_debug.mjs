import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function run() {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });

  // 同じフックをsoviet_game.mjsから使用
  await context.addInitScript(() => {
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
      if (type === 'webgl' || type === 'webgl2') {
        attrs = attrs || {};
        attrs.preserveDrawingBuffer = true;
      }
      return origGetContext.call(this, type, attrs);
    };

    window.__wglCapturing = false;
    window.__wglGameObjects = [];

    function hookPrototype(proto) {
      if (!proto) return;
      let currentStride = 0;
      let currentMat = null;

      const origVAP = proto.vertexAttribPointer;
      if (origVAP) {
        proto.vertexAttribPointer = function(index, size, type, normalized, stride, offset) {
          if (window.__wglCapturing && index === 0) {
            currentStride = stride;
          }
          return origVAP.apply(this, arguments);
        };
      }

      const origU4fv = proto.uniform4fv;
      if (origU4fv) {
        proto.uniform4fv = function(loc, data, srcOffset, srcLength) {
          if (window.__wglCapturing && data) {
            const offset = srcOffset || 0;
            const length = srcLength || (data.length - offset);
            if (length === 16) {
              const o = offset;
              currentMat = {
                tx: data[o + 12], ty: data[o + 13], tz: data[o + 14],
                sx: Math.sqrt(data[o]**2 + data[o+1]**2 + data[o+2]**2),
                sy: Math.sqrt(data[o+4]**2 + data[o+5]**2 + data[o+6]**2),
                rot: Math.atan2(data[o+1], data[o]) * 180 / Math.PI,
              };
            }
          }
          return origU4fv.apply(this, arguments);
        };
      }

      const origDE = proto.drawElements;
      if (origDE) {
        proto.drawElements = function(mode, count, type, offset) {
          if (window.__wglCapturing && currentStride === 24 && currentMat) {
            window.__wglGameObjects.push({
              tx: Math.round(currentMat.tx * 10000) / 10000,
              ty: Math.round(currentMat.ty * 10000) / 10000,
              tz: Math.round(currentMat.tz * 10000) / 10000,
              sx: Math.round(currentMat.sx * 10000) / 10000,
              sy: Math.round(currentMat.sy * 10000) / 10000,
              rot: Math.round(currentMat.rot * 100) / 100,
              v: count,
            });
            currentMat = null;
          }
          return origDE.apply(this, arguments);
        };
      }

      const origUP = proto.useProgram;
      if (origUP) {
        proto.useProgram = function(prog) {
          if (window.__wglCapturing) {
            currentMat = null;
            currentStride = 0;
          }
          return origUP.call(this, prog);
        };
      }
    }

    hookPrototype(WebGLRenderingContext.prototype);
    if (typeof WebGL2RenderingContext !== 'undefined') {
      hookPrototype(WebGL2RenderingContext.prototype);
    }
  });

  const page = await context.newPage();

  console.log('Loading main page...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(10000);

  const gameFrame = page.frames().find(f => f.url().includes('no_bg=true'));
  const gameUrl = gameFrame ? gameFrame.url() : null;
  if (gameUrl) {
    console.log('Navigating to game URL directly...');
    await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.waitForTimeout(10000);
  }

  // ゲーム開始
  await page.mouse.click(640, 216);
  await page.waitForTimeout(3000);

  // rAFベースのキャプチャ（soviet_game.mjsと同じ方式）
  console.log('\n=== rAF capture (same as soviet_game.mjs) ===');
  const rafData = await page.evaluate(() => {
    window.__wglGameObjects = [];
    window.__wglCapturing = true;
    return new Promise(resolve => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          window.__wglCapturing = false;
          resolve({
            count: window.__wglGameObjects.length,
            objects: window.__wglGameObjects,
          });
        });
      });
    });
  });

  console.log(`rAF captured: ${rafData.count} objects`);
  for (const o of rafData.objects) {
    console.log(`  pos=(${o.tx}, ${o.ty}, ${o.tz}) scale=(${o.sx}, ${o.sy}) rot=${o.rot}° v=${o.v}`);
  }

  // setTimeoutベースのキャプチャ（比較用）
  console.log('\n=== setTimeout capture (500ms) ===');
  const timerData = await page.evaluate(() => {
    window.__wglGameObjects = [];
    window.__wglCapturing = true;
    return new Promise(resolve => {
      setTimeout(() => {
        window.__wglCapturing = false;
        resolve({
          count: window.__wglGameObjects.length,
          objects: window.__wglGameObjects.slice(0, 50),
        });
      }, 500);
    });
  });

  console.log(`setTimeout captured: ${timerData.count} objects`);
  // 重複排除して表示
  const unique = [];
  for (const o of timerData.objects) {
    let isDup = false;
    for (const u of unique) {
      if (Math.abs(u.tx - o.tx) < 0.01 && Math.abs(u.ty - o.ty) < 0.01 && Math.abs(u.sx - o.sx) < 0.01) {
        isDup = true;
        break;
      }
    }
    if (!isDup) unique.push(o);
  }
  console.log(`Unique: ${unique.length}`);
  for (const o of unique) {
    console.log(`  pos=(${o.tx}, ${o.ty}, ${o.tz}) scale=(${o.sx}, ${o.sy}) rot=${o.rot}° v=${o.v}`);
  }

  // ピース落下後
  console.log('\n=== After dropping piece ===');
  await page.mouse.click(500, 200);
  await page.waitForTimeout(2500);

  const afterDrop = await page.evaluate(() => {
    window.__wglGameObjects = [];
    window.__wglCapturing = true;
    return new Promise(resolve => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          window.__wglCapturing = false;
          resolve(window.__wglGameObjects);
        });
      });
    });
  });

  console.log(`After drop rAF: ${afterDrop.length} objects`);
  const uniqueAfter = [];
  for (const o of afterDrop) {
    let isDup = false;
    for (const u of uniqueAfter) {
      if (Math.abs(u.tx - o.tx) < 0.01 && Math.abs(u.ty - o.ty) < 0.01 && Math.abs(u.sx - o.sx) < 0.01) {
        isDup = true;
        break;
      }
    }
    if (!isDup) uniqueAfter.push(o);
  }
  console.log(`Unique after: ${uniqueAfter.length}`);
  for (const o of uniqueAfter) {
    console.log(`  pos=(${o.tx}, ${o.ty}, ${o.tz}) scale=(${o.sx}, ${o.sy}) rot=${o.rot}° v=${o.v}`);
  }

  await page.waitForTimeout(3000);
  await browser.close();
}

run().catch(console.error);

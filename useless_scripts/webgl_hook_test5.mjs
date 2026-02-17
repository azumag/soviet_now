import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function run() {
  const browser = await chromium.launch({ headless: false });
  const context = await browser.newContext({ viewport: { width: 1280, height: 720 } });

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
    window.__wglGameObjects = []; // stride=24のdraw calls（ゲームオブジェクト）

    function hookPrototype(proto) {
      if (!proto) return;

      let currentStride = 0;
      let currentMat = null;
      let currentVertexSize = null; // quad sizeを取得

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
              // 4x4行列 - translation & scale抽出
              currentMat = {
                tx: Math.round(data[offset + 12] * 10000) / 10000,
                ty: Math.round(data[offset + 13] * 10000) / 10000,
                tz: Math.round(data[offset + 14] * 10000) / 10000,
                sx: Math.round(Math.sqrt(data[offset]**2 + data[offset+1]**2 + data[offset+2]**2) * 10000) / 10000,
                sy: Math.round(Math.sqrt(data[offset+4]**2 + data[offset+5]**2 + data[offset+6]**2) * 10000) / 10000,
                // rotation angle (from 2D rotation matrix elements)
                rot: Math.round(Math.atan2(data[offset+1], data[offset]) * 180 / Math.PI * 100) / 100,
              };
            }
          }
          return origU4fv.apply(this, arguments);
        };
      }

      // bufferSubData - stride=24のvertexサイズを取得
      const origBSD = proto.bufferSubData;
      if (origBSD) {
        proto.bufferSubData = function(target, dstOffset, srcData, srcOffset, length) {
          if (window.__wglCapturing && target === 34962 && currentStride === 24) {
            const so = srcOffset || 0;
            const len = length || (srcData.byteLength - so);
            // 最初の4頂点から quad size を読み取り
            if (len >= 24 * 4) {
              const view = new DataView(srcData.buffer, srcData.byteOffset + so, Math.min(len, 96));
              const v0x = view.getFloat32(0, true);
              const v0y = view.getFloat32(4, true);
              const v1x = view.getFloat32(24, true);
              const v1y = view.getFloat32(28, true);
              currentVertexSize = {
                halfW: Math.round(Math.abs(v1x - v0x) / 2 * 10000) / 10000,
                halfH: Math.round(Math.abs(v0y) * 10000) / 10000,
              };
            }
          }
          return origBSD.apply(this, arguments);
        };
      }

      const origDE = proto.drawElements;
      if (origDE) {
        proto.drawElements = function(mode, count, type, offset) {
          if (window.__wglCapturing && currentStride === 24 && currentMat) {
            window.__wglGameObjects.push({
              ...currentMat,
              v: count,
              size: currentVertexSize,
            });
            currentMat = null;
            currentVertexSize = null;
          }
          return origDE.apply(this, arguments);
        };
      }

      const origUP = proto.useProgram;
      if (origUP) {
        proto.useProgram = function(prog) {
          if (window.__wglCapturing) {
            currentMat = null;
            currentVertexSize = null;
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

  // ゲームオブジェクトをキャプチャする関数
  async function captureFrame(label) {
    const data = await page.evaluate(() => {
      window.__wglGameObjects = [];
      window.__wglCapturing = true;
      return new Promise(resolve => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            window.__wglCapturing = false;
            // 重複排除（2パス分あるので）
            const objs = window.__wglGameObjects;
            const unique = [];
            const seen = new Set();
            for (const o of objs) {
              const key = `${o.tx},${o.ty},${o.sx}`;
              if (!seen.has(key)) {
                seen.add(key);
                unique.push(o);
              }
            }
            resolve(unique);
          });
        });
      });
    });

    console.log(`\n=== ${label} (${data.length} unique objects) ===`);

    // 分類
    const dangerLine = data.filter(o => o.sx > 10); // scale > 10 = danger line
    const background = data.filter(o => o.v > 100 && o.sx <= 10); // many vertices = background tilemap
    const nextPiece = data.filter(o => o.tx > 5); // X > 5 = NEXT area
    const gamePieces = data.filter(o => o.v <= 6 && o.sx <= 10 && o.tx <= 5 && o.sx < 10);

    if (dangerLine.length > 0) {
      console.log(`\n  [DANGER LINE] Y=${dangerLine[0].ty}`);
    }

    if (nextPiece.length > 0) {
      const n = nextPiece[0];
      console.log(`\n  [NEXT PIECE] pos=(${n.tx}, ${n.ty}) scale=${n.sx} size=${JSON.stringify(n.size)}`);
    }

    console.log(`\n  [GAME PIECES] (${gamePieces.length}):`);
    // Y座標でソート（上から下）
    gamePieces.sort((a, b) => b.ty - a.ty);
    for (const p of gamePieces) {
      const type = p.ty > 3.3 ? 'CURSOR' : 'BOARD';
      console.log(`    [${type}] pos=(${p.tx}, ${p.ty}) scale=${p.sx} rot=${p.rot}° size=${JSON.stringify(p.size)}`);
    }

    if (background.length > 0) {
      console.log(`\n  [BACKGROUND] ${background.length} tilemaps`);
    }

    return { dangerLine, nextPiece, gamePieces, background, all: data };
  }

  // 初期状態
  await captureFrame('Initial state');

  // 5回ピースを落とす
  const dropPositions = [
    { x: 500, label: 'center-left' },
    { x: 640, label: 'center' },
    { x: 780, label: 'center-right' },
    { x: 420, label: 'left' },
    { x: 640, label: 'center-2' },
  ];

  for (const dp of dropPositions) {
    console.log(`\n>>> Dropping piece at X=${dp.x} (${dp.label}) <<<`);
    await page.mouse.click(dp.x, 200);
    await page.waitForTimeout(2500); // wait for piece to settle
    await captureFrame(`After drop at ${dp.label} (X=${dp.x})`);
  }

  await page.waitForTimeout(3000);
  await browser.close();
}

run().catch(console.error);

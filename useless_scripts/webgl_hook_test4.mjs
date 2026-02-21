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
    window.__wglDrawCalls = [];
    window.__wglCurrentUniforms = [];
    window.__wglVertexAttribs = {}; // attrib index -> {size, type, stride, offset}
    window.__wglLastBufferData = null; // 最後のbufferSubDataの中身

    function hookPrototype(proto, protoName) {
      if (!proto) return;

      // vertexAttribPointer - 頂点フォーマットを記録
      const origVAP = proto.vertexAttribPointer;
      if (origVAP) {
        proto.vertexAttribPointer = function(index, size, type, normalized, stride, offset) {
          if (window.__wglCapturing) {
            window.__wglVertexAttribs[index] = { size, type, stride, offset, normalized };
          }
          return origVAP.apply(this, arguments);
        };
      }

      // bufferSubData - ARRAY_BUFFERの実データを取得
      const origBSD = proto.bufferSubData;
      if (origBSD) {
        proto.bufferSubData = function(target, dstOffset, srcData, srcOffset, length) {
          if (window.__wglCapturing && target === 34962) { // ARRAY_BUFFER
            // srcDataの実際のバイトデータを取得
            const so = srcOffset || 0;
            const len = length || (srcData.byteLength - so);
            // Float32として読み取り（最初の256バイト=64 floats）
            const maxBytes = Math.min(len, 512);
            const view = new DataView(srcData.buffer, srcData.byteOffset + so, maxBytes);
            const floats = [];
            for (let i = 0; i < maxBytes; i += 4) {
              floats.push(Math.round(view.getFloat32(i, true) * 10000) / 10000);
            }
            window.__wglLastBufferData = {
              target, dstOffset,
              dataLen: len,
              floats: floats,
            };
          }
          return origBSD.apply(this, arguments);
        };
      }

      // uniform4fv
      const origU4fv = proto.uniform4fv;
      if (origU4fv) {
        proto.uniform4fv = function(loc, data, srcOffset, srcLength) {
          if (window.__wglCapturing && data) {
            const offset = srcOffset || 0;
            const length = srcLength || (data.length - offset);
            const slice = [];
            for (let i = 0; i < Math.min(length, 16); i++) {
              slice.push(Math.round(data[offset + i] * 10000) / 10000);
            }
            window.__wglCurrentUniforms.push({ len: length, data: slice });
          }
          return origU4fv.apply(this, arguments);
        };
      }

      // drawElements
      const origDE = proto.drawElements;
      if (origDE) {
        proto.drawElements = function(mode, count, type, offset) {
          if (window.__wglCapturing) {
            window.__wglDrawCalls.push({
              vertices: count,
              uniforms: window.__wglCurrentUniforms.slice(),
              attribs: {...window.__wglVertexAttribs},
              bufferData: window.__wglLastBufferData,
            });
            window.__wglCurrentUniforms = [];
            window.__wglLastBufferData = null;
          }
          return origDE.apply(this, arguments);
        };
      }

      // useProgram
      const origUP = proto.useProgram;
      if (origUP) {
        proto.useProgram = function(prog) {
          if (window.__wglCapturing) {
            window.__wglCurrentUniforms = [];
            window.__wglVertexAttribs = {};
          }
          return origUP.call(this, prog);
        };
      }

      console.log(`[WebGL Hook] Hooked ${protoName}`);
    }

    hookPrototype(WebGLRenderingContext.prototype, 'WGL1');
    if (typeof WebGL2RenderingContext !== 'undefined') {
      hookPrototype(WebGL2RenderingContext.prototype, 'WGL2');
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
  await page.waitForTimeout(2000);

  // 1フレームキャプチャ
  console.log('\n=== Capturing 1 frame ===');
  const result = await page.evaluate(() => {
    window.__wglDrawCalls = [];
    window.__wglCurrentUniforms = [];
    window.__wglVertexAttribs = {};
    window.__wglLastBufferData = null;
    window.__wglCapturing = true;

    return new Promise(resolve => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          window.__wglCapturing = false;
          resolve({
            total: window.__wglDrawCalls.length,
            calls: window.__wglDrawCalls.map(dc => ({
              v: dc.vertices,
              nU: dc.uniforms.length,
              uniforms: dc.uniforms,
              attribs: dc.attribs,
              hasBuf: !!dc.bufferData,
              bufLen: dc.bufferData ? dc.bufferData.dataLen : 0,
              bufFloats: dc.bufferData ? dc.bufferData.floats : null,
            })),
          });
        });
      });
    });
  });

  console.log(`Total draw calls: ${result.total}\n`);

  // 全draw callの詳細を表示
  for (let i = 0; i < result.calls.length; i++) {
    const dc = result.calls[i];
    const hasUniforms = dc.nU > 0;
    const hasBuf = dc.hasBuf;

    console.log(`--- Draw #${i}: vertices=${dc.v}, uniforms=${dc.nU}, hasBuf=${hasBuf}, bufLen=${dc.bufLen} ---`);

    // 頂点属性フォーマット
    if (Object.keys(dc.attribs).length > 0) {
      for (const [idx, attr] of Object.entries(dc.attribs)) {
        console.log(`  attrib[${idx}]: size=${attr.size} stride=${attr.stride} offset=${attr.offset}`);
      }
    }

    // uniform行列
    for (const u of dc.uniforms) {
      if (u.len === 16) {
        // translation抽出
        console.log(`  uniform mat4: translate=(${u.data[12]}, ${u.data[13]}, ${u.data[14]}) scale=(${Math.round(Math.sqrt(u.data[0]**2+u.data[1]**2+u.data[2]**2)*1000)/1000}, ${Math.round(Math.sqrt(u.data[4]**2+u.data[5]**2+u.data[6]**2)*1000)/1000})`);
      } else {
        console.log(`  uniform vec${u.len}: [${u.data.join(', ')}]`);
      }
    }

    // バッファデータ（頂点位置の抽出）
    if (hasBuf && dc.bufFloats) {
      const stride = Object.values(dc.attribs)[0]?.stride || 0;
      const posOffset = 0;

      if (stride > 0) {
        const floatsPerVertex = stride / 4;
        const numVertices = Math.min(dc.v, 24); // 最大24頂点分
        console.log(`  Vertex data (stride=${stride}, floatsPerVertex=${floatsPerVertex}):`);

        for (let vi = 0; vi < numVertices && vi * floatsPerVertex < dc.bufFloats.length; vi++) {
          const base = vi * floatsPerVertex;
          const x = dc.bufFloats[base];
          const y = dc.bufFloats[base + 1];
          const z = dc.bufFloats[base + 2];
          console.log(`    v${vi}: pos=(${x}, ${y}, ${z || 'N/A'}) raw=[${dc.bufFloats.slice(base, base + Math.min(floatsPerVertex, 8)).join(', ')}]`);
        }
      } else {
        // stride不明の場合、最初の32 floatsを表示
        console.log(`  Raw buffer floats: [${dc.bufFloats.slice(0, 32).join(', ')}]`);
      }
    }
  }

  await page.waitForTimeout(3000);
  await browser.close();
}

run().catch(console.error);

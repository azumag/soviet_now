import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function extractState() {
  const browser = await chromium.launch({ headless: false, args: ['--start-maximized'] });
  const context = await browser.newContext();

  await context.addInitScript(() => {
    const origThen = Promise.prototype.then;
    let hooked = false;
    Promise.prototype.then = function(onFulfilled, onRejected) {
      if (!hooked && onFulfilled && onFulfilled.toString().includes('loadingBar')) {
        hooked = true;
        return origThen.call(this, function(inst) {
          window.__unityInstance = inst;
          console.log('[HOOK] Unity instance captured');
          return onFulfilled.call(this, inst);
        }, onRejected);
      }
      return origThen.call(this, onFulfilled, onRejected);
    };

    // Hook WebGL to enable preserveDrawingBuffer
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
      if (type === 'webgl' || type === 'webgl2') {
        attrs = attrs || {};
        attrs.preserveDrawingBuffer = true;
        console.log('[HOOK] WebGL context with preserveDrawingBuffer=true');
      }
      return origGetContext.call(this, type, attrs);
    };
  });

  const page = await context.newPage();
  const consoleLogs = [];
  page.on('console', msg => {
    const text = msg.text();
    consoleLogs.push(text);
    if (text.includes('[HOOK]')) console.log(`[CONSOLE] ${text}`);
  });

  console.log('Navigating...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(20000);

  const gf = page.frames().find(f => f.url().includes('no_bg=true'));
  if (!gf) { console.log('No game frame'); await browser.close(); return; }
  if (!(await gf.evaluate(() => !!window.__unityInstance))) {
    console.log('No Unity instance'); await browser.close(); return;
  }
  console.log('Unity instance found!');

  // Click to start
  const iframe = await page.$('#webgl-frame');
  if (iframe) {
    const box = await iframe.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.3);
    await page.waitForTimeout(3000);
  }

  // === Phase 1: Proper IL2CPP Metadata Parsing ===
  console.log('\n=== Phase 1: IL2CPP Metadata - Complete Game Classes ===');

  const gameMetadata = await gf.evaluate(() => {
    const Module = window.__unityInstance.Module;
    const heap = new Uint8Array(Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);
    const metaBase = 21307480;

    // Read and validate header
    const sanity = view.getUint32(metaBase, true);
    const version = view.getInt32(metaBase + 4, true);
    if (sanity !== 0xFAB11BAF) return { error: 'Bad sanity: 0x' + sanity.toString(16) };

    // Read all header pairs (offset, size) - v29 format
    // Each entry is 8 bytes (offset: int32, size: int32)
    const headerPairs = {};
    const headerNames = [
      'stringLiteral', 'string', 'events', 'properties', 'methods',
      'parameterDefaultValues', 'fieldDefaultValues', 'fieldAndParameterDefaultValueData',
      'fieldMarshaledSizes', 'parameters', 'fields',
      'genericParameters', 'genericParameterConstraints',
      'genericContainers', 'nestedTypes', 'interfaces',
      'vtableMethods', 'interfaceOffsets', 'typeDefinitions',
      'rgctxEntries', 'images', 'assemblies',
      'metadataUsageLists', 'metadataUsagePairs', 'fieldRefs',
      'referencedAssemblies', 'attributesInfo', 'attributeTypes',
      'unresolvedVirtualCallParameterTypes', 'unresolvedVirtualCallParameterRanges',
      'windowsRuntimeTypeNames', 'windowsRuntimeStrings',
      'exportedTypeDefinitions'
    ];

    for (let i = 0; i < headerNames.length; i++) {
      const off = metaBase + 8 + i * 8;
      headerPairs[headerNames[i]] = {
        offset: view.getInt32(off, true),
        size: view.getInt32(off + 4, true)
      };
    }

    // Helper: read null-terminated string from string table
    function readStr(idx) {
      if (idx < 0 || idx >= headerPairs.string.size) return '';
      let s = '';
      let p = metaBase + headerPairs.string.offset + idx;
      let maxLen = 200;
      while (p < heap.length && heap[p] !== 0 && maxLen-- > 0) {
        s += String.fromCharCode(heap[p++]);
      }
      return s;
    }

    // Parse Images (assemblies loaded in runtime)
    // Il2CppImageDefinition: { nameIndex(4), assemblyIndex(4), typeStart(4), typeCount(4), ... }
    // Size varies by version. In v29, it's typically 40 bytes
    const imgOff = headerPairs.images.offset;
    const imgSize = headerPairs.images.size;

    // Try to determine image definition size
    // Read first image's nameIndex and find assembly-csharp
    const images = [];
    const imgDefSize = 40; // v29
    const numImages = Math.floor(imgSize / imgDefSize);

    for (let i = 0; i < numImages; i++) {
      const base = metaBase + imgOff + i * imgDefSize;
      const nameIdx = view.getInt32(base, true);
      const assemblyIdx = view.getInt32(base + 4, true);
      const typeStart = view.getInt32(base + 8, true);
      const typeCount = view.getUint32(base + 12, true);
      const exportedTypeStart = view.getInt32(base + 16, true);
      const exportedTypeCount = view.getUint32(base + 20, true);
      const name = readStr(nameIdx);

      images.push({ name, assemblyIdx, typeStart, typeCount, exportedTypeStart, exportedTypeCount });
    }

    // Find Assembly-CSharp (game code)
    const gameImage = images.find(img => img.name.includes('Assembly-CSharp'));
    if (!gameImage) {
      return { error: 'No Assembly-CSharp found', images: images.map(i => i.name) };
    }

    // Parse TypeDefinitions for the game assembly
    // Il2CppTypeDefinition v29: 88 bytes
    // Layout:
    // 0: nameIndex (4)
    // 4: namespaceIndex (4)
    // 8: byvalTypeIndex (4)
    // 12: byrefTypeIndex (4) [v29+ may have this]
    // 16: declaringTypeIndex (4)
    // 20: parentIndex (4)
    // 24: elementTypeIndex (4)
    // 28: genericContainerIndex (4)
    // 32: flags (4)
    // 36: fieldStart (4)
    // 40: methodStart (4)
    // 44: eventStart (4)
    // 48: propertyStart (4)
    // 52: nestedTypesStart (4)
    // 56: interfacesStart (4)
    // 60: vtableStart (4)
    // 64: interfaceOffsetsStart (4)
    // 68: method_count (2)
    // 70: property_count (2)
    // 72: field_count (2)
    // 74: event_count (2)
    // 76: nested_type_count (2)
    // 78: vtable_count (2)
    // 80: interfaces_count (2)
    // 82: interface_offsets_count (2)
    // 84: bitfield (4)
    // Total: 88 bytes

    const typeDefOff = headerPairs.typeDefinitions.offset;
    const typeDefSize = 88;
    const totalTypeDefs = Math.floor(headerPairs.typeDefinitions.size / typeDefSize);

    const gameTypes = [];

    for (let i = gameImage.typeStart; i < gameImage.typeStart + gameImage.typeCount; i++) {
      const base = metaBase + typeDefOff + i * typeDefSize;
      if (base + typeDefSize > heap.length) break;

      const nameIdx = view.getInt32(base, true);
      const nsIdx = view.getInt32(base + 4, true);
      const fieldStart = view.getInt32(base + 36, true);
      const methodStart = view.getInt32(base + 40, true);
      const methodCount = view.getUint16(base + 68, true);
      const fieldCount = view.getUint16(base + 72, true);

      const name = readStr(nameIdx);
      const ns = readStr(nsIdx);

      // Read methods
      // Il2CppMethodDefinition: { returnType(4), nameIndex(4), declaringType(4), token(4), ... }
      // In v29, typically 24 bytes:
      // 0: returnType (4)
      // 4: nameIndex (4)
      // 8: declaringType (4)
      // 12: token (4)
      // 16: parameterStart (2) or (4)
      // 20: genericContainerIndex (2) or (4)
      // ... padding
      // Let's try 24 bytes
      const methodDefSize = 24; // approximate
      const methods = [];
      for (let m = 0; m < methodCount && m < 100; m++) {
        const mBase = metaBase + headerPairs.methods.offset + (methodStart + m) * methodDefSize;
        if (mBase + methodDefSize > heap.length) break;
        const mNameIdx = view.getInt32(mBase + 4, true);
        const mName = readStr(mNameIdx);
        if (mName && mName.length > 0 && mName.length < 100) {
          methods.push(mName);
        }
      }

      // Read fields
      // Il2CppFieldDefinition: { nameIndex(4), typeIndex(4), token(4) } = 12 bytes
      const fieldDefSize = 12;
      const fields = [];
      for (let f = 0; f < fieldCount && f < 100; f++) {
        const fBase = metaBase + headerPairs.fields.offset + (fieldStart + f) * fieldDefSize;
        if (fBase + fieldDefSize > heap.length) break;
        const fNameIdx = view.getInt32(fBase, true);
        const fName = readStr(fNameIdx);
        if (fName && fName.length > 0 && fName.length < 100) {
          fields.push(fName);
        }
      }

      gameTypes.push({ name, namespace: ns, methods, fields, methodCount, fieldCount });
    }

    return {
      version,
      totalTypeDefs,
      gameImage: { name: gameImage.name, typeStart: gameImage.typeStart, typeCount: gameImage.typeCount },
      allImages: images.map(i => ({ name: i.name, typeCount: i.typeCount })),
      gameTypes,
    };
  });

  if (gameMetadata.error) {
    console.log('ERROR:', gameMetadata.error);
    if (gameMetadata.images) console.log('Available images:', gameMetadata.images.join(', '));
  } else {
    console.log(`IL2CPP v${gameMetadata.version}, Total types: ${gameMetadata.totalTypeDefs}`);
    console.log(`Game assembly: ${gameMetadata.gameImage.name} (${gameMetadata.gameImage.typeCount} types, starting at ${gameMetadata.gameImage.typeStart})`);
    console.log(`\nAll assemblies:`);
    for (const img of gameMetadata.allImages) {
      console.log(`  ${img.name}: ${img.typeCount} types`);
    }
    console.log(`\n=== Game Types (${gameMetadata.gameTypes.length}) ===`);
    for (const t of gameMetadata.gameTypes) {
      console.log(`\n--- ${t.namespace ? t.namespace + '.' : ''}${t.name} ---`);
      if (t.fields.length > 0) {
        console.log(`  Fields: ${t.fields.join(', ')}`);
      }
      if (t.methods.length > 0) {
        console.log(`  Methods: ${t.methods.join(', ')}`);
      }
    }
  }

  // === Phase 2: Test discovered methods on all GameObjects ===
  console.log('\n\n=== Phase 2: Test Game Methods on GameObjects ===');

  const gameObjects = [
    'VolumeBGM', 'VolumeSE', 'Collider', 'UnityroomApiClient', 'Score',
    'EventSystem', 'Slider', 'Background', 'Kage', 'Circle', 'Frame',
    'Daigomi', 'SE', 'BGM', 'Next', 'Main Camera', 'Canvas'
  ];

  // Collect all unique method names from game types
  const allGameMethods = new Set();
  if (gameMetadata.gameTypes) {
    for (const t of gameMetadata.gameTypes) {
      for (const m of t.methods) {
        // Skip constructors and common inherited methods
        if (!m.startsWith('.') && !m.startsWith('get_') && !m.startsWith('set_') &&
            m !== 'Finalize' && m !== 'GetHashCode' && m !== 'Equals' && m !== 'ToString' &&
            m !== 'GetType' && m !== 'MemberwiseClone' && m.length > 1) {
          allGameMethods.add(m);
        }
      }
    }
  }

  console.log(`Total unique game methods to test: ${allGameMethods.size}`);
  const methodList = [...allGameMethods];
  console.log(`Sample: ${methodList.slice(0, 30).join(', ')}`);

  const foundMethods = {};
  for (const obj of gameObjects) {
    foundMethods[obj] = [];
    for (const method of methodList) {
      consoleLogs.length = 0;
      await gf.evaluate(({o, m}) => {
        try { window.__unityInstance.SendMessage(o, m); } catch(e) {}
      }, {o: obj, m: method});
      await page.waitForTimeout(20);

      const smLogs = consoleLogs.filter(l => l.includes('SendMessage'));
      if (smLogs.length === 0) {
        foundMethods[obj].push(method);
      }
    }
    if (foundMethods[obj].length > 0) {
      console.log(`\n${obj}: ${foundMethods[obj].length} methods: ${foundMethods[obj].join(', ')}`);
    }
  }

  // === Phase 3: WebGL readPixels with preserveDrawingBuffer ===
  console.log('\n\n=== Phase 3: WebGL readPixels (with preserveDrawingBuffer hook) ===');

  // Take a screenshot first to compare
  await page.screenshot({ path: 'debug_screenshot.png' });

  const pixelData = await gf.evaluate(() => {
    const canvas = document.getElementById('unity-canvas');
    if (!canvas) return { error: 'No canvas' };

    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return { error: 'No GL' };

    const w = canvas.width, h = canvas.height;

    // Read full canvas
    const pixels = new Uint8Array(w * h * 4);
    gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

    // Check if we got any data
    let nonZero = 0;
    for (let i = 0; i < pixels.length; i += 100) {
      if (pixels[i] > 0) nonZero++;
    }

    // Read specific regions (screen coordinates to GL coordinates)
    // GL Y is flipped: GL_Y = canvas.height - screen_Y - region_height
    // Game board is roughly center: screen x=300-650, y=30-680 (in 960x540 canvas)
    // NEXT area: screen x=720-900, y=10-150

    const regions = {};
    function readRegion(name, sx, sy, sw, sh) {
      // Convert screen coords to GL coords (flip Y)
      const gx = sx;
      const gy = h - sy - sh;
      const regionPixels = new Uint8Array(sw * sh * 4);
      gl.readPixels(gx, gy, sw, sh, gl.RGBA, gl.UNSIGNED_BYTE, regionPixels);

      const colors = {};
      let total = 0;
      for (let i = 0; i < regionPixels.length; i += 4) {
        const r = regionPixels[i], g = regionPixels[i+1], b = regionPixels[i+2], a = regionPixels[i+3];
        if (r === 0 && g === 0 && b === 0 && a === 0) continue;
        total++;
        const key = `rgb(${Math.floor(r/16)*16},${Math.floor(g/16)*16},${Math.floor(b/16)*16})`;
        colors[key] = (colors[key] || 0) + 1;
      }

      const topColors = Object.entries(colors)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10)
        .map(([c, n]) => `${c}:${n}`);

      regions[name] = { nonZeroPixels: total, totalPixels: sw * sh, topColors };
    }

    readRegion('next', 720, 10, 180, 140);
    readRegion('board_top', 300, 30, 350, 150);
    readRegion('board_mid', 300, 200, 350, 150);
    readRegion('board_bot', 300, 370, 350, 170);
    readRegion('score', 750, 200, 150, 50);
    readRegion('full_center', 400, 200, 200, 200);

    return { canvasSize: { w, h }, nonZeroSampled: nonZero, regions };
  });

  console.log(JSON.stringify(pixelData, null, 2));

  // === Phase 4: Hook requestAnimationFrame for pixel reading ===
  if (pixelData.nonZeroSampled === 0) {
    console.log('\n=== Phase 4: Reading pixels via requestAnimationFrame hook ===');

    const rafPixels = await gf.evaluate(() => {
      return new Promise((resolve) => {
        const origRAF = window.requestAnimationFrame;
        let captured = false;

        window.requestAnimationFrame = function(callback) {
          if (!captured) {
            captured = true;
            return origRAF.call(window, function(ts) {
              // Call Unity's render first
              callback(ts);

              // Now read pixels after render
              const canvas = document.getElementById('unity-canvas');
              const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
              const w = canvas.width, h = canvas.height;

              // Read a small sample region
              const sampleW = 200, sampleH = 200;
              const sx = Math.floor(w / 2 - sampleW / 2);
              const sy = Math.floor(h / 2 - sampleH / 2);
              const pixels = new Uint8Array(sampleW * sampleH * 4);
              gl.readPixels(sx, sy, sampleW, sampleH, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

              let nonZero = 0;
              const colors = {};
              for (let i = 0; i < pixels.length; i += 4) {
                const r = pixels[i], g = pixels[i+1], b = pixels[i+2];
                if (r > 0 || g > 0 || b > 0) {
                  nonZero++;
                  const key = `${Math.floor(r/32)*32},${Math.floor(g/32)*32},${Math.floor(b/32)*32}`;
                  colors[key] = (colors[key] || 0) + 1;
                }
              }

              const topColors = Object.entries(colors)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 15)
                .map(([c, n]) => ({ rgb: c, count: n }));

              // Restore original RAF
              window.requestAnimationFrame = origRAF;
              resolve({ nonZero, totalPixels: sampleW * sampleH, topColors });
            });
          }
          return origRAF.call(window, callback);
        };

        // Trigger a frame
        origRAF.call(window, () => {});
      });
    });

    console.log('RAF pixel capture:', JSON.stringify(rafPixels, null, 2));
  }

  // === Phase 5: Full-canvas pixel dump for game state ===
  console.log('\n\n=== Phase 5: Full Canvas Pixel State ===');

  // Read pixels using requestAnimationFrame hook
  const fullState = await gf.evaluate(() => {
    return new Promise((resolve) => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const canvas = document.getElementById('unity-canvas');
          const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
          if (!gl) { resolve({ error: 'No GL' }); return; }

          const w = canvas.width, h = canvas.height;

          // Read NEXT area (top-right of screen, in GL coords = bottom-right)
          // Screen coords: roughly x=730, y=30, 150x120
          // GL coords: x=730, y=h-30-120=390
          function sampleRegion(name, sx, sy, sw, sh) {
            const gy = h - sy - sh;
            const p = new Uint8Array(sw * sh * 4);
            gl.readPixels(sx, gy, sw, sh, gl.RGBA, gl.UNSIGNED_BYTE, p);

            let nonZero = 0;
            const colors = {};
            // Sample every 4th pixel for speed
            for (let i = 0; i < p.length; i += 16) {
              const r = p[i], g = p[i+1], b = p[i+2];
              if (r > 5 || g > 5 || b > 5) {
                nonZero++;
                const key = `${Math.floor(r/16)*16},${Math.floor(g/16)*16},${Math.floor(b/16)*16}`;
                colors[key] = (colors[key] || 0) + 1;
              }
            }

            return {
              nonZero,
              sampled: Math.floor(sw * sh / 4),
              topColors: Object.entries(colors)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 8)
                .map(([c, n]) => `${c}:${n}`)
            };
          }

          resolve({
            canvasSize: { w, h },
            next: sampleRegion('next', 730, 30, 150, 120),
            board_top: sampleRegion('board_top', 320, 40, 320, 130),
            board_mid: sampleRegion('board_mid', 320, 180, 320, 150),
            board_bot: sampleRegion('board_bot', 320, 340, 320, 170),
            score_area: sampleRegion('score', 730, 180, 170, 40),
          });
        });
      });
    });
  });

  console.log(JSON.stringify(fullState, null, 2));

  console.log('\n=== Complete ===');
  await new Promise(() => {});
}

extractState().catch(console.error);

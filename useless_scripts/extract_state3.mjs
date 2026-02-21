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
          return onFulfilled.call(this, inst);
        }, onRejected);
      }
      return origThen.call(this, onFulfilled, onRejected);
    };

    // Hook WebGL for preserveDrawingBuffer
    const origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {
      if (type === 'webgl' || type === 'webgl2') {
        attrs = attrs || {};
        attrs.preserveDrawingBuffer = true;
      }
      return origGetContext.call(this, type, attrs);
    };
  });

  const page = await context.newPage();
  const consoleLogs = [];
  page.on('console', msg => consoleLogs.push(msg.text()));

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

  // === Phase 1: Dump raw IL2CPP header and parse correctly ===
  console.log('\n=== Phase 1: IL2CPP Header Dump ===');

  const headerDump = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);
    const metaBase = 21307480;

    const sanity = view.getUint32(metaBase, true);
    const version = view.getInt32(metaBase + 4, true);

    // Dump first 300 bytes as int32 pairs
    const rawPairs = [];
    for (let i = 8; i < 280; i += 8) {
      rawPairs.push({
        byteOffset: i,
        offset: view.getInt32(metaBase + i, true),
        size: view.getInt32(metaBase + i + 4, true)
      });
    }

    return { sanity: '0x' + sanity.toString(16), version, rawPairs };
  });

  console.log(`Sanity: ${headerDump.sanity}, Version: ${headerDump.version}`);
  console.log('\nRaw header pairs (offset, size):');
  // v29 header layout with stringLiteralData pair
  const v29Names = [
    'stringLiteral',           // 8
    'stringLiteralData',       // 16 (added in v27)
    'string',                  // 24
    'events',                  // 32
    'properties',              // 40
    'methods',                 // 48
    'parameterDefaultValues',  // 56
    'fieldDefaultValues',      // 64
    'fieldAndParameterDefaultValueData', // 72
    'fieldMarshaledSizes',     // 80
    'parameters',              // 88
    'fields',                  // 96
    'genericParameters',       // 104
    'genericParameterConstraints', // 112
    'genericContainers',       // 120
    'nestedTypes',             // 128
    'interfaces',              // 136
    'vtableMethods',           // 144
    'interfaceOffsets',        // 152
    'typeDefinitions',         // 160
    'rgctxEntries',            // 168
    'images',                  // 176
    'assemblies',              // 184
    'fieldRefs',               // 192 (metadataUsageLists removed in v27)
    'referencedAssemblies',    // 200
    'attributesInfo',          // 208
    'attributeTypes',          // 216
    'unresolvedVirtualCallParameterTypes', // 224
    'unresolvedVirtualCallParameterRanges', // 232
    'windowsRuntimeTypeNames', // 240
    'windowsRuntimeStrings',   // 248
    'exportedTypeDefinitions', // 256
  ];

  for (let i = 0; i < headerDump.rawPairs.length && i < v29Names.length; i++) {
    const p = headerDump.rawPairs[i];
    const name = v29Names[i] || '???';
    console.log(`  [${p.byteOffset}] ${name}: offset=${p.offset}, size=${p.size}`);
  }

  // === Phase 2: Parse with corrected offsets ===
  console.log('\n=== Phase 2: Parse Game Assembly Types ===');

  const gameData = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);
    const metaBase = 21307480;

    // v29 corrected header offsets (including stringLiteralData pair)
    function readPair(byteOffset) {
      return {
        offset: view.getInt32(metaBase + byteOffset, true),
        size: view.getInt32(metaBase + byteOffset + 4, true)
      };
    }

    const tables = {
      stringLiteral: readPair(8),
      stringLiteralData: readPair(16),
      string: readPair(24),
      events: readPair(32),
      properties: readPair(40),
      methods: readPair(48),
      parameterDefaultValues: readPair(56),
      fieldDefaultValues: readPair(64),
      fieldAndParameterDefaultValueData: readPair(72),
      fieldMarshaledSizes: readPair(80),
      parameters: readPair(88),
      fields: readPair(96),
      genericParameters: readPair(104),
      genericParameterConstraints: readPair(112),
      genericContainers: readPair(120),
      nestedTypes: readPair(128),
      interfaces: readPair(136),
      vtableMethods: readPair(144),
      interfaceOffsets: readPair(152),
      typeDefinitions: readPair(160),
      rgctxEntries: readPair(168),
      images: readPair(176),
      assemblies: readPair(184),
    };

    // Read string from string table
    function readStr(idx) {
      if (idx < 0 || idx >= tables.string.size) return '<invalid>';
      let s = '';
      let p = metaBase + tables.string.offset + idx;
      for (let n = 0; n < 200 && p < heap.length && heap[p] !== 0; n++, p++) {
        s += String.fromCharCode(heap[p]);
      }
      return s;
    }

    // Validate: try reading a string at index 0
    const testStr = readStr(0);

    // Parse Images
    // Il2CppImageDefinition v29: nameIndex(4) + assemblyIndex(4) + typeStart(4) + typeCount(4) + ...
    // Try different sizes: 40, 28, 24
    const imgPair = tables.images;
    const results = { testString: testStr, stringTableOK: testStr.length > 0 };

    // Try finding image definition size by scanning for valid name strings
    for (const imgDefSize of [40, 32, 28, 24, 20]) {
      const numImages = Math.floor(imgPair.size / imgDefSize);
      if (numImages < 1 || numImages > 200) continue;

      let validCount = 0;
      const testImages = [];

      for (let i = 0; i < Math.min(numImages, 5); i++) {
        const base = metaBase + imgPair.offset + i * imgDefSize;
        const nameIdx = view.getInt32(base, true);
        const name = readStr(nameIdx);

        if (name.length > 0 && name.length < 200 && /^[\x20-\x7E]+$/.test(name)) {
          validCount++;
        }
        testImages.push({ nameIdx, name: name.substring(0, 80) });
      }

      if (validCount >= 2) {
        results[`imgSize_${imgDefSize}`] = { numImages, validCount, samples: testImages };
      }
    }

    // Also try to directly find Assembly-CSharp by scanning the image table area
    const imgStart = metaBase + imgPair.offset;
    const imgEnd = imgStart + imgPair.size;

    // Scan for "Assembly-CSharp" string in string table
    const asmTarget = 'Assembly-CSharp';
    let asmStrIdx = -1;
    const strStart = metaBase + tables.string.offset;
    for (let i = 0; i < tables.string.size - asmTarget.length; i++) {
      let match = true;
      for (let j = 0; j < asmTarget.length; j++) {
        if (heap[strStart + i + j] !== asmTarget.charCodeAt(j)) { match = false; break; }
      }
      if (match && (i === 0 || heap[strStart + i - 1] === 0)) {
        asmStrIdx = i;
        break;
      }
    }

    results.asmCSharpStringIndex = asmStrIdx;

    if (asmStrIdx >= 0) {
      // Search image table for entries that reference this string index
      for (const imgDefSize of [40, 32, 28, 24, 20]) {
        const numImages = Math.floor(imgPair.size / imgDefSize);
        for (let i = 0; i < numImages; i++) {
          const base = metaBase + imgPair.offset + i * imgDefSize;
          const nameIdx = view.getInt32(base, true);
          if (nameIdx === asmStrIdx) {
            // Found it! Read type range
            const assemblyIdx = view.getInt32(base + 4, true);
            const typeStart = view.getInt32(base + 8, true);
            const typeCount = view.getUint32(base + 12, true);

            results.gameAssembly = {
              imgDefSize,
              imageIndex: i,
              assemblyIdx,
              typeStart,
              typeCount,
              nameIdx
            };
            break;
          }
        }
        if (results.gameAssembly) break;
      }
    }

    // If we found the game assembly, parse its types
    if (results.gameAssembly) {
      const ga = results.gameAssembly;
      const typeDefPair = tables.typeDefinitions;
      const typeDefSize = 88; // v29

      results.gameTypes = [];

      for (let i = ga.typeStart; i < ga.typeStart + ga.typeCount; i++) {
        const base = metaBase + typeDefPair.offset + i * typeDefSize;
        if (base + typeDefSize > heap.length) break;

        const nameIdx = view.getInt32(base, true);
        const nsIdx = view.getInt32(base + 4, true);
        const fieldStart = view.getInt32(base + 36, true);
        const methodStart = view.getInt32(base + 40, true);
        const methodCount = view.getUint16(base + 68, true);
        const fieldCount = view.getUint16(base + 72, true);

        const name = readStr(nameIdx);
        const ns = readStr(nsIdx);

        // Skip compiler-generated and generic types
        if (name.startsWith('<') || name.includes('`')) continue;

        // Read methods (Il2CppMethodDefinition: 24 bytes in v29)
        const methods = [];
        const methodPair = tables.methods;
        for (let m = 0; m < methodCount && m < 60; m++) {
          const mBase = metaBase + methodPair.offset + (methodStart + m) * 24;
          if (mBase + 24 > heap.length) break;
          const mNameIdx = view.getInt32(mBase + 4, true);
          const mName = readStr(mNameIdx);
          if (mName && !mName.startsWith('.') && mName.length > 0 && mName.length < 80) {
            methods.push(mName);
          }
        }

        // Read fields
        const fields = [];
        const fieldPair = tables.fields;
        for (let f = 0; f < fieldCount && f < 60; f++) {
          const fBase = metaBase + fieldPair.offset + (fieldStart + f) * 12;
          if (fBase + 12 > heap.length) break;
          const fNameIdx = view.getInt32(fBase, true);
          const fName = readStr(fNameIdx);
          if (fName && fName.length > 0 && fName.length < 80) {
            fields.push(fName);
          }
        }

        results.gameTypes.push({ name, namespace: ns, methodCount, fieldCount, methods, fields });
      }
    }

    return results;
  });

  if (gameData.testString) {
    console.log(`String table OK, test string: "${gameData.testString}"`);
  }
  console.log(`Assembly-CSharp string index: ${gameData.asmCSharpStringIndex}`);

  if (gameData.gameAssembly) {
    const ga = gameData.gameAssembly;
    console.log(`\nGame assembly found! imgDefSize=${ga.imgDefSize}, typeStart=${ga.typeStart}, typeCount=${ga.typeCount}`);
  }

  // Display image size tests
  for (const key of Object.keys(gameData)) {
    if (key.startsWith('imgSize_')) {
      const v = gameData[key];
      console.log(`  ${key}: ${v.numImages} images, ${v.validCount} valid`);
      for (const s of v.samples) {
        console.log(`    [${s.nameIdx}] "${s.name}"`);
      }
    }
  }

  if (gameData.gameTypes) {
    console.log(`\n=== Game Types (${gameData.gameTypes.length}) ===`);
    for (const t of gameData.gameTypes) {
      console.log(`\n--- ${t.namespace ? t.namespace + '.' : ''}${t.name} (${t.methodCount} methods, ${t.fieldCount} fields) ---`);
      if (t.fields.length > 0) {
        console.log(`  Fields: ${t.fields.join(', ')}`);
      }
      if (t.methods.length > 0) {
        console.log(`  Methods: ${t.methods.join(', ')}`);
      }
    }
  }

  // === Phase 3: Test ALL game methods on ALL GameObjects ===
  console.log('\n\n=== Phase 3: Method-to-GameObject Mapping ===');

  const gameObjects = [
    'VolumeBGM', 'VolumeSE', 'Collider', 'UnityroomApiClient', 'Score',
    'EventSystem', 'Slider', 'Background', 'Kage', 'Circle', 'Frame',
    'Daigomi', 'SE', 'BGM', 'Next', 'Main Camera', 'Canvas'
  ];

  // Collect all unique methods from game types
  const allMethods = new Set();
  if (gameData.gameTypes) {
    for (const t of gameData.gameTypes) {
      for (const m of t.methods) {
        if (m !== 'Finalize' && m !== 'GetHashCode' && m !== 'Equals' &&
            m !== 'ToString' && m !== 'GetType' && m !== 'MemberwiseClone' &&
            !m.startsWith('get_') && !m.startsWith('set_') &&
            !m.startsWith('add_') && !m.startsWith('remove_') &&
            m.length > 1) {
          allMethods.add(m);
        }
      }
    }
  }

  console.log(`Testing ${allMethods.size} unique methods on ${gameObjects.length} GameObjects...`);

  const hits = {};
  const methodArray = [...allMethods];

  for (const obj of gameObjects) {
    for (const method of methodArray) {
      consoleLogs.length = 0;
      await gf.evaluate(({o, m}) => {
        try { window.__unityInstance.SendMessage(o, m); } catch(e) {}
      }, {o: obj, m: method});
      await page.waitForTimeout(15);

      const smLogs = consoleLogs.filter(l => l.includes('SendMessage'));
      if (smLogs.length === 0) {
        // Method executed successfully (no warning)
        if (!hits[obj]) hits[obj] = [];
        hits[obj].push(method);
      }
    }

    if (hits[obj] && hits[obj].length > 0) {
      console.log(`\n  ${obj}: ${hits[obj].join(', ')}`);
    }
  }

  // === Phase 4: Summary and game state extraction strategy ===
  console.log('\n\n=== Phase 4: Summary ===');
  console.log('GameObjects with game-specific methods:');
  for (const [obj, methods] of Object.entries(hits)) {
    // Filter out standard Unity lifecycle methods
    const gameSpecific = methods.filter(m =>
      !['Start', 'Awake', 'Update', 'LateUpdate', 'FixedUpdate',
        'OnEnable', 'OnDisable', 'OnDestroy', 'OnApplicationQuit',
        'OnApplicationPause', 'OnPointerDown', 'OnPointerUp',
        'OnPointerClick', 'OnDrag', 'OnBeginDrag', 'OnEndDrag',
        'OnValueChanged'].includes(m)
    );
    if (gameSpecific.length > 0) {
      console.log(`  ${obj}: ${gameSpecific.join(', ')}`);
    }
  }

  // === Phase 5: WebGL pixel-based state extraction ===
  console.log('\n=== Phase 5: Pixel-Based Game State ===');

  // Drop a few pieces to have game state
  if (iframe) {
    const box = await iframe.boundingBox();
    const positions = [0.4, 0.5, 0.35, 0.55, 0.45];
    for (const xRatio of positions) {
      await page.mouse.click(box.x + box.width * xRatio, box.y + box.height * 0.3);
      await page.waitForTimeout(1500);
    }
  }

  // Read game board state from pixels
  const boardState = await gf.evaluate(() => {
    return new Promise(resolve => {
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const canvas = document.getElementById('unity-canvas');
          const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
          const w = canvas.width, h = canvas.height;

          // Scan board area column by column to detect pieces
          // Board area: approximately x=295-665, y=30-510 (screen coords)
          // Each column is ~10px wide
          const boardLeft = 295, boardRight = 665, boardTop = 30, boardBottom = 510;
          const colWidth = 10;
          const rowHeight = 10;

          const columns = [];
          for (let x = boardLeft; x < boardRight; x += colWidth) {
            const col = [];
            for (let y = boardTop; y < boardBottom; y += rowHeight) {
              const gy = h - y - rowHeight;
              const pixels = new Uint8Array(colWidth * rowHeight * 4);
              gl.readPixels(x, gy, colWidth, rowHeight, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

              // Average color
              let rSum = 0, gSum = 0, bSum = 0, count = 0;
              for (let i = 0; i < pixels.length; i += 4) {
                if (pixels[i+3] > 0) {
                  rSum += pixels[i]; gSum += pixels[i+1]; bSum += pixels[i+2];
                  count++;
                }
              }
              if (count > 0) {
                col.push({
                  y,
                  r: Math.round(rSum/count),
                  g: Math.round(gSum/count),
                  b: Math.round(bSum/count)
                });
              }
            }
            columns.push({ x, cells: col });
          }

          // Analyze NEXT piece area
          const nextX = 740, nextY = 40, nextW = 140, nextH = 100;
          const nextGY = h - nextY - nextH;
          const nextPixels = new Uint8Array(nextW * nextH * 4);
          gl.readPixels(nextX, nextGY, nextW, nextH, gl.RGBA, gl.UNSIGNED_BYTE, nextPixels);

          const nextColors = {};
          for (let i = 0; i < nextPixels.length; i += 4) {
            const r = nextPixels[i], g = nextPixels[i+1], b = nextPixels[i+2];
            if (r < 10 && g < 10 && b < 10) continue;
            const key = `${Math.floor(r/32)*32},${Math.floor(g/32)*32},${Math.floor(b/32)*32}`;
            nextColors[key] = (nextColors[key] || 0) + 1;
          }

          const nextTopColors = Object.entries(nextColors)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 10)
            .map(([c, n]) => `${c}:${n}`);

          // Find non-background regions on the board
          // Background is roughly rgb(128-160, 128-160, 112-160) - gray/brown tones
          const pieces = [];
          for (const col of columns) {
            for (const cell of col.cells) {
              const { r, g, b } = cell;
              // Red pieces (Soviet flags are red): r > 170, g < 100
              const isRed = r > 170 && g < 100;
              // Green pieces: g > 150, r < 100
              const isGreen = g > 150 && r < 120;
              // Blue pieces: b > 150, r < 100
              const isBlue = b > 150 && r < 100;
              // White pieces
              const isWhite = r > 200 && g > 200 && b > 200;
              // Yellow
              const isYellow = r > 200 && g > 180 && b < 100;
              // Dark (border/shadow)
              const isDark = r < 60 && g < 60 && b < 60;

              if (isRed || isGreen || isBlue || isWhite || isYellow) {
                pieces.push({
                  x: col.x,
                  y: cell.y,
                  r, g, b,
                  color: isRed ? 'red' : isGreen ? 'green' : isBlue ? 'blue' :
                         isWhite ? 'white' : isYellow ? 'yellow' : 'unknown'
                });
              }
            }
          }

          resolve({
            canvasSize: { w, h },
            boardBounds: { left: boardLeft, right: boardRight, top: boardTop, bottom: boardBottom },
            nextColors: nextTopColors,
            coloredPieces: pieces.length,
            piecesByColor: pieces.reduce((acc, p) => {
              acc[p.color] = (acc[p.color] || 0) + 1;
              return acc;
            }, {}),
            samplePieces: pieces.slice(0, 30).map(p => `(${p.x},${p.y})=${p.color}[${p.r},${p.g},${p.b}]`)
          });
        });
      });
    });
  });

  console.log(JSON.stringify(boardState, null, 2));

  console.log('\n=== Complete. Press Ctrl+C to close. ===');
  await new Promise(() => {});
}

extractState().catch(console.error);

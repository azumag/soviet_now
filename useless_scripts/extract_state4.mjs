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

  const iframe = await page.$('#webgl-frame');
  if (iframe) {
    const box = await iframe.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.3);
    await page.waitForTimeout(3000);
  }

  // === Phase 1: Find game assembly types by finding the image definition ===
  console.log('\n=== Phase 1: Find Assembly-CSharp Image Definition ===');

  const asmInfo = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);
    const metaBase = 21307480;

    // Known header offsets (v29)
    const stringOff = view.getInt32(metaBase + 24, true);
    const stringSz = view.getInt32(metaBase + 28, true);
    const imagesOff = view.getInt32(metaBase + 176, true);
    const imagesSz = view.getInt32(metaBase + 180, true);
    const typeDefsOff = view.getInt32(metaBase + 160, true);
    const typeDefsSz = view.getInt32(metaBase + 164, true);
    const methodsOff = view.getInt32(metaBase + 48, true);
    const methodsSz = view.getInt32(metaBase + 52, true);
    const fieldsOff = view.getInt32(metaBase + 96, true);
    const fieldsSz = view.getInt32(metaBase + 100, true);

    function readStr(idx) {
      if (idx < 0 || idx >= stringSz) return '';
      let s = '';
      let p = metaBase + stringOff + idx;
      for (let n = 0; n < 300 && p < heap.length && heap[p] !== 0; n++, p++) {
        s += String.fromCharCode(heap[p]);
      }
      return s;
    }

    // Find "Assembly-CSharp" in string table
    const target = 'Assembly-CSharp';
    const strBase = metaBase + stringOff;
    let asmIdx = -1;
    for (let i = 0; i < stringSz - target.length; i++) {
      if (heap[strBase + i - 1] !== 0 && i > 0) continue; // must be start of string
      let match = true;
      for (let j = 0; j < target.length; j++) {
        if (heap[strBase + i + j] !== target.charCodeAt(j)) { match = false; break; }
      }
      if (match) { asmIdx = i; break; }
    }

    if (asmIdx < 0) return { error: 'Assembly-CSharp not found in string table' };

    // The full name might be "Assembly-CSharp.dll"
    const fullName = readStr(asmIdx);

    // Search image table for this name index
    // Try to find the nameIndex value in the image table bytes
    const imgBase = metaBase + imagesOff;
    const asmIdxBytes = new Uint8Array(4);
    new DataView(asmIdxBytes.buffer).setInt32(0, asmIdx, true);

    const imgMatches = [];
    for (let i = 0; i < imagesSz - 3; i += 4) {
      if (heap[imgBase + i] === asmIdxBytes[0] &&
          heap[imgBase + i + 1] === asmIdxBytes[1] &&
          heap[imgBase + i + 2] === asmIdxBytes[2] &&
          heap[imgBase + i + 3] === asmIdxBytes[3]) {
        imgMatches.push(i);
      }
    }

    // For each match, read surrounding data to determine structure
    const candidates = [];
    for (const offset of imgMatches) {
      // This is the nameIndex field at this offset
      // Read the subsequent fields
      const assemblyIdx = view.getInt32(imgBase + offset + 4, true);
      const typeStart = view.getInt32(imgBase + offset + 8, true);
      const typeCount = view.getUint32(imgBase + offset + 12, true);

      // Validate: typeStart should be reasonable (0 to totalTypeDefs)
      const totalTypeDefs = Math.floor(typeDefsSz / 88);
      if (typeStart >= 0 && typeStart < totalTypeDefs &&
          typeCount > 0 && typeCount < 500 &&
          typeStart + typeCount <= totalTypeDefs) {
        candidates.push({
          tableOffset: offset,
          assemblyIdx,
          typeStart,
          typeCount,
          // Figure out struct size based on position
          possibleStructSize: offset > 0 ? offset : 'first'
        });
      }
    }

    // Also scan other images to determine struct size
    // Read all entries with nameIdx that map to valid strings
    const validEntries = [];
    for (let stride = 16; stride <= 48; stride += 4) {
      let valid = 0;
      const numEntries = Math.floor(imagesSz / stride);
      for (let i = 0; i < numEntries; i++) {
        const nameIdx = view.getInt32(imgBase + i * stride, true);
        const name = readStr(nameIdx);
        if (name.length > 0 && name.length < 100 && /^[A-Za-z]/.test(name)) {
          valid++;
        }
      }
      if (valid > numEntries * 0.6) { // at least 60% valid
        validEntries.push({ stride, numEntries, valid, pct: (valid/numEntries*100).toFixed(0) + '%' });
      }
    }

    // Pick best stride
    const bestStride = validEntries.sort((a, b) => b.valid - a.valid)[0];

    let result = {
      asmCSharpIdx: asmIdx,
      fullName,
      imagesSz,
      imgMatches,
      candidates,
      validEntries,
      bestStride,
    };

    // If we found the game assembly, parse its types
    if (candidates.length > 0) {
      const ga = candidates[0];
      const totalTypeDefs = Math.floor(typeDefsSz / 88);

      result.gameTypeStart = ga.typeStart;
      result.gameTypeCount = ga.typeCount;
      result.totalTypeDefs = totalTypeDefs;
      result.gameTypes = [];

      for (let i = ga.typeStart; i < ga.typeStart + ga.typeCount; i++) {
        const base = metaBase + typeDefsOff + i * 88;
        if (base + 88 > heap.length) break;

        const nameIdx = view.getInt32(base, true);
        const nsIdx = view.getInt32(base + 4, true);
        const fieldStart = view.getInt32(base + 36, true);
        const methodStart = view.getInt32(base + 40, true);
        const methodCount = view.getUint16(base + 68, true);
        const fieldCount = view.getUint16(base + 72, true);

        const name = readStr(nameIdx);
        const ns = readStr(nsIdx);
        if (name.startsWith('<') || name.includes('`')) continue;

        const methods = [];
        for (let m = 0; m < methodCount && m < 80; m++) {
          // Il2CppMethodDefinition v29: returnType(4), nameIndex(4), declaringType(4), token(4), genericContainerIndex(2), parameterStart(2), methodIndex(2), parameterCount(2), ...
          // Try sizes: 24, 20, 16, 28
          const mBase = metaBase + methodsOff + (methodStart + m) * 24;
          if (mBase + 24 > heap.length) break;
          const mNameIdx = view.getInt32(mBase + 4, true);
          const mName = readStr(mNameIdx);
          if (mName && mName.length > 0 && mName.length < 100 && !mName.startsWith('.')) {
            methods.push(mName);
          }
        }

        const fields = [];
        for (let f = 0; f < fieldCount && f < 80; f++) {
          const fBase = metaBase + fieldsOff + (fieldStart + f) * 12;
          if (fBase + 12 > heap.length) break;
          const fNameIdx = view.getInt32(fBase, true);
          const fName = readStr(fNameIdx);
          if (fName && fName.length > 0 && fName.length < 100) {
            fields.push(fName);
          }
        }

        result.gameTypes.push({ name, ns, methods, fields, methodCount, fieldCount });
      }
    }

    return result;
  });

  console.log(`Assembly-CSharp at string index: ${asmInfo.asmCSharpIdx}`);
  console.log(`Full name: "${asmInfo.fullName}"`);
  console.log(`Image table size: ${asmInfo.imagesSz}`);
  console.log(`Matches in image table at byte offsets: ${asmInfo.imgMatches?.join(', ')}`);
  console.log(`Valid stride analysis:`, asmInfo.validEntries);
  console.log(`Best stride:`, asmInfo.bestStride);

  if (asmInfo.candidates?.length > 0) {
    console.log(`\nGame assembly candidate:`, asmInfo.candidates[0]);
    console.log(`Total type definitions: ${asmInfo.totalTypeDefs}`);
    console.log(`Game types: ${asmInfo.gameTypeCount} (starting at ${asmInfo.gameTypeStart})`);
  }

  if (asmInfo.gameTypes) {
    console.log(`\n=== Game Types (${asmInfo.gameTypes.length}) ===`);
    for (const t of asmInfo.gameTypes) {
      console.log(`\n--- ${t.ns ? t.ns + '.' : ''}${t.name} (${t.methodCount}m, ${t.fieldCount}f) ---`);
      if (t.fields.length > 0) console.log(`  Fields: ${t.fields.join(', ')}`);
      if (t.methods.length > 0) console.log(`  Methods: ${t.methods.join(', ')}`);
    }
  }

  // === Phase 2: Brute-force test ALL game methods on ALL GameObjects ===
  console.log('\n\n=== Phase 2: Brute-Force Method Testing ===');

  const gameObjects = [
    'VolumeBGM', 'VolumeSE', 'Collider', 'UnityroomApiClient', 'Score',
    'EventSystem', 'Slider', 'Background', 'Kage', 'Circle', 'Frame',
    'Daigomi', 'SE', 'BGM', 'Next', 'Main Camera', 'Canvas'
  ];

  // Collect unique method names from game types
  const methodsToTest = new Set();
  if (asmInfo.gameTypes) {
    for (const t of asmInfo.gameTypes) {
      for (const m of t.methods) {
        if (!['Finalize', 'GetHashCode', 'Equals', 'ToString', 'GetType',
              'MemberwiseClone', 'op_Equality', 'op_Inequality', 'op_Implicit',
              'op_Explicit'].includes(m) &&
            !m.startsWith('get_') && !m.startsWith('set_') &&
            !m.startsWith('add_') && !m.startsWith('remove_') &&
            !m.startsWith('<') && m.length > 1) {
          methodsToTest.add(m);
        }
      }
    }
  }

  // Also add strings from the end of string table (game-specific area)
  const extraMethods = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);
    const metaBase = 21307480;
    const stringOff = view.getInt32(metaBase + 24, true);
    const stringSz = view.getInt32(metaBase + 28, true);
    const strBase = metaBase + stringOff;

    // Extract all strings from last 50K of string table (game-specific area)
    const startIdx = Math.max(0, stringSz - 50000);
    const methods = [];
    let current = '';
    for (let i = startIdx; i < stringSz; i++) {
      const b = heap[strBase + i];
      if (b >= 32 && b < 127) {
        current += String.fromCharCode(b);
      } else if (b === 0 && current.length >= 2 && current.length <= 60) {
        if (/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(current)) {
          methods.push(current);
        }
        current = '';
      } else {
        current = '';
      }
    }
    return methods;
  });

  for (const m of extraMethods) methodsToTest.add(m);

  const methodArray = [...methodsToTest];
  console.log(`Total methods to test: ${methodArray.length}`);
  console.log(`Sample: ${methodArray.slice(0, 50).join(', ')}`);

  const hits = {};
  let tested = 0;
  for (const obj of gameObjects) {
    for (const method of methodArray) {
      consoleLogs.length = 0;
      await gf.evaluate(({o, m}) => {
        try { window.__unityInstance.SendMessage(o, m); } catch(e) {}
      }, {o: obj, m: method});
      await page.waitForTimeout(10);

      const smLogs = consoleLogs.filter(l => l.includes('SendMessage'));
      if (smLogs.length === 0) {
        if (!hits[obj]) hits[obj] = [];
        hits[obj].push(method);
      }
      tested++;
      if (tested % 2000 === 0) {
        console.log(`  Progress: ${tested}/${gameObjects.length * methodArray.length}...`);
      }
    }
  }

  console.log('\n=== Results: Methods that executed on each GameObject ===');
  for (const obj of gameObjects) {
    if (hits[obj] && hits[obj].length > 0) {
      // Separate standard Unity methods from game-specific ones
      const standard = ['Start', 'Awake', 'Update', 'LateUpdate', 'FixedUpdate',
        'OnEnable', 'OnDisable', 'OnDestroy', 'OnApplicationQuit',
        'OnApplicationPause', 'OnApplicationFocus', 'OnGUI',
        'OnPointerDown', 'OnPointerUp', 'OnPointerClick',
        'OnPointerEnter', 'OnPointerExit', 'OnDrag', 'OnBeginDrag',
        'OnEndDrag', 'OnDrop', 'OnSelect', 'OnDeselect', 'OnSubmit',
        'OnCancel', 'OnMove', 'OnScroll', 'OnInitializePotentialDrag',
        'OnValueChanged', 'OnValidate', 'Reset', 'OnRectTransformDimensionsChange',
        'OnDidApplyAnimationProperties', 'OnBecameInvisible', 'OnBecameVisible',
        'OnTransformParentChanged', 'OnTransformChildrenChanged',
        'OnCanvasGroupChanged', 'OnCanvasHierarchyChanged'];
      const gameSpecific = hits[obj].filter(m => !standard.includes(m));
      const stdMethods = hits[obj].filter(m => standard.includes(m));

      console.log(`\n  ${obj}:`);
      if (stdMethods.length > 0) console.log(`    Standard: ${stdMethods.join(', ')}`);
      if (gameSpecific.length > 0) console.log(`    Game-specific: ${gameSpecific.join(', ')}`);
    }
  }

  // === Phase 3: Game state via WebGL pixel analysis ===
  console.log('\n\n=== Phase 3: WebGL Pixel Game State ===');

  // Drop some pieces
  if (iframe) {
    const box = await iframe.boundingBox();
    for (const xr of [0.4, 0.5, 0.35, 0.55, 0.42]) {
      await page.mouse.click(box.x + box.width * xr, box.y + box.height * 0.3);
      await page.waitForTimeout(1500);
    }
  }

  const pixelState = await gf.evaluate(() => {
    return new Promise(resolve => {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        const canvas = document.getElementById('unity-canvas');
        const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
        const w = canvas.width, h = canvas.height;

        function readRegion(sx, sy, sw, sh) {
          const gy = h - sy - sh;
          const p = new Uint8Array(sw * sh * 4);
          gl.readPixels(sx, gy, sw, sh, gl.RGBA, gl.UNSIGNED_BYTE, p);
          return p;
        }

        function analyzeColors(p) {
          const colors = {};
          for (let i = 0; i < p.length; i += 4) {
            const r = p[i], g = p[i+1], b = p[i+2];
            const key = `${Math.floor(r/16)*16},${Math.floor(g/16)*16},${Math.floor(b/16)*16}`;
            colors[key] = (colors[key] || 0) + 1;
          }
          return Object.entries(colors).sort((a,b) => b[1]-a[1]).slice(0,10).map(([c,n]) => `${c}:${n}`);
        }

        // Detect pieces by scanning for non-background colors
        // Background colors: ~(128-160, 128-160, 112-160) gray tones
        function isBackground(r, g, b) {
          return r > 100 && r < 170 && g > 90 && g < 170 && b > 80 && b < 170 &&
                 Math.abs(r - g) < 30 && Math.abs(g - b) < 40;
        }

        // Scan board area: x=295-665, y=60-510
        const boardL = 295, boardR = 665, boardT = 60, boardB = 510;
        const step = 5;
        const rawP = readRegion(boardL, boardT, boardR-boardL, boardB-boardT);
        const bw = boardR - boardL, bh = boardB - boardT;

        const nonBgPixels = [];
        for (let y = 0; y < bh; y += step) {
          for (let x = 0; x < bw; x += step) {
            const idx = (y * bw + x) * 4;
            const r = rawP[idx], g = rawP[idx+1], b = rawP[idx+2];
            if (!isBackground(r, g, b) && (r > 10 || g > 10 || b > 10)) {
              nonBgPixels.push({
                sx: boardL + x, sy: boardT + y,
                r, g, b,
                type: r > 170 && g < 100 ? 'red' :
                      g > 150 && r < 120 ? 'green' :
                      b > 150 && r < 100 && g < 100 ? 'blue' :
                      r > 200 && g > 200 && b > 200 ? 'white' :
                      r > 200 && g > 180 && b < 100 ? 'yellow' :
                      r < 50 && g < 50 && b < 50 ? 'dark' : 'other'
              });
            }
          }
        }

        // Cluster nearby non-background pixels into pieces
        const clusters = [];
        const visited = new Set();
        for (let i = 0; i < nonBgPixels.length; i++) {
          if (visited.has(i)) continue;
          const cluster = [nonBgPixels[i]];
          visited.add(i);
          const queue = [i];
          while (queue.length > 0) {
            const ci = queue.shift();
            const cp = nonBgPixels[ci];
            for (let j = 0; j < nonBgPixels.length; j++) {
              if (visited.has(j)) continue;
              const np = nonBgPixels[j];
              if (Math.abs(cp.sx - np.sx) <= step * 2 && Math.abs(cp.sy - np.sy) <= step * 2) {
                visited.add(j);
                cluster.push(np);
                queue.push(j);
              }
            }
          }
          if (cluster.length >= 3) { // minimum size
            const avgX = cluster.reduce((s,p) => s+p.sx, 0) / cluster.length;
            const avgY = cluster.reduce((s,p) => s+p.sy, 0) / cluster.length;
            const typeCounts = {};
            for (const p of cluster) {
              typeCounts[p.type] = (typeCounts[p.type] || 0) + 1;
            }
            const mainType = Object.entries(typeCounts).sort((a,b) => b[1]-a[1])[0][0];
            clusters.push({
              x: Math.round(avgX),
              y: Math.round(avgY),
              size: cluster.length,
              type: mainType,
              types: typeCounts
            });
          }
        }

        // NEXT piece analysis
        const nextP = readRegion(730, 30, 150, 120);
        const nextColors = analyzeColors(nextP);

        resolve({
          canvasSize: { w, h },
          totalNonBgPixels: nonBgPixels.length,
          byType: nonBgPixels.reduce((a,p) => { a[p.type]=(a[p.type]||0)+1; return a; }, {}),
          clusters: clusters.sort((a,b) => a.y - b.y),
          nextColors
        });
      }));
    });
  });

  console.log(`Non-background pixels: ${pixelState.totalNonBgPixels}`);
  console.log(`By type: ${JSON.stringify(pixelState.byType)}`);
  console.log(`\nDetected clusters (pieces): ${pixelState.clusters.length}`);
  for (const c of pixelState.clusters) {
    console.log(`  (${c.x}, ${c.y}) size=${c.size} type=${c.type} types=${JSON.stringify(c.types)}`);
  }
  console.log(`\nNEXT colors: ${pixelState.nextColors.join(', ')}`);

  console.log('\n=== Complete. Press Ctrl+C to close. ===');
  await new Promise(() => {});
}

extractState().catch(console.error);

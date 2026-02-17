import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function extractState() {
  const browser = await chromium.launch({ headless: false, args: ['--start-maximized'] });
  const context = await browser.newContext();

  // Unity instance capture via Promise.then hook
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
  });

  const page = await context.newPage();

  // Console log capture
  const consoleLogs = [];
  page.on('console', msg => {
    const text = msg.text();
    consoleLogs.push(text);
    if (text.includes('[HOOK]') || text.includes('SendMessage')) {
      console.log(`[CONSOLE] ${text}`);
    }
  });

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(20000);

  const gf = page.frames().find(f => f.url().includes('no_bg=true'));
  if (!gf) { console.log('No game frame'); await browser.close(); return; }

  const ok = await gf.evaluate(() => !!window.__unityInstance);
  if (!ok) { console.log('No Unity instance'); await browser.close(); return; }
  console.log('Unity instance found!');

  // Click to start game
  const iframe = await page.$('#webgl-frame');
  if (iframe) {
    const box = await iframe.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.3);
    await page.waitForTimeout(3000);
  }

  // === Phase 1: Find all GameObjects and their methods ===
  console.log('\n=== Phase 1: Complete Method Discovery ===');

  const gameObjects = [
    'VolumeBGM', 'VolumeSE', 'Collider', 'UnityroomApiClient', 'Score',
    'EventSystem', 'Slider', 'Background', 'Kage', 'Circle', 'Frame',
    'Daigomi', 'SE', 'BGM', 'Next', 'Main Camera', 'Canvas'
  ];

  // Key method candidates from memory strings
  const keyMethods = [
    // Game control
    'Start', 'Awake', 'Update', 'OnEnable', 'OnDisable',
    'Init', 'Initialize', 'Reset', 'Restart', 'GameStart',
    // Score
    'AddScore', 'inputScore', 'SendScore', 'GetScore', 'ResetScore',
    'SetScore', 'UpdateScore', 'ShowScore',
    // Soviet/merge specific
    'AddMakeSovietCount', 'SovietBGMPlay',
    'Merge', 'CanMerge', 'IsMergeable', 'GetMergedPatterns',
    // Land/Piece
    'LandPiece', 'AddPiece', 'RemovePiece', 'GetPiece',
    'Drop', 'DropPiece', 'SpawnPiece', 'CreatePiece',
    // State
    'GetState', 'ExportState', 'DumpState', 'GetGameState',
    'GetBoardState', 'GetPieceList', 'GetLandPieceList',
    // Next
    'SetNext', 'GetNext', 'ShowNext', 'UpdateNext', 'NextPiece',
    // GameOver
    'GameOver', 'CheckGameOver', 'IsGameOver', 'OnGameOver',
    // Click/Input
    'OnClick', 'OnPointerDown', 'OnPointerUp', 'OnMouseDown',
    'OnTouchStart', 'SetPosition', 'MoveTo',
    // UI
    'Show', 'Hide', 'SetActive', 'Toggle',
    'SetText', 'UpdateText', 'Refresh',
    // Audio
    'Play', 'Stop', 'Pause', 'PlaySE', 'PlayBGM',
    'SetVolume', 'Mute', 'Unmute',
    // Collider
    'OnCollisionEnter2D', 'OnTriggerEnter2D',
    // Common Unity
    'OnDestroy', 'OnApplicationQuit', 'OnApplicationPause',
  ];

  const methodResults = {};

  for (const obj of gameObjects) {
    methodResults[obj] = { executed: [], noReceiver: [] };

    for (const method of keyMethods) {
      consoleLogs.length = 0;

      await gf.evaluate(({obj, method}) => {
        try {
          window.__unityInstance.SendMessage(obj, method);
        } catch(e) {}
      }, {obj, method});
      await page.waitForTimeout(30);

      const smLogs = consoleLogs.filter(l => l.includes('SendMessage'));
      if (smLogs.length === 0) {
        methodResults[obj].executed.push(method);
      } else if (smLogs.some(l => l.includes('does not have receiver'))) {
        methodResults[obj].noReceiver.push(method);
      }
    }

    const exec = methodResults[obj].executed;
    if (exec.length > 0) {
      console.log(`\n${obj}: ${exec.length} methods executed: ${exec.join(', ')}`);
    }
  }

  // === Phase 2: Try SendMessage with string parameter ===
  console.log('\n\n=== Phase 2: SendMessage with String Parameters ===');

  // For objects that had methods execute, try sending string params
  for (const obj of gameObjects) {
    const executedMethods = methodResults[obj].executed;
    if (executedMethods.length === 0) continue;

    for (const method of executedMethods) {
      consoleLogs.length = 0;

      // Try calling with a string parameter
      await gf.evaluate(({obj, method}) => {
        try {
          window.__unityInstance.SendMessage(obj, method, 'test');
        } catch(e) {
          console.log(`[ERROR] ${obj}.${method}('test'): ${e.message}`);
        }
      }, {obj, method});
      await page.waitForTimeout(30);

      const errorLogs = consoleLogs.filter(l => l.includes('[ERROR]') || l.includes('Exception'));
      if (errorLogs.length > 0) {
        console.log(`  ${obj}.${method}('test') -> ${errorLogs[0]}`);
      }
    }
  }

  // === Phase 3: IL2CPP Metadata Parsing - Find MainManager class ===
  console.log('\n\n=== Phase 3: IL2CPP Metadata - MainManager Fields ===');

  const il2cppInfo = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);

    // IL2CPP metadata header at offset 21307480
    const metaOffset = 21307480;

    // Read header
    const sanity = view.getUint32(metaOffset, true);
    const version = view.getInt32(metaOffset + 4, true);

    if (sanity !== 0xFAB11BAF) {
      return { error: 'Invalid IL2CPP metadata signature', sanity: sanity.toString(16) };
    }

    // IL2CPP metadata header offsets (version 29)
    // stringOffset = offset 8
    // stringCount = offset 12
    const stringLiteralOffset = view.getInt32(metaOffset + 8, true);
    const stringLiteralSize = view.getInt32(metaOffset + 12, true);

    // String offset
    const stringOffset = view.getInt32(metaOffset + 16, true);
    const stringSize = view.getInt32(metaOffset + 20, true);

    // Events
    const eventsOffset = view.getInt32(metaOffset + 24, true);
    const eventsSize = view.getInt32(metaOffset + 28, true);

    // Properties
    const propertiesOffset = view.getInt32(metaOffset + 32, true);
    const propertiesSize = view.getInt32(metaOffset + 36, true);

    // Methods
    const methodsOffset = view.getInt32(metaOffset + 40, true);
    const methodsSize = view.getInt32(metaOffset + 44, true);

    // ParameterDefaultValues
    const paramDefaultOffset = view.getInt32(metaOffset + 48, true);
    const paramDefaultSize = view.getInt32(metaOffset + 52, true);

    // FieldDefaultValues
    const fieldDefaultOffset = view.getInt32(metaOffset + 56, true);
    const fieldDefaultSize = view.getInt32(metaOffset + 60, true);

    // FieldAndParameterDefaultValueData
    const fieldParamDataOffset = view.getInt32(metaOffset + 64, true);
    const fieldParamDataSize = view.getInt32(metaOffset + 68, true);

    // FieldMarshaledSize (skip - version dependent)
    // skip 72-75

    // Parameters
    const parametersOffset = view.getInt32(metaOffset + 76, true);
    const parametersSize = view.getInt32(metaOffset + 80, true);

    // Fields
    const fieldsOffset = view.getInt32(metaOffset + 84, true);
    const fieldsSize = view.getInt32(metaOffset + 88, true);

    // GenericParameters - skip
    // GenericParameterConstraints - skip

    // GenericContainers (96-103)
    const genericContainersOffset = view.getInt32(metaOffset + 96, true);
    const genericContainersSize = view.getInt32(metaOffset + 100, true);

    // NestedTypes (104-111)
    const nestedTypesOffset = view.getInt32(metaOffset + 104, true);
    const nestedTypesSize = view.getInt32(metaOffset + 108, true);

    // Interfaces (112-119)
    const interfacesOffset = view.getInt32(metaOffset + 112, true);
    const interfacesSize = view.getInt32(metaOffset + 116, true);

    // VTables (120-127)
    const vTablesOffset = view.getInt32(metaOffset + 120, true);
    const vTablesSize = view.getInt32(metaOffset + 124, true);

    // InterfaceOffsets (128-135)
    const interfaceOffsetsOffset = view.getInt32(metaOffset + 128, true);
    const interfaceOffsetsSize = view.getInt32(metaOffset + 132, true);

    // TypeDefinitions
    const typeDefsOffset = view.getInt32(metaOffset + 136, true);
    const typeDefsSize = view.getInt32(metaOffset + 140, true);

    // Images
    const imagesOffset = view.getInt32(metaOffset + 168, true);
    const imagesSize = view.getInt32(metaOffset + 172, true);

    // Assemblies
    const assembliesOffset = view.getInt32(metaOffset + 176, true);
    const assembliesSize = view.getInt32(metaOffset + 180, true);

    // Helper: read string from string table
    function readString(index) {
      let s = '';
      let i = metaOffset + stringOffset + index;
      while (i < heap.length && heap[i] !== 0) {
        s += String.fromCharCode(heap[i]);
        i++;
      }
      return s;
    }

    // Parse TypeDefinitions to find MainManager and related classes
    // TypeDefinition size in v29: 88 bytes
    const typeDefSize = 88;
    const numTypeDefs = typeDefsSize / typeDefSize;

    const gameClasses = [];
    const classKeywords = ['Manager', 'Piece', 'Land', 'Soviet', 'Score', 'Next',
      'Game', 'Board', 'Merge', 'Dead', 'Line', 'Flag', 'Country',
      'Daigomi', 'Block', 'Drop', 'Field', 'Collider', 'Slider'];

    for (let i = 0; i < numTypeDefs && i < 10000; i++) {
      const base = metaOffset + typeDefsOffset + i * typeDefSize;

      const nameIndex = view.getInt32(base, true);
      const namespaceIndex = view.getInt32(base + 4, true);

      const name = readString(nameIndex);
      const ns = readString(namespaceIndex);

      // Filter for game-related classes
      if (classKeywords.some(k => name.includes(k)) || ns.includes('Soviet') || ns.includes('Game')) {
        const byvalTypeIndex = view.getInt32(base + 8, true);
        const declaringTypeIndex = view.getInt32(base + 16, true);
        const parentIndex = view.getInt32(base + 20, true);
        const elementTypeIndex = view.getInt32(base + 24, true);

        const fieldStart = view.getInt32(base + 40, true);
        const methodStart = view.getInt32(base + 48, true);

        const fieldCount = view.getUint16(base + 72, true);
        const methodCount = view.getUint16(base + 70, true);

        // Read field names
        const fields = [];
        for (let f = 0; f < fieldCount && f < 50; f++) {
          const fieldBase = metaOffset + fieldsOffset + (fieldStart + f) * 12; // FieldDefinition = 12 bytes
          if (fieldBase + 12 > heap.length) break;
          const fieldNameIdx = view.getInt32(fieldBase, true);
          const fieldTypeIdx = view.getInt32(fieldBase + 8, true);
          fields.push({
            name: readString(fieldNameIdx),
            typeIdx: fieldTypeIdx
          });
        }

        // Read method names
        const methods = [];
        for (let m = 0; m < methodCount && m < 50; m++) {
          const methodBase = metaOffset + methodsOffset + (methodStart + m) * 24; // MethodDefinition approx
          if (methodBase + 24 > heap.length) break;
          const methodNameIdx = view.getInt32(methodBase + 4, true);
          const methodRetType = view.getInt32(methodBase, true);
          methods.push({
            name: readString(methodNameIdx),
            returnTypeIdx: methodRetType
          });
        }

        gameClasses.push({
          index: i,
          name,
          namespace: ns,
          fieldCount,
          methodCount,
          fieldStart,
          methodStart,
          fields: fields.filter(f => f.name.length > 0 && f.name.length < 100),
          methods: methods.filter(m => m.name.length > 0 && m.name.length < 100)
        });
      }
    }

    return {
      version,
      numTypeDefs,
      stringOffset,
      stringSize,
      typeDefsOffset,
      typeDefsSize,
      fieldsOffset,
      fieldsSize,
      methodsOffset,
      methodsSize,
      gameClasses
    };
  });

  console.log(`IL2CPP v${il2cppInfo.version}, ${il2cppInfo.numTypeDefs} type definitions`);
  console.log(`\nGame-related classes found: ${il2cppInfo.gameClasses.length}`);

  for (const cls of il2cppInfo.gameClasses) {
    console.log(`\n--- ${cls.namespace ? cls.namespace + '.' : ''}${cls.name} ---`);
    if (cls.fields.length > 0) {
      console.log(`  Fields (${cls.fieldCount}):`);
      for (const f of cls.fields) {
        console.log(`    - ${f.name}`);
      }
    }
    if (cls.methods.length > 0) {
      console.log(`  Methods (${cls.methodCount}):`);
      for (const m of cls.methods) {
        console.log(`    - ${m.name}()`);
      }
    }
  }

  // === Phase 4: Try to read game state from memory ===
  console.log('\n\n=== Phase 4: Memory Pattern Search for Game State ===');

  const memoryPatterns = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);
    const view = new DataView(heap.buffer);

    // Search for score value patterns
    // Score in this type of game is typically 0-9999
    // Look for known patterns near "Score" strings

    // First, find all occurrences of "Score" in memory
    const scoreStr = [83, 99, 111, 114, 101]; // "Score" in ASCII
    const scoreLocations = [];

    for (let i = 0; i < heap.length - 5; i += 4) {
      let match = true;
      for (let j = 0; j < 5; j++) {
        if (heap[i + j] !== scoreStr[j]) { match = false; break; }
      }
      if (match) {
        scoreLocations.push(i);
      }
    }

    // Search for float arrays that could represent piece positions
    // In a Suika game, pieces have x,y positions typically in range -5 to 5 (Unity units)
    // or 0-960 pixels. Look for clusters of float values.

    // Also search for "LandPiece" string to find the class metadata
    const lpStr = [76, 97, 110, 100, 80, 105, 101, 99, 101]; // "LandPiece"
    const lpLocations = [];
    for (let i = 0; i < heap.length - 9; i += 4) {
      let match = true;
      for (let j = 0; j < 9; j++) {
        if (heap[i + j] !== lpStr[j]) { match = false; break; }
      }
      if (match) {
        lpLocations.push(i);
      }
    }

    // Search for "MainManager" string
    const mmStr = [77, 97, 105, 110, 77, 97, 110, 97, 103, 101, 114]; // "MainManager"
    const mmLocations = [];
    for (let i = 0; i < heap.length - 11; i += 4) {
      let match = true;
      for (let j = 0; j < 11; j++) {
        if (heap[i + j] !== mmStr[j]) { match = false; break; }
      }
      if (match) {
        mmLocations.push(i);
      }
    }

    return {
      scoreLocations: scoreLocations.slice(0, 20),
      lpLocations: lpLocations.slice(0, 20),
      mmLocations: mmLocations.slice(0, 20),
      memSize: heap.length
    };
  });

  console.log('Memory locations:');
  console.log(`  "Score" found at: ${memoryPatterns.scoreLocations.join(', ')}`);
  console.log(`  "LandPiece" found at: ${memoryPatterns.lpLocations.join(', ')}`);
  console.log(`  "MainManager" found at: ${memoryPatterns.mmLocations.join(', ')}`);

  // === Phase 5: Hook into Unity's internal JS bridge ===
  console.log('\n\n=== Phase 5: JS Bridge / jslib Hooks ===');

  const jsBridge = await gf.evaluate(() => {
    const Module = window.__unityInstance.Module;

    // Check for any registered JS functions (from .jslib plugins)
    const registeredFunctions = [];

    // Look at Module's dynCall functions - these are the actual callable WASM functions
    const dynCallFunctions = Object.keys(Module).filter(k => k.startsWith('dynCall_'));

    // Check if there's a way to call functions directly
    const hasAsmFunctions = typeof Module.asm === 'object';
    const asmKeys = hasAsmFunctions ? Object.keys(Module.asm) : [];

    // Look for JS lib registrations
    // Unity jslib functions are typically registered on Module
    const possibleJsLib = Object.keys(Module).filter(k => {
      return typeof Module[k] === 'function' &&
        !k.startsWith('_') &&
        !k.startsWith('dynCall') &&
        !['print', 'printErr', 'locateFile', 'cacheControl'].includes(k) &&
        k.length > 3;
    });

    // Try calling Module._SendMessage directly with known object names
    // _SendMessage(objectName, methodName, value) takes pointers
    const hasSendMessage = typeof Module._SendMessage === 'function';
    const hasSendMessageString = typeof Module._SendMessageString === 'function';

    // Check for Pointer_stringify
    const hasPointerStringify = typeof Module.Pointer_stringify === 'function';

    return {
      dynCallCount: dynCallFunctions.length,
      dynCallSample: dynCallFunctions.slice(0, 5),
      asmKeyCount: asmKeys.length,
      possibleJsLib,
      hasSendMessage,
      hasSendMessageString,
      hasPointerStringify,
      hasMalloc: typeof Module._malloc === 'function',
      hasFree: typeof Module._free === 'function',
      hasStackSave: typeof Module.stackSave === 'function',
    };
  });

  console.log('JS Bridge info:', JSON.stringify(jsBridge, null, 2));

  // === Phase 6: Direct WASM function call to get state ===
  console.log('\n\n=== Phase 6: Direct State Extraction via ccall ===');

  // Try using ccall/cwrap to invoke IL2CPP internal functions
  const directCall = await gf.evaluate(() => {
    const Module = window.__unityInstance.Module;
    const results = {};

    // Try to find and call IL2CPP domain functions
    // il2cpp_domain_get_assemblies would give us access to game assemblies
    // But these are mangled in the WASM export

    // Instead, let's try to hook the SendMessage implementation
    // The internal _SendMessage function takes (char* objectName, char* methodName, char* value)
    // We can use it with stackAlloc to create strings

    try {
      const stackSave = Module.stackSave();

      // Allocate strings on stack
      function allocString(str) {
        const len = str.length + 1;
        const ptr = Module.stackAlloc(len);
        for (let i = 0; i < str.length; i++) {
          Module.HEAPU8[ptr + i] = str.charCodeAt(i);
        }
        Module.HEAPU8[ptr + str.length] = 0;
        return ptr;
      }

      // We can try sending messages via the low-level _SendMessage
      // but we need to understand the result

      Module.stackRestore(stackSave);
    } catch(e) {
      results.stackError = e.message;
    }

    // Try to enumerate all Unity objects via the scripting backend
    // In IL2CPP, Object.FindObjectsOfType can be called via reflection

    return results;
  });

  console.log('Direct call results:', JSON.stringify(directCall, null, 2));

  // === Phase 7: WebGL Canvas Analysis ===
  console.log('\n\n=== Phase 7: WebGL Canvas Analysis ===');

  const canvasAnalysis = await gf.evaluate(() => {
    const canvas = document.getElementById('unity-canvas');
    if (!canvas) return { error: 'No canvas' };

    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return { error: 'No WebGL context' };

    const width = canvas.width;
    const height = canvas.height;

    // Read the entire canvas
    const pixels = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

    // Analyze specific game regions
    // Game field is roughly center of canvas
    // Next piece is top-right
    // Score is somewhere on right side

    // Sample key regions (WebGL Y is flipped: 0 = bottom)
    const regions = {
      // Next piece area (top-right in screen = bottom-right in WebGL coords)
      next_area: { x: Math.floor(width * 0.75), y: Math.floor(height * 0.6), w: Math.floor(width * 0.2), h: Math.floor(height * 0.2) },
      // Board center
      board_top: { x: Math.floor(width * 0.3), y: Math.floor(height * 0.7), w: Math.floor(width * 0.35), h: Math.floor(height * 0.15) },
      board_mid: { x: Math.floor(width * 0.3), y: Math.floor(height * 0.4), w: Math.floor(width * 0.35), h: Math.floor(height * 0.2) },
      board_bot: { x: Math.floor(width * 0.3), y: Math.floor(height * 0.1), w: Math.floor(width * 0.35), h: Math.floor(height * 0.2) },
    };

    const results = { canvasSize: { width, height } };

    for (const [name, region] of Object.entries(regions)) {
      const regionPixels = new Uint8Array(region.w * region.h * 4);
      gl.readPixels(region.x, region.y, region.w, region.h, gl.RGBA, gl.UNSIGNED_BYTE, regionPixels);

      // Collect color histogram (quantized to 32 levels)
      const colors = {};
      let nonBlackCount = 0;
      for (let i = 0; i < regionPixels.length; i += 4) {
        const r = regionPixels[i], g = regionPixels[i+1], b = regionPixels[i+2], a = regionPixels[i+3];
        if (a < 10) continue;
        if (r > 20 || g > 20 || b > 20) nonBlackCount++;
        const key = `${Math.floor(r/32)*32},${Math.floor(g/32)*32},${Math.floor(b/32)*32}`;
        colors[key] = (colors[key] || 0) + 1;
      }

      const topColors = Object.entries(colors)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 8)
        .map(([color, count]) => ({ rgb: color, count, pct: (count / (region.w * region.h) * 100).toFixed(1) + '%' }));

      results[name] = { region, topColors, nonBlackPct: (nonBlackCount / (region.w * region.h) * 100).toFixed(1) + '%' };
    }

    return results;
  });

  console.log(JSON.stringify(canvasAnalysis, null, 2));

  // === Phase 8: Hook mod.print to capture all Unity output ===
  console.log('\n\n=== Phase 8: Intercepting All Unity Output ===');

  // Override Module.print and Module.printErr to capture everything
  await gf.evaluate(() => {
    const origPrint = window.__unityInstance.Module.print;
    const origPrintErr = window.__unityInstance.Module.printErr;

    window.__unityCapturedLogs = [];

    window.__unityInstance.Module.print = function(...args) {
      window.__unityCapturedLogs.push({ type: 'log', msg: args.join(' ') });
      origPrint.apply(this, args);
    };

    window.__unityInstance.Module.printErr = function(...args) {
      window.__unityCapturedLogs.push({ type: 'err', msg: args.join(' ') });
      origPrintErr.apply(this, args);
    };
  });

  // Now trigger some game actions and see what gets logged
  // Click a few places on the game board
  if (iframe) {
    const box = await iframe.boundingBox();
    // Click in the center of game board (should drop a piece)
    await page.mouse.click(box.x + box.width * 0.45, box.y + box.height * 0.3);
    await page.waitForTimeout(2000);

    // Click another position
    await page.mouse.click(box.x + box.width * 0.35, box.y + box.height * 0.3);
    await page.waitForTimeout(2000);

    // Click another position
    await page.mouse.click(box.x + box.width * 0.55, box.y + box.height * 0.3);
    await page.waitForTimeout(2000);
  }

  // Check captured logs
  const capturedLogs = await gf.evaluate(() => window.__unityCapturedLogs || []);
  console.log(`Captured ${capturedLogs.length} Unity logs:`);
  for (const log of capturedLogs) {
    console.log(`  [${log.type}] ${log.msg}`);
  }

  console.log('\n=== Summary ===');
  console.log('GameObjects with methods that executed:');
  for (const obj of gameObjects) {
    const exec = methodResults[obj].executed;
    if (exec.length > 0) {
      console.log(`  ${obj}: ${exec.join(', ')}`);
    }
  }

  console.log('\n=== Complete. Press Ctrl+C to close. ===');
  await new Promise(() => {});
}

extractState().catch(console.error);

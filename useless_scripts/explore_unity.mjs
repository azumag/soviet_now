import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

async function explore() {
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
  });

  const page = await context.newPage();

  // コンソール出力をキャプチャ（SendMessage結果の判定用）
  const consoleLogs = [];
  page.on('console', msg => {
    consoleLogs.push(msg.text());
  });

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.waitForTimeout(20000);

  const gf = page.frames().find(f => f.url().includes('no_bg=true'));
  if (!gf) { console.log('No game frame'); await browser.close(); return; }

  const ok = await gf.evaluate(() => !!window.__unityInstance);
  if (!ok) { console.log('No instance'); await browser.close(); return; }

  // ゲーム開始
  const iframe = await page.$('#webgl-frame');
  if (iframe) {
    const box = await iframe.boundingBox();
    await page.mouse.click(box.x + box.width / 2, box.y + box.height * 0.3);
    await page.waitForTimeout(3000);
  }

  // === 1. ゲーム固有の全文字列をWASMメモリからダンプ ===
  console.log('=== Game-Specific Strings from WASM Memory ===');
  const gameStrings = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);

    // ゲーム固有領域（通常は高アドレスにユーザーコードの文字列がある）
    // IL2CPP metadata offset is around 21307480
    // Game strings are likely in range 21900000-22100000 based on previous findings
    const ranges = [
      [21900000, 22100000],   // game-specific strings area
      [31000000, 31500000],   // another possible area
      [144000000, 145000000], // high address area
    ];

    const allStrings = [];

    for (const [start, end] of ranges) {
      const rangeEnd = Math.min(end, heap.length);
      if (start >= heap.length) continue;

      let currentStr = '';
      for (let i = start; i < rangeEnd; i++) {
        const byte = heap[i];
        if (byte >= 32 && byte < 127) {
          currentStr += String.fromCharCode(byte);
        } else if (byte === 0 && currentStr.length >= 3) {
          allStrings.push({ str: currentStr, offset: i - currentStr.length });
          currentStr = '';
        } else {
          currentStr = '';
        }
      }
    }

    return allStrings.filter(s => s.str.length >= 3 && s.str.length < 120);
  });

  // ゲーム固有の文字列を表示
  console.log(`Total strings found: ${gameStrings.length}`);
  for (const s of gameStrings) {
    console.log(`  @${s.offset}: "${s.str}"`);
  }

  // === 2. 正しいSendMessageテスト（console.logをチェック） ===
  console.log('\n=== SendMessage with Console Check ===');

  // メモリ上で見つかった候補名でSendMessageテスト
  // console.logの出力から "not found" と "does not have receiver" を区別
  const candidateNames = new Set();

  // メモリ文字列からGameObject候補を抽出
  for (const s of gameStrings) {
    const str = s.str;
    // CamelCase or PascalCase の短い名前をGameObject候補とする
    if (str.length >= 3 && str.length <= 40 && /^[A-Z][a-zA-Z0-9_]*$/.test(str)) {
      candidateNames.add(str);
    }
  }

  console.log(`\nCandidate GameObject names: ${candidateNames.size}`);

  // バッチでSendMessageテスト
  const existingObjects = [];
  for (const name of candidateNames) {
    consoleLogs.length = 0; // clear

    await gf.evaluate((name) => {
      window.__unityInstance.SendMessage(name, '_test_nonexistent_method_');
    }, name);

    await page.waitForTimeout(50); // コンソール出力を待つ

    const lastLogs = consoleLogs.filter(l => l.includes('SendMessage'));
    if (lastLogs.length > 0) {
      const lastLog = lastLogs[lastLogs.length - 1];
      if (lastLog.includes('does not have receiver')) {
        existingObjects.push(name);
        console.log(`  FOUND: "${name}" (exists, no receiver for _test_)`);
      }
      // "not found" の場合は存在しない
    } else {
      // ログなし - これもおかしい（存在しないはず）
      // 実際にはUnityがconsole.logに出力するので、ログがない=メソッドが実行された
      existingObjects.push(name);
      console.log(`  FOUND (silent): "${name}"`);
    }
  }

  // === 3. 追加の候補名テスト ===
  console.log('\n=== Additional Name Tests ===');
  const additionalNames = [
    // ゲーム関連の追加候補
    'Spawner', 'SpawnManager', 'PieceSpawner',
    'DeadLine', 'DeadLineManager', 'Deadline',
    'GameOver', 'GameOverManager',
    'ClickArea', 'TouchArea', 'InputArea',
    'PlayField', 'PlayArea', 'GameField',
    'WallLeft', 'WallRight', 'Wall', 'Walls',
    'Floor', 'Bottom', 'Ground',
    'Pointer', 'Cursor', 'Arrow',
    'Container', 'Pool', 'ObjectPool',
    'SoundManager', 'AudioManager', 'SE',
    'BackGround', 'Background', 'BG',
    'Effect', 'EffectManager', 'Particle',
    'UI_Score', 'ScoreText', 'ScoreUI',
    'UI_Next', 'NextText', 'NextUI',
    'Title', 'TitleScreen', 'StartButton',
    'RetryButton', 'RestartButton', 'GameOverUI',
    'Directional Light', 'Light',
    // naichilab unityroom SDK
    'naichilab', 'Scoreboard',
    // Japanese names that might be used
    'Soren', 'Soviet',
  ];

  for (const name of additionalNames) {
    consoleLogs.length = 0;

    await gf.evaluate((name) => {
      window.__unityInstance.SendMessage(name, '_test_nonexistent_method_');
    }, name);
    await page.waitForTimeout(50);

    const lastLogs = consoleLogs.filter(l => l.includes('SendMessage'));
    if (lastLogs.length > 0) {
      const lastLog = lastLogs[lastLogs.length - 1];
      if (lastLog.includes('does not have receiver')) {
        existingObjects.push(name);
        console.log(`  FOUND: "${name}"`);
      }
    } else {
      existingObjects.push(name);
      console.log(`  FOUND (silent): "${name}"`);
    }
  }

  // === 4. 存在するGameObject一覧 ===
  console.log('\n=== All Found GameObjects ===');
  const uniqueObjects = [...new Set(existingObjects)];
  for (const name of uniqueObjects) {
    console.log(`  - ${name}`);
  }

  // === 5. 見つかったGameObjectのメソッド探索 ===
  console.log('\n=== Method Discovery for Found Objects ===');

  // IL2CPPメモリから取得したメソッド名候補
  const methodCandidates = await gf.evaluate(() => {
    const heap = new Uint8Array(window.__unityInstance.Module.HEAPU8.buffer);

    // game code area のメソッド名候補を抽出
    const methods = new Set();
    let currentStr = '';

    // スキャン範囲: ゲームコード領域
    const start = 21900000;
    const end = Math.min(22100000, heap.length);

    for (let i = start; i < end; i++) {
      const byte = heap[i];
      if (byte >= 32 && byte < 127) {
        currentStr += String.fromCharCode(byte);
      } else if (byte === 0 && currentStr.length >= 3 && currentStr.length <= 50) {
        // メソッド名っぽいもの（CamelCase, 小文字始まり等）
        if (/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(currentStr) && !currentStr.includes('__')) {
          methods.add(currentStr);
        }
        currentStr = '';
      } else {
        currentStr = '';
      }
    }

    return [...methods];
  });

  console.log(`Method candidates: ${methodCandidates.length}`);

  // 各GameObjectに対してメソッドをテスト
  for (const obj of uniqueObjects.slice(0, 10)) { // 最初の10個だけ
    console.log(`\n--- Testing methods on "${obj}" ---`);
    const receivedMethods = [];

    for (const method of methodCandidates) {
      consoleLogs.length = 0;

      await gf.evaluate(({obj, method}) => {
        window.__unityInstance.SendMessage(obj, method);
      }, {obj, method});
      await page.waitForTimeout(20);

      const smLogs = consoleLogs.filter(l => l.includes('SendMessage') && l.includes(obj));
      if (smLogs.length === 0) {
        // ログなし = メソッドが正常に実行された
        receivedMethods.push(method);
      } else if (smLogs.some(l => l.includes('does not have receiver'))) {
        // メソッドがない
      }
    }

    if (receivedMethods.length > 0) {
      console.log(`  Methods that executed (${receivedMethods.length}):`);
      for (const m of receivedMethods) {
        console.log(`    - ${m}`);
      }
    } else {
      console.log('  No custom methods found');
    }
  }

  // === 6. WebGL readPixels テスト ===
  console.log('\n=== WebGL readPixels Test ===');
  const pixelTest = await gf.evaluate(() => {
    const canvas = document.getElementById('unity-canvas');
    if (!canvas) return { error: 'No canvas' };

    const gl = canvas.getContext('webgl2') || canvas.getContext('webgl');
    if (!gl) return { error: 'No WebGL context' };

    // NEXT領域の色を読み取る（右上エリア）
    const width = canvas.width;
    const height = canvas.height;

    // WebGLではY軸が反転している
    const regions = {
      next: { x: Math.floor(width * 0.78), y: Math.floor(height * 0.72), w: 100, h: 100 },
      board_center: { x: Math.floor(width * 0.45), y: Math.floor(height * 0.3), w: 100, h: 100 },
      score: { x: Math.floor(width * 0.85), y: Math.floor(height * 0.9), w: 80, h: 30 },
    };

    const results = {};
    results.canvasSize = { width, height };

    for (const [name, region] of Object.entries(regions)) {
      const pixels = new Uint8Array(region.w * region.h * 4);
      gl.readPixels(region.x, region.y, region.w, region.h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);

      // 主要色を集計
      const colors = {};
      for (let i = 0; i < pixels.length; i += 4) {
        const r = pixels[i], g = pixels[i+1], b = pixels[i+2], a = pixels[i+3];
        if (a < 10) continue; // 透明ピクセルをスキップ
        const key = `${Math.floor(r/16)*16},${Math.floor(g/16)*16},${Math.floor(b/16)*16}`;
        colors[key] = (colors[key] || 0) + 1;
      }

      // 上位5色
      const topColors = Object.entries(colors)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 5)
        .map(([color, count]) => ({ color, count }));

      results[name] = {
        region,
        topColors,
        totalPixels: region.w * region.h
      };
    }

    return results;
  });

  console.log(JSON.stringify(pixelTest, null, 2));

  console.log('\n=== Complete ===');
  await new Promise(() => {});
}

explore().catch(console.error);

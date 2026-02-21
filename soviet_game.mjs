import { chromium } from 'playwright';
import fs from 'fs';
import sharp from 'sharp';

const URL = 'https://unityroom.com/games/sorengame';
const COMMAND_FILE = 'commands.txt';
// const GAME_STATE_PATH = 'tmp/game_state.json';



// WebGL Draw Callフックからカーソル/NEXTの存在を検出（ループのタイミング制御用）
// 盤面分析はAIのOBSERVEフェーズがスクリーンショットから行う
async function extractGameStateFromDrawCalls(page) {
  try {
    const data = await page.evaluate(() => {
      window.__wglGameObjects = [];
      window.__wglCapturing = true;
      return new Promise(resolve => {
        requestAnimationFrame(() => {
          requestAnimationFrame(() => {
            window.__wglCapturing = false;
            const objs = window.__wglGameObjects;
            const unique = [];
            for (const o of objs) {
              let isDup = false;
              for (const u of unique) {
                if (Math.abs(u.tx - o.tx) < 0.01 && Math.abs(u.ty - o.ty) < 0.01 && Math.abs(u.sx - o.sx) < 0.01) {
                  isDup = true;
                  break;
                }
              }
              if (!isDup) unique.push(o);
            }
            resolve(unique);
          });
        });
      });
    });

    if (!data || data.length === 0) return null;

    const gameData = data.filter(o => o.stride === 24);
    const DANGER_LINE_Y = 3.32;
    const BOARD_LEFT = -3.0;
    const BOARD_RIGHT = 3.0;

    // カーソル検出: デンジャーライン上の通常スケールオブジェクト
    const cursor = gameData.find(o =>
      o.ty > DANGER_LINE_Y + 0.3 && o.tx >= BOARD_LEFT && o.tx <= BOARD_RIGHT
      && o.sx < 10 && o.sx > 0.5 && o.v <= 6
    );

    // NEXT検出: 右側UI領域のオブジェクト
    const next = data.find(o => o.tx > 5);

    return {
      cursor: cursor ? true : false,
      next: next ? true : false,
    };
  } catch (e) {
    console.error('Error extracting game state:', e.message);
    return null;
  }
}

// スクリーンショットを1回だけ撮影し、sharpでクロップ（ちらつき防止）
async function takeScreenshots(page) {
  try {
    const buf = await page.screenshot();
    const writes = [
      fs.promises.writeFile('soviet_now.png', buf),
      sharp(buf).extract({ left: 300, top: 0, width: 650, height: 720 }).toFile('board.png'),
      sharp(buf).extract({ left: 980, top: 60, width: 180, height: 300 }).toFile('next_block.png'),
    ];
    await Promise.all(writes);
    console.log('Screenshots updated');
  } catch (e) {
    console.error('Screenshot error:', e.message);
  }
}

// ゲーム状態を更新（cursor/nextの有無 + ゲームオーバー判定）+ スクリーンショット
async function updateGameState(page) {
  const gameState = await extractGameStateFromDrawCalls(page);
  if (gameState) {
    // fs.writeFileSync(GAME_STATE_PATH, JSON.stringify(gameState, null, 2));
    console.log(`State: cursor=${gameState.cursor}, next=${gameState.next}`);
  }
  await takeScreenshots(page);
  return gameState;
}

// コマンドファイルから操作を読み取る
function readCommands() {
  try {
    if (!fs.existsSync(COMMAND_FILE)) {
      return [];
    }
    const content = fs.readFileSync(COMMAND_FILE, 'utf-8').trim();
    if (!content) return [];

    // フォーマット: x,y (1行につき1コマンド)
    // または JSON: [{"x": 100, "y": 200}, ...]
    const lines = content.split('\n').filter(line => line.trim());
    const commands = [];

    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed.toLowerCase() === 'retry') {
        // リトライコマンド（ゲームオーバー時に中央クリック）
        commands.push({ action: 'retry' });
      } else if (trimmed.startsWith('[')) {
        // JSON形式
        try {
          const jsonCommands = JSON.parse(trimmed);
          commands.push(...jsonCommands);
        } catch (e) {
          console.log('Failed to parse JSON:', trimmed);
        }
      } else {
        // x,y 形式
        const parts = trimmed.split(',').map(s => parseInt(s.trim()));
        if (parts.length === 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
          commands.push({ x: parts[0], y: parts[1] });
        }
      }
    }

    return commands;
  } catch (e) {
    console.error('Error reading commands:', e);
    return [];
  }
}

// コマンドファイルをクリアする
function clearCommands() {
  try {
    fs.writeFileSync(COMMAND_FILE, '');
  } catch (e) {
    console.error('Error clearing commands:', e);
  }
}

// キャンバス内部座標をビューポート座標に変換
function canvasToViewport(canvasX, canvasY, canvasInfo) {
  const { rect, width, height } = canvasInfo;
  const viewportX = rect.x + (canvasX / width) * rect.w;
  const viewportY = rect.y + (canvasY / height) * rect.h;
  return { viewportX, viewportY };
}

// コマンドを実行してスクリーンショットを撮る
async function executeCommand(page, command, canvasInfo) {
  if (command.action === 'retry') {
    // ゲームオーバー時のRETRYボタン：横中央、縦73%あたり
    const centerX = canvasInfo.width / 2;
    const centerY = canvasInfo.height * 0.73;
    const { viewportX, viewportY } = canvasToViewport(centerX, centerY, canvasInfo);
    console.log(`Executing: RETRY (canvas center ${centerX},${centerY} -> viewport ${viewportX.toFixed(0)},${viewportY.toFixed(0)})`);
    await page.mouse.click(viewportX, viewportY);
    await page.waitForTimeout(1000);
    await updateGameState(page);
    return;
  }

  const { x, y } = command;

  // 座標が Canvas の範囲内にあるか確認
  const clampedX = Math.max(0, Math.min(x, canvasInfo.width));
  const clampedY = Math.max(0, Math.min(y, canvasInfo.height));

  const { viewportX, viewportY } = canvasToViewport(clampedX, clampedY, canvasInfo);
  console.log(`Executing: click at canvas (${clampedX}, ${clampedY}) -> viewport (${viewportX.toFixed(0)}, ${viewportY.toFixed(0)})`);

  // マウスを移動してクリック
  await page.mouse.move(viewportX, viewportY, { steps: 3 });
  await page.waitForTimeout(100);
  await page.mouse.click(viewportX, viewportY);

  // ピースが着地+マージ演出が完了するまで待つ
  await page.waitForTimeout(3000);

  // ゲーム状態を更新
  await updateGameState(page);
}

async function runGameController() {
  const browser = await chromium.launch({
    headless: false,
    args: ['--window-size=1280,720']
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 720 },
    deviceScaleFactor: 1,
  });

  // WebGL Draw Call フック（ゲームオブジェクト位置を直接取得）
  // stride=24のdraw callがゲームオブジェクト（ピース、カーソル、NEXT、危険線）
  // stride=76はUI要素（フィルタ対象外）
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

      // vertexAttribPointer でストライドを記録（stride=24がゲームオブジェクト）
      const origVAP = proto.vertexAttribPointer;
      if (origVAP) {
        proto.vertexAttribPointer = function(index, size, type, normalized, stride, offset) {
          if (window.__wglCapturing && index === 0) {
            currentStride = stride;
          }
          return origVAP.apply(this, arguments);
        };
      }

      // uniform4fv で4x4行列を取得（srcLength=16のとき）
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

      // drawElements で行列ありのdraw callを記録（stride情報付き）
      const origDE = proto.drawElements;
      if (origDE) {
        proto.drawElements = function(mode, count, type, offset) {
          if (window.__wglCapturing && currentMat) {
            window.__wglGameObjects.push({
              tx: Math.round(currentMat.tx * 10000) / 10000,
              ty: Math.round(currentMat.ty * 10000) / 10000,
              tz: Math.round(currentMat.tz * 10000) / 10000,
              sx: Math.round(currentMat.sx * 10000) / 10000,
              sy: Math.round(currentMat.sy * 10000) / 10000,
              rot: Math.round(currentMat.rot * 100) / 100,
              v: count,
              stride: currentStride,
            });
            currentMat = null;
          }
          return origDE.apply(this, arguments);
        };
      }

      // useProgram で状態リセット
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

  console.log('=== Starting Soren Game Controller ===');

  // Step 1: メインページからゲームiframeのURLを取得
  console.log('Loading main page to find game iframe URL...');
  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  } catch (e) {
    console.log(`Navigation warning: ${e.message.split('\n')[0]} — waiting for page to settle...`);
  }
  await page.waitForTimeout(10000);

  const gameFrame = page.frames().find(f => f.url().includes('no_bg=true'));
  if (!gameFrame) {
    console.log('Game iframe not found! Trying direct canvas...');
  }
  const gameUrl = gameFrame ? gameFrame.url() : null;

  // Step 2: ゲームURLに直接ナビゲート（フルスクリーン表示）
  if (gameUrl) {
    console.log(`Found game URL, navigating directly for full-screen display...`);
    try {
      await page.goto(gameUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });
    } catch (e) {
      console.log(`Game navigation warning: ${e.message.split('\n')[0]} — waiting...`);
    }
    await page.waitForTimeout(10000);
  }

  // Step 3: Canvas情報を取得（Unity初期化完了まで待機）
  let canvasInfo = null;
  for (let i = 0; i < 30; i++) {
    canvasInfo = await page.evaluate(() => {
      const canvas = document.getElementById('unity-canvas') || document.querySelector('canvas');
      if (canvas && canvas.width > 300) {
        const rect = canvas.getBoundingClientRect();
        return {
          width: canvas.width,
          height: canvas.height,
          rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height }
        };
      }
      return null;
    });
    if (canvasInfo) break;
    console.log(`Waiting for Unity canvas init... (${i + 1}/30)`);
    await page.waitForTimeout(1000);
  }

  if (!canvasInfo) {
    console.log('Canvas not found or Unity failed to initialize!');
    await browser.close();
    return;
  }

  console.log(`Canvas: ${canvasInfo.width}x${canvasInfo.height}, Display: ${Math.round(canvasInfo.rect.w)}x${Math.round(canvasInfo.rect.h)} at (${Math.round(canvasInfo.rect.x)},${Math.round(canvasInfo.rect.y)})`);
  console.log(`Waiting for commands from: ${COMMAND_FILE}`);
  console.log(`Format: "x,y" per line, "retry" for game over, or JSON [{"x":100,"y":200},...]`);

  // ゲーム開始クリック
  const startClick = canvasToViewport(canvasInfo.width / 2, canvasInfo.height * 0.3, canvasInfo);
  await page.mouse.click(startClick.viewportX, startClick.viewportY);
  await page.waitForTimeout(2000);

  // 初期ゲーム状態取得
  await updateGameState(page);
  console.log('Initial game state saved');

  // メインループ：コマンドファイルを監視
  let processedCount = 0;
  let idleCount = 0;
  const STATE_REFRESH_INTERVAL = 4; // 4ループ(2秒)ごとに状態更新

  while (true) {
    const commands = readCommands();

    // 新しいコマンドがあれば実行
    if (commands.length > processedCount) {
      idleCount = 0;
      for (let i = processedCount; i < commands.length; i++) {
        await executeCommand(page, commands[i], canvasInfo);
        processedCount++;

        // すべてのコマンドを実行したらファイルをクリア
        if (i === commands.length - 1) {
          clearCommands();
          processedCount = 0;
        }
      }
    } else {
      // コマンドがないときも定期的にゲーム状態を更新（state_loop用）
      idleCount++;
      if (idleCount >= STATE_REFRESH_INTERVAL) {
        idleCount = 0;
        await updateGameState(page);
      }
    }

    // ポーリング間隔
    await page.waitForTimeout(500);
  }
}

runGameController().catch(console.error);

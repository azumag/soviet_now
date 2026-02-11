import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';
const COMMAND_FILE = 'commands.txt';
const SCREENSHOT_PATH = 'soviet_now.png';

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
    await page.screenshot({ path: SCREENSHOT_PATH });
    console.log(`Screenshot saved to ${SCREENSHOT_PATH}`);
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

  // 少し待つ
  await page.waitForTimeout(500);

  // スクリーンショットを撮る
  await page.screenshot({ path: SCREENSHOT_PATH });
  console.log(`Screenshot saved to ${SCREENSHOT_PATH}`);
}

async function runGameController() {
  const browser = await chromium.launch({
    headless: false,
    args: ['--start-maximized']
  });

  const context = await browser.newContext();

  const page = await context.newPage();

  console.log('=== Starting Soviet Game Controller ===');
  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(5000);

  // Canvas情報を取得（内部解像度 + ビューポート上の位置・サイズ）
  const canvasInfo = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      return {
        width: canvas.width,
        height: canvas.height,
        rect: { x: rect.x, y: rect.y, w: rect.width, h: rect.height }
      };
    }
    return null;
  });

  if (!canvasInfo) {
    console.log('Canvas not found!');
    await browser.close();
    return;
  }

  console.log(`Canvas size: ${canvasInfo.width}x${canvasInfo.height}`);
  console.log(`Waiting for commands from: ${COMMAND_FILE}`);
  console.log(`Format: "x,y" per line, "retry" for game over, or JSON [{"x":100,"y":200},...]`);

  // ゲーム開始クリック
  const startClick = canvasToViewport(canvasInfo.width / 2, canvasInfo.height * 0.3, canvasInfo);
  await page.mouse.click(startClick.viewportX, startClick.viewportY);
  await page.waitForTimeout(1000);

  // 初期スクリーンショット
  await page.screenshot({ path: SCREENSHOT_PATH });
  console.log('Initial screenshot saved');

  // メインループ：コマンドファイルを監視
  let lastCommands = [];
  let processedCount = 0;

  while (true) {
    const commands = readCommands();

    // 新しいコマンドがあれば実行
    if (commands.length > processedCount) {
      for (let i = processedCount; i < commands.length; i++) {
        await executeCommand(page, commands[i], canvasInfo);
        processedCount++;

        // すべてのコマンドを実行したらファイルをクリア
        if (i === commands.length - 1) {
          clearCommands();
          processedCount = 0;
        }
      }
    }

    // ポーリング間隔
    await page.waitForTimeout(500);
  }
}

runGameController().catch(console.error);

import { chromium } from 'playwright';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

// グローバル変数（サーバーとして保持）
let browser = null;
let page = null;
let canvasInfo = null;

// ゲームを開始
export async function startGame() {
  if (browser) {
    console.log('Game already started');
    return { success: false, message: 'Game already running' };
  }

  browser = await chromium.launch({
    headless: false
  });

  const context = await browser.newContext();
  page = await context.newPage();

  console.log('Navigating to game...');
  await page.goto(URL, { waitUntil: 'networkidle' });

  // ゲームロード待機
  await page.waitForTimeout(6000);

  canvasInfo = await page.evaluate(() => {
    const canvas = document.querySelector('canvas');
    if (canvas) {
      const rect = canvas.getBoundingClientRect();
      return {
        width: canvas.width,
        height: canvas.height,
        rectX: rect.x,
        rectY: rect.y,
        rectWidth: rect.width,
        rectHeight: rect.height
      };
    }
    return null;
  });

  // ゲーム開始クリック
  await page.mouse.click(canvasInfo.rectX + canvasInfo.rectWidth / 2, canvasInfo.rectY + 200);
  await page.waitForTimeout(1000);

  return {
    success: true,
    canvasInfo: canvasInfo,
    message: 'Game started successfully'
  };
}

// スクリーンショットを撮る
export async function takeScreenshot(path = 'soviet_current.png') {
  if (!page) {
    return { success: false, message: 'Game not started' };
  }

  await page.screenshot({ path });
  return {
    success: true,
    path: path,
    message: `Screenshot saved to ${path}`
  };
}

// マウスを移動
export async function moveMouse(x, y) {
  if (!page || !canvasInfo) {
    return { success: false, message: 'Game not started' };
  }

  // Canvasの相対座標を絶対座標に変換
  const absX = canvasInfo.rectX + x;
  const absY = canvasInfo.rectY + y;

  await page.mouse.move(absX, absY);
  return {
    success: true,
    message: `Mouse moved to (${x}, ${y})`
  };
}

// クリック
export async function clickAt(x, y) {
  if (!page || !canvasInfo) {
    return { success: false, message: 'Game not started' };
  }

  const absX = canvasInfo.rectX + x;
  const absY = canvasInfo.rectY + y;

  await page.mouse.click(absX, absY);
  return {
    success: true,
    message: `Clicked at (${x}, ${y})`
  };
}

// 国旗を落とす（移動→クリック）
export async function dropFlag(x, y) {
  if (!page || !canvasInfo) {
    return { success: false, message: 'Game not started' };
  }

  const absX = canvasInfo.rectX + x;
  const absY = canvasInfo.rectY + y;

  await page.mouse.move(absX, absY);
  await page.waitForTimeout(300);
  await page.mouse.click(absX, absY);

  return {
    success: true,
    message: `Dropped flag at (${x}, ${y})`
  };
}

// ゲームを終了
export async function endGame() {
  if (browser) {
    await browser.close();
    browser = null;
    page = null;
    canvasInfo = null;
    return { success: true, message: 'Game closed' };
  }
  return { success: false, message: 'No game running' };
}

// ステータス確認
export function getStatus() {
  return {
    running: browser !== null,
    canvasInfo: canvasInfo
  };
}

// CLIから実行する場合の処理
const args = process.argv.slice(2);
const command = args[0];

async function main() {
  switch (command) {
    case 'start':
      await startGame();
      console.log('Game started. Press Ctrl+C to keep it running, or use another terminal to control.');
      console.log('Canvas info:', canvasInfo);
      // ブラウザを開いたまま保持
      process.stdin.resume();
      break;

    case 'screenshot':
      const result = await takeScreenshot(args[1] || 'soviet_current.png');
      console.log(result.message);
      await endGame();
      break;

    case 'drop':
      const x = parseInt(args[1]);
      const y = parseInt(args[2]);
      await dropFlag(x, y);
      await page.waitForTimeout(1000);
      await takeScreenshot(args[3] || 'soviet_after_drop.png');
      console.log('Flag dropped and screenshot taken');
      await endGame();
      break;

    default:
      console.log(`
Usage:
  node soviet_controller.mjs start
  node soviet_controller.mjs screenshot [path]
  node soviet_controller.mjs drop <x> <y> [screenshot_path]

For interactive play, use the controller functions in a Node.js REPL.
      `);
  }

  if (command !== 'start') {
    await endGame();
  }
}

if (import.meta.url === `file://${process.argv[1]}`) {
  main().catch(console.error);
}

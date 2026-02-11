import { chromium } from 'playwright';
import fs from 'fs';

const URL = 'https://43469.play.unityroom.com/?expires=1770895465&salt=204822083100176348322172862835957129961&sig=9e18bdbb430a5b26db652e81c2c8f992f314ce7b';

class SovietGameController {
  constructor() {
    this.browser = null;
    this.page = null;
    this.canvasInfo = null;
    this.moveCount = 0;
  }

  async init() {
    if (this.browser) {
      console.log('Already initialized');
      return;
    }

    this.browser = await chromium.launch({
      headless: false
    });

    const context = await this.browser.newContext();
    this.page = await context.newPage();

    console.log('Loading game...');
    await this.page.goto(URL, { waitUntil: 'networkidle' });
    await this.page.waitForTimeout(6000);

    // Canvas情報取得
    this.canvasInfo = await this.page.evaluate(() => {
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

    console.log('Canvas info:', this.canvasInfo);

    // ゲーム開始クリック
    const centerX = this.canvasInfo.rectX + this.canvasInfo.width / 2;
    await this.page.mouse.click(centerX, this.canvasInfo.rectY + 200);
    await this.page.waitForTimeout(1500);

    console.log('Game started!');
  }

  async screenshot(path = null) {
    if (!this.page) {
      console.log('Not initialized');
      return null;
    }

    const screenshotPath = path || `soviet_move_${this.moveCount}.png`;
    await this.page.screenshot({ path: screenshotPath });
    console.log(`Screenshot saved: ${screenshotPath}`);
    return screenshotPath;
  }

  async moveAndDrop(x, y) {
    if (!this.page || !this.canvasInfo) {
      console.log('Not initialized');
      return false;
    }

    const absX = this.canvasInfo.rectX + x;
    const absY = this.canvasInfo.rectY + y;

    console.log(`Move to (${x}, ${y}) and drop`);
    await this.page.mouse.move(absX, absY);
    await this.page.waitForTimeout(300);
    await this.page.mouse.click(absX, absY);
    await this.page.waitForTimeout(800);

    this.moveCount++;
    return true;
  }

  async waitForDrop(ms = 1000) {
    await this.page.waitForTimeout(ms);
  }

  async close() {
    if (this.browser) {
      await this.browser.close();
      this.browser = null;
      this.page = null;
      console.log('Browser closed');
    }
  }
}

// コマンドプロセッサ
const controller = new SovietGameController();

async function processCommand(command) {
  const parts = command.trim().split(/\s+/);
  const cmd = parts[0].toLowerCase();

  switch (cmd) {
    case 'init':
    case 'start':
      await controller.init();
      break;

    case 'drop':
      const x = parseInt(parts[1]);
      const y = parseInt(parts[2]);
      await controller.moveAndDrop(x, y);
      if (parts[3] === 'screenshot' || parts.includes('-s')) {
        await controller.screenshot();
      }
      break;

    case 'screenshot':
    case 'snap':
      await controller.screenshot(parts[1]);
      break;

    case 'wait':
      const ms = parseInt(parts[1]) || 1000;
      await controller.waitForDrop(ms);
      break;

    case 'close':
    case 'exit':
    case 'quit':
      await controller.close();
      process.exit(0);

    default:
      console.log(`Unknown command: ${cmd}`);
      console.log('Available: init, drop <x> <y> [-s], screenshot [path], wait <ms>, close');
  }
}

// メイン処理
async function main() {
  // 初期化
  await controller.init();
  await controller.screenshot('soviet_initial.png');

  console.log('\n=== Soviet Game Controller Ready ===');
  console.log('Commands: drop <x> <y> [-s], screenshot [path], wait <ms>, close');
  console.log('Example: drop 400 200 -s');
  console.log('');

  // 標準入力からコマンドを受け取る
  const readline = require('readline');
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: '> '
  });

  rl.prompt();

  rl.on('line', async (line) => {
    if (line.trim()) {
      await processCommand(line);
    }
    rl.prompt();
  });

  rl.on('close', async () => {
    await controller.close();
    process.exit(0);
  });
}

main().catch(console.error);
